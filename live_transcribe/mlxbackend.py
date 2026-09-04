"""MLX (Apple Metal) ASR backend: the mlx-whisper adapter for the Engine seam.

mlx-whisper exposes a module-level `transcribe(audio, path_or_hf_repo=...)` that
returns a plain dict, not a model object. This module wraps it in the exact
`WhisperModel.transcribe(audio, language=, initial_prompt=, vad_filter=,
vad_parameters=, beam_size=, **GUARD) -> (segments, info)` duck type the Engine, warm-up and the
tests already speak, so nothing in `Engine._run`, the hallucination guards or
the fan-out changes when a session runs on Metal.

mlx-whisper differences the adapter absorbs (pinned by tests/test_mlx_backend.py):
  - no `vad_filter`, no `vad_parameters` and no `beam_size`: all dropped silently
    (noted once at build time, never per chunk). Losing in-decoder VAD is accepted
    by design (and with it the per-source MIC VAD tightening),
    the engine pre-gates silence with _chunk_is_silence and every post-ASR
    hallucination guard stays active.
  - `log_prob_threshold` is spelled `logprob_threshold`.
  - `temperature` takes a tuple, not a list (same fallback ladder semantics).
  - `word_timestamps` is always False (sidesteps mlx-examples#1254) and
    `verbose` always None.

`mlx_whisper` is imported ONLY inside the adapter constructor, never at module
import, so importing this module (or live_transcribe.transcribe) on Windows
stays byte-identical in behaviour.
"""
import numpy as np


# ct2 model id -> MLX repo. The single source of truth for which models have an
# MLX form (D3). Anything outside this map (local Fluister ct2 dirs, unmapped
# sizes, Swivuriso) has no MLX form and runs on ct2 CPU on the Mac; that
# fallback happens at selection time, never here.
MLX_REPOS = {
    "digiphyte/fluister-turbo": "digiphyte/fluister-turbo-mlx",
    "large-v3":                 "mlx-community/whisper-large-v3-mlx",
}

# The kwarg surface the adapter translates, as module constants so the tests pin
# them: names renamed for mlx-whisper, and names it has no equivalent for.
KWARG_RENAMES = {"log_prob_threshold": "logprob_threshold"}
DROPPED_KWARGS = frozenset({"vad_filter", "vad_parameters", "beam_size"})


def mlx_model_for(ct2_model_id):
    """The MLX repo for a ct2 model id, or None when no MLX form exists."""
    return MLX_REPOS.get(ct2_model_id)


class _Seg:
    """One transcribed segment in the faster-whisper shape (.text/.start/.end)."""
    __slots__ = ("text", "start", "end")

    def __init__(self, text, start, end):
        self.text = text
        self.start = start
        self.end = end


class MlxWhisperModel:
    """Duck-typed WhisperModel running mlx-whisper on the Apple GPU.

    `model_id` is an MLX repo id (a value of MLX_REPOS). The constructor resolves
    it to a LOCAL snapshot path, local-only like _build_model's ct2 contract but
    STRICTER: an uncached repo raises (see _resolve_local) rather than letting
    mlx-whisper's own downloader fetch it, so no MLX download can ever bypass
    voicedl's consent/progress/single-slot machinery. compute_type/cpu_threads
    have no MLX equivalent (the repo holds its own precision), so the adapter
    takes neither.
    """

    def __init__(self, model_id):
        import mlx_whisper   # lazy: only present (and only importable) on darwin-arm64
        self._mlx = mlx_whisper
        self._path = self._resolve_local(model_id)
        # Once at build time, not per chunk: these engine kwargs have no
        # mlx-whisper equivalent and are dropped by transcribe() below.
        print(f"[mlx] {model_id}: {', '.join(sorted(DROPPED_KWARGS))} not supported "
              "by mlx-whisper; ignored", flush=True)

    @staticmethod
    def _resolve_local(model_id):
        """Local cache only: NEVER let mlx-whisper's own downloader touch the network.
        An uncached repo raises instead of falling back to the repo id: handing the repo
        id to mlx_whisper.transcribe would let its downloader silently pull a multi-GB
        snapshot (warm-up's dummy inference triggers this before any consent), outside
        voicedl's single global download slot and progress state, and racing a later
        voicedl download over the same cache. Every MLX download must go through voicedl
        first; the engine build path guarantees that before this constructor runs."""
        try:
            import huggingface_hub
            return huggingface_hub.snapshot_download(model_id, local_files_only=True)
        except Exception as e:
            raise RuntimeError(
                f"The MLX model {model_id} is not downloaded yet; download it first "
                "(all MLX downloads go through voicedl, never mlx-whisper's own fetch)"
            ) from e

    def transcribe(self, audio, language=None, initial_prompt=None, **kwargs):
        """The Engine call surface: returns (segments, info) where each segment
        exposes .text/.start/.end and info is the raw mlx result dict (the Engine
        discards it). Audio must already be 16 kHz float32 numpy, which is what
        the Engine feeds; mlx-whisper's ffmpeg path is never taken."""
        if not isinstance(audio, np.ndarray):
            raise TypeError(f"MlxWhisperModel.transcribe expects ndarray audio, got {type(audio).__name__}")
        kw = {}
        for k, v in kwargs.items():
            if k in DROPPED_KWARGS:
                continue
            k = KWARG_RENAMES.get(k, k)
            if k == "temperature" and isinstance(v, list):
                v = tuple(v)   # mlx-whisper wants a tuple for the fallback ladder
            kw[k] = v
        result = self._mlx.transcribe(
            audio,
            path_or_hf_repo=self._path,
            language=language,
            initial_prompt=initial_prompt,
            word_timestamps=False,
            verbose=None,
            **kw,
        )
        segs = [
            _Seg(s.get("text", ""), float(s.get("start", 0.0)), float(s.get("end", 0.0)))
            for s in result.get("segments", [])
        ]
        return segs, result
