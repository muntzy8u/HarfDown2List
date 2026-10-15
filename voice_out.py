"""Voice read-back: speak the final transcript or an agent's answer aloud.

Michaela = edge-tts en-US-AriaNeural (free, no API key, no account).
Runs as a background process; audio plays through the default sink.
If edge-tts is missing or offline, falls back to espeak-ng.
"""
import os
import subprocess
import tempfile
import threading

import config

# Voice personas: (edge-tts voice, espeak-ng fallback)
VOICES = {
    "michaela": ("en-US-AriaNeural", "en-us"),
    "libby": ("en-GB-LibbyNeural", "en+rp"),
    "sonia": ("en-GB-SoniaNeural", "en+rp"),
    "ryan": ("en-GB-RyanNeural", "en"),
    "aussie_f": ("en-AU-NatashaNeural", "en"),
    "aussie_m": ("en-AU-WilliamNeural", "en"),
    "us_f": ("en-US-AriaNeural", "en-us"),
    "us_m": ("en-US-GuyNeural", "en-us"),
}


def speak(text, persona=None):
    """Speak text aloud (non-blocking). Returns the launched process or None."""
    if not text or not text.strip():
        return None
    persona = persona or config.TTS_PERSONA
    edge_voice, espeak_voice = VOICES.get(persona, VOICES["michaela"])
    try:
        return _speak_edge(text, edge_voice)
    except Exception:
        return _speak_espeak(text, espeak_voice)


def _speak_edge(text, voice):
    """edge-tts via the venv binary; writes a temp mp3 then plays it."""
    exe = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                       "venv", "bin", "edge-tts")
    if not os.path.exists(exe):
        raise FileNotFoundError("edge-tts not installed")
    out = tempfile.NamedTemporaryFile(suffix=".mp3", delete=False)
    out.close()
    subprocess.run(
        [exe, "--voice", voice, "--text", text, "--write-media", out.name],
        check=True, capture_output=True, timeout=30,
    )
    return subprocess.Popen(["paplay", out.name],
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def _speak_espeak(text, voice):
    cmd = ["espeak-ng", "-v", voice, text]
    return subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def speak_async(text, persona=None):
    t = threading.Thread(target=speak, args=(text, persona), daemon=True)
    t.start()
    return t