"""Regenerate the idle-button icon set (five professional renditions).

Outputs into assets/ :
  idle-mic.png            the DEFAULT icon (v1) — wired via config.BUBBLE_ICON
  icons/idle-mic-1..5.png all five variants, 256 px, transparent
  icons-preview.png       labeled contact sheet: each icon composited on the
                          app's real dark-glass disc at 4x pill scale

All glyphs are drawn at 8x supersampling then LANCZOS-downscaled, so edges
stay smooth/crisp when the app shrinks them onto the small pill.

Usage:  ./venv/bin/python assets/gen_idle_icon.py
Design dials are the constants below.
"""
import os

import numpy as np
from PIL import Image, ImageDraw, ImageFilter

# --- design dials -------------------------------------------------------
HERE = os.path.dirname(os.path.abspath(__file__))
ASSETS = HERE
ICONS = os.path.join(ASSETS, "icons")
TARGET = 256            # output png size
SS = 8                  # supersample factor (draw at TARGET * SS)
S = TARGET * SS

C_TOP = (240, 244, 250)   # capsule top colour (bright silver-blue)
C_BOT = (176, 188, 204)   # capsule bottom colour
C_STAND = (168, 181, 199)  # cradle/stand/base tone (flat, slightly dimmer)
C_ACCENT_A = (99, 132, 255)   # app accent indigo
C_ACCENT_B = (168, 108, 255)  # app accent violet
C_RING = (99, 132, 255)
# -----------------------------------------------------------------------

W = H = S
cx = W // 2


def gradient_rgb(w, h, c_top, c_bot):
    t = np.linspace(0.0, 1.0, h)[:, None, None]
    top = np.array(c_top, dtype=float)[None, None, :]
    bot = np.array(c_bot, dtype=float)[None, None, :]
    return np.clip(top + (bot - top) * t * np.ones((h, w, 3)), 0, 255)


def colorize(mask, c_top, c_bot):
    m = np.asarray(mask, dtype=float) / 255.0
    out = np.zeros((H, W, 4), dtype=np.uint8)
    out[..., :3] = gradient_rgb(W, H, c_top, c_bot).astype(np.uint8)
    out[..., 3] = (m * 255).astype(np.uint8)
    return out


def capsule_mask():
    m = Image.new("L", (W, H), 0)
    ImageDraw.Draw(m).rounded_rectangle(
        [cx - 170, 500, cx + 170, 1140], radius=170, fill=255)
    return m


def stand_mask():
    m = Image.new("L", (W, H), 0)
    sd = ImageDraw.Draw(m)
    # PIL arcs sweep clockwise: 5°..175° passes 6 o'clock (bottom U cradle)
    sd.arc([cx - 300, 760, cx + 300, 1360], start=5, end=175,
           fill=255, width=120)
    sd.line([cx, 1340, cx, 1500], fill=255, width=100)
    sd.rounded_rectangle([cx - 180, 1515, cx + 180, 1595], radius=40, fill=255)
    return m


def compose(cap_mode="fill", use="silver", ring=False, glow=False):
    """Return a 256 px RGBA icon per style switches."""
    cap = capsule_mask()
    stand = stand_mask()
    if cap_mode == "outline":
        # outline ring = outer capsule minus an inner capsule (no MaxFilter:
        # big-kernel filters on a 2048px canvas take forever)
        stroke = 90
        o = capsule_mask()
        ImageDraw.Draw(o).rounded_rectangle(
            [cx - 170 + stroke, 500 + stroke, cx + 170 - stroke, 1140 - stroke],
            radius=170 - stroke, fill=0)
        cap = o
    if use == "accent":
        cap_img = colorize(cap, C_ACCENT_A, C_ACCENT_B)
        stand_img = colorize(stand, (131, 158, 240), (110, 86, 186))
    else:
        cap_img = colorize(cap, C_TOP, C_BOT)
        stand_img = colorize(stand, C_STAND, tuple(int(c * 0.82) for c in C_STAND))
    img = np.maximum(cap_img, stand_img)
    if glow:
        g = Image.fromarray(img, "RGBA")
        blur = g.filter(ImageFilter.GaussianBlur(radius=26))
        base = np.zeros((H, W, 4), dtype=np.float32)
        b = np.asarray(blur, dtype=np.float32)
        base[..., :3] = 255.0
        base[..., 3] = np.clip(b[..., 3] * 1.05, 0, 255)
        img = np.clip(np.asarray(img, dtype=np.float32) +
                      base * (1.0 - np.asarray(img, dtype=np.float32)[..., 3:4] / 255.0),
                      0, 255).astype(np.uint8)
    if ring:
        r = Image.new("L", (W, H), 0)
        # thin accent ring just inside the outer edge (badge look)
        d = ImageDraw.Draw(r)
        d.ellipse([64, 64, W - 64, H - 64], outline=255, width=26)
        # alpha_COMPOSITE the ring layer (never add RGBA arrays: adding the
        # ring's RGB into every pixel washed the glyph toward white)
        base = Image.fromarray(img, "RGBA")
        base.alpha_composite(Image.fromarray(colorize(r, C_RING, C_RING), "RGBA"))
        img = np.asarray(base)
    png = Image.fromarray(np.clip(img, 0, 255), "RGBA")
    return png.resize((TARGET, TARGET), Image.LANCZOS)


