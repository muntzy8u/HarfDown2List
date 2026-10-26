# HarfDown2List

Floating-bubble voice dictation for Linux/X11. Press the pill once to listen 
and then again when you want recording to stop — speech in, clean text out,
pasted into the text box of the window you had selected before hitting
the bubble.

## Quick Start

```bash
git clone https://github.com/muntzy8u/HarfDown2List.git
cd HarfDown2List
python3 -m venv venv
./venv/bin/pip install -r requirements.txt
./venv/bin/python main.py
```

Requires a Linux/X11 session (see Runtime needs below — no Wayland support
yet). Everything is free and runs locally by default.

## The floating interface

A small oval pill (about 1.7 cm) floats at the bottom-center of your screen.
It never steals keyboard focus, so paste always lands wherever you last
clicked — any app, any monitor, any workspace.

    click the pill   ->  expands to a compact flow bar: [X] [waveform] [tick]
    cross (X)        ->  cancel, discard audio
    tick  (tick)     ->  transcribe (polished) -> paste at your cursor
    click waveform   ->  PAUSE / RESUME mid-dictation (paused audio dropped;
                         session cap and silence-stop suspended while paused)
    Esc (anywhere)   ->  cancel the open session

### Alternatively

    hold the pill    ->  push-to-talk (release to transcribe + paste)
    drag             ->  reposition; release near a screen edge to snap
    right-click      ->  menu (paste last, history, mic, hide, quit)
    middle-click     ->  recenter the pill

A one-line live-transcript strip floats above the bar while you
speak: committed words in white, still-volatile words in grey. If no window
is focused when you finish, the paste keystroke is skipped and your text is
left safely on the clipboard.

## How it works

- **UI**: pure python-xlib override-redirect overlay + X SHAPE click-through
  regions. No toolkit, no WM interaction. Works on any X11 window manager.
- **STT**: faster-whisper int8 on CPU. tiny.en streams live partials
  (~0.4 s/call); small runs the final pass (~2 s for 7 s of speech on an
  i7-5820K). Local hallucination guard + local-agreement prefix commit.
- **Polish**: optional LLM pass via local Ollama (`127.0.0.1:11434`,
  `/api/chat` — see the "Swapping models" section to retarget it) that
  removes fillers, resolves
  self-corrections, fixes punctuation. Ships raw transcript if the endpoint
  is unreachable or too slow — dictation never blocks on it.
- **Custom vocabulary**: whisper mis-hears domain words ("GitHub" ->
  "get", "Ollama" -> "a llama"). Your personal dictionary at
  ~/.local/state/HarfDown2List/vocabulary.json fixes them after
  transcription, before paste and before polish.
- **Paste**: xclip + xdotool, terminal-aware (ctrl+shift+v in terminals,
  ctrl+v elsewhere). Your previous clipboard content is restored after.
- **Voice read-out**: optional for transcripts (`TTS_ENABLED`), always
  available for the right-click menu. edge-tts (Microsoft cloud) speaks
  first, because the British voices live there; piper (local neural TTS,
  fully offline) is the fallback, then espeak-ng. Every utterance is
  supervised: it runs under a watchdog, is killed if it overruns, its temp
  files are removed, and a new read-back always stops the one before it —
  so voices can never pile up on top of each other. The right-click menu
  can switch voice, pause and resume a read-back, and stop it (Esc works
  too). If the requested cloud voice is unavailable the app says so
  instead of quietly reading in a different accent.
  The voice only reads what the model has already generated — a delivery
  layer, not an agent.
- **Read the selection aloud**: the app asks the X selection owner (the
  ICCCM `TIMESTAMP` target) when the selection last changed, and reads
  what you highlighted — not an old highlight from yesterday, and not a
  refusal because a poller missed the change. "Read selection anyway"
  ignores the freshness check.

## Requirements

**System packages** (Debian/Ubuntu names):

    sudo apt install python3 python3-venv python3-pip xdotool xclip \
                     libportaudio2 fonts-dejavu-core pulseaudio-utils \
                     espeak-ng libnotify-bin

