"""Mic capture: sounddevice + PipeWire/Pulse, RMS level metering."""
import queue
from collections import deque

import numpy as np
import sounddevice as sd

import config


class Mic:
    """Continuously captures 16 kHz mono s16le from the default (or configured)
    source, keeps a rolling prebuffer, and exposes per-block RMS for the UI.

    One persistent stream runs the whole time the app is alive; recording is
    just "mark the blocks we care about" so we never lose the first syllable.
    """

    def __init__(self):
        self.q = queue.Queue(maxsize=800)  # ~24 s of 30 ms blocks before overflow
        self.device = self._find_device()
        self._stream = None
        self._recording = False
        self._paused = False
        self._buffered = []                # list of np.int16 arrays
        # ~0.5 s of audio buffered before you press, so clipped onsets survive
        pre_blocks = max(1, int(0.5 * config.SAMPLE_RATE / config.BLOCK))
        self._prebuf = deque(maxlen=pre_blocks)

    @staticmethod
    def _find_device():
        if config.MIC_SOURCE:
            try:
                for i, dev in enumerate(sd.query_devices()):
                    if dev.get("max_input_channels", 0) < 1:
                        continue
                    name = (dev.get("name") or "") + " " + str(dev)
                    if config.MIC_SOURCE.lower() in name.lower():
                        return i
            except Exception:
                return None
        return None  # system default

    def start(self):
        if self._stream is not None:
            return
        self._stream = sd.RawInputStream(
            samplerate=config.SAMPLE_RATE,
            blocksize=config.BLOCK,
            device=self.device if self.device is not None else sd.default.device[0],
            dtype="int16",
            channels=1,
            callback=self._on_block,
        )
        self._stream.start()

    def stop(self):
        if self._stream is not None:
            try:
                self._stream.stop()
                self._stream.close()
            finally:
                self._stream = None

    def _on_block(self, indata, frames, t_time, status):
        blk = np.frombuffer(indata, dtype=np.int16).copy()
        try:
            self.q.put_nowait(blk)
        except queue.Full:
            pass

    @property
    def recording(self):
        return self._recording

    def start_recording(self):
        self._buffered = list(self._prebuf)
        self._recording = True

    def stop_recording(self):
        self._recording = False
        blocks, self._buffered = self._buffered, []
        return blocks

    def drain(self):
        """Pull everything queued. Feeds prebuf/record buffer, returns
        (rms, n_blocks_consumed). RMS is normalized 0..1 (approx)."""
        rms = 0.0
        n = 0
        while True:
            try:
                blk = self.q.get_nowait()
            except queue.Empty:
                break
            n += 1
            self._prebuf.append(blk)
            if self._recording and not self._paused:
                self._buffered.append(blk)
            f = blk.astype(np.float32) / 32768.0
            rms = max(rms, float(np.sqrt(np.mean(f * f))))
        return rms, n

    def set_paused(self, paused: bool):
        """Pause/resume capture: paused blocks are dropped, metering continues."""
        self._paused = paused

    @staticmethod
    def blocks_to_audio(blocks, trim=True):
        """Concatenate int16 blocks into a float32 mono array, optionally
        trimming leading/trailing silence (simple RMS gate)."""
        if not blocks:
            return np.zeros(0, dtype=np.float32)
        audio = np.concatenate(blocks).astype(np.float32) / 32768.0
        if not trim or audio.size == 0:
            return audio
        win = int(0.03 * config.SAMPLE_RATE)  # 30 ms frames
        if audio.size < win * 3:
            return audio
        nframes = audio.size // win
        fr = audio[: nframes * win].reshape(nframes, win)
        frame_rms = np.sqrt(np.mean(fr * fr, axis=1))
        speech = frame_rms >= config.SILENCE_RMS
        if not speech.any():
            return audio
        first = int(np.argmax(speech))
        last = nframes - 1 - int(np.argmax(speech[::-1]))
        lead = max(0, int(config.TRIM_MARGIN_S_LEAD * config.SAMPLE_RATE))
        trail = int(config.TRIM_MARGIN_S * config.SAMPLE_RATE)
        start = max(0, first * win - lead)
        end = min(audio.size, (last + 1) * win + trail)
        return audio[start:end]