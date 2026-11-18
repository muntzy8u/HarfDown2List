"""Voice read-back: speak the final transcript, a selected passage, or an
agent's answer aloud.

One engine, exactly like the agent-facing MCP speak tool:
  edge-tts — Microsoft cloud neural voices (the venv edge-tts 7.x CLI),
  played back with ffplay (ffmpeg). There are no fallback engines: if the
  cloud is unreachable the read-back fails loudly in the log and nothing
  is spoken — never a different, never a robotic, voice.

Every utterance is SUPERVISED: it runs in its own process group with a
watchdog that kills it if it overruns (config.TTS_MAX_SECONDS), its temp
files are removed afterwards, and a new utterance always stops the
previous one — never two voices at once.
"""
import atexit
import json
import os
import re
import shutil
import signal
import subprocess
import tempfile
import threading
import time
from concurrent.futures import ThreadPoolExecutor

import config
import logs

log = logs.get_logger("tts")


class Persona:
    """One voice: a cloud voice plus the delivery knobs that turn it
    into a different character."""

    __slots__ = ("key", "label", "edge", "rate", "pitch", "volume")

    def __init__(self, key, label, edge,
                 rate="+0%", pitch="+0Hz", volume="+0%"):
        self.key = key
        self.label = label
        self.edge = edge
        self.rate = rate
        self.pitch = pitch
        self.volume = volume


def _p(key, label, edge, rate="+0%", pitch="+0Hz", volume="+0%"):
    return Persona(key, label, edge, rate, pitch, volume)


# The only synthesis engine is edge-tts; every persona is a cloud voice.
VOICES = {
    # the three London voices
    "london": _p("london", "Sonia - London (classy)",
                 "en-GB-SoniaNeural", rate="+9%", pitch="+2Hz"),
    "london_warm": _p("london_warm", "Libby - London (warm)",
                      "en-GB-LibbyNeural", rate="+9%", pitch="+2Hz"),
    "london_bright": _p("london_bright", "Maisie - London (bright)",
                        "en-GB-MaisieNeural", rate="+9%", pitch="+3Hz"),
    "lisboa": _p("lisboa", "Raquel - Lisboa (pt-PT, cinematic)",
                 "pt-PT-RaquelNeural", rate="-8%", pitch="-2Hz"),
    # the rest, all of them
    "michaela": _p("michaela", "Aria - US (warm)  [michaela]",
                   "en-US-AriaNeural"),
    "aria_cinema": _p("aria_cinema", "Aria - US (cinematic, slow)",
                      "en-US-AriaNeural", rate="-8%", pitch="-2Hz"),
    "ava_rich": _p("ava_rich", "Ava - US (multilingual, rich)",
                   "en-US-AvaMultilingualNeural", rate="-5%"),
    "emma": _p("emma", "Emma - US (friendly)", "en-US-EmmaNeural"),
    "jenny": _p("jenny", "Jenny - US (clear)", "en-US-JennyNeural"),
    "aussie_f": _p("aussie_f", "Natasha - Australian", "en-AU-NatashaNeural"),
    "ryan": _p("ryan", "Ryan - British (male)", "en-GB-RyanNeural"),
    "aussie_m": _p("aussie_m", "William - Australian (male)",
                   "en-AU-WilliamNeural"),
    "us_f": _p("us_f", "Aria - US  [us_f]", "en-US-AriaNeural"),
    "us_m": _p("us_m", "Guy - US (male)", "en-US-GuyNeural"),
}

# Older names still work, so old configs and muscle memory do not break.
ALIASES = {
    "sonia": "london",
    "maisie": "london_bright",
    "libby": "london_warm",
}


# The chosen voice is remembered between runs so the menu choice survives
# a restart (and so "classy London" stays London after an update).
VOICE_STATE = os.path.join(
    getattr(config, "STATE_DIR",
            os.path.expanduser("~/.local/state/HarfDown2List")),
    "voice.json")
_persona_override = None      # set by the menu at runtime


