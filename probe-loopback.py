"""Diagnostic: probe every WASAPI loopback device with a matrix of open
parameters and report which combination (if any) actually opens.

The v1.0.x app fails to start audio capture with `[Errno -9996] Invalid device`
on the system-audio (loopback) source on Sean's laptop, in BOTH the packaged
exe AND from source. This tries each loopback with paFloat32 + paInt16, at
the device's native rate AND 44.1/48 kHz, mono AND device-channel-count, to
narrow down which dimension is the problem.

Run:  python probe-loopback.py    (from the project root)
Reads only; opens streams briefly and closes them. No audio is captured or saved.
"""
import sys
import traceback

import pyaudiowpatch as pa


def open_and_close(p, info, fmt, channels, rate):
    """Try opening a stream with these params; return (ok, err_str)."""
    try:
        stream = p.open(
            format=fmt,
            channels=channels,
            rate=rate,
            input=True,
            input_device_index=info["index"],
            frames_per_buffer=int(rate * 0.5),
        )
        try:
            stream.stop_stream()
        except Exception:
            pass
        try:
            stream.close()
        except Exception:
            pass
        return True, None
    except Exception as e:
        return False, repr(e)


def main():
    p = pa.PyAudio()
    print(f"pyaudiowpatch: {getattr(pa, '__version__', 'unknown')}")
    print()

    # Default mic + default loopback first.
    try:
        wasapi = p.get_host_api_info_by_type(pa.paWASAPI)
        print(f"WASAPI host: index={wasapi['index']} name={wasapi['name']!r} "
              f"defaultOutput={wasapi['defaultOutputDevice']}")
    except Exception as e:
        print(f"WASAPI host lookup FAILED: {e!r}")

    try:
        default_lb = p.get_default_wasapi_loopback()
        print(f"Default loopback: index={default_lb['index']} name={default_lb['name']!r} "
              f"rate={int(default_lb['defaultSampleRate'])} ch={default_lb['maxInputChannels']}")
    except Exception as e:
        print(f"get_default_wasapi_loopback FAILED: {e!r}")
    print()

    # Enumerate all loopback devices, probe each with a matrix.
    loopbacks = list(p.get_loopback_device_info_generator())
    print(f"Found {len(loopbacks)} loopback device(s).")
    print()

    formats = [("paFloat32", pa.paFloat32), ("paInt16", pa.paInt16)]
    for info in loopbacks:
        print(f"=== device #{info['index']}: {info['name']} ===")
        print(f"    defaultSampleRate={int(info['defaultSampleRate'])}  "
              f"maxInputChannels={info['maxInputChannels']}  "
              f"hostApi={info['hostApi']}  isLoopback={info.get('isLoopbackDevice')}")

        native_rate = int(info["defaultSampleRate"])
        native_ch = max(1, int(info["maxInputChannels"]))
        rates = sorted({native_rate, 48000, 44100})
        channels_options = sorted({native_ch, 2, 1})

        for fname, fmt in formats:
            for rate in rates:
                for ch in channels_options:
                    ok, err = open_and_close(p, info, fmt, ch, rate)
                    marker = "OK " if ok else "FAIL"
                    detail = "" if ok else f" -- {err}"
                    print(f"    [{marker}] fmt={fname} rate={rate} ch={ch}{detail}")
        print()

    p.terminate()


if __name__ == "__main__":
    try:
        main()
    except Exception:
        traceback.print_exc()
        sys.exit(1)