**Python** — create a venv and install (versions are what this build was
developed and verified against):

    python3 -m venv venv
    ./venv/bin/pip install -r requirements.txt

Python >= 3.13 recommended (developed on 3.13.5).

**Runtime needs**:
- X11 session (no Wayland support — overlay windows need X SHAPE).
- A working microphone (any PulseAudio/PipeWire source; select via
  `pactl set-default-source` or `MIC_SOURCE` in config.py).
- ~500 MB disk for the two Whisper models (auto-downloaded on first run
  from HuggingFace, then cached in ~/.cache/huggingface).
- Optional: Ollama (or any server speaking Ollama's `/api/chat`) for the
  polish pass.
- Optional: piper TTS voice (~63 MB, downloaded on first speech into
  `~/.local/state/HarfDown2List/voices` — free, offline afterwards).

## Cost — what runs where

- **Free, 100% local**: the entire dictation path — capture, streaming
  partials, final transcription, paste, UI. No API keys, no accounts, no
  telemetry. This is the default configuration.
- **Optional, only if enabled**: the LLM polish pass calls your own
  configured endpoint (default: the local Ollama-compatible router with
  `gemma4:cloud`, ~0.6–2 s per dictation, verified in testing). For a
  fully local setup, `config.py` documents `qwen3.5:2b` (~3 s warm for short
  dictations; medium/long exceed CPU-friendly timeouts). If the endpoint is
  down, the raw transcript ships immediately. Each dictation is one small
  chat request (a few hundred tokens).

## Run

    ./venv/bin/python main.py

First run warms both models (~15 s) before the pill appears. Kill from the
right-click menu -> Quit.

## Config

Everything tunable in `config.py`: models, mic source, bar sizes
(in pixels; 96 dpi ≈ 3.78 px/mm if you think in centimetres), session caps,
polish on/off, paste method.

## Swapping models (transcription & polish)

All model choices live in `config.py` — edit, then restart the app:

- **Transcription** needs no Ollama, no keys, no accounts — the whisper
  models are pulled automatically and run in-process. Swap them via
  `PARTIAL_MODEL` (live preview) and `FINAL_MODEL` (final text).
  Defaults: `tiny.en` + `small` on CPU. Bigger models = more accuracy
  but slower: a large model on CPU will lag the live preview and make
  the final pass take longer than a quick dictation.
- **LLM polish** is optional. `LLM_POLISH = False` turns it off entirely;
  the app then always pastes the raw transcript. Default model:
  `gemma4:cloud` (fast; needs the local router). For a 100%-local
  machine, set `LLM_POLISH_MODEL` to e.g. `qwen3.5:2b` (~3 s warm for short
  dictations; long ones exceed CPU-friendly timeouts). `LLM_POLISH_TIMEOUT`
  (default 10 s) lets a slow endpoint fail over to raw text.
- The polish endpoint is fixed to local Ollama (`127.0.0.1:11434`) in
  `engine.py` (the `OLLAMA` constant) — a one-line edit there aims polish
  at any server speaking the same `/api/chat` shape.
- **Voice**: `TTS_PERSONA` picks the voice (default `london` = Sonia,
  en-GB RP), `TTS_PERSONA_CYCLE` is the short list the right-click menu
  cycles through (`london`, `london_warm`, `london_bright` — one click
  back to the first), and the menu remembers your choice in
  `~/.local/state/HarfDown2List/voice.json`. Other personas exist for
  reference: `emma`, `jenny`, `michaela`, `ryan`, `aussie_f`, `aussie_m`,
  `us_f`, `us_m`. `TTS_ENGINE` chooses the engine order (`edge` cloud
  first, or `piper` local first), `TTS_PIPER_VOICE` is the offline voice
  (a British one, so a network outage does not change your accent),
  `TTS_MAX_SECONDS` is the watchdog that kills a runaway read-back, and
  `TTS_PIPER_SENTENCE_SILENCE` / `TTS_PIPER_LENGTH_SCALE` /
  `TTS_PIPER_NOISE_SCALE` / `TTS_PIPER_NOISE_W_SCALE` shape the local
  delivery. The per-persona `rate` / `pitch` live in `voice_out.py`.
- **Selection read-back**: `SELECTION_MAX_AGE_S` (default 120 s) is how
  old a highlight may be before the app asks before reading it,
  `SELECTION_POLL_S` is how often the owner is asked for its timestamp,
  and `SELECTION_READ_TIMEOUT_S` is the patience of one selection read.
- **Logs**: everything now goes to `~/.local/state/HarfDown2List/hd2l.log`
  (rotating) as well as the terminal — that file is the first place to
  look when a read-back misbehaves.

## Your own vocabulary

Whisper mis-hears your domain words ("GitHub" -> "get", "Ollama" ->
"a llama"). Two rule sets merge and apply to every transcript, before
paste and polish:

- the code defaults — `_DEFAULTS` pairs at the bottom of `vocabulary.py`
- your personal dictionary: `~/.local/state/HarfDown2List/vocabulary.json`
  (auto-written on first run)

Rule format — an exact JSON list of pairs:

    [
      ["phoned me of", "phoneme"],
      ["get hub", "GitHub"]
    ]

- Word-boundary matching, case-insensitive; the replacement is inserted
  verbatim.
- Prefer phrase-level pairs ("phoned me of" -> "phoneme") over single
  words — real words ("annotation", "conflict") then survive untouched
  in other contexts.
- Your file wins over the defaults for the same misheard key.
- JSON edits apply on the NEXT dictation — no restart needed. Changing
  the code defaults does require an app restart.

## Files

    main.py       app wiring + state machine
    ui_bubble.py  pill + flow bar (X / waveform / tick)
    ui_bar.py     live transcript strip
    ui_menu.py    right-click menu
    xbase.py      X11 layer plumbing (SHAPE, painting)
    audio.py      mic capture, level metering, silence trim, pause
    engine.py     partial/final transcription + LLM polish
    vocabulary.py custom dictionary (post-STT corrections before paste)
    voice_out.py  TTS engines (edge-tts -> piper -> espeak) + personas
    logs.py       rotating log file (config.LOG_FILE) + stdout mirror
    notify.py     desktop notifications, reaped (no zombie notify-send)
    paste.py      clipboard paste + no-window guard + history
    assets/gen_idle_icon.py generates assets/idle-mic.png (design dials inside)
    assets/idle-mic.png    idle mic icon shown on the pill (config.BUBBLE_ICON)
    requirements.txt       pinned Python dependencies

History: ~/.local/state/HarfDown2List/history.jsonl
Logs:    ~/.local/state/HarfDown2List/hd2l.log

## Status (verified on the dev box, 2026-10-15)

- [x] pill renders, click-to-expand, X/tick zones, hover highlight
- [x] capture -> transcribe -> polish -> paste at focused cursor
      (terminal-verified; bracketed paste visible at the prompt)
- [x] live transcript strip: committed white prefix + grey tail
      (pixel-verified mid-speech)
- [x] waveform-click pause/resume (PAUSED label; transcript splices at pause)
- [x] Esc cancels an open session
- [x] center-stable expand/collapse (no widget drift)
- [x] clipboard save/restore; no-window guard (transcript kept on clipboard)
- [x] transcript history (jsonl)
- [x] drag + edge snap, multi-monitor clamp
- [x] global Esc cancel (pynput)
- [ ] Wayland: not supported (X11 only)

## Known behaviour (by design)

- Sessions cap at 10 minutes (configurable) so a forgotten recording can't
  run forever; it finishes and pastes like a normal tick.
- Self-corrections ("let's meet Tuesday — no, Wednesday") are resolved within
  a session by the polish pass. A correction spoken in a NEW session can't
  retro-edit the previous one — that's what "paste last transcript" in the
  right-click menu is for.
- The dictation path is single-language (English models by default);
  swap `PARTIAL_MODEL`/`FINAL_MODEL` for multilingual variants in config.