def load_persona():
    """The persona key to use: the saved choice, else config.TTS_PERSONA."""
    try:
        with open(VOICE_STATE, encoding="utf-8") as f:
            key = json.load(f).get("persona")
        if key:
            key = ALIASES.get(key, key)
            if key in VOICES:
                return key
    except Exception:
        pass
    return ALIASES.get(getattr(config, "TTS_PERSONA", "london"),
                       getattr(config, "TTS_PERSONA", "london"))


def save_persona(key):
    """Remember the chosen persona. Returns True on success."""
    key = ALIASES.get(key, key)
    if key not in VOICES:
        return False
    try:
        os.makedirs(os.path.dirname(VOICE_STATE), exist_ok=True)
        with open(VOICE_STATE, "w", encoding="utf-8") as f:
            json.dump({"persona": key}, f)
        return True
    except Exception as e:
        log.warning("could not save the voice choice: %s", e)
        return False


TRANSLATE_STATE = os.path.join(
    getattr(config, "STATE_DIR",
            os.path.expanduser("~/.local/state/HarfDown2List")),
    "translate.json")


def get_translate_target():
    """Effective read-back language: the menu choice (translate.json) wins,
    else the config default (config.TTS_TRANSLATE_TO). Empty = read as
    written, no translation."""
    try:
        with open(TRANSLATE_STATE, encoding="utf-8") as f:
            v = json.load(f).get("translated_to")
        if v is not None:
            return v
    except Exception:
        pass
    return getattr(config, "TTS_TRANSLATE_TO", "")


def set_translate_target(value):
    """Menu toggle: remember the read-back language (empty = English as
    written). Returns the value on success, None if it could not save."""
    try:
        os.makedirs(os.path.dirname(TRANSLATE_STATE), exist_ok=True)
        data = {}
        try:
            with open(TRANSLATE_STATE, encoding="utf-8") as f:
                data = json.load(f)
        except Exception:
            data = {}
        data["translated_to"] = value
        with open(TRANSLATE_STATE, "w", encoding="utf-8") as f:
            json.dump(data, f)
        return value
    except Exception as e:
        log.warning("could not save the translate choice: %s", e)
        return None


def set_persona(key):
    """Switch the voice now and remember it. Returns the Persona or None."""
    global _persona_override
    key = ALIASES.get(key, key)
    if key not in VOICES:
        return None
    _persona_override = key
    save_persona(key)
    return VOICES[key]


def effective_persona():
    """The persona key currently in force."""
    return _persona_override or load_persona()


def persona_for(key=None):
    """Resolve a persona key (aliases included) to a Persona."""
    key = key or effective_persona()
    key = ALIASES.get(key, key)
    return VOICES.get(key, VOICES["london"])


