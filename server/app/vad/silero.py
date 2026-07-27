"""Silero VAD v5 on bare onnxruntime.

The official `silero-vad` PyPI package pulls in torch + torchaudio (~2.5GB) to
run a 1.8MB model. We only need a forward pass, so we drive the ONNX graph
directly and keep the GPU entirely free for Whisper and the LLM.

The model is a streaming RNN: it takes 512 samples (32ms @ 16kHz) plus a
recurrent state, and returns P(speech) for that frame along with the next state.

CRITICAL: v5 also requires the previous chunk's last 64 samples prepended, so the
tensor handed to ONNX is 576 wide, not 512. The graph declares its input shape as
[None, None], so feeding a bare 512 does not raise -- it just returns ~0.001 for
everything, i.e. a VAD that is silently deaf to speech. Measured on a clear
Mandarin clip: 0/90 frames over threshold without the context, 84/90 with it.
"""

from __future__ import annotations

import logging
from collections import deque
from dataclasses import dataclass
from enum import Enum, auto
from pathlib import Path

import numpy as np
import onnxruntime as ort

from ..config import FRAME_SAMPLES, SAMPLE_RATE

log = logging.getLogger(__name__)

#: Samples of lookback v5 expects ahead of each window (16kHz; it is 32 at 8kHz).
CONTEXT_SAMPLES = 64


class VadEvent(Enum):
    NONE = auto()
    SPEECH_START = auto()  # user started talking -> barge-in, kill playback
    SPEECH_END = auto()    # user stopped -> flush buffered audio to STT


@dataclass
class VadResult:
    event: VadEvent
    probability: float
    # Populated only on SPEECH_END: the complete utterance including the
    # pre-roll captured before the VAD actually fired.
    audio: np.ndarray | None = None