VARIANTS = {
    "idle-mic-1": dict(cap_mode="fill", use="silver",
                       desc="refined silver — the smooth default"),
    "idle-mic-2": dict(cap_mode="outline", use="silver",
                       desc="outline stroke — airy, modern"),
    "idle-mic-3": dict(cap_mode="fill", use="accent",
                       desc="app accent colours — indigo/violet"),
    "idle-mic-4": dict(cap_mode="fill", use="silver", glow=True,
                       desc="silver with a soft halo glow"),
    "idle-mic-5": dict(cap_mode="fill", use="silver", ring=True,
                       desc="silver mic + accent badge ring"),
    "idle-mic-6": dict(cap_mode="fill", use="accent", ring=True,
                       desc="MIX: accent mic centre + v5 badge ring"),
}


def disc(w=224):
    """The app's real idle disc look at 4x pill scale (radial-ish glass)."""
    top, bot = np.array((30, 34, 48)), np.array((13, 15, 22))
    t = np.linspace(0, 1, w)[:, None]
    body = (top * (1 - t) + bot * t)          # (w, 1, 3) vertical ramp
    arr = np.zeros((w, w, 4), dtype=np.uint8)
    arr[..., :3] = np.broadcast_to(body[:, None, :], (w, w, 3))
    yy, xx = np.mgrid[0:w, 0:w]
    cxp = w / 2.0
    inside = ((xx + 0.5) - cxp) ** 2 + ((yy + 0.5) - cxp) ** 2 <= (w / 2.0) ** 2
    arr[..., 3] = np.where(inside, 255, 0)
    return Image.fromarray(arr, "RGBA")


def preview():
    cell, pad, label_h = 224, 34, 40
    n = len(VARIANTS)
    sheet_w = pad + n * (cell + pad)
    sheet_h = pad + cell + label_h + pad
    sheet = Image.new("RGB", (sheet_w, sheet_h), (58, 62, 72))
    names = list(VARIANTS)
    for i, name in enumerate(names):
        x0 = pad + i * (cell + pad)
        icon = Image.fromarray(
            np.asarray(make_variant(name)), "RGBA")
        # composite icon onto the disc at 4x pill scale (icon = 88/152 of disc)
        d = disc(cell)
        side = int(cell * 152 / 224)
        ic = icon.resize((side, side), Image.LANCZOS)
        d.alpha_composite(ic, ((cell - side) // 2, (cell - side) // 2))
        sheet.paste(d, (x0, pad), d)
        ImageDraw.Draw(sheet).text(
            (x0 + cell // 2 - 12, pad + cell + 10),
            "v%d" % (i + 1), fill=(225, 230, 238))
    out = os.path.join(ASSETS, "icons-preview.png")
    sheet.save(out)
    return out


def make_variant(name):
    kw = VARIANTS[name]
    return compose(cap_mode=kw["cap_mode"], use=kw["use"],
                   ring=kw.get("ring", False), glow=kw.get("glow", False))


def main():
    os.makedirs(ICONS, exist_ok=True)
    for name in VARIANTS:
        p = os.path.join(ICONS, f"{name}.png")
        make_variant(name).save(p)
        print("wrote", p, "-", VARIANTS[name]["desc"])
    # default icon file = v6 (user's pick: v3 centre + v5 ring)
    make_variant("idle-mic-6").save(os.path.join(ASSETS, "idle-mic.png"))
    print("wrote", os.path.join(ASSETS, "idle-mic.png"), "(default: v6 mix)")
    print("preview:", preview())


if __name__ == "__main__":
    main()