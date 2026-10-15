"""Right-click popup menu."""
from PIL import Image, ImageDraw

from xbase import C_BG, C_BG_ACTIVE, C_FG, XLayer, font, new_canvas, text_size


ROW_H = 26


class Menu:
    """One-column popup. Auto-closes on selection or outside click."""

    def __init__(self, ctx):
        self.ctx = ctx
        self.layer = None
        self.items = []
        self.hover = -1
        self.on_select = None
        self.on_close = None
        self._img = None

    def is_open(self):
        return self.layer is not None

    def window(self):
        return self.layer.win if self.layer else None

    def open(self, x, y, items, on_select, on_close=None):
        if self.layer is not None:
            self.close()
        f = font(13)
        w = 150
        for label, _ in items:
            tw, _ = text_size(f, label)
            w = max(w, tw + 28)
        h = 8 + len(items) * ROW_H
        x = max(4, min(x, self.ctx.sw - w - 4))
        y = max(4, min(y, self.ctx.sh - h - 4))
        self.layer = XLayer(self.ctx, x, y, w, h)
        self.items = items
        self.hover = -1
        self.on_select = on_select
        self.on_close = on_close
        self._img = self._paint()
        self.layer.map()
        self.layer.render(self._img)

    def close(self):
        if self.layer is not None:
            layer = self.layer
            self.layer = None
            layer.destroy()
        self.items = []
        self.hover = -1
        cb = self.on_close
        self.on_close = None
        if cb:
            cb()

    def _row_at(self, local_y):
        return (local_y - 4) // ROW_H

    def handle_event(self, e):
        """Returns 'selected' | 'consumed' | None (None = not our event)."""
        from Xlib import X

        if self.layer is None:
            return None
        t = e.type
        if t == X.MotionNotify:
            row = self._row_at(e.event_y)
            if 0 <= row < len(self.items):
                if row != self.hover:
                    self.hover = row
                    self._repaint()
            elif self.hover != -1:
                self.hover = -1
                self._repaint()
            return "consumed"
        if t == X.ButtonPress:
            if e.detail == 1:
                row = self._row_at(e.event_y)
                if 0 <= row < len(self.items):
                    payload = self.items[row][1]
                    self.close()
                    self.on_select(payload)
                    return "selected"
            self.close()
            return "consumed"
        if t == X.Expose:
            self._repaint()
            return "consumed"
        return None

    def _paint(self):
        f = font(13)
        w, h = self.layer.w, self.layer.h
        img, d = new_canvas(w, h, bg=C_BG)
        d.rounded_rectangle([0, 0, w - 1, h - 1], radius=10, fill=C_BG)
        for i, (label, _) in enumerate(self.items):
            ty = 4 + i * ROW_H
            if i == self.hover:
                d.rounded_rectangle([4, ty, w - 5, ty + ROW_H - 2], radius=6, fill=C_BG_ACTIVE)
            d.text((14, ty + 6), label, font=f, fill=C_FG)
        self._img = img
        return img

    def _repaint(self):
        if self.layer is not None and self.layer.mapped:
            self.layer.render(self._paint())