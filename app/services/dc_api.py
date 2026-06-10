from .dc.api import *  # noqa: F401,F403
from .dc import api as _api


def __getattr__(name):
    return getattr(_api, name)
