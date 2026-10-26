"""Paste the transcript at the caret of the focused window.

Methods (config.PASTE_METHOD):
  clipboard  — xclip sets the selection, xdotool sends ctrl+v (fast, atomic)
  type       — xdotool type (slow, but works where ctrl+v is intercepted)
The previous clipboard content is saved and restored after a short delay so
your clipboard stays yours.
"""
import re
import subprocess
import threading
import time

import config


def _run(cmd, timeout=5):
    return subprocess.run(
        cmd, capture_output=True, text=True, timeout=timeout, check=False
    )


def get_clipboard(timeout=0.5):
    """Returns text content of the clipboard or None."""
    try:
        r = _run(["xclip", "-o", "-selection", "clipboard"], timeout)
        if r.returncode == 0:
            return r.stdout
    except Exception:
        pass
    return None


def get_selection(timeout=0.5):
    """Returns the PRIMARY selection (what the user last highlighted) or None."""
    try:
        r = _run(["xclip", "-o", "-selection", "primary"], timeout)
        if r.returncode == 0 and r.stdout.strip():
            return r.stdout
    except Exception:
        pass
    return None


def get_selection_any(timeout=0.5):
    """PRIMARY first (last highlighted), then CLIPBOARD (last copied).

    NOTE: PRIMARY never goes empty — X11 keeps the last highlight forever —
    so this can return ancient text. For "read selection" use
    grab_selection_fresh() instead, which copies the CURRENT highlight.
    """
    sel = get_selection(timeout)
    if sel:
        return sel
    try:
        r = _run(["xclip", "-o", "-selection", "clipboard"], timeout)
        if r.returncode == 0 and r.stdout.strip():
            return r.stdout
    except Exception:
        pass
    return None


def grab_selection_fresh(timeout=1.0):
    """Copy the CURRENT highlight to the clipboard and return it.

    Reading PRIMARY directly is useless for "read selection": it never
    empties, so it keeps serving whatever was highlighted ages ago (the
    stale-URL bug). Instead: remember the old clipboard, send ctrl+c to
    the focused window (our overlays never take focus, so this is the app
    the user is reading in; terminals get ctrl+shift+c), read the
    clipboard, then ALWAYS put the user's clipboard back. If the clipboard
    never changed, nothing was highlighted -> None. Never falls back to
    stale sources: no fresh highlight means "nothing selected".
    """
    prev = get_clipboard(timeout)
    cls = focused_class()
    keys = (["ctrl", "shift", "c"] if cls and cls in TERMINAL_CLASSES
            else ["ctrl", "c"])
    r = _run(["xdotool", "key", "--clearmodifiers", "+".join(keys)], timeout=3)
    if r.returncode != 0:
        return None
    text = None
    for _ in range(3):     # apps can be slow to take clipboard ownership
        time.sleep(0.3)
        text = get_clipboard(timeout)
        if text is not None and text != prev:
            break
    changed = text is not None and text != prev
    if prev is not None and (changed or text is None):
        set_clipboard(prev)   # the user's clipboard stays theirs
    return text if changed else None


def set_clipboard(text):
    try:
        p = subprocess.Popen(
            ["xclip", "-i", "-selection", "clipboard"],
            stdin=subprocess.PIPE,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        p.communicate(text.encode(), timeout=5)
        return p.returncode == 0
    except Exception:
        return False


def paste_via_clipboard(text):
    """Set clipboard, then ctrl+v into the focused window."""
    if not set_clipboard(text):
        return False
    time.sleep(0.05)
    r = _run(["xdotool", "key", "--clearmodifiers", "ctrl+v"], timeout=3)
    return r.returncode == 0


def paste_via_type(text):
    timeout = max(5, int(len(text) * config.TYPE_SPEED_MS / 1000.0) + 3)
    r = _run(
        ["xdotool", "type", "--clearmodifiers", "--delay", str(config.TYPE_SPEED_MS), text],
        timeout=timeout,
    )
    arrive_ok = r.returncode == 0
    return arrive_ok


def _restore_later(data):
    set_clipboard(data)


def get_active_window():
    """The WM's active window — our override-redirect layers never become it,
    so this remains the app the user was typing in while they click us."""
    try:
        r = _run(["xdotool", "getactivewindow"], timeout=1)
        if r.returncode == 0:
            return r.stdout.strip()
    except Exception:
        pass
    return None


# Terminal emulators: ctrl+v isn't paste; they use ctrl+shift+v (or shift+insert)
TERMINAL_CLASSES = {
    "xfce4-terminal", "xterm", "uxterm", "gnome-terminal", "konsole",
    "kitty", "alacritty", "wezterm", "foot", "st", "terminator",
    "tilix", "guake", "tilda", "termite", "lxterminal", "mate-terminal",
    "terminology", "sakura", "puddy", "putty",
}


def focused_class(win_id=None):
    """WM_CLASS (instance) of the focused window, lowercase; None if unknown."""
    try:
        if win_id is None:
            win_id = get_active_window()
        if not win_id:
            return None
        r = _run(["xprop", "-id", win_id, "WM_CLASS"], timeout=1)
        m = re.search(r'"([^"]+)",\s*"([^"]+)"', r.stdout)
        if m:
            return (m.group(1) or m.group(2)).lower()
    except Exception:
        pass
    return None


def _paste_keys(win_id=None):
    """Key events that paste into the currently focused window."""
    cls = focused_class(win_id)
    if cls and cls in TERMINAL_CLASSES:
        return [["ctrl", "shift", "v"], ["shift", "insert"]]
    return [["ctrl", "v"]]


def paste_to_window(text, win_id=None):
    """Set clipboard, then send the right paste keys to the focused window.

    If no focusable window can be identified (desktop, no window selected),
    skip the synthetic keypress — the transcript is still on the clipboard
    so the user can paste it manually wherever they want.
    """
    if not set_clipboard(text):
        return False
    win = win_id or get_active_window()
    if not win:
        print("[paste] no focused window; transcript left on clipboard", flush=True)
        return False
    time.sleep(0.05)
    for keys in _paste_keys(win):
        r = _run(
            ["xdotool", "key", "--clearmodifiers", "+".join(keys)], timeout=3
        )
        if r.returncode == 0:
            return True
    return False


def paste(text, restore=True, win_id=None):
    """Main entry: paste text into the focused/target window, restore clipboard."""
    prev = get_clipboard() if restore else None
    if config.PASTE_METHOD == "clipboard":
        ok = paste_to_window(text, win_id)
        if not ok:
            ok = paste_via_type(text)
    else:
        ok = paste_via_type(text)
    if ok and restore and prev is not None:
        _t = threading.Timer(
            config.RESTORE_CLIPBOARD_DELAY_S, _restore_later, (prev,)
        )
        _t.daemon = True
        _t.start()
    return ok


# --- history ---
import json
import os

HIST_PATH = os.path.expanduser("~/.local/state/HarfDown2List/history.jsonl")


def save_history(text, audio_seconds):
    """Append one entry to the transcript history (jsonl)."""
    try:
        os.makedirs(os.path.dirname(HIST_PATH), exist_ok=True)
        import datetime

        rec = {
            "ts": datetime.datetime.now().isoformat(timespec="seconds"),
            "text": text,
            "seconds": round(audio_seconds, 2),
        }
        with open(HIST_PATH, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec) + "\n")
    except Exception:
        pass


def load_history(n=20):
    try:
        with open(HIST_PATH, encoding="utf-8") as f:
            lines = f.readlines()
        return [json.loads(x) for x in lines[-n:] if x.strip()]
    except FileNotFoundError:
        return []
    except Exception:
        return []