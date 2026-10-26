"""HarfDown2List main app: state machine wiring mic, engine, UI, and paste."""
import queue
import select
import signal
import subprocess
import sys
import threading
import time

import numpy as np

import config
import engine
import logs
import notify
import paste
import vocabulary
import voice_out
from audio import Mic
from ui_bar import Bar
from ui_bubble import Bubble, FLOWBAR, HANDSFREE, IDLE, PROCESSING, PTT_DOWN
from ui_menu import Menu
from xbase import XCtx

# Xlib thread-safety: many threads paint UI layers
from Xlib import threaded as xlib_threaded

HANDSFREE_SILENCE_S = 1.6     # stop after this much trailing silence
HANDSFREE_MAX_S = 120.0       # hard cap
LEVEL_SMOOTH = 0.55            # EMA alpha for the waveform
LEVEL_BOOST = 3.0              # mic RMS -> bar height gain


class App:
    def __init__(self):
        self.ctx = XCtx()
        self.mic = Mic()
        self.bubble = Bubble(
            self.ctx,
            on_hold_start=self.ptt_start,
            on_hold_end=self.ptt_end,
            on_tap=self.tap,
            on_menu=self.open_menu,
            on_cancel=self.flowbar_cancel,
            on_finish=self.flowbar_finish,
            on_drag=self.reposition_bar,
            on_wave_click=self.wave_click,
        )
        self.bar = Bar(self.ctx, 0, 0)
        self.menu = Menu(self.ctx)
        self.state = IDLE
        self.rec_start = None
        self.committed = ""
        self.prev_partial = ""
        self.last_transcript = ""
        self.hidden = False
        self._partial_stop = threading.Event()
        self._final_lock = threading.RLock()
        self._hf_last_voice = None
        self._quit = threading.Event()
        self._paste_after = None  # window id to check post-paste focus
        self._hf_silence_s = HANDSFREE_SILENCE_S
        self._hotkey_on = False

    # ---------- esc cancel (global listener) ----------
    def _install_esc_cancel(self):
        """Global Esc listener: cancels an open dictation session anywhere.

        (The old F13 hold-to-talk / double-press hands-free hotkey was scrapped:
        F13 needs hardware most keyboards don't have. Push-to-talk lives on the
        pill itself — hold it; click it for the flow bar.)
        """
        from pynput import keyboard

        def on_press(key):
            if key != keyboard.Key.esc:
                return
            # Esc always stops whatever is being spoken aloud.
            if voice_out.stop():
                print("[app] speech stopped via Esc", flush=True)
            # ...and cancels an open dictation session anywhere
            if self.state in (FLOWBAR, HANDSFREE, PTT_DOWN):
                self._cancel_session()
                print("[app] session cancelled via Esc", flush=True)

        try:
            listener = keyboard.Listener(on_press=on_press)
            listener.start()
            self._hotkey_listener = listener
        except Exception as e:
            print(f"[app] esc-cancel unavailable: {e}", file=sys.stderr)

    # ---------- layer wiring ----------
    def build(self):
        from xbase import XLayer
        import config as cfg

        # Watch the X selection from the start: the read-back needs to know
        # when the selection last changed, and a watcher that only starts
        # when the menu is opened has no history to compare against.
        paste.watch_selection()

        layer = XLayer(self.ctx, 0, 0, cfg.BUBBLE_W, cfg.BUBBLE_H)
        # collapsed idle button is a true circle (visible + input shape)
        layer.set_disc_visible()
        self.bubble.attach_layer(layer)
        self.bubble.set_state(IDLE)
        layer.map()
        self.bubble.paint()
        # bar hidden until first dictation
        self.bar.layer.move_resize(
            0, 0, cfg.TRANSCRIPT_BAR_W, cfg.TRANSCRIPT_BAR_H
        )
        self.reposition_bar(self.bubble.pos)
        # warm the engine so first press is fast
        threading.Thread(target=self._warm, daemon=True).start()

    def _warm(self):
        try:
            engine._get_model(config.PARTIAL_MODEL)
            engine._get_model(config.FINAL_MODEL)
        except Exception as e:
            print(f"[app] warmup failed: {e}", file=sys.stderr)

    # ---------- state transitions ----------
    def ptt_start(self):
        with self._final_lock:
            if self.state == PROCESSING:
                return
        self._begin_session(PTT_DOWN)

    def ptt_end(self):
        if self.state != PTT_DOWN:
            return
        self._finish_session()

    def tap(self):
        """Click the collapsed pill: expand into the flow bar and record."""
        if self.state == IDLE:
            self.bubble.expand()
            self._begin_session(FLOWBAR)

    def flowbar_cancel(self):
        """X in the flow bar: discard everything, collapse."""
        if self.state in (FLOWBAR, PTT_DOWN, HANDSFREE):
            self._cancel_session()

    def flowbar_finish(self):
        """Tick in the flow bar: transcribe + paste, then collapse."""
        if self.state in (FLOWBAR, HANDSFREE):
            self._finish_session()

    def wave_click(self):
        """Click the waveform zone: toggle pause/resume mid-dictation."""
        if self.state not in (FLOWBAR, HANDSFREE):
            return
        self.bubble.paused = not self.bubble.paused
        self.mic.set_paused(self.bubble.paused)
        status = "paused — click waveform to resume" if self.bubble.paused else "listening…"
        self.bar.set_text(self.committed, len(self.committed), status)
        print(f"[app] dictation {'paused' if self.bubble.paused else 'resumed'}", flush=True)

    def _begin_session(self, new_state):
        with self._final_lock:
            self.state = new_state
        self._partial_stop.clear()  # FIX: a previous cancel left this set forever
        self.committed = ""
        self.prev_partial = ""
        self.bubble.paused = False
        self.mic.set_paused(False)
        self._hf_last_voice = time.monotonic()
        self.mic.start_recording()
        self.rec_start = time.monotonic()
        self.bar.set_text("", 0, status="listening…")
        self.bar.show()
        self.bubble.set_state(new_state)
        self.reposition_bar(self.bubble.pos)
        threading.Thread(target=self._partial_loop, daemon=True).start()

    def _cancel_session(self):
        with self._final_lock:
            self.state = IDLE
        self._partial_stop.set()
        self.mic.stop_recording()
        self.mic.set_paused(False)
        self.bubble.paused = False
        self.committed = ""
        self.prev_partial = ""
        self.bubble.collapse()
        self.bubble.set_state(IDLE)
        self.bar.set_text("", 0, status="cancelled")
        _t = threading.Timer(1.0, self._hide_bar)
        _t.daemon = True
        _t.start()

    def _finish_session(self):
        with self._final_lock:
            self.state = PROCESSING
        blocks = self.mic.stop_recording()
        self.mic.set_paused(False)
        self.bubble.paused = False
        self._partial_stop.set()
        self.bubble.set_state(PROCESSING)
        self.bar.set_text(self.committed or "…", len(self.committed), "transcribing…")
        audio = Mic.blocks_to_audio(blocks, trim=config.TRIM_SILENCE)
        dur = len(audio) / config.SAMPLE_RATE
        print(f"[app] session {dur:.1f}s audio captured", flush=True)

        def work():
            t0 = time.monotonic()
            text = engine.transcribe_final(
                audio, initial_prompt=vocabulary.initial_prompt()
            )
            if not text and self.committed:
                text = self.committed
            # user's bracket-correction convention first, then vocabulary,
            # then optional LLM polish
            text = vocabulary.apply_bracket_corrections(text)
            text = vocabulary.apply(text)
            if text and config.LLM_POLISH:
                polished = engine.polish(text)
                if polished:
                    text = polished
            dt = time.monotonic() - t0
            print(f"[app] final: {dt:.1f}s  {text!r}", flush=True)
            if text:
                win = paste.get_active_window()
                ok = paste.paste(
                    text, restore=config.RESTORE_CLIPBOARD, win_id=win
                )
            else:
                ok = False
            if text:
                paste.save_history(text, dur)
                self.last_transcript = text
                if config.TTS_ENABLED:
                    voice_out.speak_async(text)
            self._settle(ok and text != "")

        t = threading.Thread(target=work, daemon=True)
        t.start()

    def _settle(self, ok):
        with self._final_lock:
            self.state = IDLE
        self._partial_stop.clear()
        self.bubble.collapse()
        self.bar.set_text("", 0, status="done" if ok else "no speech detected")
        self.bubble.set_state(IDLE)
        _t = threading.Timer(1.2, self._hide_bar)
        _t.daemon = True
        _t.start()

    def _hide_bar(self):
        if self.state == IDLE:
            self.bar.hide()

    # ---------- live partials ----------
    def _partial_loop(self):
        """Re-transcribe the growing buffer with tiny.en every interval.

        Wrapped: if a partial pass raises (model/decoder hiccup), log it
        and keep the loop alive — live preview degrades, dictation does not.
        """
        prev = ""
        while not self._partial_stop.is_set():
            time.sleep(config.PARTIAL_INTERVAL_S)
            if self._partial_stop.is_set() or self.state not in (PTT_DOWN, HANDSFREE, FLOWBAR):
                return
            if self.bubble.paused:
                continue  # don't transcribe while paused
            try:
                if self.mic.recording:
                    blocks = list(self.mic._buffered)
                    audio = Mic.blocks_to_audio(blocks, trim=False)
                    if audio.size > config.SAMPLE_RATE * config.PARTIAL_MAX_S:
                        continue
                    cur = engine.transcribe_partial(audio)
                    # local agreement: commit longest agreed prefix
                    committed_prefix = engine.stable_prefix(prev, cur)
                    if len(committed_prefix) > len(self.committed):
                        self.committed = committed_prefix
                    # show committed + volatile tail
                    if cur.startswith(self.committed):
                        tail = cur[len(self.committed):].strip()
                    else:
                        tail = cur
                    display = (self.committed + " " + tail).strip()
                    self.bar.set_text(display, len(self.committed), "listening…")
                    self.bubble.set_partial(display, len(self.committed))
                    prev = cur
            except Exception as e:
                print(f"[app] partial pass failed ({e}); preview paused", flush=True)
                time.sleep(1.0)  # brief backoff, then retry on next interval

    # ---------- hands-free / flowbar auto-stop ----------
    def _hf_check(self):
        if self.state in (HANDSFREE, FLOWBAR) and self.rec_start is not None:
            elapsed = time.monotonic() - self.rec_start
            if self.bubble.paused:
                return  # caps and silence-stop are suspended while paused
            if self.state == FLOWBAR and elapsed > config.FLOWBAR_MAX_S:
                print(f"[app] flowbar cap reached ({elapsed:.0f}s), finishing", flush=True)
                self._finish_session()
                return
            # BOTH hands-free and flowbar: if no voice is heard for a while,
            # stop and return to normal. Flowbar = config.FLOWBAR_SILENCE_S
            # (1 minute), hands-free = config.HANDSFREE_SILENCE_S.
            silent_s = (
                config.FLOWBAR_SILENCE_S
                if self.state == FLOWBAR
                else self._hf_silence_s
            )
            silent_for = time.monotonic() - self._hf_last_voice
            if silent_for >= silent_s:
                print(f"[app] {self.state} silent for {silent_for:.0f}s, stopping", flush=True)
                self._finish_session()
                return
            if self.state == HANDSFREE and elapsed > HANDSFREE_MAX_S:
                self._finish_session()

    # ---------- mic pump ----------
    def _pump(self):
        """UI-thread task: consume mic blocks, smooth level, hands-free watch."""
        rms, n = self.mic.drain()
        if n:
            if rms > config.SILENCE_RMS:
                self._hf_last_voice = time.monotonic()
                sm = self.bubble.level * LEVEL_SMOOTH + min(1.0, rms * LEVEL_BOOST) * (
                    1 - LEVEL_SMOOTH
                )
            else:
                sm = self.bubble.level * 0.82
            self.bubble.set_level(sm, rms)
            self._hf_check()
        # menu hover repainting handled in event loop

    # ---------- menu ----------
    def open_menu(self):
        if self.menu.is_open():
            self.menu.close()
            return
        bx, by = self.bubble.pos
        persona = voice_out.persona_for()
        # The pause item changes its name with the state, so the menu
        # always says what the click will do.
        if voice_out.is_paused():
            pause_label = "Resume reading"
        else:
            pause_label = "Pause reading"
        items = [
            ("Read selection aloud", "speak_selection"),
            ("Read selection anyway", "speak_selection_anyway"),
            ("Speak last transcript", "speak_last"),
            (pause_label, "toggle_pause"),
            ("Stop speaking", "stop_speaking"),
            ("Paste last transcript", "paste_last"),
            ("History", "history"),
            ("Mic: default", "mic"),
            ("Hide 1 hour", "hide"),
            ("Quit", "quit"),
            ("__sep__", "__sep__"),
            ("VOICES", "__header__"),
        ]
        # Every voice is its own row, so you pick one instead of guessing
        # what a cycling item will do next. The current one is marked.
        for key, label in voice_out.available_personas():
            mark = "* " if key == persona.key else "  "
            items.append((mark + label, "voice:" + key))
        self.menu.open(
            bx + 6,
            by - len(items) * 26 - 12,
            items,
            on_select=self.menu_select,
            on_close=None,
        )

    def _read_selection(self, anyway=False):
        """Read the current selection aloud.

        Freshness comes from the selection owner itself (the ICCCM
        TIMESTAMP), so this is the text the user just highlighted - not a
        stale highlight the watcher happened to see, and not a refusal
        because the watcher missed a change.
        """
        state = paste.selection_state()
        sel = state["text"]
        age = state["age"]
        if not sel:
            notify.notify("HarfDown2List",
                          "Nothing is selected — highlight some text "
                          "first, then try again")
            print("[app] read-selection: nothing selected", flush=True)
            return
        preview = " ".join(sel.split())[:70]
        if anyway or state["fresh"]:
            notify.notify("HarfDown2List", f"Reading: {preview}…")
            print(f"[app] read-selection: {len(sel)} chars "
                  f"(age {age if age is None else round(age, 1)}s)",
                  flush=True)
            voice_out.speak_async(sel)
        else:
            when = "a while ago" if age is None else \
                "%d min ago" % max(1, int(age // 60))
            notify.notify(
                "HarfDown2List",
                "That highlight is %s. Use 'Read selection anyway' to "
                "hear it." % when)
            print(f"[app] read-selection: stale ({when})", flush=True)

    def menu_select(self, payload):
        print(f"[dbg] menu_select: {payload!r}", flush=True)
        if payload == "speak_selection":
            # Off the UI thread: reading the selection can take a moment
            # when ownership is changing hands, and the event loop must
            # keep breathing.
            threading.Thread(target=self._read_selection, daemon=True).start()
        elif payload == "speak_selection_anyway":
            threading.Thread(target=self._read_selection,
                             kwargs={"anyway": True}, daemon=True).start()
        elif payload.startswith("voice:"):
            # Every voice is listed in the menu: pick one, hear it, keep it
            # if you like it. The choice is remembered for next time.
            key = payload.split(":", 1)[1]
            persona = voice_out.set_persona(key)
            if persona is None:
                notify.notify("HarfDown2List", "Unknown voice.")
                return
            notify.notify("HarfDown2List", "Voice: %s" % persona.label)
            print(f"[app] voice -> {persona.key} ({persona.label})", flush=True)
            voice_out.speak_async(
                "Good evening. This is your reading voice, speaking from "
                "London.")
        elif payload == "toggle_pause":
            state = voice_out.toggle_pause()
            if state == "paused":
                notify.notify("HarfDown2List", "Paused.")
            elif state == "resumed":
                notify.notify("HarfDown2List", "Resumed.")
            else:
                notify.notify("HarfDown2List", "Nothing is being read.")
        elif payload == "stop_speaking":
            if voice_out.stop():
                notify.notify("HarfDown2List", "Stopped.")
        elif payload == "paste_last":
            if self.last_transcript:
                paste.paste(self.last_transcript)
        elif payload == "speak_last":
            if self.last_transcript:
                voice_out.speak_async(self.last_transcript)
        elif payload == "history":
            recs = paste.load_history(5)
            text = "\n".join(r.get("text", "")[:80] for r in reversed(recs)) or "No history yet"
            notify.notify("HarfDown2List", text)
        elif payload == "mic":
            notify.notify("HarfDown2List",
                          "Mic config: edit config.py MIC_SOURCE")
        elif payload == "hide":
            self.hide(3600)
        elif payload == "quit":
            self._quit.set()

    def hide(self, seconds):
        self.hidden = True
        self.bubble.layer.unmap()
        self.bar.hide()
        _t = threading.Timer(seconds, self._unhide)
        _t.daemon = True
        _t.start()

    def _unhide(self):
        self.hidden = False
        self.bubble.layer.map()
        self.bubble.paint()

    def reposition_bar(self, pos):
        bx, by = self._bar_xy(pos)
        self.bar.layer.move(bx, by)

    def _bar_xy(self, pos):
        bw, bh = config.TRANSCRIPT_BAR_W, config.TRANSCRIPT_BAR_H
        # center the bar on the widget's CURRENT width (pill or expanded bar)
        cur_w = self.bubble.layer.w
        bx = pos[0] + (cur_w - bw) // 2
        by = pos[1] - bh - 10
        if by < 4:
            by = pos[1] + self.bubble.layer.h + 10
        bx = max(4, min(bx, self.ctx.sw - bw - 4))
        return int(bx), int(by)

    # ---------- main loop ----------
    def run(self):
        self.build()
        self.mic.start()
        self._install_esc_cancel()
        print("[app] bubble up — click to expand, hold the pill to talk", flush=True)
        d = self.ctx.d
        try:
            while not self._quit.is_set():
                while d.pending_events() > 0:
                    e = d.next_event()
                    self._dispatch(e)
                self._pump()
                self._hf_check()
                if self.state == PROCESSING:
                    self.bubble.paint()
                select.select([d], [], [], 0.03)
        except KeyboardInterrupt:
            pass
        finally:
            try:
                if self._hotkey_listener is not None:
                    self._hotkey_listener.stop()
            except Exception:
                pass
            self.mic.stop()
            self.menu.close()
            self.bar.layer.destroy()
            self.bubble.layer.destroy()
            print("[app] thanks for using HarfDown2List — bye!", flush=True)

    def _dispatch(self, e):
        from Xlib import X

        t = e.type
        if self.menu.is_open():
            mw = self.menu.window()
            # compare X resource ids, not object identity: python-xlib may
            # wrap the same X window in different Python objects per event
            if mw is not None and getattr(e, "window", None) is not None \
                    and e.window.id == mw.id:
                self.menu.handle_event(e)
                return
            # swallow the right-button release that follows opening the menu
            # (the release lands on the bubble layer and would close it)
            if t == X.ButtonRelease and e.detail == 3:
                return
            if t in (X.ButtonPress, X.ButtonRelease):
                self.menu.close()
            return
        if t in (X.ButtonPress, X.ButtonRelease, X.MotionNotify, X.Expose):
            self.bubble.handle_event(e)


def main():
    logs.setup()
    log = logs.get_logger("app")
    log.info("HarfDown2List starting")
    App().run()


if __name__ == "__main__":
    main()