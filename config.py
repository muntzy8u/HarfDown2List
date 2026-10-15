"""User-tunable configuration for harfdown2listen."""
import os

# --- Branding ---
APP_NAME = "harfdown2listen"
APP_TITLE = "harfdown2listen"

# --- STT models (faster-whisper) ---
# tiny.en is loaded at startup for live partials (fast, english-only).
# small is loaded lazily for the final polished pass. Sizes: tiny.en / base.en /
# small / distil-small.en etc. See faster-whisper docs for all options.
PARTIAL_MODEL = "tiny.en"
FINAL_MODEL = "small"

# If True, the final transcript also runs through an LLM "polish" pass
# (ollama) that removes fillers ("um", "uh", false starts) and resolves
# self-corrections ("let's meet at 5, actually 6" -> "Let's meet at 6 pm").
# MEASURED (2026-09-11, via ollama-launch cloud router):
#   deepseek-v4-flash:cloud + think:false  -> 0.7s  (best)
#   qwen3.5:cloud + think:false            -> 1.4s
#   local llama2-uncensored:7b             -> 28.5s (unusable)
# NOTE: "think": false is REQUIRED for the cloud router models or they
# reason until the token budget is gone and return empty content.
LLM_POLISH = True
LLM_POLISH_MODEL = "deepseek-v4-flash:cloud"
# Max seconds to wait for the polish LLM. If it exceeds this, raw transcript ships.
LLM_POLISH_TIMEOUT = 5.0

# --- Audio ---
SAMPLE_RATE = 16000
BLOCK = 480            # 30 ms blocks; meter cadence
# Mic device name (as shown in `pactl list sources short`). None = system default.
MIC_SOURCE = None
# RMS-based voice activity: below this level = silence, used only for metering
# and idle visuals — whisper VAD is what actually gates transcription.
SILENCE_RMS = 0.004
# Drop leading/trailing silence from final transcription input.
TRIM_SILENCE = True
TRIM_MARGIN_S = 0.25   # padding kept around detected speech
TRIM_MARGIN_S_LEAD = 0.15

# --- Streaming partials ---
# How often (seconds) to run the partial (tiny) model on the audio so far.
PARTIAL_INTERVAL_S = 1.2
# Hard cap for live partials (seconds of audio); beyond this partials stop updating
# (tiny.en cost per call is ~fixed, so a large window is cheap).
PARTIAL_MAX_S = 300.0
# Flow-bar session hard cap (seconds) so a forgotten tick can't record forever.
FLOWBAR_MAX_S = 600.0
# If no voice is detected in the flowbar for this many seconds, auto-stop and
# return to normal (the recording length itself stays at FLOWBAR_MAX_S).
FLOWBAR_SILENCE_S = 60.0

# --- Bubble (X11 floating UI) ---
# Collapsed idle button is a CIRCLE (BUBBLE_W == BUBBLE_H). ~1.5 cm at 96 dpi ≈ 56px.
BUBBLE_W = 56
BUBBLE_H = 56
# Idle face: hd.jpg shrunk into the black circle button (image kept, just scaled).
BUBBLE_IMG = os.path.join(os.path.dirname(__file__), "assets", "hd.jpg")
# OPTIONAL: your own idle-button icon. Set this to any PNG (recommended) or
# SVG (needs `cairosvg` installed in the venv) and it replaces the drawn
# mic glyph on the stationary button. None = the built-in drawn mic.
#   BUBBLE_ICON = os.path.expanduser("~/Pictures/my-icon.png")
BUBBLE_ICON = None
# expanded flow bar: [X | waveform | tick]
FLOWBAR_W = 280
FLOWBAR_H = 48
ZONE_W = 44                # width of the X / tick hit zones
# default anchor position (bottom-center of screen)
DEFAULT_POS = (0.5, 0.92)   # fractions of screen width/height
SNAP_MARGIN_PX = 24        # dead-zone before edge-snapping kicks in
SNAP_TOLERANCE_PX = 40    # drag within this of an edge to snap
EDGE_MARGIN_PX = 16       # final resting offset from screen edges
SNAP_ANIM_STEPS = 8
SNAP_ANIM_MS = 90
# 144 px on a 96-dpi display ≈ 3.8 cm — the "top line" stays compact
TRANSCRIPT_BAR_W = 144
TRANSCRIPT_BAR_H = 30
TRANSCRIPT_FONT = ("xft:DejaVu Sans:size=10")
# 0=off 1=errors only 2=status 3=debug
LOG_LEVEL = 2
LOG_FILE = os.path.expanduser("~/.local/state/harfdown2listen/hd2l.log")

# --- Paste behaviour ---
# Use xdotool key ctrl+v to paste at the caret; fallback: type text with xdotool.
PASTE_METHOD = "clipboard"     # "clipboard" | "type"
TYPE_SPEED_MS = 12            # per-char delay for the "type" fallback
# Global hold-to-talk hotkey (pynput key name). F13 is unbound on Linux by
# default — bind a mouse extra-button or any spare key to F13 with
# input-remapper / xkb / your mouse's driver, then hold it to dictate.
HOTKEY_PTT = "f13"
# Double-press the hotkey within this window toggles hands-free mode.
HOTKEY_DOUBLE_PRESS_S = 0.4
# If the focused window is one of these wm_class names, skip insertion and show
# transcript in the bubble menu instead (safety for terminals, etc.)
NO_PASTE_CLASSES = set()
# After pasting, restore the clipboard to the user's previous content.
RESTORE_CLIPBOARD = True
RESTORE_CLIPBOARD_DELAY_S = 0.3

# --- TTS (voice read-back of transcripts / answers) ---
# Voice personas in voice_out.py: michaela, libby, ryan, sonia, aussie_f,
# aussie_m, us_f, us_m. Michaela = en-US-AriaNeural (US female).
TTS_ENABLED = False          # speak the final transcript after pasting
TTS_PERSONA = "michaela"
