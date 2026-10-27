# Configuration

Everything tunable lives in `config.py` — edit, then restart the app.

## Swapping models (transcription & polish)

- **Transcription** needs no Ollama, no keys, no accounts — the whisper
  models are pulled automatically and run in-process. Swap them via
  `PARTIAL_MODEL` (live preview) and `FINAL_MODEL` (final text).
  Defaults: `tiny.en` + `small` on CPU. Bigger models = more accuracy
  but slower: a large model on CPU will lag the live preview and make
  the final pass take longer than a quick dictation.
- **LLM polish** is optional. `LLM_POLISH = False` turns it off entirely;
  the app then always pastes the raw transcript. Default model:
  `gemma4:cloud` (fast; needs the local router). For a 100%-local
  machine, set `LLM_POLISH_MODEL` to e.g. `qwen3.5:2b` (~3 s warm for
  short dictations; long ones exceed CPU-friendly timeouts).
  `LLM_POLISH_TIMEOUT` (default 10 s) lets a slow endpoint fail over to
  raw text.
- The polish endpoint is fixed to local Ollama (`127.0.0.1:11434`) in
  `engine.py` (the `OLLAMA` constant) — a one-line edit there aims polish
  at any server speaking the same `/api/chat` shape.

### Model notes (measured on the dev box, 2026-10-15)

    gemma4:cloud               -> 0.6-2s incl. long dictations, honours
                                  think:false, returns ONLY the cleaned
                                  dictation — DEFAULT
    minimaxm3-cloud            -> 1.4s clean, minor caps ("Llama notes")
    glm-5.3-flash:cloud        -> UNUSABLE for polish: ignores think:false
                                  and returns its reasoning instead
    qwen3.5:cloud              -> 410 Gone (retired)
    deepseek-v4-flash:cloud    -> RETIRED by the router 2026-09-25 (HTTP 410)

NOTE: `"think": false` is REQUIRED for cloud router models or they reason
until the token budget is gone and return empty content; qwen3.5 accepts
it. Test any new candidate with a real dictation before trusting it —
some cloud models return reasoning text that would leak into pastes.

## Voice read-out

- **Natural delivery**: long text is synthesized sentence by sentence (in
  parallel, so it starts no later) and the pieces are joined, so every
  sentence gets its own phrase ending and a breath between instead of one
  flat run. Turn it off with `TTS_SENTENCE_SPLIT = False`.
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

## Auditioning a voice

`voice_preview.py` lets you hear a change without restarting the app:

    venv/bin/python voice_preview.py --list
    venv/bin/python voice_preview.py --persona london_warm --rate +12% --pitch -3Hz
    venv/bin/python voice_preview.py --persona london_warm --rate +12% --save

Without `--save` it changes nothing: a separate process, it speaks through
your speakers, writes no files, and neither the running app nor the next
boot is affected. With `--save` the tweaked `rate`/`pitch`/`volume` are
written back into that persona's line in `voice_out.py` (a `.bak` is kept
alongside) and they take effect the next time the app starts. The persona
itself — the `edge` voice, the `piper` fallback, the label — is edited by
hand in the `VOICES` dict in `voice_out.py`.

It talks to the speakers directly and knows nothing about the running
app, so do not run it while the app is reading something out.

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

## Logs & state

- Everything goes to `~/.local/state/HarfDown2List/hd2l.log` (rotating)
  as well as the terminal — that file is the first place to look when a
  read-back misbehaves.
- `HD2L_STATE_DIR` overrides the state directory, so a second copy can
  run side by side without the two fighting over the same files.
- History: `~/.local/state/HarfDown2List/history.jsonl`