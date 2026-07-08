r"""Local, offline summarisation via llama-cpp-python (a GGUF model, in-process).

The summary model is an optional, free, on-device extra: a single .gguf file the
user downloads in one click from Settings (see modeldl.py). llama.cpp runs
it in-process with no daemon, applies the model's own chat template, and supports
the Gemma 4 architecture (including the KV-sharing that ctranslate2 4.7.2 cannot
yet convert). CPU by default, which is what the target machines have; long
transcripts are summarised map-reduce so a small model stays coherent past its
useful attention span.
"""
import os

DEFAULT_INSTRUCTION = (
    "Produce concise meeting minutes from this transcript: a short overview, the "
    "main points, any decisions, and any action items. Do not invent anything that "
    "is not supported by the text."
)

# Always-on guidance about the transcript itself, added regardless of the user's
# chosen instruction. The transcript is raw speech recognition output: the blocklist
# in transcribe.py removes most hallucination artifacts, but older transcripts and
# edge cases can still carry junk, mis-recognised words, and Afrikaans/English code-
# switching. This keeps junk out of the summary and tells the model how to read messy
# SA speech without inventing content.
TRANSCRIPT_NOTE = (
    "This transcript was produced by automatic speech recognition, so it is imperfect. "
    "Ignore obvious transcription artifacts and never treat them as something that was "
    "said: for example subtitle credits like 'Amara.org' or 'Ondertitels', "
    "'thank you for watching', 'please subscribe', and isolated repeated filler. "
    "The speakers mix Afrikaans and English in one conversation; treat both as the same "
    "discussion. Where a word is clearly mis-recognised, infer the intended meaning from "
    "context, but never invent names, numbers, decisions, or action items the text does "
    "not support."
)


# Cached: whether the installed llama-cpp-python was built with a GPU (CUDA) backend.
# The shipped CPU wheel returns False, so GPU summaries only ever light up in a CUDA
# build or a source install with the CUDA wheel. Probed once (importing llama_cpp is
# not free) and reused.
_GPU_OFFLOAD = None


def gpu_offload_supported():
    """True only if this llama-cpp-python can offload layers to an NVIDIA GPU."""
    global _GPU_OFFLOAD
    if _GPU_OFFLOAD is None:
        try:
            from llama_cpp import llama_supports_gpu_offload
            _GPU_OFFLOAD = bool(llama_supports_gpu_offload())
        except Exception:
            _GPU_OFFLOAD = False
    return _GPU_OFFLOAD


def fits_on_gpu(model_path, vram_mb, headroom_mb=2048):
    """Whether a GGUF of this size should be fully offloaded to a GPU with vram_mb of VRAM.

    Full offload (all layers) only when the file plus a working-memory headroom (KV cache,
    context, overhead) fits; otherwise keep it on the CPU rather than risk a CUDA out-of-
    memory partway through a summary. Partial offload is deliberately not used yet: it
    complicates the fit maths for little gain on the machines this targets (a 24 GB card
    holds even the 12B comfortably; a 4 GB card cannot, and falls back to the CPU)."""
    try:
        if not vram_mb or vram_mb <= 0:
            return False
        size_mb = os.path.getsize(model_path) / (1024 * 1024)
        return vram_mb >= size_mb + headroom_mb
    except OSError:
        return False


class Summariser:
    """Load a GGUF summary model and generate summaries. CPU by default; pass
    n_gpu_layers=-1 to offload all layers to an NVIDIA GPU (needs a CUDA build of
    llama-cpp-python, see gpu_offload_supported()).

    chunk_tokens caps how much transcript a single generation sees; beyond that the
    transcript is summarised in parts and the parts are combined. The cap is about
    small-model coherence, not the model's context limit, which is far larger.
    """

    def __init__(self, model_path, n_ctx=8192, n_gpu_layers=0):
        from llama_cpp import Llama
        if not os.path.isfile(model_path):
            raise FileNotFoundError(f"summary model not found: {model_path}")
        self.llm = Llama(model_path=model_path, n_ctx=n_ctx,
                         n_gpu_layers=n_gpu_layers, verbose=False)

    def _generate(self, content, max_tokens):
        out = self.llm.create_chat_completion(
            messages=[{"role": "user", "content": content}],
            max_tokens=max_tokens,
            temperature=0.3,
        )
        return out["choices"][0]["message"]["content"].strip()

    def _ntokens(self, text):
        return len(self.llm.tokenize(text.encode("utf-8"), add_bos=False))

    def _split_by_tokens(self, text, chunk_tokens):
        """Split on line boundaries into chunks of at most chunk_tokens tokens."""
        chunks, cur, cur_n = [], [], 0
        for line in text.splitlines(keepends=True):
            n = self._ntokens(line)
            if cur and cur_n + n > chunk_tokens:
                chunks.append("".join(cur))
                cur, cur_n = [], 0
            cur.append(line)
            cur_n += n
        if cur:
            chunks.append("".join(cur))
        return chunks

    def summarise(self, transcript, instruction=None, language=None, notes=None,
                  chunk_tokens=3000, max_output_tokens=512):
        instruction = (instruction or DEFAULT_INSTRUCTION).strip()
        if language:
            lang_name = {"af": "Afrikaans", "en": "English"}.get(language)
            if lang_name:
                instruction += ("\n\nWrite the summary in " + lang_name +
                                ", regardless of the language spoken in the transcript.")
        transcript = transcript.strip()
        if not transcript:
            return ""
        # The user's own notes, when supplied, are authoritative human input: a person wrote them,
        # so prefer them over the noisy transcript and make sure their points land. Fed into the
        # single-shot summary and, for a long transcript, into the final combine step (not each part).
        notes = (notes or "").strip()
        notes_block = ("" if not notes else
                       "The user also kept their own written notes during this meeting. Treat these "
                       "as authoritative: where they differ from the transcript, prefer the notes, and "
                       "make sure the points they raise are reflected.\n\nUSER NOTES:\n" + notes + "\n\n")
        if self._ntokens(transcript) <= chunk_tokens:
            return self._generate(f"{instruction}\n\n{TRANSCRIPT_NOTE}\n\n{notes_block}TRANSCRIPT:\n{transcript}", max_output_tokens)
        # map: summarise each part on its own (the notes are applied at the combine step below)
        chunks = self._split_by_tokens(transcript, chunk_tokens)
        partials = []
        for i, ch in enumerate(chunks, 1):
            p = self._generate(
                f"This is part {i} of {len(chunks)} of a meeting transcript. Summarise the "
                f"key points, decisions, and action items in this part only.\n\n{TRANSCRIPT_NOTE}\n\nTRANSCRIPT:\n{ch}",
                max_output_tokens,
            )
            partials.append(f"[Part {i}]\n{p}")
        # reduce: fold the part-summaries into one result under the user's instruction
        combined = "\n\n".join(partials)
        return self._generate(
            f"{instruction}\n\n{notes_block}The following are summaries of consecutive parts of one "
            f"meeting. Combine them into a single coherent result, removing repetition.\n\n{combined}",
            max_output_tokens + 128,
        )
