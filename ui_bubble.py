"""Floating bubble + flow bar: the HarfDown2List control surface.

Collapsed pill (64x40):
  - hold            -> push-to-talk, release to transcribe+paste
  - click           -> EXPAND into the flow bar and start recording
  - right-click     -> menu; middle-click -> recenter; drag -> move+edge-snap
Expanded flow bar ([X] [waveform] [tick], 280x48):
  - X               -> cancel session (discard audio, collapse)
  - tick            -> finish: transcribe + paste into the focused field
  - middle area     -> drag to move; waveform animates while recording
"""
import math
import threading
import time
import os

from PIL import Image, ImageDraw

import config
from xbase import (
    C_ACCENT,
    C_ACCENT2,
    C_BG,
    C_BG_ACTIVE,
    C_EDGE,
    C_FG,
    C_GREEN,
    C_MUTED,
    C_RED,
    C_YELLOW,
    XLayer,
    font,
    glass_pill,
    new_canvas,
    text_size,
)

# Idle button face: hd.jpg loaded once, kept exactly as-is (only resized for
# display). Fallback to None if the asset is missing — the pill falls back to
# the gradient style below.
hd_img = None
try:
    if config.BUBBLE_IMG and os.path.exists(config.BUBBLE_IMG):
        # Original image kept exactly as-is (no auto-crop) — shown full when idle.
        hd_img = Image.open(config.BUBBLE_IMG).convert("RGB")
except Exception as e:
    print(f"[bubble] idle image unavailable ({e}); using gradient pill", flush=True)
    hd_img = None

# Optional user-supplied idle button icon (BUBBLE_ICON config). Loaded once.
# If SVG, we need cairosvg; PNG/JPEG load straight in. Returns None when
# unset or unreadable -> falls back to the built-in drawn mic glyph.
_user_icon = None
try:
    if config.BUBBLE_ICON and os.path.exists(config.BUBBLE_ICON):
        _icon_path = config.BUBBLE_ICON
        if _icon_path.lower().endswith(".svg"):
            import cairosvg  # may not be installed
            png_bytes = cairosvg.svg2png(url=_icon_path)
            import io
            _user_icon = Image.open(io.BytesIO(png_bytes)).convert("RGBA")
        else:
            _user_icon = Image.open(_icon_path).convert("RGBA")
except Exception as e:
    print(f"[bubble] custom icon unavailable ({e}); using drawn mic", flush=True)
    _user_icon = None

# states
IDLE = "idle"
PTT_DOWN = "ptt"          # holding the pill: live dictation
HANDSFREE = "handsfree"   # hotkey double-press: silence auto-stop
FLOWBAR = "flowbar"       # expanded bar: explicit tick/X stop
PROCESSING = "processing"

COLLAPSED = "collapsed"
EXPANDED = "expanded"

HOLD_MS = 260
HOLD_SLOP = 6
ZONE_W = config.ZONE_W      # single source of truth: config.py


