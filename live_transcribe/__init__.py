"""SA-Live-Transcribe, local Afrikaans live-transcription POC."""

# Force HuggingFace to write REAL FILES into its model cache, never Windows symlinks.
# The frozen build bundles MSVCP140.dll 14.50, whose C++ std::ifstream cannot open a Windows
# reparse point (symlink), so CTranslate2 fails with "Unable to open file 'model.bin'" for every
# symlinked model on Developer-Mode / power-user machines (the ones where HuggingFace creates
# symlinks). Setting these BEFORE huggingface_hub is first imported makes its
# are_symlinks_supported() return False, so freshly downloaded models are written as real files.
#
# This lives in the package __init__ on purpose: Python guarantees a package's __init__ runs to
# completion before ANY submodule body, so the env is set before faster_whisper / huggingface_hub
# are imported by transcribe.py, voicedl.py, modeldl.py (and friends) on every entry point: the
# frozen app_main.py, `python -m live_transcribe[.web]`, and direct `import live_transcribe.*` in
# the tests. A new hf_hub-importing submodule is covered automatically, with no per-file discipline.
# setdefault, so a real user or CI override still wins. Do NOT drop or relocate this without reading
# docs/volksmond-model-load-symlink-bug-2026-08-18.md.
import os

os.environ.setdefault("HF_HUB_DISABLE_SYMLINKS", "1")
os.environ.setdefault("HF_HUB_DISABLE_SYMLINKS_WARNING", "1")

__version__ = "0.1.0"
