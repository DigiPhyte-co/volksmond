# Volksmond v1.0.7 (early access)

Volksmond turns any meeting into a private transcript on your own Windows PC. It listens to your microphone and to the other side of the call, transcribes on your machine, and can write a short summary, without sending your audio or transcripts anywhere.

It is built for South African meetings: Afrikaans, English, and the natural mix of the two.

This is an early access build, marked Research Preview, and it is free while in early access.

## What you get

- **Private by design.** Everything runs on your computer. Volksmond never phones home and never uploads your audio, transcripts, or summaries.
- **Both sides of the call.** It captures your microphone and your computer's own audio, so you get the whole conversation, not just your half.
- **Afrikaans, English, and the mix.** Transcribes South African speech, including switching between the two languages mid-sentence.
- **Pick your accuracy.** Choose from several transcription models (small, medium, large-v3-turbo, large-v3). Bigger models are more accurate and slower. You download the one you want on first run, and you can add or remove models later from Settings.
- **Optional NVIDIA GPU speed-up (new in 1.0.7).** If your PC has an NVIDIA graphics card, Volksmond can download the NVIDIA CUDA libraries and run transcription on the GPU, which is much faster. Without an NVIDIA card it runs on your CPU as usual. (AMD and Intel graphics are not supported for the speed-up yet; the app still works on CPU.)
- **Optional local summaries.** A small on-device model can draft a summary of the transcript, also fully offline.
- **Yours to keep.** Transcripts are saved as plain Markdown files in a folder you choose.

## Before you start

- **Windows SmartScreen.** When you unzip and run it, Windows may say "Windows protected your PC". Click **More info**, then **Run anyway**. The app is unsigned for now.
- **To capture the other side of a call,** pick your **Speakers** as the system audio source. If you listen on Headphones, the sound can echo.
- **First run needs the internet once.** The first launch downloads the transcription model you chose (and the optional summary model and GPU libraries if you pick them). After that, Volksmond runs fully offline.

The Quick Start guide, in English and Afrikaans, is inside the zip.

## Requirements

- Windows 10 or 11, 64-bit.
- The app download is about 150 MB. On first run it fetches your chosen model, from under a gigabyte for the small model up to a few gigabytes for the largest.
- An NVIDIA GPU is optional, and only used if you turn on GPU acceleration.

From the team at DigiPhyte. https://digiphyte.com
