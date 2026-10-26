"""Voice read-back: speak the final transcript, a selected passage, or an
agent's answer aloud.

Engines, tried in order (config.TTS_ENGINE puts one first):
  edge-tts  — Microsoft cloud. The British personas live here.
  piper     — local neural TTS, offline. Streams raw PCM into paplay.
  espeak-ng — last-resort robotic fallback.

Every utterance is SUPERVISED. Each engine runs in its own process group
with a watchdog that kills it if it overruns, its temp files are removed
afterwards, and a new utterance always stops the previous one. That is the
difference between "speaks" and "wedges the audio device until the machine
looks frozen" — which is exactly what the old code did when paplay stalled:
the speak thread blocked forever on piper's stdin, every later request
stacked another stuck piper+paplay pair on the audio server, and nothing
ever cleaned up.

The persona is explicit about which voice actually spoke. If the requested
British cloud voice is unavailable and we fall back to a local voice, the
user is told rather than being handed a different accent in silence.
"""
import atexit
import json
import os
import shutil
import signal
import subprocess
import tempfile
import threading
import time

import config
import logs

log = logs.get_logger("tts")


class Persona:
    """One voice: a cloud voice, a local voice, a robotic voice and the
    delivery knobs that turn the same voice into a different character."""

    __slots__ = ("key", "label", "edge", "espeak", "piper", "rate", "pitch",
                 "volume")

    def __init__(self, key, label, edge, espeak, piper=None,
                 rate="+0%", pitch="+0Hz", volume="+0%"):
        self.key = key
        self.label = label
        self.edge = edge
        self.espeak = espeak
        self.piper = piper
        self.rate = rate
        self.pitch = pitch
        self.volume = volume

    @property
    def british(self):
        return self.edge.startswith("en-GB")


def _p(key, label, edge, espeak, piper=None, rate="+0%", pitch="+0Hz",
       volume="+0%"):
    return Persona(key, label, edge, espeak, piper, rate, pitch, volume)


# The British voices are the reason this app asks for edge-tts first: the
# en-GB personas only exist in the cloud. The piper column is the LOCAL
# fallback, so a network outage still leaves a British voice in the room.
VOICES = {
    "london": _p("london", "Sonia - classy London (RP)",
                 "en-GB-SoniaNeural", "en+rp",
                 "en_GB-jenny_dioco-medium", rate="+5%", pitch="+0Hz"),
    "london_warm": _p("london_warm", "Libby - warm London",
                      "en-GB-LibbyNeural", "en+rp",
                      "en_GB-jenny_dioco-medium", rate="+5%", pitch="+2Hz"),
    "london_bright": _p("london_bright", "Maisie - bright London",
                        "en-GB-MaisieNeural", "en+rp",
                        "en_GB-jenny_dioco-medium", rate="+5%", pitch="+3Hz"),
    "emma": _p("emma", "Emma - friendly US", "en-US-EmmaNeural", "en-us",
               "en_US-amy-medium"),
    "jenny": _p("jenny", "Jenny - clear US", "en-US-JennyNeural", "en-us",
                "en_US-amy-medium"),
    "michaela": _p("michaela", "Aria - warm US", "en-US-AriaNeural", "en-us",
                   "en_US-amy-medium"),
    "ryan": _p("ryan", "Ryan - British", "en-GB-RyanNeural", "en",
               "en_GB-northern_english_male-medium"),
    "aussie_f": _p("aussie_f", "Natasha - Australian", "en-AU-NatashaNeural",
                   "en", "en_US-amy-medium"),
    "aussie_m": _p("aussie_m", "William - Australian", "en-AU-WilliamNeural",
                   "en", "en_US-amy-medium"),
    "us_f": _p("us_f", "Aria - US", "en-US-AriaNeural", "en-us",
               "en_US-amy-medium"),
    "us_m": _p("us_m", "Guy - US", "en-US-GuyNeural", "en-us",
               "en_US-amy-medium"),
}

