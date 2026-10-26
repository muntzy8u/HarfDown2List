"""Voice read-back: speak the final transcript or an agent's answer aloud.

Engines, tried in order (config.TTS_ENGINE puts one first):
  piper    — local neural TTS (piper-tts). Clear voice, fully offline, no
             tokens; streams raw PCM straight into paplay, so no audio
             files are created at all. DEFAULT.
  edge-tts — Microsoft cloud. Prettier accents, but needs network and gets
             403 whenever MS rotates the Sec-MS-GEC handshake token
             (audio only arrives as a downloadable clip).
  espeak-ng — last-resort robotic fallback.

All free, no API keys, no accounts.
"""
import json
import os
import shutil
import subprocess
import tempfile
import threading

import config

# Persona map: persona -> (edge-tts voice, espeak-ng fallback).
# Piper is persona-independent: its voice comes from config.TTS_PIPER_VOICE
# (local model files, e.g. en_US-amy-medium, en_US-lessac-medium).
VOICES = {
    "michaela": ("en-US-AriaNeural", "en-us"),
    "emma": ("en-US-EmmaNeural", "en-us"),
    "jenny": ("en-US-JennyNeural", "en-us"),
    "maisie": ("en-GB-MaisieNeural", "en+rp"),
    "libby": ("en-GB-LibbyNeural", "en+rp"),
    "sonia": ("en-GB-SoniaNeural", "en+rp"),
    "ryan": ("en-GB-RyanNeural", "en"),
    "aussie_f": ("en-AU-NatashaNeural", "en"),
    "aussie_m": ("en-AU-WilliamNeural", "en"),
    "us_f": ("en-US-AriaNeural", "en-us"),
    "us_m": ("en-US-GuyNeural", "en-us"),
}


def _log(msg):
    print(f"[tts] {msg}", flush=True)


def _voice_dir():
    return os.path.expanduser(getattr(config, "TTS_PIPER_DATA_DIR",
                                      "~/.local/state/HarfDown2List/voices"))


def _piper_voice():
    return getattr(config, "TTS_PIPER_VOICE", "en_US-amy-medium")


def _piper_rate():
    """Audio sample rate of the loaded piper voice (Hz)."""
    rate = getattr(config, "TTS_PIPER_RATE", 0)
    if rate:
        return int(rate)
    try:
        with open(os.path.join(_voice_dir(), _piper_voice() + ".onnx.json"),
                  encoding="utf-8") as f:
            meta = json.load(f)
        rate = (meta.get("audio", {}).get("sample_rate")
                or meta.get("audio", {}).get("sampling_rate")
                or meta.get("sample_rate") or meta.get("sampling_rate"))
        return int(rate or 22050)
    except Exception:
        return 22050


def _piper_pkg():
    """(piper executable, voice) if piper + its model are both present."""
    exe = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                       "venv", "bin", "piper")
    if not os.path.exists(exe):
        exe = shutil.which("piper")
        if not exe:
            raise RuntimeError("piper not installed")
    if not os.path.exists(os.path.join(_voice_dir(), _piper_voice() + ".onnx")):
        raise RuntimeError(
            "piper voice '%s' missing in %s (download with: venv/bin/python "
            "-m piper.download_voices --download-dir '%s' '%s')"
            % (_piper_voice(), _voice_dir(), _voice_dir(), _piper_voice()))
    return exe, _piper_voice()


def _speak_piper(text, persona=None):
    """Piper synth -> raw PCM piped straight into paplay. No temp files."""
    exe, voice = _piper_pkg()
    cmd = [exe, "--data-dir", _voice_dir(), "-m", voice, "--output-raw"]
    length = getattr(config, "TTS_PIPER_LENGTH_SCALE", 1.0)
    if length and length != 1.0:
        cmd += ["--length-scale", str(length)]
    silence = getattr(config, "TTS_PIPER_SENTENCE_SILENCE", 0)
    if silence and silence > 0:
        cmd += ["--sentence-silence", str(silence)]
    noise = getattr(config, "TTS_PIPER_NOISE_SCALE", 0)
    if noise:
        cmd += ["--noise-scale", str(noise)]
    noisew = getattr(config, "TTS_PIPER_NOISE_W_SCALE", 0)
    if noisew:
        cmd += ["--noise-w-scale", str(noisew)]
    piper = subprocess.Popen(
        cmd,
        stdin=subprocess.PIPE, stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL)
    play = subprocess.Popen(
        ["paplay", "--raw", "--format=s16le",
         "--rate=%d" % _piper_rate(), "--channels=1"],
        stdin=piper.stdout, stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL)
    piper.stdout.close()
    piper.stdin.write((text + "\n").encode("utf-8"))
    piper.stdin.close()
    return play


def _speak_edge(text, edge_voice):
    """edge-tts via the venv binary; writes a temp clip then plays it."""
    exe = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                       "venv", "bin", "edge-tts")
    if not os.path.exists(exe):
        raise FileNotFoundError("edge-tts not installed")
    out = tempfile.NamedTemporaryFile(suffix=".mp3", delete=False)
    out.close()
    subprocess.run(
        [exe, "--voice", edge_voice, "--text", text, "--write-media", out.name],
        check=True, capture_output=True, timeout=30,
    )
    return subprocess.Popen(["paplay", out.name],
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def _speak_espeak(text, espeak_voice):
    cmd = ["espeak-ng", "-v", espeak_voice, text]
    return subprocess.Popen(cmd, stdout=subprocess.DEVNULL,
                            stderr=subprocess.DEVNULL)


def speak(text, persona=None):
    """Speak text aloud with the configured engine chain (non-blocking).
    Returns the playback process of the engine that worked, or None."""
    if not text or not text.strip():
        return None
    persona = persona or config.TTS_PERSONA
    edge_voice, espeak_voice = VOICES.get(persona, VOICES["emma"])
    if getattr(config, "TTS_ENGINE", "piper") == "edge":
        chain = ((_speak_edge, (text, edge_voice)),
                 (_speak_piper, (text, persona)),
                 (_speak_espeak, (text, espeak_voice)))
    else:
        chain = ((_speak_piper, (text, persona)),
                 (_speak_edge, (text, edge_voice)),
                 (_speak_espeak, (text, espeak_voice)))
    for engine, args in chain:
        name = engine.__name__[7:]   # _speak_piper -> piper
        try:
            play = engine(*args)
            _log(f"speaking with {name}")
            return play
        except Exception as e:
            _log(f"{name} failed: {e}")
    _log("no engine worked; nothing was spoken")
    return None


def speak_async(text, persona=None):
    t = threading.Thread(target=speak, args=(text, persona), daemon=True)
    t.start()
    return t