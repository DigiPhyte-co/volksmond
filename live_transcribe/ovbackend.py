"""OpenVINO GenAI ASR backend adapter. SPIKE, NOT WIRED.

STATUS: this file is the shape sketch produced by the 1.14 OpenVINO iGPU spike
(spike-part1-report, 2026-09-04). It is deliberately NOT imported by accel.py,
transcribe.py or anything else, and nothing here runs in the shipping app yet.
It exists so the eventual backend has a duck-typed adapter to grow into, exactly
as mlxbackend.py wraps mlx-whisper. Do not wire it in until the iGPU kill
criterion has passed on real Intel hardware (that is Part 2 of the spike).

Design intent, mirroring MlxWhisperModel:
  - Present the faster-whisper duck type the Engine already speaks:
    transcribe(audio, language=, initial_prompt=, vad_filter=, vad_parameters=,
    beam_size=, **GUARD) -> (segments, info), each segment exposing
    .text/.start/.end.
  - Import openvino_genai ONLY inside the constructor, never at module import,
    so importing this module on a machine without OpenVINO is a no-op.
  - Resolve the model to a LOCAL exported-IR directory only; never let any
    downloader touch the network (downloads go through voicedl, like MLX).
  - The device string ("CPU" / "GPU" / "NPU") is the whole backend switch. The
    locked design rule is "the iGPU is a gear, not a home": a falling-behind
    signal must move the encoder back to CPU cores. That gearing decision lives
    in the engine/selection layer, NOT here; this adapter only runs the device
    it is handed.

Differences from faster-whisper the adapter absorbs (measured in the spike):
  - openvino-genai WhisperPipeline has no in-decoder VAD, no vad_parameters and
    no beam_size surface: dropped silently (noted once at build time), exactly as
    the MLX adapter drops them; the engine's _chunk_is_silence pre-gate and the
    post-ASR hallucination guards stay active.
  - the anchor prompt maps to WhisperGenerationConfig.initial_prompt (verified
    present in openvino-genai 2026.3.1.0); language maps to a "<|xx|>" token.
  - the IR must be exported stateful, i.e. with
    `optimum-cli export openvino --task automatic-speech-recognition-with-past`;
    a plain automatic-speech-recognition export is stateless and the pipeline
    fails with "Port for tensor name beam_idx was not found".
"""
import numpy as np


# ct2 model id -> exported-IR subdirectory name. Single source of truth for
# which models have an OpenVINO form, mirroring mlxbackend.MLX_REPOS. Filled in
# for real only when the backend is wired; left illustrative for the spike.
OV_MODELS = {
    # "digiphyte/fluister-turbo": "fluister-turbo-ov-int8",
    # "digiphyte/fluister-small": "fluister-small-ov-int8",
}

DROPPED_KWARGS = frozenset({"vad_filter", "vad_parameters", "beam_size"})


class _Seg:
    """One transcribed segment in the faster-whisper shape (.text/.start/.end)."""
    __slots__ = ("text", "start", "end")

    def __init__(self, text, start, end):
        self.text = text
        self.start = start
        self.end = end


class OpenvinoWhisperModel:
    """Duck-typed WhisperModel running openvino-genai WhisperPipeline. SPIKE STUB.

    `ir_dir` is a local directory holding a stateful Whisper IR export. `device`
    is an OpenVINO device string ("CPU", "GPU", "NPU"). Neither compute_type nor
    cpu_threads is taken: the IR carries its own precision and OpenVINO manages
    its own threads.
    """

    def __init__(self, ir_dir, device="CPU"):
        import openvino_genai as ovg   # lazy: only where OpenVINO is installed
        self._pipe = ovg.WhisperPipeline(ir_dir, device)
        self._device = device
        print(f"[openvino] {ir_dir} on {device}: "
              f"{', '.join(sorted(DROPPED_KWARGS))} not supported by WhisperPipeline; "
              "ignored (engine pre-gate + guards stay active)", flush=True)

    def transcribe(self, audio, language=None, initial_prompt=None, **kwargs):
        """Engine call surface: returns (segments, info). Audio must be 16 kHz
        float32 numpy, which is what the Engine feeds."""
        if not isinstance(audio, np.ndarray):
            raise TypeError(
                f"OpenvinoWhisperModel.transcribe expects ndarray audio, got "
                f"{type(audio).__name__}")
        cfg = self._pipe.get_generation_config()
        if language:
            cfg.language = language if language.startswith("<|") else f"<|{language}|>"
        cfg.task = "transcribe"
        cfg.return_timestamps = True
        if initial_prompt:
            cfg.initial_prompt = initial_prompt
        res = self._pipe.generate(np.ascontiguousarray(audio, dtype=np.float32), cfg)
        text = "".join(res.texts) if hasattr(res, "texts") else str(res)
        dur = len(audio) / 16000.0
        segs = [_Seg(text.strip(), 0.0, dur)] if text.strip() else []
        return segs, res
