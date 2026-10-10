"""Request guards."""
from functools import wraps

from flask import abort, request

LOCAL_ADDRESSES = {"127.0.0.1", "::1"}


def is_local_request():
    return request.remote_addr in LOCAL_ADDRESSES


def localhost_only(view):
    """Display write routes may only be called by the kiosk on the device itself."""
    @wraps(view)
    def wrapper(*args, **kwargs):
        if not is_local_request():
            abort(403)
        return view(*args, **kwargs)
    wrapper.localhost_only = True      # MF-13: CSRF token not required (kiosk only; Origin still checked)
    return wrapper