# --------------------------------------------------------------------------
# supervision
# --------------------------------------------------------------------------
class Utterance:
    """A running utterance.

    Owns every child process, kills them if the utterance overruns, reaps
    them afterwards and removes any temp files. stop() is idempotent and
    safe to call from any thread.
    """

    def __init__(self, procs, primary=None, cleanup=None, max_seconds=None):
        self.procs = list(procs)
        self.primary = primary if primary is not None else (
            self.procs[0] if self.procs else None)
        self._cleanup = cleanup
        self._max = max_seconds or getattr(config, "TTS_MAX_SECONDS", 300.0)
        self._stopped = threading.Event()
        self._paused = threading.Event()
        self._done = threading.Event()
        self._player = None
        self._thread = threading.Thread(target=self._supervise, daemon=True)
        self._thread.start()

    # --- pause / resume ---
    def set_player(self, proc):
        """Hand the utterance the process that makes the sound.

        Pause is implemented by freezing that process (SIGSTOP) and
        unfreezing it (SIGCONT): the audio stops exactly where it was
        and picks up from the same spot, with no re-synthesis.
        """
        self._player = proc
        if self._paused.is_set():
            self._signal(signal.SIGSTOP)

    def _signal(self, sig):
        proc = self._player
        if proc is None:
            return False
        try:
            if proc.poll() is None:
                os.kill(proc.pid, sig)
                return True
        except Exception:
            pass
        return False

    def pause(self):
        """Freeze the read-back. Returns True if it was running."""
        if self._paused.is_set() or self._stopped.is_set():
            return False
        self._paused.set()
        # Freeze the player so the sound stops exactly here. If the player
        # does not exist yet (still synthesising), set_player() stops it
        # the moment it appears.
        self._signal(signal.SIGSTOP)
        return True

    def resume(self):
        """Carry on from where it stopped."""
        if not self._paused.is_set():
            return False
        self._paused.clear()
        self._signal(signal.SIGCONT)
        return True

    @property
    def paused(self):
        return self._paused.is_set()

    def toggle_pause(self):
        if self._paused.is_set():
            self.resume()
            return False
        self.pause()
        return True

    def _supervise(self):
        deadline = time.monotonic() + self._max
        try:
            if self.primary is not None:
                while not self._stopped.is_set():
                    if self._paused.is_set():
                        # a paused utterance is not running out of time
                        deadline += 1.0
                    left = deadline - time.monotonic()
                    if left <= 0:
                        log.warning("utterance overran %.0fs; killing it",
                                    self._max)
                        self.stop()
                        break
                    try:
                        self.primary.wait(timeout=min(1.0, left))
                        break            # it finished
                    except subprocess.TimeoutExpired:
                        continue
        finally:
            for p in self.procs:
                try:
                    p.wait(timeout=2)
                except Exception:
                    try:
                        p.kill()
                    except Exception:
                        pass
            self._cleanup_files()
            self._done.set()

    def _cleanup_files(self):
        if self._cleanup:
            try:
                self._cleanup()
            except Exception:
                pass

    def stop(self):
        """Kill the utterance now (Esc, or a newer utterance taking over)."""
        if self._stopped.is_set():
            return
        self._stopped.set()
        for p in self.procs:
            try:
                if p.poll() is None:
                    p.kill()
            except Exception:
                pass

    def wait(self, timeout=None):
        return self._done.wait(timeout)

    @property
    def stopped(self):
        return self._stopped.is_set()


_current = None
_current_lock = threading.Lock()


def current():
    with _current_lock:
        return _current


def stop():
    """Stop whatever is being spoken right now (if anything)."""
    with _current_lock:
        utt = _current
    if utt is not None:
        utt.stop()
        log.info("speech stopped")
    return utt is not None


def pause():
    """Freeze the current read-back. Returns True if something was playing."""
    utt = current()
    if utt is None:
        return False
    if utt.pause():
        log.info("speech paused")
        return True
    return False


def resume():
    """Carry on from where the read-back was paused."""
    utt = current()
    if utt is None:
        return False
    if utt.resume():
        log.info("speech resumed")
        return True
    return False


def toggle_pause():
    """Pause if playing, resume if paused. Returns 'paused'/'resumed'/None."""
    utt = current()
    if utt is None:
        return None
    if utt.paused:
        resume()
        return "resumed"
    pause()
    return "paused"


def is_paused():
    utt = current()
    return bool(utt is not None and utt.paused)


@atexit.register
def _cleanup_at_exit():
    """Never leave a player holding the audio device after we exit."""
    with _current_lock:
        utt = _current
    if utt is not None:
        utt.stop()


# --------------------------------------------------------------------------
# engines
# --------------------------------------------------------------------------
def _venv_bin(name):
    return os.path.join(os.path.dirname(os.path.abspath(__file__)),
                        "venv", "bin", name)


def _edge_bin():
    """edge-tts binary, resolved exactly like the agent-facing MCP speak
    tool: app venv first, then env override, sibling venvs, then PATH."""
    exe = _venv_bin("edge-tts")
    if os.path.isfile(exe) and os.access(exe, os.X_OK):
        return exe
    candidates = [
        os.environ.get("EDGE_TTS_BIN"),
        "/home/nether/.random_queries/HarfList/venv/bin/edge-tts",
        "/home/nether/.random_queries/HarfDown2List-dev/venv/bin/edge-tts",
        shutil.which("edge-tts"),
    ]
    for cand in candidates:
        if cand and os.path.isfile(cand) and os.access(cand, os.X_OK):
            return cand
    raise FileNotFoundError("edge-tts not installed")




# Abbreviations that must not end a sentence.
_ABBREV = re.compile(
    r"(?:^|\s)(mr|mrs|ms|dr|prof|sr|jr|st|vs|etc|e\.g|i\.e|approx|"
    r"no|fig|vol|ch|pp|ed|inc|ltd|co|jr|sr)\.\s*$", re.I)


