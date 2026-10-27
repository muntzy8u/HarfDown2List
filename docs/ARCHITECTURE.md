# Architecture

How HarfDown2List works, and what runs where.

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

    hold the pill    ->  push-to-talk (release to transcribe + paste)
    drag             ->  reposition; release near a screen edge to snap
    right-click      ->  menu (paste last, history, mic, hide, quit)
    middle-click     ->  recenter the pill

A one-line live-transcript strip floats above the bar while you speak:
committed words in white, still-volatile words in grey. If no window is
focused when you finish, the paste keystroke is skipped and your text is
left safely on the clipboard.

## How it works

- **UI**: pure python-xlib override-redirect overlay + X SHAPE click-through
  regions. No toolkit, no WM interaction. Works on any X11 window manager.
- **STT**: faster-whisper int8 on CPU. tiny.en streams live partials
  (~0.4 s/call); small runs the final pass (~2 s for 7 s of speech on an
  i7-5820K). Local hallucination guard + local-agreement prefix commit.
- **Polish**: optional LLM pass via local Ollama (`127.0.0.1:11434`,
  `/api/chat` — see [CONFIGURATION.md](CONFIGURATION.md) to retarget it)
  that removes fillers, resolves self-corrections, fixes punctuation.
  Ships raw transcript if the endpoint is unreachable or too slow —
  dictation never blocks on it.
- **Custom vocabulary**: whisper mis-hears domain words ("GitHub" ->
  "get", "Ollama" -> "a llama"). Your personal dictionary at
  `~/.local/state/HarfDown2List/vocabulary.json` fixes them after
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

## Cost — what runs where

- **Free, 100% local**: the entire dictation path — capture, streaming
  partials, final transcription, paste, UI. No API keys, no accounts, no
  telemetry. This is the default configuration.
- **Optional, only if enabled**: the LLM polish pass calls your own
  configured endpoint (default: the local Ollama-compatible router with
  `gemma4:cloud`, ~0.6–2 s per dictation, verified in testing). For a
  fully local setup, `config.py` documents `qwen3.5:2b` (~3 s warm for
  short dictations; medium/long exceed CPU-friendly timeouts). If the
  endpoint is down, the raw transcript ships immediately. Each dictation
  is one small chat request (a few hundred tokens).

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
    voice_preview.py audition a voice without restarting the app
    logs.py       rotating log file (config.LOG_FILE) + stdout mirror
    notify.py     desktop notifications, reaped (no zombie notify-send)
    paste.py      clipboard paste + no-window guard + history
    assets/gen_idle_icon.py generates assets/idle-mic.png (design dials inside)
    assets/idle-mic.png    idle mic icon shown on the pill (config.BUBBLE_ICON)
    requirements.txt       pinned Python dependencies

History: `~/.local/state/HarfDown2List/history.jsonl`
Logs:    `~/.local/state/HarfDown2List/hd2l.log`