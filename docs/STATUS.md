# Status

Verified on the dev box, 2026-10-15.

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
- Self-corrections ("let's meet Tuesday — no, Wednesday") are resolved
  within a session by the polish pass. A correction spoken in a NEW
  session can't retro-edit the previous one — that's what "paste last
  transcript" in the right-click menu is for.
- The dictation path is single-language (English models by default);
  swap `PARTIAL_MODEL`/`FINAL_MODEL` for multilingual variants in config.