def _split_sentences(text, max_len=320):
    """Split text into sentence-ish chunks.

    Long text read in one request comes out as one long, flat run - the
    voice never gets to breathe at a sentence end. Synthesising each
    sentence separately gives every one its own phrase-final contour and
    a natural pause between, which is most of what "sounds natural"
    actually means.
    """
    if not getattr(config, "TTS_SENTENCE_SPLIT", True):
        return [text]
    parts = re.split(r"(?<=[.!?…])\s+", text.strip())
    out = []
    for part in parts:
        if not part:
            continue
        if out and _ABBREV.search(out[-1]):
            out[-1] = out[-1] + " " + part
            continue
        if len(part) > max_len:
            # a "sentence" with no punctuation in it: break on commas
            bits = re.split(r"(?<=[,;:])\s+", part)
            buf = ""
            for bit in bits:
                if buf and len(buf) + len(bit) + 1 > max_len:
                    out.append(buf)
                    buf = bit
                else:
                    buf = (buf + " " + bit).strip()
            if buf:
                out.append(buf)
            continue
        out.append(part)
    return out or [text]


def _edge_cli(persona, text, out_path, timeout=90):
    """One edge-tts synthesis -> one file. Raises on failure."""
    exe = _edge_bin()
    cmd = [exe, "--voice", persona.edge,
           "--rate=%s" % persona.rate, "--pitch=%s" % persona.pitch,
           "--volume=%s" % persona.volume,
           "--no-progress", "--write-media", out_path]
    if len(text) > 4000:
        textfile = out_path + ".txt"
        with open(textfile, "w", encoding="utf-8") as f:
            f.write(text)
        cmd += ["--file", textfile]
    else:
        cmd += ["--text", text]
    r = subprocess.run(cmd, capture_output=True, timeout=timeout, check=False)
    if r.returncode != 0 or not os.path.exists(out_path) \
            or os.path.getsize(out_path) < 512:
        err = (r.stderr or b"").decode("utf-8", "replace").strip()[-200:]
        raise RuntimeError("edge-tts failed (%s): %s" % (r.returncode, err))


def _synth_edge(text, persona, tmpdir, clip):
    """Synthesize `text` with edge-tts into `clip`.

    Multi-sentence text is synthesized sentence by sentence - in
    parallel, so the wait is no longer than one big request - and the
    results are joined. Each sentence then gets its own natural
    phrase ending and a breath between. If any part fails, the whole
    thing is synthesized in one request instead, so a flaky network
    never costs you the read-back.
    """
    chunks = _split_sentences(text)
    if len(chunks) < 2:
        _edge_cli(persona, text, clip)
        return
    workers = min(4, len(chunks))
    paths = []
    try:
        with ThreadPoolExecutor(max_workers=workers) as ex:
            futures = []
            for i, chunk in enumerate(chunks):
                p = os.path.join(tmpdir, "part%03d.mp3" % i)
                futures.append((p, ex.submit(_edge_cli, persona, chunk, p)))
            for p, fut in futures:
                fut.result()
                paths.append(p)
        with open(clip, "wb") as out:
            for p in paths:
                with open(p, "rb") as fh:
                    out.write(fh.read())
    except Exception as e:
        log.warning("sentence-wise synthesis failed (%s); one request", e)
        for p in paths:
            try:
                os.remove(p)
            except Exception:
                pass
        _edge_cli(persona, text, clip)


