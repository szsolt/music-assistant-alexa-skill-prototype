"""The app's log format, with the part of the app each record comes from."""

import logging

_original_factory = logging.getLogRecordFactory()


def _log_record_factory(*args, **kwargs):
    record = _original_factory(*args, **kwargs)
    if not hasattr(record, 'component'):
        name = record.name or ''
        if name.startswith('web.ma_routes'):
            record.component = 'API'
        elif name.startswith(('ask_sdk', 'skill.')):
            record.component = 'Skill'
        else:
            record.component = 'UI/Web'
    return record


def setup():
    """Give every record a `component` (third-party loggers like werkzeug log before filters run), then log at INFO."""
    logging.setLogRecordFactory(_log_record_factory)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s [%(component)s] %(name)s %(message)s",
        datefmt="%H:%M:%S %Y-%m-%d %z"
    )
