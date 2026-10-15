"""Custom vocabulary: post-transcription corrections before paste.

Whisper mis-hears domain words ("GitHub" -> "get", "Ollama" -> "a llama").
This layer applies user-defined replacements AFTER transcription, BEFORE
pasting — a personal dictionary of known-word corrections.

Edit ~/.local/state/HarfDown2List/vocabulary.json to add your own:
    ["get hub", "GitHub"]
Word-boundary matching, case-insensitive; runs before the LLM polish pass
so the polish model sees the corrected terms too.
"""
import json
import os
import re

VOCAB_PATH = os.path.expanduser(
    "~/.local/state/HarfDown2List/vocabulary.json"
)

# Sensible defaults for the tech-domain user; merged with the user file.
_DEFAULTS = [
    # context-dependent: "get" alone after login/clone/see verbs = GitHub
    ["log in to get", "log in to GitHub"],
    ["log into get", "log into GitHub"],
    ["login to get", "log in to GitHub"],
    ["to get and see", "to GitHub and see"],
    ["to get hub and see", "to GitHub and see"],
    ["on to get and", "onto GitHub and"],
    ["get hub", "GitHub"],
    ["git hub", "GitHub"],
    ["a llama", "Ollama"],
    ["oh llama", "Ollama"],
    ["G L M", "GLM"],
    ["j l m", "GLM"],
    ["H D two T T", "HD-2TT-nBA"],
    ["half down to listen", "HarfDown2List"],
    ["harf down to listen", "HarfDown2List"],
    # observed mishearings (2026-10-15 dictation sessions) — phrase-level
    # pairs keep real words like "annotation"/"conflict" safe in other use:
    ["in conflict", "in config"],
    ["phoned me of", "phoneme"],
    ["annotation variation", "intonation variation"],
    ["commuted code", "committed code"],
    ["half down to list", "HarfDown2List"],
    ["harf down to list", "HarfDown2List"],
]


def load_vocab():
    """User vocab file wins; defaults fill in only for keys not present."""
    rules = []
    seen = set()
    try:
        with open(VOCAB_PATH, encoding="utf-8") as f:
            user_rules = json.load(f)
    except FileNotFoundError:
        user_rules = []
        # first run: write the defaults out so the user can edit them
        try:
            os.makedirs(os.path.dirname(VOCAB_PATH), exist_ok=True)
            with open(VOCAB_PATH, "w", encoding="utf-8") as f:
                json.dump(_DEFAULTS, f, indent=2, ensure_ascii=False)
        except Exception:
            pass
    except Exception:
        user_rules = []
    for pair in list(user_rules) + _DEFAULTS:
        if not isinstance(pair, list) or len(pair) != 2:
            continue
        key = pair[0].lower()
        if key in seen:
            continue
        seen.add(key)
        rules.append((pair[0], pair[1]))
    return rules


def apply(text):
    """Apply vocabulary corrections to a transcript. Returns corrected text."""
    if not text:
        return text
    for wrong, right in load_vocab():
        pattern = re.compile(
            r"(?<!\w)" + re.escape(wrong) + r"(?!\w)", re.IGNORECASE
        )
        text = pattern.sub(right, text)
    return text


def apply_bracket_corrections(text):
    """User correction convention: 'don't(didn't) need(mean)' ->
    'didn't mean'. A bracketed word/phrase replaces the token it is
    attached to. Apply AFTER transcription, BEFORE vocabulary/polish.
    Standalone bracketed notes (no preceding word) are left untouched."""
    if not text or "(" not in text:
        return text
    pattern = re.compile(r"([A-Za-z][\w'’\-]*)\s*\(([^()]+)\)")
    prev = None
    while prev != text:
        prev = text
        text = pattern.sub(lambda m: m.group(2), text)
    return text


def initial_prompt():
    """Domain terms fed to whisper as an initial_prompt so decoding is
    biased toward the user's vocabulary (fixes 'get hub' at the source)."""
    seen = []
    for _, right in load_vocab():
        if right not in seen:
            seen.append(right)
    return " ".join(seen)[:400]