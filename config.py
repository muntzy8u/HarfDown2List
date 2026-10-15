"""User-tunable configuration for HarfDown2List."""
import os

# --- Branding ---
APP_NAME = "HarfDown2List"
APP_TITLE = "HarfDown2List"

# --- STT models (faster-whisper) ---
# tiny.en is loaded at startup for live partials (fast, english-only).
# small is loaded lazily for the final polished pass. Sizes: tiny.en / base.en /
# small / distil-small.en etc. See faster-whisper docs for all options.
PARTIAL_MODEL = "tiny.en"
FINAL_MODEL = "small"

# If True, the final transcript also runs through an LLM "polish" pass
# (ollama) that removes fillers ("um", "uh", false starts) and resolves
# self-corrections ("let's meet at 5, actually 6" -> "Let's meet at 6 pm").
# MEASURED (2026-10-15, local Ollama):
#   qwen3.5:2b   + think:false -> ~3.2s warm; ~11.7s first cold model load
#                                (first polish after a boot may time out once,
#                                then the model stays cached and warm)
#   qwen2.5:1.5b + think:false -> ~1.1s warm, but leaks "Sure, let's clean
#                                up the voice dictation:" into the text
#   glm-5.3-flash:cloud        -> ~0.7s (cloud, verified 200; needs the router)
#   deepseek-v4-flash:cloud    -> RETIRED by the router 2026-09-25 (HTTP 410)
# NOTE: "think": false is REQUIRED for cloud router models or they reason
# until the token budget is gone and return empty content; qwen3.5 accepts it.
LLM_POLISH = True
LLM_POLISH_MODEL = "qwen3.5:2b"
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
LOG_FILE = os.path.expanduser("~/.local/state/HarfDown2List/hd2l.log")

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
# Voice personas in voice_out.py: emma, jenny, michaela, maisie, libby, ryan,
# sonia, aussie_f, aussie_m, us_f, us_m.
# emma = en-US-EmmaNeural (cheerful, friendly US female — default)
# maisie = young en-GB female; libby = en-GB friendly; natasha = en-AU.
TTS_ENABLED = False          # speak the final transcript after pasting
TTS_PERSONA = "emma"
# Engine order for actually making sound: "piper" (local neural TTS, offline,
# streams straight into paplay — no audio files, no cloud, no 403s) or
# "edge" (Microsoft cloud via edge-tts; nicer accents but 403s whenever MS
# rotates its handshake token).
TTS_ENGINE = "piper"
# piper voice (downloaded on first use into TTS_PIPER_DATA_DIR; more at
# rhasspy/piper-voices: en_US-lessac-medium, en_GB-alba-medium, en_US-joe…):
TTS_PIPER_VOICE = "en_US-amy-medium"
TTS_PIPER_DATA_DIR = os.path.expanduser("~/.local/state/HarfDown2List/voices")
TTS_PIPER_RATE = 0    # 0 = read the rate from the voice's .json (≈22050 Hz)
# Piper delivery shaping — the "articulate" dials:
TTS_PIPER_SENTENCE_SILENCE = 0.35  # pause between sentences (s) — phrasing
TTS_PIPER_LENGTH_SCALE = 1.0       # >1 = slower/more deliberate, <1 = brisker
TTS_PIPER_NOISE_SCALE = 0.70       # intonation expressiveness (piper 0.667)
TTS_PIPER_NOISE_W_SCALE = 0.90     # phoneme timing variation (piper 0.8)
