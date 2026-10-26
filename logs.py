"""Logging that actually writes somewhere.

config.LOG_FILE has existed since the first commit but nothing ever used it,
which is why TTS failures (edge-tts 403s, missing voices) were invisible:
the app printed to stdout and the terminal was usually closed by then.

Everything goes to the log file AND to stdout, so a terminal launch still
shows what is happening.
"""
import logging
import logging.handlers
import os
import sys

_configured = False


def setup(path=None, level=None):
    """Attach a rotating file handler + a stdout handler to the root logger."""
    global _configured
    if _configured:
        return
    try:
        import config
        path = path or config.LOG_FILE
        level = level if level is not None else getattr(config, "LOG_LEVEL", 2)
    except Exception:
        path = path or os.path.expanduser("~/.local/state/HarfDown2List/hd2l.log")

    root = logging.getLogger()
    root.setLevel(logging.DEBUG)

    fmt = logging.Formatter("%(asctime)s %(levelname)-7s %(name)-8s %(message)s",
                            "%Y-%m-%d %H:%M:%S")
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        fh = logging.handlers.RotatingFileHandler(
            path, maxBytes=2_000_000, backupCount=3)
        fh.setLevel(logging.INFO)
        fh.setFormatter(fmt)
        root.addHandler(fh)
    except Exception as e:                       # unwritable log dir is not fatal
        print(f"[log] file logging disabled: {e}", file=sys.stderr)

    sh = logging.StreamHandler(sys.stdout)
    sh.setLevel(logging.DEBUG if level >= 3 else logging.INFO)
    sh.setFormatter(logging.Formatter("%(message)s"))
    root.addHandler(sh)
    _configured = True


def get_logger(name):
    return logging.getLogger(name)
