"""Cache-busting for static assets: templates use {{ static('script.js') }} -> /static/script.js?v=<hash>.
The hash is of the file's content (cached per mtime/size), so a deploy that changes a file changes its URL
and Chromium can't keep running the old copy. HTML itself is sent with Cache-Control: no-cache."""
import hashlib
import os

from flask import current_app, url_for

_cache = {}


def version(path):
    st = os.stat(path)
    key = (path, st.st_mtime_ns, st.st_size)
    v = _cache.get(key)
    if v is None:
        with open(path, "rb") as f:
            v = hashlib.sha256(f.read()).hexdigest()[:10]
        _cache[key] = v
    return v


def static(filename):
    try:
        v = version(os.path.join(current_app.static_folder, filename))
    except OSError:
        return url_for("static", filename=filename)
    return url_for("static", filename=filename, v=v)


def no_cache_html(resp):
    if resp.mimetype == "text/html":
        resp.headers["Cache-Control"] = "no-cache"
    return resp


def init_app(app):
    app.jinja_env.globals["static"] = static
    app.after_request(no_cache_html)
