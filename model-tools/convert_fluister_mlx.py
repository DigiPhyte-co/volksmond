"""Convert digiphyte/fluister-turbo-transformers (HF fp16 safetensors) to MLX.

Produces two variants under --out-dir:
  fluister-turbo-mlx-fp16   (dtype float16)
  fluister-turbo-mlx-q8     (8-bit quantised, group size 64)

The pip package mlx-whisper (0.4.3) does NOT ship a conversion module; the
canonical converter is whisper/convert.py in ml-explore/mlx-examples, which
lives next to the package source and imports the installed mlx_whisper. That
script is vendored here as mlx_examples_whisper_convert.py, pinned to commit
adaab81029eb5f53d9a40c94968bf143cbc5985c (2024-11-25), the layout matching the
released 0.4.3 package: it writes weights.safetensors, which the pip package's
load_models expects (the later commit e52c128d switched to model.safetensors
for a newer, unreleased package version). Expected vendored-file sha256:
9d5a03b60843e89512d1f825a6a6ee662fa2d17af347220c5e6a8b69af7574aa

Nothing here ever uploads to Hugging Face: the vendored script only uploads
when --upload-name is passed, and this wrapper never passes it.
"""
import argparse
import hashlib
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
CONVERT_PY = HERE / "mlx_examples_whisper_convert.py"


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def run(cmd):
    print("+", " ".join(str(c) for c in cmd), flush=True)
    subprocess.run([str(c) for c in cmd], check=True)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--hf-repo", default="digiphyte/fluister-turbo-transformers")
    ap.add_argument("--out-dir", default="mlx-models")
    args = ap.parse_args()

    print(f"vendored convert.py sha256: {sha256_file(CONVERT_PY)}", flush=True)

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    fp16_dir = out_dir / "fluister-turbo-mlx-fp16"
    q8_dir = out_dir / "fluister-turbo-mlx-q8"

    run([sys.executable, CONVERT_PY,
         "--torch-name-or-path", args.hf_repo,
         "--mlx-path", fp16_dir,
         "--dtype", "float16"])

    run([sys.executable, CONVERT_PY,
         "--torch-name-or-path", args.hf_repo,
         "--mlx-path", q8_dir,
         "--dtype", "float16",
         "-q", "--q-bits", "8", "--q-group-size", "64"])

    for d in (fp16_dir, q8_dir):
        print(f"\n== {d} ==", flush=True)
        total = 0
        for f in sorted(d.rglob("*")):
            if f.is_file():
                size = f.stat().st_size
                total += size
                print(f"  {f.name}  {size:>12} bytes  "
                      f"sha256={sha256_file(f)}", flush=True)
        print(f"  total: {total / 1e9:.2f} GB", flush=True)


if __name__ == "__main__":
    main()
