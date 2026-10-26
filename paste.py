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
import logs


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


# ---------- selection watcher --------------------------------------------
# PRIMARY never empties on X11, so a one-off read can serve an ancient
# highlight. The watcher below keeps a live picture of the selection:
#
#   * the ICCCM TIMESTAMP target says WHEN the owner last changed the
#     selection. It is the only signal that notices the SAME text being
#     selected again, which a content diff can never see. It is read on a
#     PRIVATE X connection, so the reply never lands in the app's own
#     event loop.
#   * the content itself is read with xclip, which handles large (INCR)
#     transfers for us.
#
# The timestamp is used as a CHANGE DETECTOR, not as a clock: owners
# implement it with varying enthusiasm, and a stale timestamp would make
# a perfectly fresh selection look ancient. The age that matters is
# "how long since WE noticed the selection change", which is always
# accurate to within one poll. When there is no history at all (the app
# started after the selection was made) the timestamp is used to date it,
# but only when it is plausible.

_sel_lock = threading.Lock()
_sel_text = None       # last content we saw
_sel_stamp = None      # last ICCCM timestamp we saw
_sel_changed_at = 0.0  # monotonic time we noticed the last change
_sel_observed = False  # have we ever seen a selection?
_watcher_started = False
_watcher_stop = threading.Event()

# An owner that does not implement TIMESTAMP may still answer the request
# - with the selection text instead of a timestamp. This sentinel is
# written to the property first, so an answer that is still the sentinel
# means "no timestamp here".
_STAMP_SENTINEL = 0x5A5A5A5A
# A timestamp older than this is treated as "unknown", not as "ancient".
_STAMP_PLAUSIBLE_S = 24 * 3600


class _XProbe:
    """A private X connection used to interrogate the selection owner.

    It never touches the app's own Display, so the replies cannot land in
    the middle of the UI event loop.
    """

    _INTEGER = 19        # X predefined atom types
    _CARDINAL = 6

    def __init__(self):
        from Xlib import X, Xatom, display as xdisplay

        self._X = X
        self._Xatom = Xatom
        self.d = xdisplay.Display()
        root = self.d.screen().root
        self.depth = self.d.screen().root_depth
        self.primary = self.d.get_atom("PRIMARY")
        self.timestamp = self.d.get_atom("TIMESTAMP")
        self._prop = self.d.get_atom("_HD2L_SEL_TIMESTAMP")
        self._time_seq = 0
        # The owner only answers a request whose requestor window is
        # viewable, so this window has to be mapped, not just created.
        self.win = root.create_window(
            0, 0, 1, 1, 0, self.depth, X.InputOutput, X.CopyFromParent,
            event_mask=X.PropertyChangeMask)
        self.win.map()
        self.d.sync()

    def close(self):
        try:
            self.d.close()
        except Exception:
            pass

    def _wait_for(self, types, timeout):
        import select
        fd = self.d.fileno()
        end = time.monotonic() + timeout
        while True:
            left = end - time.monotonic()
            if left <= 0:
                return None
            r, _, _ = select.select([fd], [], [], left)
            if not r:
                return None
            while self.d.pending_events():
                ev = self.d.next_event()
                if ev.type in types:
                    return ev
        return None

    def _drain(self):
        """Throw away any events still queued from earlier calls."""
        try:
            while self.d.pending_events():
                self.d.next_event()
        except Exception:
            pass

    def selection_timestamp(self, timeout=1.0):
        """ICCCM TIMESTAMP of PRIMARY (ms since X server start), or None.

        None means "no usable timestamp" - either the owner does not
        implement the target, or it answered with the selection text
        instead of a time, or it simply did not answer this time.
        """
        try:
            owner = self.d.get_selection_owner(self.primary)
            if not getattr(owner, "id", owner):
                return None
            # Some owners only answer a request whose requestor already
            # has the property, so plant the sentinel first.
            try:
                self.win.change_property(self._prop, self._Xatom.INTEGER, 32,
                                         [_STAMP_SENTINEL])
            except Exception:
                pass
            self.win.convert_selection(self.primary, self.timestamp,
                                       self._prop, self._X.CurrentTime)
            ev = self._wait_for({self._X.SelectionNotify}, timeout)
            if ev is None or ev.property == self._X.NONE:
                return None      # no answer, or the owner refused the target
            req = getattr(ev, "requestor", None)
            if req is not None and getattr(req, "id", req) != \
                    getattr(self.win, "id", None):
                return None      # a reply to somebody else's request
            prop = self.win.get_full_property(self._prop,
                                               self._X.AnyPropertyType)
            if prop is None or not len(prop.value):
                return None
            if prop.format != 32 or prop.property_type not in (
                    self._INTEGER, self._CARDINAL):
                return None      # not a timestamp - the owner sent text
            value = int(prop.value[0])
            if value <= 0 or value == _STAMP_SENTINEL:
                return None
            return value
        except Exception:
            return None

    def server_time(self, timeout=1.0):
        """Current X server time (ms) via a property round trip.

        A FRESH property atom is used for every call and the reply is
        matched on that atom: otherwise a queued PropertyNotify from an
        earlier call is read back as "now", and every age computed from it
        is wrong by however long ago that stale event was.
        """
        try:
            self._drain()
            self._time_seq += 1
            atom = self.d.get_atom("_HD2L_TIME_%d" % self._time_seq)
            self.win.change_property(atom, self._Xatom.STRING, 8, b"x",
                                     mode=self._X.PropModeAppend)
            end = time.monotonic() + timeout
            while True:
                left = end - time.monotonic()
                if left <= 0:
                    return None
                ev = self._wait_for({self._X.PropertyNotify}, left)
                if ev is None:
                    return None
                if getattr(ev, "atom", None) == atom:
                    return ev.time
        except Exception:
            return None


