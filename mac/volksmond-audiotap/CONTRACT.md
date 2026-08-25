# volksmond-audiotap binary contract

This is the frozen wire contract between the `volksmond-audiotap` Swift helper and
its Python consumer (the macOS SYS capture backend). The consumer is coded against
THIS document, not against the Swift source. Anything marked "frozen" is fixed by
the macOS port design; anything marked "decision" is an implementation detail the
helper author fixed here where the design was silent. Do not change a frozen line
without updating both sides.

Target platform: macOS 14.4+, Apple Silicon (arm64) only. arm64 is little-endian,
so all multi-byte integers and floats on the wire are little-endian (see Endianness).

## 1. Invocation (frozen)

```
volksmond-audiotap --sample-rate 16000 --mono
```

The helper captures the whole system audio output mix via a Core Audio process tap,
resamples it to the requested rate, optionally downmixes to mono, and streams it on
stdout. It runs until it receives SIGTERM or SIGINT (or its stdout reader closes).

### CLI flags

| Flag | Meaning | Default |
|---|---|---|
| `--sample-rate <hz>` | Output sample rate in Hz. Positive integer. The helper resamples the system rate (typically 48000) down to this. | `16000` |
| `--mono` | Downmix to a single channel. When absent, output is stereo interleaved. | absent (stereo) |
| `--help`, `-h` | Print usage to stderr, exit 0. Human convenience; never emitted with the protocol. | - |
| `--version`, `-V` | Print version to stderr, exit 0. | - |

Decision: `--sample-rate=16000` (joined form) is also accepted, equivalent to the
space-separated form.

Decision: the production caller passes exactly `--sample-rate 16000 --mono`. At
16000 the Python `_emit` path stores 16000 as the SYS source rate and skips its own
resampling (the helper has already delivered 16 kHz), matching the live-AEC path.

## 2. stdout: two phases (frozen shape, phase boundary is a decision)

stdout carries a control phase then a data phase. The transition is unambiguous:
the control phase is newline-terminated JSON text; the data phase is raw binary and
begins immediately after the `started` event line. Once `started` is seen, NO further
JSON is ever written to stdout.

### 2.1 Control phase (newline-delimited JSON, UTF-8)

Line 1 is ALWAYS the format line, emitted before any audio work:

```json
{"format":"f32le","rate":16000,"channels":1}
```

- `format`: always `"f32le"` (32-bit float, little-endian).
- `rate`: the `--sample-rate` value.
- `channels`: `1` with `--mono`, else `2`.

The consumer should read `rate`/`channels` from this line rather than assuming.

After the format line, exactly one terminal control event follows:

| Event line | Meaning | Then |
|---|---|---|
| `{"event":"started"}` | Tap is live; audio follows. | Switch to data phase (section 2.2). |
| `{"event":"permission_denied"}` | Audio-capture TCC permission not granted. | Process exits 2. No audio. |
| `{"event":"error","code":"tap_failed","message":"..."}` | Tap / aggregate / IO-proc creation failed (includes pre-14.4 API failure). | Process exits 3. No audio. |

Between the format line and the terminal event, the helper MAY emit optional
non-terminal control events. The consumer must tolerate and ignore any control event
it does not recognise (forward compatibility). One such event is defined:

| Event line | Meaning | Then |
|---|---|---|
| `{"event":"waiting_permission"}` | The helper is about to BLOCK on the first-run TCC permission dialog (it has not been granted or denied yet). Emitted after the format line, before `started`. | The dialog can take a human many seconds to answer. A consumer that recognises this should extend its handshake deadline (it will still receive `started` on grant, or `permission_denied` on denial); a consumer that does not recognise it ignores it. No audio has started. |

`waiting_permission` is OPTIONAL and additive: it is emitted only on the interactive
first-run request path, never when permission is already granted or denied, and never
more than once. It is not a terminal event and does not change the exit codes.

Notes:
- Each control line is a complete JSON object terminated by a single `\n`. The
  consumer should read the format line first, then read lines until it sees a
  terminal event, tolerating any unrecognised (e.g. future non-terminal) event.
- `message` in the error event is a human string for logs only; branch on `code`,
  not on `message`. The only `code` currently emitted is `tap_failed`.
- Decision: key order is fixed as shown, but the consumer must parse as JSON and
  not rely on byte-exact key order.

### 2.2 Data phase (length-prefixed binary frames)

After `{"event":"started"}`, stdout is a continuous stream of frames:

```
[uint32 LE byte count N][N bytes: float32 LE samples]
```

- The 4-byte little-endian unsigned length `N` is the number of PCM BYTES that
  follow (not sample count, not frame count). Samples = N / 4. Frames (sample sets)
  = N / (4 * channels).
- Samples are 32-bit IEEE-754 floats, little-endian, nominally in [-1.0, 1.0].
- For stereo, samples are interleaved L, R, L, R, ... For mono, one channel.
- Decision: frame sizes are VARIABLE (one frame per Core Audio callback block after
  resampling, so typically a few hundred samples). The consumer MUST read exactly N
  bytes per frame and never assume a fixed frame size.
- Frames continue until the stream ends (clean stop, section 4) at which point
  stdout reaches EOF.