class Bubble:
    def __init__(self, ctx, on_hold_start, on_hold_end, on_tap, on_menu,
                 on_cancel, on_finish, on_drag=None, on_wave_click=None):
        self.ctx = ctx
        self.on_hold_start = on_hold_start
        self.on_hold_end = on_hold_end
        self.on_tap = on_tap
        self.on_menu = on_menu
        self.on_cancel = on_cancel
        self.on_finish = on_finish
        self.on_wave_click = on_wave_click or (lambda: None)
        self.on_drag = on_drag
        self.state = IDLE
        self.mode = COLLAPSED
        self.paused = False
        self.level = 0.0
        self.level_hist = [0.0] * 48
        self._press_t = None
        self._press_pos = None
        self._press_local = (0, 0)
        self._dragging = False
        self._hold_fired = False
        self._hold_timer = None
        self.hover = None      # None | 'x' | 'tick' when expanded
        self.pos = None
        self.layer = None

    # ---- geometry ----
    def default_pos(self):
        mx, my, mw, mh = self.ctx.monitors[0]
        w = config.BUBBLE_W if self.mode == COLLAPSED else config.FLOWBAR_W
        h = config.BUBBLE_H
        x = int(mx + mw * config.DEFAULT_POS[0] - w / 2)
        y = int(my + mh * config.DEFAULT_POS[1] - h / 2)
        return x, y

    def size(self):
        if self.mode == EXPANDED:
            return config.FLOWBAR_W, config.FLOWBAR_H
        return config.BUBBLE_W, config.BUBBLE_H

    def attach_layer(self, layer, pos=None):
        self.layer = layer
        if pos is None:
            pos = self.default_pos()
        self.pos = pos
        layer.move(*pos)

    # ---- mode switch ----
    def expand(self):
        if self.mode == EXPANDED:
            return
        self.mode = EXPANDED
        w, h = self.size()
        # keep the pill center fixed: expanded bar grows symmetrically
        cx = self.pos[0] + config.BUBBLE_W // 2
        x = cx - w // 2
        y = self.pos[1]
        self.pos = self.ctx.clamp_to_monitor(x, y, w, h)
        self.layer.move_resize(self.pos[0], self.pos[1], w, h)
        # expanded bar: clip visible + input shape to the pill so the dark
        # canvas corners outside the rounded ends are transparent, not black
        self.layer.set_pill_visible(radius=h // 2)
        self.paint()

    def collapse(self):
        if self.mode == COLLAPSED:
            return
        self.mode = COLLAPSED
        w, h = self.size()
        # keep the bar center fixed: pill re-centers inside the old bar rect
        cx = self.pos[0] + config.FLOWBAR_W // 2
        x = cx - w // 2
        y = self.pos[1]
        self.pos = self.ctx.clamp_to_monitor(x, y, w, h)
        self.layer.move_resize(self.pos[0], self.pos[1], w, h)
        # collapsed pill is a true circle: clip both input AND visible shape
        self.layer.set_disc_visible()
        self.paint()

    # ---- event handling ----
    def handle_event(self, e):
        from Xlib import X

        if self.mode == EXPANDED:
            return self._handle_expanded(e)
        return self._handle_collapsed(e)

    def _handle_collapsed(self, e):
        from Xlib import X

        if e.type == X.ButtonPress:
            if e.detail == 3:
                self.on_menu()
                return True
            if e.detail == 2:
                self.pos = self.default_pos()
                self.layer.move(*self.pos)
                if self.on_drag:
                    self.on_drag(self.pos)
                return True
            if e.detail == 1:
                self._press_t = time.monotonic()
                self._press_pos = (e.root_x, e.root_y)
                self._press_local = (e.event_x, e.event_y)
                self._dragging = False
                self._hold_fired = False
                self._hold_timer = threading.Timer(
                    HOLD_MS / 1000.0, self._hold_tick
                )
                self._hold_timer.daemon = True
                self._hold_timer.start()
                return True
            return False

        if e.type == X.ButtonRelease:
            self._cancel_hold_timer()
            if e.detail != 1 or self._press_t is None:
                self._press_t = None
                return False
            dt_ms = (time.monotonic() - self._press_t) * 1000
            moved = (
                abs(e.root_x - self._press_pos[0])
                + abs(e.root_y - self._press_pos[1])
            )
            self._press_t = None
            if self._dragging:
                self._dragging = False
                self._snap_if_near_edge()
                return True
            if self._hold_fired:
                self._hold_fired = False
                if self.state == PTT_DOWN:
                    self.on_hold_end()
                return True
            if dt_ms < HOLD_MS and moved <= HOLD_SLOP and self.state == IDLE:
                self.on_tap()
            return True

        if e.type == X.MotionNotify:
            if self._press_t is None:
                return False
            dx = e.root_x - self._press_pos[0]
            dy = e.root_y - self._press_pos[1]
            if not self._dragging and (abs(dx) > HOLD_SLOP or abs(dy) > HOLD_SLOP):
                self._dragging = True
                self._cancel_hold_timer()
                if self._hold_fired:
                    self._hold_fired = False
                    if self.state == PTT_DOWN:
                        self.on_hold_end()
                    return True
            if self._dragging:
                x = e.root_x - self._press_local[0]
                y = e.root_y - self._press_local[1]
                self.pos = self.ctx.clamp_to_monitor(
                    x, y, self.layer.w, self.layer.h
                )
                self.layer.move(*self.pos)
                if self.on_drag:
                    self.on_drag(self.pos)
            return True

        if e.type == X.Expose:
            self.paint()
            return True
        return False

    def _handle_expanded(self, e):
        from Xlib import X

        w = self.layer.w
        if e.type == X.ButtonPress:
            if e.detail == 3:
                self.on_menu()
                return True
            if e.detail == 1:
                if e.event_x < ZONE_W:
                    self.on_cancel()
                    return True
                if e.event_x > w - ZONE_W:
                    self.on_finish()
                    return True
                # waveform zone: click = pause/resume, drag = move
                self._press_t = time.monotonic()
                self._press_pos = (e.root_x, e.root_y)
                self._press_local = (e.event_x, e.event_y)
                self._dragging = False
                return True
            return False

        if e.type == X.ButtonRelease:
            if e.detail == 1:
                if self._press_t is not None and not self._dragging:
                    # click (not drag) on the waveform: toggle pause
                    self.on_wave_click()
                self._dragging = False
                self._press_t = None
                self._snap_if_near_edge()
            return True

        if e.type == X.MotionNotify:
            if not self._dragging and self._press_t is not None:
                dx = e.root_x - self._press_pos[0]
                dy = e.root_y - self._press_pos[1]
                if abs(dx) > HOLD_SLOP or abs(dy) > HOLD_SLOP:
                    self._dragging = True
            if self._dragging:
                x = e.root_x - self._press_local[0]
                y = e.root_y - self._press_local[1]
                self.pos = self.ctx.clamp_to_monitor(
                    x, y, self.layer.w, self.layer.h
                )
                self.layer.move(*self.pos)
                if self.on_drag:
                    self.on_drag(self.pos)
            else:
                # hover highlight for the X / tick zones
                h = self.hover
                if e.event_x < ZONE_W:
                    self.hover = "x"
                elif e.event_x > w - ZONE_W:
                    self.hover = "tick"
                else:
                    self.hover = None
                if h != self.hover:
                    self.paint()
            return True

        if e.type == X.Expose:
            self.paint()
            return True
        return False

    # ---- hold / snap ----
    def _hold_tick(self):
        if self._press_t is not None and not self._dragging and self.state == IDLE:
            self._hold_fired = True
            self.on_hold_start()

    def _cancel_hold_timer(self):
        if self._hold_timer is not None:
            self._hold_timer.cancel()
            self._hold_timer = None

    def _snap_if_near_edge(self):
        x, y = self.pos
        sw, sh = self.ctx.sw, self.ctx.sh
        m = config.EDGE_MARGIN_PX
        snap = config.SNAP_TOLERANCE_PX
        changed = False
        if y <= snap:
            y = m
            changed = True
        elif y + self.layer.h >= sh - snap:
            y = sh - m - self.layer.h
            changed = True
        if x <= snap:
            x = m
            changed = True
        elif x + self.layer.w >= sw - snap:
            x = sw - m - self.layer.w
            changed = True
        if changed:
            self.pos = self.ctx.clamp_to_monitor(x, y, self.layer.w, self.layer.h)
            self.layer.move(*self.pos)
            if self.on_drag:
                self.on_drag(self.pos)

    # ---- visual state ----
    def set_state(self, state):
        self.state = state
        self.paint()

    def set_level(self, smoothed, raw):
        self.level = smoothed
        # push the RAW per-pump energy into the rolling history so the equalizer
        # bars can ACTUALLY move with your voice. smoothed is only the silence gate.
        self.level_hist.append(raw)
        self.level_hist = self.level_hist[-48:]
        self.paint()

    def set_partial(self, text, committed_len):
        # partials render in the transcript bar, not the pill
        pass

    # ---- painting ----
    def paint(self):
        if self.layer is None or not self.layer.mapped:
            return
        if self.mode == EXPANDED:
            self._paint_flowbar()
        else:
            self._paint_pill()

    def _paint_pill(self):
        w, h = self.layer.w, self.layer.h
        r = h // 2  # oval: fully rounded ends
        listening = self.state in (PTT_DOWN, HANDSFREE, FLOWBAR)
        if listening:
            img, d = glass_pill(w, h, r, (44, 56, 92), (24, 28, 46))
        elif self.state == PROCESSING:
            img, d = glass_pill(w, h, r, (34, 40, 60), (18, 21, 34))
        else:
            img, d = glass_pill(w, h, r, (30, 34, 48), (13, 15, 22))
        if self.state == IDLE:
            # If the user set a custom icon (BUBBLE_ICON), paste it centered;
            # otherwise fall back to the built-in drawn mic glyph.
            if _user_icon is not None:
                try:
                    ic = _user_icon
                    # BUBBLE_ICON PNGs are sized 1:1 to the pill (56x56 =
                    # BUBBLE_W/BUBBLE_H): paste at full size, zero resample
                    side = min(w, h)
                    scale = min(side / ic.size[0], side / ic.size[1])
                    dw, dh = max(1, int(ic.size[0] * scale)), max(1, int(ic.size[1] * scale))
                    thumb = ic.resize((dw, dh))
                    # paste with alpha (RGBA) over the pill image
                    if thumb.mode == "RGBA":
                        img.paste(thumb, ((w - dw) // 2, (h - dh) // 2), thumb)
                    else:
                        img.paste(thumb, ((w - dw) // 2, (h - dh) // 2))
                except Exception as e:
                    print(f"[bubble] icon paste failed ({e}); using mic glyph", flush=True)
                    self._paint_mic_glyph(d, w, h)
            else:
                self._paint_mic_glyph(d, w, h)
            # thicker circular border around the idle pill button
            d.ellipse([0, 0, w - 1, h - 1], outline=C_EDGE, width=3)
        if self.state == HANDSFREE or self.state == FLOWBAR:
            # live dot with accent ring while recording
            d.ellipse([5, 5, 12, 12], fill=C_RED)
            d.ellipse([3, 3, 14, 14], outline=C_ACCENT, width=1)
        if self.state == PTT_DOWN:
            # listening: accent halo bars either side of center
            cy = h // 2
            for dx in (-14, -9, 9, 14):
                bh = 10 + int(self.level * 8)
                col = C_ACCENT if dx > 0 else C_ACCENT2
                d.rounded_rectangle(
                    [w // 2 + dx, cy - bh // 2, w // 2 + dx + 3, cy + bh // 2],
                    radius=1, fill=col,
                )
        if self.state == PROCESSING:
            a0 = int((time.time() * 360) % 360)
            d.arc(
                [w - 22, h // 2 - 6, w - 10, h // 2 + 6],
                a0, a0 + 110, fill=C_GREEN, width=2,
            )
        self.layer.render(img)

    def _paint_flowbar(self):
        w, h = self.layer.w, self.layer.h
        r = h // 2  # oval
        active = self.state in (FLOWBAR, HANDSFREE, PTT_DOWN)
        if active:
            img, d = glass_pill(w, h, r, (44, 56, 92), (24, 28, 46))
        else:
            img, d = glass_pill(w, h, r, (30, 34, 48), (13, 15, 22))
        # X zone (cancel) — circled X. Red while recording (standard "stop/cancel"),
        # brighter on hover; otherwise muted until hovered.
        if active:
            xcol = C_RED if self.hover != "x" else self._brighter(C_RED)
        else:
            xcol = C_RED if self.hover == "x" else C_MUTED
        cxc = ZONE_W // 2
        cyc = h // 2
        rad = min(ZONE_W, h) // 2 - 8
        # solid button disc FIRST so the capsule's rounded-end rim arc doesn't
        # bleed through and create a jagged double-ring around the button
        d.ellipse(
            [cxc - rad - 2, cyc - rad - 2, cxc + rad + 2, cyc + rad + 2],
            fill=(26, 30, 42),
        )
        d.ellipse([cxc - rad, cyc - rad, cxc + rad, cyc + rad], outline=xcol, width=2)
        self._draw_x(d, cxc, cyc, rad - 2, xcol)
        # tick zone (finish) — circled tick. Green while recording (standard "confirm"),
        # brighter on hover; otherwise muted until hovered.
        if active:
            tcol = C_GREEN if self.hover != "tick" else self._brighter(C_GREEN)
        else:
            tcol = C_GREEN if self.hover == "tick" else C_MUTED
        txc = w - ZONE_W // 2
        trad = min(ZONE_W, h) // 2 - 8
        d.ellipse(
            [txc - trad - 2, cyc - trad - 2, txc + trad + 2, cyc + trad + 2],
            fill=(26, 30, 42),
        )
        d.ellipse([txc - trad, cyc - trad, txc + trad, cyc + trad], outline=tcol, width=2)
        self._draw_tick(d, txc, cyc, trad - 2, tcol)
        # waveform in the middle — a smooth animated wave while recording
        mid_w = w - 2 * ZONE_W
        zone_x0 = ZONE_W + 10
        zone_w = mid_w - 20
        cy = h // 2
        if self.state == PROCESSING:
            # processing: green scanning bars (existing behaviour)
            bars = 13
            gap = 4
            bar_w = (zone_w - (bars - 1) * gap) / bars
            for i in range(bars):
                lvl = 0.10 + 0.05 * math.sin(time.time() * 4 + i)
                bh = max(5, min(h - 14, int(lvl * (h - 14))))
                x = zone_x0 + i * (bar_w + gap)
                d.rounded_rectangle(
                    [x, cy - bh / 2, x + bar_w - 1, cy + bh / 2],
                    radius=bar_w / 2, fill=C_GREEN,
                )
        elif self.paused:
            # on hold: flat muted line (the PAUSED label sits on top)
            d.line([zone_x0, cy, zone_x0 + zone_w, cy], fill=C_MUTED, width=3)
        elif active:
            # recording: always-moving wavy line that swells with your voice
            self._draw_live_wave(d, zone_x0, zone_w, cy, h)
        else:
            # idle (unused in the bar): a faint static line
            d.line([zone_x0, cy, zone_x0 + zone_w, cy], fill=C_MUTED, width=1)
        # "PAUSED" pill over the waveform when paused
        if self.paused:
            f = font(10, bold=True)
            label = "PAUSED"
            tw, th = text_size(f, label)
            px = (w - tw) // 2
            d.rounded_rectangle(
                [px - 8, cy - 11, px + tw + 8, cy + 11], radius=11, fill=C_BG
            )
            d.text((px, cy - 5), label, font=f, fill=C_FG)
        self.layer.render(img)

    def _draw_waveform(self, d, w, h, bars=9):
        gap = 3
        x0 = 14
        bar_w = (w - 2 * x0 - (bars - 1) * gap) / bars
        cy = h // 2
        recent = self.level_hist[-bars:]
        t = time.time()
        for i in range(bars):
            if self.state == IDLE:
                lvl = 0.18 + 0.10 * math.sin(t * 2.2 + i * 0.9)
                col = C_MUTED
            else:
                lvl = recent[i] if len(recent) == bars else 0.0
                if self.state == PROCESSING:
                    lvl = max(lvl, 0.12)
                col = C_GREEN if self.state == PROCESSING else C_ACCENT
            bh = max(4, min(h - 14, int(lvl * (h - 14))))
            x = x0 + i * (bar_w + gap)
            d.rounded_rectangle(
                [x, cy - bh / 2, x + bar_w - 1, cy + bh / 2],
                radius=bar_w / 2, fill=col,
            )

    @staticmethod
    def _paint_mic_glyph(d, w, h):
        """Draw the classic mic (capsule + stand) centered on the idle pill."""
        cy = h // 2
        mw, mh = 8, 14
        d.rounded_rectangle(
            [w // 2 - mw // 2, cy - mh // 2, w // 2 + mw // 2, cy + mh // 2],
            radius=mw // 2, outline=C_MUTED, width=2,
        )
        d.arc([w // 2 - 9, cy - 5, w // 2 + 9, cy + 9], 10, 170, fill=C_MUTED, width=2)
        d.line([w // 2, cy + 9, w // 2, cy + 13], fill=C_MUTED, width=2)

    @staticmethod
    def _draw_x(d, cx, cy, r, color):
        d.line([cx - r // 2, cy - r // 2, cx + r // 2, cy + r // 2], fill=color, width=3)
        d.line([cx - r // 2, cy + r // 2, cx + r // 2, cy - r // 2], fill=color, width=3)

    @staticmethod
    def _draw_tick(d, cx, cy, r, color):
        pts = [(cx - r // 2, cy), (cx - r // 6, cy + r // 3), (cx + r // 2, cy - r // 3)]
        d.line([pts[0], pts[1], pts[1], pts[2]], fill=color, width=3, joint="curve")

    def _draw_live_wave(self, d, x0, zone_w, cy, h):
        """Live 'voice machine' wave: flat when silent, waves with your voice.

        While silent (or paused) it stays a dull, dotted yellow line. When you
        speak it becomes a smooth, bright-yellow sine curve whose WAVELENGTH
        and SCROLL follow the audio — the amplitude is driven by the incoming
        mic energy, so it moves exactly with your voice like an equalizer /
        sine-wave machine. No idle undulation: silence = flat.
        """
        # bright vs dull: animate only when there's real voice energy
        speaking = self.level >= 0.04
        silent_bar = 3  # dull dotted line height when idle/paused

        # live amplitude: a little smoothing so it rises/falls with your voice
        # but doesn't flicker frame to frame
        target = min(self.level, 1.0)
        # scroll the wave as a function of time so it reads as flowing audio
        # (wavelength stays readable across the zone)
        n = 64
        wl = zone_w / 7.0                     # ~7 full waves across the bar
        t = time.time() * 5.0                 # scroll speed while speaking

        amp_px = int(target * (h - 16))
        if amp_px < 3:
            amp_px = 0

        def wf(xx):
            return math.sin((xx / wl) * math.tau - t)

        if not speaking:
            # dull dotted line (small dashes) — matches the design's idle state
            dash = 6
            x = x0
            while x < x0 + zone_w:
                x2 = min(x + dash, x0 + zone_w)
                d.line([x, cy, x2, cy], fill=self._brighter(C_YELLOW, 0.5), width=2)
                x += dash * 2
            return

        # speaking: draw the bright yellow wave, thin underlay for depth
        pts = []
        for i in range(n + 1):
            xx = x0 + zone_w * i / n
            yy = cy + wf(xx) * amp_px * (0.55 + 0.45 * wf(i * 0.5))
            pts.append((xx, yy))
        # underlay (slightly offset +1 + dimmer) then thin bright top
        d.line(
            [(x, y + 1) for x, y in pts],
            fill=(150, 118, 30), width=4, joint="curve",
        )
        d.line(pts, fill=C_YELLOW, width=3, joint="curve")

    @staticmethod
    def _brighter(rgb, f=1.35):
        return tuple(min(255, int(c * f)) for c in rgb)