def _read_selection_text(timeout=None):
    """Current PRIMARY content as text (or None)."""
    timeout = timeout or getattr(config, "SELECTION_READ_TIMEOUT_S", 3.0)
    try:
        r = subprocess.run(
            ["xclip", "-o", "-selection", "primary"],
            capture_output=True, text=True, timeout=timeout, check=False)
        if r.returncode == 0 and r.stdout.strip():
            return r.stdout
    except subprocess.TimeoutExpired:
        pass
    except Exception:
        pass
    return None


def watch_selection(interval=None):
    """Start (idempotently) the background selection watcher."""
    global _watcher_started
    with _sel_lock:
        if _watcher_started:
            return
        _watcher_started = True
    interval = interval or getattr(config, "SELECTION_POLL_S", 0.4)
    t = threading.Thread(target=_watch_loop, args=(interval,), daemon=True)
    t.start()


def _note_change(text, stamp, first=False):
    """Record that the selection changed (and when we noticed)."""
    global _sel_text, _sel_stamp, _sel_changed_at, _sel_observed
    with _sel_lock:
        if text is not None:
            _sel_text = text
        if stamp is not None:
            _sel_stamp = stamp
        if not first:
            _sel_changed_at = time.monotonic()
        _sel_observed = True


def _watch_loop(interval):
    probe = None
    try:
        probe = _XProbe()
    except Exception as e:
        logs.get_logger("selection").warning(
            "selection probe unavailable (%s); content diffing only", e)

    last_stamp = None
    last_text = None
    first = True
    while not _watcher_stop.is_set():
        try:
            stamp = probe.selection_timestamp() if probe is not None else None
            if stamp is not None:
                if stamp != last_stamp:
                    last_stamp = stamp
                    # The owner says the selection changed - even if the
                    # text is identical to what we saw before.
                    text = _read_selection_text()
                    _note_change(text, stamp, first=first)
                    first = False
                    if text is not None:
                        last_text = text
            else:
                # No timestamp available: fall back to diffing the content.
                text = _read_selection_text()
                if text is not None and text != last_text:
                    last_text = text
                    _note_change(text, None, first=first)
                    first = False
                elif text is not None:
                    last_text = text
        except Exception as e:
            logs.get_logger("selection").debug("watch pass failed: %s", e)
        _watcher_stop.wait(interval)


def _stamp_age(probe, stamp):
    """Age implied by the owner's timestamp, or None if it is not usable."""
    if stamp is None or probe is None:
        return None
    now = probe.server_time()
    if now is None or now < stamp:
        return None
    age = (now - stamp) / 1000.0
    if age < 0 or age > _STAMP_PLAUSIBLE_S:
        return None
    return age


def selection_state(max_age=None, probe=None):
    """Everything we know about the current PRIMARY selection.

    Returns a dict:
        text    - the selection content (or None)
        age     - seconds since the selection changed (None if unknown)
        fresh   - True when the selection is young enough to read
        changed - True when the text differs from the last one we saw
    """
    global _sel_text, _sel_stamp, _sel_changed_at
    max_age = (getattr(config, "SELECTION_MAX_AGE_S", 120.0)
               if max_age is None else max_age)
    watch_selection()
    own_probe = probe is None
    if own_probe:
        try:
            probe = _XProbe()
        except Exception:
            probe = None
    try:
        with _sel_lock:
            stamp = _sel_stamp
            changed_at = _sel_changed_at
            cached = _sel_text

        # A live read: this is exactly what the user has highlighted now.
        text = _read_selection_text()

        # ...and the owner's own timestamp, in case it moved since we looked.
        if probe is not None:
            live_stamp = probe.selection_timestamp()
            if live_stamp is not None and live_stamp != stamp:
                stamp = live_stamp
                changed_at = time.monotonic()
                with _sel_lock:
                    _sel_stamp = live_stamp
                    _sel_changed_at = changed_at

        changed = bool(text) and text != cached
        if changed:
            with _sel_lock:
                if _sel_text != text:
                    _sel_text = text
                    if not _sel_changed_at:
                        _sel_changed_at = time.monotonic()

        # Age: prefer what WE observed (always accurate to one poll),
        # fall back to the owner's timestamp when we have no history.
        if changed_at:
            age = time.monotonic() - changed_at
        else:
            age = _stamp_age(probe, stamp)

        if not text:
            fresh = False
        elif age is None:
            # Nothing to date the selection with. The user just asked for
            # it to be read, so read it.
            fresh = True
        else:
            fresh = age <= max_age
        return {"text": text, "age": age, "fresh": fresh,
                "changed": changed, "stamp": stamp}
    finally:
        if own_probe and probe is not None:
            probe.close()


def recent_selection(max_age=None):
    """PRIMARY content if it is fresh enough to read, else None.

    Freshness is measured against the moment the selection actually
    changed, so this returns the text the user JUST highlighted - not
    whatever the watcher happened to see last, and not an ancient
    highlight either.
    """
    state = selection_state(max_age)
    return state["text"] if state["fresh"] else None


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

HIST_PATH = os.path.join(getattr(config, "STATE_DIR",
                                 os.path.expanduser("~/.local/state/HarfDown2List")),
                          "history.jsonl")


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