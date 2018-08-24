import json
import sys
import base64
import inspect
from io import BytesIO

import param
import bokeh
import bokeh.embed.notebook
from bokeh.models import Model, LayoutDOM, Div as BkDiv, Spacer, Row
from bokeh.protocol import Protocol
from bokeh.util.string import encode_utf8
from pyviz_comms import JupyterCommManager, bokeh_msg_handler, PYVIZ_PROXY, embed_js


def get_method_owner(meth):
    """
    Returns the instance owning the supplied instancemethod or
    the class owning the supplied classmethod.
    """
    if inspect.ismethod(meth):
        if sys.version_info < (3,0):
            return meth.im_class if meth.im_self is None else meth.im_self
        else:
            return meth.__self__

################################
# Display and update utilities #
################################


def Div(**kwargs):
    # Hack to work around issues with Div height in notebooks
    div = BkDiv(**kwargs)
    if 'height' in kwargs:
        return Row(div, Spacer(height=kwargs['height']))
    return div


def diff(doc, binary=True, events=None):
    """
    Returns a json diff required to update an existing plot with
    the latest plot data.
    """
    events = list(doc._held_events) if events is None else events
    if not events:
        return None
    msg = Protocol("1.0").create("PATCH-DOC", events, use_buffers=binary)
    doc._held_events = [e for e in doc._held_events if e not in events]
    return msg


def push(doc, comm, binary=True):
    """
    Pushes events stored on the document across the provided comm.
    """
    msg = diff(doc, binary=binary)
    if msg is None:
        return
    comm.send(msg.header_json)
    comm.send(msg.metadata_json)
    comm.send(msg.content_json)
    for header, payload in msg.buffers:
        comm.send(json.dumps(header))
        comm.send(buffers=[payload])


def add_to_doc(obj, doc, hold=False):
    """
    Adds a model to the supplied Document removing it from any existing Documents.
    """
    # Handle previously displayed models
    for model in obj.select({'type': Model}):
        prev_doc = model.document
        model._document = None
        if prev_doc:
            prev_doc.remove_root(model)

    # Add new root
    doc.add_root(obj)
    if doc._hold is None and hold:
        doc.hold()


def render_mimebundle(model, doc, comm):
    """
    Displays bokeh output inside a notebook using the PyViz display
    and comms machinery.
    """
    from IPython.display import publish_display_data
    if not isinstance(model, LayoutDOM): 
        raise ValueError('Can only render bokeh LayoutDOM models')

    add_to_doc(model, doc, True)

    target = model.ref['id']
    load_mime = 'application/vnd.holoviews_load.v0+json'
    exec_mime = 'application/vnd.holoviews_exec.v0+json'

    # Publish plot HTML
    bokeh_script, bokeh_div, _ = bokeh.embed.notebook.notebook_content(model, comm.id)
    html = encode_utf8(bokeh_div)

    # Publish comm manager
    JS = '\n'.join([PYVIZ_PROXY, JupyterCommManager.js_manager])
    publish_display_data(data={load_mime: JS, 'application/javascript': JS})

    # Publish bokeh plot JS
    msg_handler = bokeh_msg_handler.format(plot_id=target)
    comm_js = comm.js_template.format(plot_id=target, comm_id=comm.id, msg_handler=msg_handler)
    bokeh_js = '\n'.join([comm_js, bokeh_script])
    bokeh_js = embed_js.format(widget_id=target, plot_id=target, html=html) + bokeh_js

    data = {exec_mime: '', 'text/html': html, 'application/javascript': bokeh_js}
    metadata = {exec_mime: {'id': target}}
    return data, metadata