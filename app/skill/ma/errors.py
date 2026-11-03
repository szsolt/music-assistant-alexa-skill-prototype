"""The one error contract of MA calls, and MA's error text fit for logs and pages."""

import logging
import re

logger = logging.getLogger(__name__)

_QUERY = re.compile(r"(?<=\S)\?[^\s'\"<>]*=[^\s'\"<>]*")


class MAUnavailable(Exception):
    """MA can't be reached, or didn't answer in time."""


class MAFailed(Exception):
    """MA refused the command."""


def safe_error(e):
    """e's text without the query strings of the URLs in it, or e's type name if no text is left."""
    return _QUERY.sub("", str(e)).strip() or type(e).__name__


def or_none(what, fn, *args, **kwargs):
    """fn(*args, **kwargs), or None after logging why MA didn't do it. what: "read the queue", ..."""
    try:
        return fn(*args, **kwargs)
    except MAUnavailable as e:
        logger.warning("Could not reach Music Assistant to %s: %s", what, safe_error(e))
    except MAFailed as e:
        logger.warning("Music Assistant refused to %s: %s", what, safe_error(e))
    return None
