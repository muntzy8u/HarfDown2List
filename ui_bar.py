"""Transcript bar: live-preview strip floating above the bubble."""
from PIL import Image, ImageDraw

import config
from xbase import C_BG, C_FG, C_MUTED, XLayer, font, new_canvas, text_size


class Bar:
    """Click-through strip above the bubble.

    partial + committed_len => committed prefix drawn white, rest grey.
    """

    def __init__(self, ctx, x, y, w=None, h=None):
        w = w or config.TRANSCRIPT_BAR_W
        h = h or config.TRANSCRIPT_BAR_H
        self.layer = XLayer(ctx, x, y, w, h)
        self.layer.set_click_through()
        self.partial = ""
        self.committed_len = 0
        self.status = ""
        self.visible = False

    # --- lifecycle ---
    def show(self):
        self.layer.map()
        self.visible = True
        self.paint()

    def hide(self):
        self.layer.unmap()
        self.visible = False

    def move(self, x, y):
        self.layer.move(x, y)

    def move_resize(self, x, y, w, h):
        self.layer.move_resize(x, y, w, h)
        self.paint()

    # --- content ---
    def set_text(self, partial, committed_len, status=""):
        self.partial = partial
        self.committed_len = committed_len
        self.status = status
        if self.visible:
            self.paint()

    def clear(self):
        self.partial = ""
        self.committed_len = 0
        self.status = ""
        self.paint()

    # --- painting ---
    def paint(self):
        w, h = self.layer.w, self.layer.h
        img, d = new_canvas(w, h, bg=C_BG)
        d.rounded_rectangle([0, 0, w - 1, h - 1], radius=h // 2, fill=C_BG)
        f = font(13)
        avail = w - 24
        text = self.partial
        if not text:
            text = self.status
        if text:
            # head-truncate: keep the tail (latest words) visible
            disp = text
            tw, _ = text_size(f, disp)
            while tw > avail and len(disp) > 4:
                disp = disp[len(disp) // 3 :]
                tw, _ = text_size(f, disp)
            # split into committed / tail at the committed_len in displayed text
            c = min(self.committed_len, len(text))
            # map committed_len into possibly-truncated display string
            drop = len(text) - len(disp)
            c_disp = max(0, c - drop)
            tw1, _ = text_size(f, disp[:c_disp])
            ty = (h - 13) // 2
            d.text((12, ty), disp[:c_disp], font=f, fill=C_FG)
            d.text((12 + tw1, ty), disp[c_disp:], font=f, fill=C_MUTED)
        else:
            d.text((12, (h - 13) // 2), "…", font=f, fill=C_MUTED)
        self.layer.render(img)