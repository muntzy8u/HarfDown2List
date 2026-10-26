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

    DEPRECATED on machines running xfce4-clipman with selection sync: it
    mirrors PRIMARY<->CLIPBOARD automatically, so the sentinel gets
    replaced with no copy at all, and xclip reads can hang ~18s while
    ownership is in flux. Use recent_selection() instead — it observes
    PRIMARY directly and is immune to all of this.
    """
    prev = get_clipboard(timeout)
    cls = focused_class()
    keys = (["ctrl", "shift", "c"] if cls and cls in TERMINAL_CLASSES
            else ["ctrl", "c"])
    sentinel = "hd2l-no-selection-sentinel"
    if not set_clipboard(sentinel):
        return None
    time.sleep(0.08)   # let xclip take ownership before the app copies
    r = _run(["xdotool", "key", "--clearmodifiers", "+".join(keys)], timeout=3)
    text = None
    if r.returncode == 0:
        # The clipboard can pass through an EMPTY phase while ownership
        # changes hands (xfce4-clipman makes this worse) — an empty read
        # means "not arrived yet", NOT "nothing selected". Only a
        # non-empty, non-sentinel value counts as the fresh copy.
        for _ in range(10):
            time.sleep(0.25)
            cand = get_clipboard(timeout)
            if cand and cand != sentinel:
                text = cand
                break
    # the user's clipboard stays theirs, whatever happened
    set_clipboard(prev if prev is not None else "")
    if text is None or text == sentinel:
        return None
    return text


# ---------- PRIMARY watcher (the reliable "read selection" source) ----------
# PRIMARY never empties, so a one-off read can't tell fresh from stale.
# Watching it continuously can: every change is timestamped, so
# recent_selection() can refuse anything the user hasn't highlighted
# within the last few seconds.

_sel_lock = threading.Lock()
_sel_text = None      # last non-empty PRIMARY content seen
_sel_time = 0.0       # monotonic timestamp of that change
_watcher_started = False


def watch_selection(interval=1.0):
    """Start (idempotently) the background PRIMARY watcher thread."""
    global _watcher_started
    with _sel_lock:
        if _watcher_started:
            return
        _watcher_started = True
    t = threading.Thread(target=_watch_loop, args=(interval,), daemon=True)
    t.start()


def _watch_loop(interval=1.0):
    global _sel_text, _sel_time
    while True:
        try:
            txt = get_selection(0.9)
            if txt and txt.strip():
                with _sel_lock:
                    if txt != _sel_text:
                        _sel_text = txt
                        _sel_time = time.monotonic()
        except Exception:
            pass
        time.sleep(interval)


def recent_selection(max_age=6.0):
    """PRIMARY content if it changed within the last `max_age` seconds.

    This is what "Read selection aloud" should use: the user highlights
    text, then clicks the menu item — a fresh highlight is seconds old.
    Anything older is stale junk (the old URL bug) and returns None.
    """
    watch_selection()
    with _sel_lock:
        age = time.monotonic() - _sel_time
        if _sel_text and age <= max_age:
            return _sel_text
    return None


def set_clipboard(text):
    if not text:
        # xclip with empty input forks and holds a BROKEN empty selection:
        # every later read errors ("can't concat str to bytes") until the
        # lingering owner is killed. Never plant an empty selection.
        return False
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