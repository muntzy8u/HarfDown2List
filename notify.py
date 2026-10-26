"""Desktop notifications that never leave zombies behind.

Every notify-send in this app used to be a bare Popen: the child was never
waited for, so each notification left a zombie process behind for the
lifetime of the app. This helper reaps them and never blocks the caller.
"""
import subprocess
import threading

import logs

log = logs.get_logger("notify")


def notify(summary, body="", timeout_ms=6000):
    """Show a desktop notification. Fire-and-forget; failures are logged."""
    def _run():
        try:
            subprocess.run(
                ["notify-send", "-t", str(int(timeout_ms)), summary, body],
                capture_output=True, timeout=10, check=False)
        except Exception as e:
            log.debug("notify failed: %s", e)

    t = threading.Thread(target=_run, daemon=True)
    t.start()
    return t
