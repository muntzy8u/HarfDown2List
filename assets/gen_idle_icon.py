"""Regenerate the idle-button icon (assets/idle-mic.png).

A professional mic glyph: gradient capsule, cradle, stem and base, drawn at
8x supersampling then LANCZOS-downscaled so edges stay crisp on the small
(~56 px) pill. Transparent background — it pastes over the dark glass disc.

Usage:  ./venv/bin/python assets/gen_idle_icon.py
Design dials are the constants below.
"""
import os

import numpy as np
from PIL import Image, ImageDraw

# --- design dials -------------------------------------------------------
OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "idle-mic.png")
TARGET = 256            # output png size
SS = 8                  # supersample factor (draw at TARGET * SS)
S = TARGET * SS

C_TOP = (234, 239, 246)   # capsule top colour (bright silver-blue)
C_BOT = (168, 181, 198)   # capsule bottom colour
C_CRADLE_TOP = (183, 195, 210)
C_CRADLE_BOT = (128, 141, 158)
# -----------------------------------------------------------------------

W = H = S
cx = W // 2


def gradient_rgb(w, h, c_top, c_bot):
    """Vertical colour ramp as an (h, w, 3) float array."""
    t = np.linspace(0.0, 1.0, h)[:, None, None]
    top = np.array(c_top, dtype=float)[None, None, :]
    bot = np.array(c_bot, dtype=float)[None, None, :]
    return top + (bot - top) * t * np.ones((h, w, 3))


def render(mask_draw, c_top, c_bot):
    """Colorize a per-shape alpha mask with a vertical gradient."""
    mask = np.asarray(mask_draw, dtype=float) / 255.0
    rgb = gradient_rgb(W, H, c_top, c_bot)
    out = np.zeros((H, W, 4), dtype=np.uint8)
    out[..., :3] = np.clip(rgb, 0, 255)
    out[..., 3] = (mask * 255).astype(np.uint8)
    return out


# capsule mask (narrow rounded bar — classic mic capsule)
cap = Image.new("L", (W, H), 0)
ImageDraw.Draw(cap).rounded_rectangle(
    [cx - 180, 460, cx + 180, 1160], radius=180, fill=255)

# cradle + stem + base mask (one connected "stand" tone).
# PIL arcs sweep clockwise: 5°..175° passes 6 o'clock (90°) = bottom U.
stand = Image.new("L", (W, H), 0)
sd = ImageDraw.Draw(stand)
sd.arc([cx - 330, 680, cx + 330, 1340], start=5, end=175,
       fill=255, width=130)
sd.line([cx, 1340, cx, 1520], fill=255, width=110)
sd.rounded_rectangle([cx - 190, 1530, cx + 190, 1610], radius=40, fill=255)

img = render(cap, C_TOP, C_BOT)
img = np.maximum(img, render(stand, C_CRADLE_TOP, C_CRADLE_BOT))

png = Image.fromarray(img, "RGBA").resize((TARGET, TARGET), Image.LANCZOS)
os.makedirs(os.path.dirname(OUT), exist_ok=True)
png.save(OUT)
print(f"wrote {OUT} ({TARGET}x{TARGET})")