class SileroVad:
    def __init__(
        self,
        model_path: Path,
        threshold: float,
        silence_ms: int,
        min_speech_ms: int,
        prefix_ms: int,
        max_speech_ms: int = 20_000,
    ) -> None:
        if not model_path.exists():
            raise FileNotFoundError(
                f"Silero VAD model missing at {model_path}.\n"
                "Run: powershell -ExecutionPolicy Bypass -File scripts/download_models.ps1"
            )

        opts = ort.SessionOptions()
        # One thread is plenty for a model this small, and it keeps the VAD off
        # the cores that Whisper and the TTS want.
        opts.inter_op_num_threads = 1
        opts.intra_op_num_threads = 1
        opts.log_severity_level = 3
        self._session = ort.InferenceSession(
            str(model_path), sess_options=opts, providers=["CPUExecutionProvider"]
        )

        # v4 exposed separate `h`/`c` inputs; v5 merged them into one `state`
        # tensor. Detect which we're on rather than pinning a single release.
        self._input_names = {i.name for i in self._session.get_inputs()}
        self._uses_merged_state = "state" in self._input_names
        self._reset_state()

        self.threshold = threshold
        # Hysteresis: once speech is detected we require a lower probability to
        # call it silence, so trailing consonants don't end a turn early.
        #
        # The floor here matters more than it looks. Silero's output is roughly
        # bimodal -- clear speech >0.9, digital silence <0.01 -- but a real room
        # picked up by a real microphone sits somewhere in between. An earlier
        # floor of 0.15 was below that noise level, so once a turn started it
        # never ended: the silence run kept resetting and the user waited tens of
        # seconds for a reply. Synthetic test audio never caught it because
        # zero-padding scores ~0.001.
        self.exit_threshold = max(0.25, threshold * 0.6)

        frame_ms = FRAME_SAMPLES * 1000 // SAMPLE_RATE
        self._silence_frames_needed = max(1, silence_ms // frame_ms)
        self._min_speech_frames = max(1, min_speech_ms // frame_ms)
        # Hard ceiling on a single turn. Whatever the threshold tuning, a turn
        # must always end -- a mains hum or a fan that parks the probability
        # above exit_threshold would otherwise hold the pipeline open forever,
        # which reads to the user as "she stopped responding".
        self._max_speech_frames = max(1, max_speech_ms // frame_ms)
        prefix_frames = max(1, prefix_ms // frame_ms)

        self._triggered = False
        self._silence_run = 0
        self._speech_frames = 0
        self._utterance: list[np.ndarray] = []
        # Ring buffer of recent frames so a turn starts slightly *before* the
        # VAD fires -- without this Whisper reliably loses the first phoneme.
        self._prefix: deque[np.ndarray] = deque(maxlen=prefix_frames)

    def _reset_state(self) -> None:
        self._context = np.zeros(CONTEXT_SAMPLES, dtype=np.float32)
        if self._uses_merged_state:
            self._state = np.zeros((2, 1, 128), dtype=np.float32)
        else:
            self._h = np.zeros((2, 1, 64), dtype=np.float32)
            self._c = np.zeros((2, 1, 64), dtype=np.float32)

    def _infer(self, frame: np.ndarray) -> float:
        if self._uses_merged_state:
            # v5: prepend the tail of the previous window. Without this the
            # model returns ~0 for everything -- see the module docstring.
            windowed = np.concatenate([self._context, frame])
            self._context = frame[-CONTEXT_SAMPLES:].copy()
        else:
            # v4 carried its lookback inside the h/c states instead.
            windowed = frame

        inputs: dict[str, np.ndarray] = {
            "input": windowed.reshape(1, -1).astype(np.float32),
            "sr": np.array(SAMPLE_RATE, dtype=np.int64),
        }
        if self._uses_merged_state:
            inputs["state"] = self._state
            prob, self._state = self._session.run(None, inputs)
        else:
            inputs["h"] = self._h
            inputs["c"] = self._c
            prob, self._h, self._c = self._session.run(None, inputs)
        return float(prob.item())

    def reset(self) -> None:
        """Drop any half-captured utterance. Called when a session starts or
        when we abandon a turn."""
        self._reset_state()
        self._triggered = False
        self._silence_run = 0
        self._speech_frames = 0
        self._utterance.clear()
        self._prefix.clear()

    def process(self, frame: np.ndarray) -> VadResult:
        """Feed exactly FRAME_SAMPLES of float32 audio; get back a transition."""
        if frame.shape[0] != FRAME_SAMPLES:
            raise ValueError(f"expected {FRAME_SAMPLES} samples, got {frame.shape[0]}")

        prob = self._infer(frame)

        if not self._triggered:
            self._prefix.append(frame.copy())
            if prob >= self.threshold:
                self._triggered = True
                self._speech_frames = 1
                self._silence_run = 0
                # Seed the utterance with the pre-roll we've been holding.
                self._utterance = list(self._prefix)
                self._prefix.clear()
                return VadResult(VadEvent.SPEECH_START, prob)
            return VadResult(VadEvent.NONE, prob)

        self._utterance.append(frame.copy())

        if prob >= self.exit_threshold:
            self._speech_frames += 1
            self._silence_run = 0
            # Safety valve. If noise holds the probability up, end the turn here
            # rather than listening indefinitely -- a truncated question still
            # gets an answer, whereas a hung turn looks like the app has died.
            if len(self._utterance) >= self._max_speech_frames:
                captured = np.concatenate(self._utterance)
                log.warning(
                    "turn hit the %.0fs ceiling with P=%.2f still above exit "
                    "threshold %.2f -- raise VAD_THRESHOLD if this repeats",
                    captured.size / SAMPLE_RATE, prob, self.exit_threshold,
                )
                self.reset()
                return VadResult(VadEvent.SPEECH_END, prob, audio=captured)
            return VadResult(VadEvent.NONE, prob)

        self._silence_run += 1
        if self._silence_run < self._silence_frames_needed:
            return VadResult(VadEvent.NONE, prob)

        # Enough trailing silence to call the turn over.
        captured = np.concatenate(self._utterance) if self._utterance else np.zeros(0, np.float32)
        was_long_enough = self._speech_frames >= self._min_speech_frames
        self.reset()

        if not was_long_enough:
            # A cough, a door, a keyboard clack. Discard rather than sending
            # noise to Whisper, which loves to hallucinate on silence.
            log.debug("discarding %.2fs blip", captured.size / SAMPLE_RATE)
            return VadResult(VadEvent.NONE, prob)

        return VadResult(VadEvent.SPEECH_END, prob, audio=captured)