- Backpressure: internally the helper buffers frames in a small bounded ring and
  drains it on a dedicated writer thread, so a slow reader can never stall the
  realtime audio callback. If the consumer reads stdout slower than audio is produced
  for long enough, the ring overflows and the helper DROPS whole frames (oldest first)
  to bound latency and memory. This does not change the wire format: every frame that
  IS delivered remains intact and correctly length-prefixed, and drops only ever fall
  on frame boundaries (never a partial frame). Drops are counted and reported out of
  band on the STATS line (section 3.1); the consumer should drain stdout promptly.

## 3. stderr (frozen intent)

Human-readable diagnostics only, one line per message, each prefixed
`[volksmond-audiotap] `. Never machine-parsed. Safe to log or discard. Nothing on
stderr is part of the stdout protocol.

### 3.1 STATS diagnostics line (additive, stderr only)

As an ADDITIVE, optional diagnostics channel that NEVER touches stdout or the audio
frame format, the helper emits a single-line throughput/health report to stderr at
most about once per second, and immediately whenever it drops a frame (section 2.2
backpressure). The line is plain ASCII and is emitted VERBATIM, i.e. WITHOUT the
`[volksmond-audiotap] ` prefix, so a consumer can match it on the exact `STATS `
prefix:

```
STATS seq=<int> dropped=<int> host_ms=<int>
```

- `seq`: total PCM frames the helper has produced (resampled) since start. Monotonic,
  non-decreasing.
- `dropped`: total frames dropped so far because the internal ring overflowed (the
  consumer drained stdout slower than audio arrived). `seq - dropped` is the number of
  frames that reached stdout. A non-zero and rising `dropped` means the consumer is
  not keeping up.
- `host_ms`: a monotonic host-clock timestamp in milliseconds (derived from
  `mach_absolute_time`). Not wall-clock and not comparable across processes; only
  differences between STATS lines from the same run are meaningful.

Parsing STATS is OPTIONAL. A consumer that ignores stderr entirely is unaffected, and
this line never appears on stdout and never alters the binary frame format in section
2.2. It is the ONLY machine-readable line on stderr; every other stderr line keeps the
`[volksmond-audiotap] ` prefix and is free-form.

## 4. Shutdown and exit codes (frozen)

The helper stops on:
- SIGTERM or SIGINT: tears down the tap and aggregate device, stops writing, exits 0.
- stdout reader closes (write returns EPIPE): treated as a clean stop, exits 0.
- stdin EOF (additive): if the consumer launches the helper with its stdin connected
  to a PIPE and later closes that pipe, the resulting EOF is treated as a shutdown
  request IDENTICAL to SIGTERM (tears down the tap/aggregate, drains and flushes the
  frames already buffered, exits 0). This is an additive convenience so a parent can
  stop the helper without POSIX signals; it never replaces SIGTERM/SIGINT, which keep
  working unchanged. The helper only watches stdin when it is a pipe or socket, so a
  dev run from a terminal, or stdin taken from `/dev/null` or a redirected file (which
  report EOF immediately), does NOT self-terminate the helper at launch. Any bytes the
  consumer writes to stdin are ignored; only the EOF is meaningful.

| Code | Meaning |
|---|---|
| `0` | Clean stop (SIGTERM/SIGINT, consumer closed stdout, or consumer closed the stdin pipe). |
| `2` | Permission denied (TCC audio-capture not granted). Preceded by the `permission_denied` event. |
| `3` | Tap creation failed: pre-14.4 API, aggregate/IO-proc failure, or unusable device format. Preceded by the `error`/`tap_failed` event. |
| `64` | Decision (EX_USAGE): bad command-line arguments. Emitted before any stdout protocol output; the production caller never triggers it. |

To stop the helper cleanly, the consumer sends SIGTERM (or, equivalently, closes the
helper's stdin pipe) and reads stdout to EOF. The consumer should NOT expect a
trailing JSON line at shutdown (the data phase is pure binary; a trailing JSON line
would corrupt the stream, so there is none).

## 5. Permission behaviour (frozen intent, hardware-verified later)

- The helper requests the audio-capture TCC permission
  (`NSAudioCaptureUsageDescription`) the first time it runs. If already authorized,
  no prompt appears. If denied, it emits `permission_denied` and exits 2.
- When shipped inside `Volksmond.app` (`Contents/Resources/bin/`) and signed as part
  of the bundle, the prompt is EXPECTED to name the parent app (Volksmond), and the
  grant persists across relaunches. This roll-up is unverified until run on real Mac
  hardware (port plan risk R1) and is marked `TODO(mac-hw)` in the source.
- No Screen Recording permission is used or requested (that is the whole point of
  using process taps rather than ScreenCaptureKit).

## 6. Dev vs bundled invocation (frozen)

- Bundled: `Volksmond.app/Contents/Resources/bin/volksmond-audiotap`.
- Dev fallback: the Python side honours the `VOLKSMOND_AUDIOTAP` environment variable
  pointing at a locally built binary (e.g. `.build/release/volksmond-audiotap`).

## 7. Endianness (decision, follows from arm64-only)

v1 targets Apple Silicon only, which is little-endian, so `f32le` and the `uint32 LE`
length prefix are the host byte order and require no swapping on either side. If a
big-endian target is ever added, this contract's `le` suffixes become load-bearing
and both sides must honour them explicitly.
