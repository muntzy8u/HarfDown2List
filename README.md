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
    double-press F13 ->  hands-free (auto-stops after ~1.6 s of silence)
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
- **Voice read-out**: optional, off by default (`TTS_ENABLED`). Piper (local
  neural TTS) speaks it: clear voice, fully offline, streams straight into
  `paplay` — no audio files are created. Fallback chain: edge-tts (Microsoft
  cloud; it 403s whenever MS rotates its handshake token) then espeak-ng.
  The voice only reads what the model has already generated — a delivery
  layer, not an agent.

## Requirements

**System packages** (Debian/Ubuntu names):

    sudo apt install python3 python3-venv python3-pip xdotool xclip \
                     libportaudio2 fonts-dejavu-core scrot pulseaudio-utils \
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
  configured endpoint (default: local Ollama, model `qwen3.5:2b`). Point it at a
  local model and it's free too (~3 s/dictation warm on this hardware —
  measured, see config.py). Cloud models are faster but cost whatever your
  provider charges per request; each dictation is one small chat request
  (a few hundred tokens).

## Run

    ./venv/bin/python main.py

First run warms both models (~15 s) before the pill appears. Kill from the
right-click menu -> Quit.

## Config

Everything tunable in `config.py`: hotkey, models, mic source, bar sizes
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
  the app then always pastes the raw transcript. To use different models,
  set `LLM_POLISH_MODEL` to any model your Ollama already has
  (e.g. `qwen2.5:1.5b` — fastest local choice; the default `qwen3.5:2b`
  polishes in ~3 s once warm). `LLM_POLISH_TIMEOUT` (default 5 s) lets a
  slow endpoint fail over to raw text mid-dictation.
- The polish endpoint is fixed to local Ollama (`127.0.0.1:11434`) in
  `engine.py` (the `OLLAMA` constant) — a one-line edit there aims polish
  at any server speaking the same `/api/chat` shape.
- **Voice**: engine and voices are `config.py` switches too — `TTS_ENGINE`
  (`piper` local default / `edge` cloud), `TTS_PIPER_VOICE` (any piper
  voice name, e.g. `en_US-lessac-medium`, downloaded to
  `TTS_PIPER_DATA_DIR` on first use) and `TTS_PERSONA` for the cloud
  voices (emma, jenny, michaela, maisie, libby, ryan, sonia, aussie_f,
  aussie_m, us_f, us_m). Delivery is tunable too: `TTS_PIPER_SENTENCE_SILENCE`
  (sentence pauses), `TTS_PIPER_LENGTH_SCALE` (overall pace) and
  `TTS_PIPER_NOISE_SCALE` / `TTS_PIPER_NOISE_W_SCALE` (expressiveness).

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
    voice_out.py  TTS engines (piper local -> edge-tts -> espeak) + personas
    paste.py      clipboard paste + no-window guard + history
    requirements.txt       pinned Python dependencies

History: ~/.local/state/HarfDown2List/history.jsonl
Logs:    ~/.local/state/HarfDown2List/hd2l.log

## Status (verified on the dev box, 2026-09-11)

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
- [x] global F13 hold-to-talk hotkey (pynput)
- [ ] hands-free auto-stop: coded, not yet E2E-tested
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
