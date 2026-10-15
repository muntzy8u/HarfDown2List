"""Low-level X11 plumbing: one connection, override-redirect painted layers."""
from PIL import Image, ImageDraw, ImageFont
from Xlib import X
from Xlib.ext import shape

# --- palette (dark, glass) ---
C_BG = (13, 15, 22)
C_BG_ACTIVE = (32, 38, 54)
C_FG = (235, 239, 244)
C_MUTED = (120, 130, 145)
C_ACCENT = (99, 132, 255)      # indigo — the modern accent
C_ACCENT2 = (168, 108, 255)    # violet — gradient partner
C_GREEN = (98, 200, 120)
C_RED = (229, 90, 96)
C_YELLOW = (224, 179, 38)      # matches the JPEG accent / baby line
# rim: bright top edge, dim bottom — simulates light from above on glass
C_RIM_TOP = (140, 152, 180)
C_RIM_BOT = (52, 60, 82)
# subtle edge highlight for definition against any background
C_EDGE = (70, 78, 96)


def glass_pill(w, h, radius, c_top, c_bot):
    """Rounded-rect canvas with a vertical gradient fill and a SINGLE smooth rim.

    Returns (img, draw). Simulates frosted glass: gradient body, one clean
    outline all the way around (no split top/bottom arcs — splitting them made
    the corners look like three stacked bars where the arcs overlapped).
    """
    img = Image.new("RGB", (w, h), c_top)
    # vertical gradient body
    grad = Image.new("RGB", (w, h))
    for y in range(h):
        t = y / max(1, h - 1)
        row = tuple(int(c_top[i] + (c_bot[i] - c_top[i]) * t) for i in range(3))
        for x0 in range(0, w, 64):
            grad.paste(row, (x0, y, min(w, x0 + 64), y + 1))
    # mask to the pill shape
    mask = Image.new("L", (w, h), 0)
    ImageDraw.Draw(mask).rounded_rectangle(
        [0, 0, w - 1, h - 1], radius=radius, fill=255
    )
    base = Image.new("RGB", (w, h), C_BG)
    base.paste(grad, (0, 0), mask)
    d = ImageDraw.Draw(base)
    # one smooth rim around the whole pill (single, thin outline)
    d.rounded_rectangle([0, 0, w - 1, h - 1], radius=radius, outline=C_EDGE, width=1)
    return base, d

FONT_PATH = "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"
FONT_BOLD_PATH = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"

_fonts = {}


def font(size, bold=False):
    key = (size, bold)
    if key not in _fonts:
        try:
            _fonts[key] = ImageFont.truetype(
                FONT_BOLD_PATH if bold else FONT_PATH, size
            )
        except Exception:
            _fonts[key] = ImageFont.load_default()
    return _fonts[key]


def text_size(f, s):
    tmp = ImageDraw.Draw(Image.new("RGB", (8, 8)))
    box = tmp.textbbox((0, 0), s, font=f)
    return box[2] - box[0], box[3] - box[1]


