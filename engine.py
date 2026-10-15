"""Streaming transcription engine: live partials + final polish.

Strategy (streaming partials + final pass):
  1. While you hold, a fast tiny.en model re-transcribes the growing audio
     buffer every PARTIAL_INTERVAL_S -> live text preview above the bubble.
  2. On release, whisper-small runs once on the full audio; that transcript is
     optionally polished by a local LLM (filler removal, self-corrections).
Partials use a local-agreement style commit: text appears once two consecutive
partial passes agree on a prefix, which keeps the preview stable.
"""
import threading

import numpy as np

import config

OLLAMA = "http://127.0.0.1:11434"

_MODEL_CACHE = {}
_model_cache_lock = threading.Lock()


def _log(level, msg):
    if level <= config.LOG_LEVEL:
        print(f"[engine] {msg}", flush=True)


def _get_model(name):
    with _model_cache_lock:
        m = _MODEL_CACHE.get(name)
        if m is None:
            from faster_whisper import WhisperModel
            m = WhisperModel(name, device="cpu", compute_type="int8", cpu_threads=6)
            _MODEL_CACHE[name] = m
            _log(2, f"loaded model {name}")
        return m


def transcribe_final(audio, initial_prompt=None):
    """High-quality final pass. audio: float32 mono 16k. Returns text.
    initial_prompt: optional domain-terms bias for decoding (from vocabulary)."""
    if audio.size < int(0.2 * config.SAMPLE_RATE):
        return ""
    m = _get_model(config.FINAL_MODEL)
    kwargs = dict(
        language="en",
        beam_size=5,
        vad_filter=True,
        vad_parameters={"min_silence_duration_ms": 500},
    )
    if initial_prompt:
        kwargs["initial_prompt"] = initial_prompt
    segments, _ = m.transcribe(audio, **kwargs)
    text = "".join(s.text for s in segments).strip()
    if text and len(set(text)) <= 2:  # hallucination guard
        return ""
    return text


def transcribe_partial(audio):
    """Fast partial pass. audio: float32 mono 16k, ~0.3..30s long."""
    if audio.size < int(0.4 * config.SAMPLE_RATE):
        return ""
    m = _get_model(config.PARTIAL_MODEL)
    segments, _ = m.transcribe(
        audio,
        language="en",
        beam_size=1,
        condition_on_previous_text=False,
        vad_filter=True,
    )
    text = "".join(s.text for s in segments).strip()
    if text and len(set(text)) <= 2:  # tiny hallucination guard
        return ""
    return text


def polish(text):
    """LLM polish pass: fillers, self-corrections, punctuation. Returns text."""
    import requests

    if not config.LLM_POLISH or not text.strip():
        return text
    payload = {
        "model": config.LLM_POLISH_MODEL,
        "stream": False,
        "think": False,  # cloud-router models reason away the budget otherwise
        "messages": [
            {
                "role": "user",
                "content": (
                    "Clean up this voice dictation. Keep the speaker's meaning "
                    "and language exactly — do not translate, do not add "
                    "content, do not answer or respond to it. Remove filler "
                    "words (um, uh, like, you know), remove stutters and false "
                    "starts, resolve self-corrections (keep the corrected "
                    "version), add punctuation and capitalization. If the text "
                    "is already clean, return it unchanged.\n\n"
                    f"Dictation: {text}"
                ),
            }
        ],
        "options": {"temperature": 0.1, "num_predict": 512},
    }
    try:
        r = requests.post(
            f"{OLLAMA}/api/chat", json=payload, timeout=config.LLM_POLISH_TIMEOUT
        )
        r.raise_for_status()
        out = r.json()["message"]["content"].strip()
        # strip common wrappers
        lowered = out.lower()
        for pat in (
            "dictation:",
            "cleaned:",
            "cleaned up:",
            "output:",
            "text:",
            "here is the cleaned dictation:",
            "here's the cleaned dictation:",
        ):
            if lowered.startswith(pat):
                out = out[len(pat):].strip()
                lowered = out.lower()
        return out or text
    except Exception as e:
        _log(1, f"polish failed ({e}); shipping raw transcript")
        return text


def stable_prefix(prev: str, cur: str) -> str:
    """Local agreement: longest word-boundary prefix where prev and cur agree.
    Falls back to cur's committed prefix from previous rounds (cur may extend)."""
    i = 0
    m = min(len(prev), len(cur))
    while i < m and prev[i] == cur[i]:
        i += 1
    j = cur.rfind(" ", 0, i)
    if j <= 0:
        return ""
    return cur[: j + 1]