# Older names still work, so old configs and muscle memory do not break.
ALIASES = {
    "sonia": "london",
    "maisie": "london_bright",
    "libby": "london_warm",
}


# The chosen voice is remembered between runs so the menu choice survives
# a restart (and so "classy London" stays London after an update).
VOICE_STATE = os.path.expanduser("~/.local/state/HarfDown2List/voice.json")
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


def _voice_dir():
    return os.path.expanduser(getattr(config, "TTS_PIPER_DATA_DIR",
                                      "~/.local/state/HarfDown2List/voices"))


def _piper_voice_for(persona):
    """The local voice to use for this persona (may not be downloaded)."""
    wanted = getattr(persona, "piper", None) or getattr(
        config, "TTS_PIPER_VOICE", "en_US-amy-medium")
    if os.path.exists(os.path.join(_voice_dir(), wanted + ".onnx")):
        return wanted
    # fall back to whatever British/US voice is actually on disk
    for alt in (wanted, "en_GB-jenny_dioco-medium", "en_US-amy-medium",
                "en_US-lessac-medium", "en_US-hfc_female-medium"):
        if os.path.exists(os.path.join(_voice_dir(), alt + ".onnx")):
            return alt
    raise RuntimeError(
        "no piper voice found in %s (download one with: venv/bin/python -m "
        "piper.download_voices --download-dir '%s' <voice>)"
        % (_voice_dir(), _voice_dir()))


def _piper_rate(voice):
    """Audio sample rate of a piper voice (Hz)."""
    import json
    rate = getattr(config, "TTS_PIPER_RATE", 0)
    if rate:
        return int(rate)
    try:
        with open(os.path.join(_voice_dir(), voice + ".onnx.json"),
                  encoding="utf-8") as f:
            meta = json.load(f)
        audio = meta.get("audio", {})
        return int(audio.get("sample_rate") or audio.get("sampling_rate")
                   or meta.get("sample_rate") or 22050)
    except Exception:
        return 22050


def _speak_piper(text, persona):
    """Piper synth -> raw PCM piped into paplay.

    The text is handed to piper through a FILE, not a pipe: a pipe can fill
    up and block the caller forever when the player stalls, which is how the
    old code wedged the audio device.
    """
    exe = _venv_bin("piper")
    if not os.path.exists(exe):
        exe = shutil.which("piper")
        if not exe:
            raise RuntimeError("piper not installed")
    voice = _piper_voice_for(persona)
    tmpdir = tempfile.mkdtemp(prefix="hd2l-tts-")
    textfile = os.path.join(tmpdir, "text.txt")
    with open(textfile, "w", encoding="utf-8") as f:
        f.write(text)
    cmd = [exe, "--data-dir", _voice_dir(), "-m", voice, "-i", textfile,
           "--output-raw"]
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

    piper = subprocess.Popen(cmd, stdout=subprocess.PIPE,
                              stderr=subprocess.DEVNULL)
    play = subprocess.Popen(
        ["paplay", "--raw", "--format=s16le",
         "--rate=%d" % _piper_rate(voice), "--channels=1"],
        stdin=piper.stdout, stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL)
    piper.stdout.close()          # the player owns the read end now

    def cleanup():
        shutil.rmtree(tmpdir, ignore_errors=True)

    utt = Utterance([play, piper], primary=play, cleanup=cleanup)
    utt.set_player(play)
    return utt