class XCtx:
    """Process-wide X context: display, screen, pixel encoding, monitors."""

    def __init__(self):
        from Xlib import display as xdisplay

        self.d = xdisplay.Display()
        self.screen = self.d.screen()
        self.root = self.screen.root
        self.depth = self.screen.root_depth
        self.sw = self.screen.width_in_pixels
        self.sh = self.screen.height_in_pixels
        self.monitors = self._query_monitors()

    def _query_monitors(self):
        """[(x, y, w, h), ...] via Xinerama; single full-screen entry fallback."""
        try:
            from Xlib.ext import xinerama

            if self.d.has_extension("XINERAMA"):
                self.d.xinerama_query_version()
                info = self.d.xinerama_query_screens()
                return [(s.x, s.y, s.width, s.height) for s in info.screens]
        except Exception:
            pass
        return [(0, 0, self.sw, self.sh)]

    def monitor_at(self, x, y):
        """Monitor containing point, else the monitor nearest to it."""
        for m in self.monitors:
            mx, my, mw, mh = m
            if mx <= x < mx + mw and my <= y < my + mh:
                return m
        best, bd = self.monitors[0], 1 << 30
        for m in self.monitors:
            mx, my, mw, mh = m
            d = abs((mx + mw // 2) - x) + abs((my + mh // 2) - y)
            if d < bd:
                best, bd = m, d
        return best

    def clamp_to_monitor(self, x, y, w, h):
        mx, my, mw, mh = self.monitor_at(x + w // 2, y + h // 2)
        x = max(mx, min(x, mx + mw - w))
        y = max(my, min(y, my + mh - h))
        return x, y


class XLayer:
    """One override-redirect top-level window painted from a PIL RGB image."""

    def __init__(self, ctx, x, y, w, h, bg=C_BG):
        self.ctx = ctx
        self.d = ctx.d
        self.w, self.h = w, h
        self.win = ctx.root.create_window(
            x,
            y,
            w,
            h,
            0,
            ctx.depth,
            window_class=X.InputOutput,
            override_redirect=True,
            event_mask=X.ExposureMask
            | X.ButtonPressMask
            | X.ButtonReleaseMask
            | X.Button1MotionMask
            | X.Button3MotionMask
            | X.PointerMotionMask,
            background_pixel=self.pixel(bg),
            save_under=True,
        )
        self.gc = self.win.create_gc()
        self.mapped = False

    def pixel(self, rgb):
        # TrueColor 24-bit, standard masks (verified live on this box)
        return (rgb[0] << 16) | (rgb[1] << 8) | rgb[2]

    # --- visibility / geometry ---
    def map(self):
        if not self.mapped:
            self.win.map()
            self.mapped = True
        self.raise_()
        self.d.flush()

    def unmap(self):
        if self.mapped:
            self.win.unmap()
            self.mapped = False
            self.d.flush()

    def raise_(self):
        self.win.configure(stack_mode=X.Above)

    def move(self, x, y):
        self.win.configure(x=int(x), y=int(y))
        self.d.flush()

    def move_resize(self, x, y, w, h):
        self.win.configure(x=int(x), y=int(y), width=w, height=h)
        self.w, self.h = w, h
        self.d.flush()

    def destroy(self):
        try:
            self.win.destroy()
            self.d.flush()
        except Exception:
            pass

    # --- input shape ---
    def set_click_through(self):
        """Zero input region: every click passes to the window below."""
        self.win.shape_rectangles(shape.SO.Set, shape.SK.Input, X.Unsorted, 0, 0, [])

    def set_input_mask(self, mask_img):
        """1-bit PIL image: white pixels accept input, black pass through."""
        w, h = mask_img.size
        px = mask_img.load()
        rects = []
        for y in range(h):
            run_start = None
            for x in range(w + 1):
                on = x < w and px[x, y]
                if on and run_start is None:
                    run_start = x
                elif not on and run_start is not None:
                    rects.append((run_start, y, x - run_start, 1))
                    run_start = None
        self.win.shape_rectangles(
            shape.SO.Set, shape.SK.Input, X.Unsorted, 0, 0, rects
        )
        self.d.flush()

    def pill_input(self, radius):
        img = Image.new("1", (self.w, self.h), 0)
        ImageDraw.Draw(img).rounded_rectangle(
            [0, 0, self.w - 1, self.h - 1], radius=radius, fill=1
        )
        self.set_input_mask(img)

    def set_disc_visible(self):
        """Clip the window's VISIBLE shape to a circle (RLE 1px rows).

        Without this, a square PIL canvas pastes opaque black corners even
        though the input region is circular — the button looks like a square
        with a circle drawn in it. Setting shape.SK.Bounding makes the window
        itself round, so the corners are transparent/click-through.
        """
        w, h = self.w, self.h
        cx, cy = (w - 1) / 2.0, (h - 1) / 2.0
        R = min(w, h) / 2.0 - 0.5
        rects = []
        for y in range(h):
            dy = y - cy
            half = R if abs(dy) >= R else int(round((R * R - dy * dy) ** 0.5))
            x0 = max(0, int(round(cx - half)))
            x1 = min(w, int(round(cx + half)) + 1)
            if x1 > x0:
                rects.append((x0, y, x1 - x0, 1))
        self._set_shape(shape.SK.Bounding, rects)
        self._set_shape(shape.SK.Input, rects)
        self.d.flush()

    def reset_visible(self):
        """Restore the window to a full rectangle (Bounding + Input)."""
        self._set_shape(shape.SK.Bounding, [(0, 0, self.w, self.h)])
        self._set_shape(shape.SK.Input, [(0, 0, self.w, self.h)])
        self.d.flush()

    def set_pill_visible(self, radius):
        """Clip the window's VISIBLE shape to a rounded rect (the flowbar pill).

        Without this the flowbar window is a plain rectangle, so the dark
        canvas corners outside the rounded pill show as black triangles at
        each corner. Clipping Bounding + Input to the pill shape makes those
        corners transparent.
        """
        w, h = self.w, self.h
        # build a 1-bit mask of the pill and RLE it into per-row rects
        img = Image.new("1", (w, h), 0)
        ImageDraw.Draw(img).rounded_rectangle(
            [0, 0, w - 1, h - 1], radius=radius, fill=1
        )
        px = img.load()
        rects = []
        for y in range(h):
            run_start = None
            for x in range(w + 1):
                on = x < w and px[x, y]
                if on and run_start is None:
                    run_start = x
                elif not on and run_start is not None:
                    rects.append((run_start, y, x - run_start, 1))
                    run_start = None
        self._set_shape(shape.SK.Bounding, rects)
        self._set_shape(shape.SK.Input, rects)
        self.d.flush()

    def _set_shape(self, kind, rects):
        self.win.shape_rectangles(
            shape.SO.Set, kind, X.Unsorted, 0, 0, rects
        )

    # --- rendering ---
    def render(self, img):
        if not self.mapped:
            return
        if img.size != (self.w, self.h):
            img = img.resize((self.w, self.h))
        if img.mode != "RGB":
            img = img.convert("RGB")
        self.win.put_pil_image(self.gc, 0, 0, img)
        self.d.flush()


def new_canvas(w, h, bg=C_BG):
    img = Image.new("RGB", (w, h), bg)
    return img, ImageDraw.Draw(img)