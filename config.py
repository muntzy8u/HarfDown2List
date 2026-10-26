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
# MEASURED (2026-10-15):
#   gemma4:cloud               -> 0.6-2s incl. long dictations, honours
#                                think:false, returns ONLY the cleaned
#                                dictation — DEFAULT
#   minimaxm3-cloud            -> 1.4s clean, minor caps ("Llama notes")
#   glm-5.3-flash:cloud        -> UNUSABLE for polish: ignores think:false
#                                and returns its reasoning instead
#   qwen3.5:cloud              -> 410 Gone (retired)
#   deepseek-v4-flash:cloud    -> RETIRED by the router 2026-09-25 (HTTP 410)
# NOTE: "think": false is REQUIRED for cloud router models or they reason
# until the token budget is gone and return empty content; qwen3.5 accepts it.
# Test any new candidate with a real dictation before trusting it — some
# cloud models return reasoning text that would leak into pastes.
LLM_POLISH = True
LLM_POLISH_MODEL = "gemma4:cloud"
# Max seconds to wait for the polish LLM; on timeout the raw (vocabulary-
# corrected) transcript ships. 10 s also covers cloud latency spikes.
LLM_POLISH_TIMEOUT = 10.0

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
# Idle-button icon pasted over the dark glass disc — a generated professional
# mic sized 1:1 to the pill (56x56 = BUBBLE_W/BUBBLE_H; regen/tweak:
# venv/bin/python assets/gen_idle_icon.py). None = the built-in rough drawn
# mic glyph; or point it at any PNG/SVG of your own.
BUBBLE_ICON = os.path.join(os.path.dirname(__file__), "assets", "idle-mic.png")
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
# British female personas (edge-tts): sonia (RP), libby (friendly), maisie
# (young, bubbly). US fallbacks: emma, jenny, michaela(=Aria). TTS_ENGINE
# decides which engine actually speaks — the British voices only exist on
# edge-tts.
TTS_PERSONA = "maisie"
# Engine order for actually making sound: "edge" (Microsoft cloud via
# edge-tts; the British personas live here — needs network, 403s when MS
# rotates its handshake token) or "piper" (local neural TTS, offline, US
# voices only — streams straight into paplay, no audio files).
TTS_ENGINE = "edge"
# piper voice (downloaded on first use into TTS_PIPER_DATA_DIR; more at
# rhasspy/piper-voices: en_US-lessac-medium, en_GB-alba-medium, en_US-joe…):
TTS_PIPER_VOICE = "en_US-hfc_female-medium"
TTS_PIPER_DATA_DIR = os.path.expanduser("~/.local/state/HarfDown2List/voices")
TTS_PIPER_RATE = 0    # 0 = read the rate from the voice's .json (≈22050 Hz)
# Piper delivery shaping — the "articulate" dials:
TTS_PIPER_SENTENCE_SILENCE = 0.35  # pause between sentences (s) — phrasing
TTS_PIPER_LENGTH_SCALE = 1.0       # >1 = slower/more deliberate, <1 = brisker
TTS_PIPER_NOISE_SCALE = 0.70       # intonation expressiveness (piper 0.667)
TTS_PIPER_NOISE_W_SCALE = 0.90     # phoneme timing variation (piper 0.8)