def _speak_edge(text, persona):
    """edge-tts -> temp clip -> ffplay."

    The clip is written to a private temp dir and deleted when playback
    finishes. The old code used NamedTemporaryFile(delete=False) and never
    removed it, so every read-back left an mp3 in /tmp forever.
    """
    exe = _edge_bin()
    tmpdir = tempfile.mkdtemp(prefix="hd2l-tts-")
    clip = os.path.join(tmpdir, "speech.mp3")
    def cleanup():
        shutil.rmtree(tmpdir, ignore_errors=True)

    try:
        _synth_edge(text, persona, tmpdir, clip)
    except Exception:
        cleanup()
        raise
    if not shutil.which("ffplay"):
        raise RuntimeError("ffplay (ffmpeg) is not installed - required "
                           "to play audio")
    play = subprocess.Popen(
        ["ffplay", "-nodisp", "-autoexit", "-loglevel", "error", clip],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    utt = Utterance([play], primary=play, cleanup=cleanup)
    utt.set_player(play)
    return utt


# --------------------------------------------------------------------------
# public API
# --------------------------------------------------------------------------
def _translate(text, target):
    """Translate text into `target` with the local ollama LLM - the same
    endpoint and request discipline as engine.polish(). Returns the
    translation, or None on any failure (callers read the original text)."""
    import requests

    if not text.strip():
        return None
    payload = {
        "model": getattr(config, "TTS_TRANSLATE_MODEL", "")
                 or config.LLM_POLISH_MODEL,
        "stream": False,
        "think": False,  # cloud-router models reason away the budget otherwise
        "messages": [
            {
                "role": "user",
                "content": (
                    "Translate this text into " + target + " for a "
                    "text-to-speech voice. Rules:\n"
                    "1. Use exactly the language variant it names - for "
                    "European Portuguese, Portugal's forms and registers, "
                    "never the Brazilian ones (no 'você' phrasing, no "
                    "gerund-style constructions).\n"
                    "2. Keep meaning, names, numbers and emphasis exactly.\n"
                    "3. Write it out-loud friendly: complete sentences a "
                    "voice reads naturally.\n"
                    "4. Return ONLY the translation, nothing else.\n\n"
                    f"Text: {text}"
                ),
            }
        ],
        "options": {"temperature": 0.2, "num_predict": 1024},
    }
    try:
        url = getattr(config, "TTS_LLM_URL", "") or "http://127.0.0.1:11434"
        r = requests.post(url + "/api/chat", json=payload, timeout=20.0)
        r.raise_for_status()
        out = r.json()["message"]["content"].strip()
        lowered = out.lower()
        for pat in ("translation:", "translated:", "output:", "text:",
                    "aqui está", "here is the translation:",
                    "here's the translation:"):
            if lowered.startswith(pat):
                out = out[len(pat):].strip()
                lowered = out.lower()
        return out or None
    except Exception as e:
        log.error("translation pass failed (%s)", e)
        return None


def speak(text, persona=None):
    """Speak text aloud with edge-tts, played back with ffplay.

    Returns the Utterance that is playing, or None if nothing could speak.
    Any previous utterance is stopped first: two voices at once is never
    what the user meant.

    With config.TTS_TRANSLATE_TO set, the text is translated on the fly and
    read in that language by the native matching persona (lisboa / Raquel
    for European Portuguese). If the translation pass fails, the ORIGINAL
    text is read - the read-back never falls silent for that reason.
    """
    if not text or not text.strip():
        return None
    p = persona_for(persona)
    stop()                      # never talk over the previous utterance

    spoken_text = text
    target = get_translate_target()
    if target:
        translated = _translate(text, target)
        if translated:
            spoken_text = translated
            if "lisboa" in VOICES and p.edge != "pt-PT-RaquelNeural":
                log.info("translation to %s: reading with the lisboa "
                         "persona (native pt-PT voice)", target)
                p = VOICES["lisboa"]
        else:
            log.error("translation to %s failed; reading the original "
                      "English text", target)

    try:
        utt = _speak_edge(spoken_text, p)
    except Exception as e:
        log.error("edge-tts read-back failed: %s", e)
        return None
    global _current
    with _current_lock:
        _current = utt
    log.info("speaking with edge (%s)", p.label)
    return utt


def speak_async(text, persona=None):
    """Speak in the background; returns the worker thread."""
    t = threading.Thread(target=speak, args=(text, persona), daemon=True)
    t.start()
    return t


def available_personas():
    """[(key, label), ...] in menu order - London voices first."""
    order = ["london", "london_warm", "london_bright", "lisboa", "michaela",
             "aria_cinema", "ava_rich", "aussie_f",
             "emma", "jenny", "ryan", "aussie_m", "us_f", "us_m"]
    return [(k, VOICES[k].label) for k in order if k in VOICES]