def _speak_edge(text, persona):
    """edge-tts -> temp clip -> paplay.

    The clip is written to a private temp dir and deleted when playback
    finishes. The old code used NamedTemporaryFile(delete=False) and never
    removed it, so every read-back left an mp3 in /tmp forever.
    """
    exe = _venv_bin("edge-tts")
    if not os.path.exists(exe):
        exe = shutil.which("edge-tts")
        if not exe:
            raise FileNotFoundError("edge-tts not installed")
    tmpdir = tempfile.mkdtemp(prefix="hd2l-tts-")
    clip = os.path.join(tmpdir, "speech.mp3")
    # The delivery knobs use the --opt=value form on purpose: argparse
    # reads a bare "-3%" as another option, not as the value of --rate.
    cmd = [exe, "--voice", persona.edge,
           "--rate=%s" % persona.rate, "--pitch=%s" % persona.pitch,
           "--volume=%s" % persona.volume,
           "--no-progress", "--write-media", clip]
    # long text on the command line can blow past ARG_MAX -> use a file
    if len(text) > 4000:
        textfile = os.path.join(tmpdir, "text.txt")
        with open(textfile, "w", encoding="utf-8") as f:
            f.write(text)
        cmd += ["--file", textfile]
    else:
        cmd += ["--text", text]

    def cleanup():
        shutil.rmtree(tmpdir, ignore_errors=True)

    try:
        r = subprocess.run(cmd, capture_output=True, timeout=90, check=False)
    except subprocess.TimeoutExpired:
        cleanup()
        raise RuntimeError("edge-tts timed out")
    if r.returncode != 0 or not os.path.exists(clip) \
            or os.path.getsize(clip) < 512:
        err = (r.stderr or b"").decode("utf-8", "replace").strip()[-200:]
        cleanup()
        raise RuntimeError("edge-tts failed (%s): %s" % (r.returncode, err))
    play = subprocess.Popen(["paplay", clip], stdout=subprocess.DEVNULL,
                            stderr=subprocess.DEVNULL)
    utt = Utterance([play], primary=play, cleanup=cleanup)
    utt.set_player(play)
    return utt


def _speak_espeak(text, persona):
    """Robotic last resort. Still supervised: it must not outlive its use."""
    exe = shutil.which("espeak-ng") or shutil.which("espeak")
    if not exe:
        raise RuntimeError("espeak-ng not installed")
    proc = subprocess.Popen([exe, "-v", persona.espeak, "-s", "155", text],
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    utt = Utterance([proc], primary=proc)
    utt.set_player(proc)
    return utt


# --------------------------------------------------------------------------
# public API
# --------------------------------------------------------------------------
def speak(text, persona=None, announce_fallback=True):
    """Speak text aloud with the configured engine chain.

    Returns the Utterance that is playing, or None if nothing could speak.
    Any previous utterance is stopped first: two voices at once is never
    what the user meant.
    """
    if not text or not text.strip():
        return None
    p = persona_for(persona)
    stop()                      # never talk over the previous utterance

    engines = [_speak_edge, _speak_piper, _speak_espeak]
    if getattr(config, "TTS_ENGINE", "edge") != "edge":
        engines = [_speak_piper, _speak_edge, _speak_espeak]

    for engine in engines:
        name = engine.__name__[len("_speak_"):]
        try:
            utt = engine(text, p)
        except Exception as e:
            log.warning("%s failed: %s", name, e)
            continue
        global _current
        with _current_lock:
            _current = utt
        log.info("speaking with %s (%s)", name, p.label)
        if name != "edge" and announce_fallback:
            _announce_fallback(p, name)
        return utt
    log.error("no engine worked; nothing was spoken")
    return None


def _announce_fallback(persona, engine_name):
    """Tell the user the voice they asked for is not the voice they got."""
    try:
        import notify
        if persona.british:
            body = ("The %s cloud voice is unavailable right now, so the "
                    "local %s voice spoke instead. Check the log for why."
                    % (persona.label, engine_name))
        else:
            body = ("The cloud voice is unavailable right now, so the local "
                    "%s voice spoke instead. Check the log for why."
                    % engine_name)
        notify.notify("HarfDown2List: voice fallback", body)
    except Exception:
        pass


def speak_async(text, persona=None):
    """Speak in the background; returns the worker thread."""
    t = threading.Thread(target=speak, args=(text, persona), daemon=True)
    t.start()
    return t


def available_personas():
    """[(key, label), ...] in menu order - London voices first."""
    order = ["london", "london_warm", "london_bright", "emma", "jenny",
             "michaela", "ryan", "aussie_f", "aussie_m", "us_f", "us_m"]
    return [(k, VOICES[k].label) for k in order if k in VOICES]
