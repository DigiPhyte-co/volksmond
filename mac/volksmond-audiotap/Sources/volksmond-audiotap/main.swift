import Foundation

// Entry point. Orchestrates the frozen protocol:
//   1. parse args, echo the format line
//   2. ensure audio-capture permission (exit 2 on denial)
//   3. prepare the process tap and resampler (exit 3 on failure)
//   4. emit "started", begin streaming PCM frames
//   5. on SIGTERM / SIGINT, tear down cleanly and exit 0

// Ignore SIGPIPE process-wide: a closed reader must surface as EPIPE on write(),
// which StdoutWriter treats as a clean stop, not as a fatal signal.
signal(SIGPIPE, SIG_IGN)

// Orderly, idempotent shutdown. Installed BEFORE the blocking permission wait and the
// tap setup, so an early SIGTERM (e.g. during the first-run TCC dialog, before any tap
// exists) still exits 0 via orderly teardown rather than the default kill disposition.
// `activeTap` is nil until the tap is built; teardown before then is a no-op.
var didShutDown = false
let shutdownLock = NSLock()
var activeTap: ProcessTap?
var activeStreamer: FrameStreamer?
func shutdown(_ code: ExitCode) {
    shutdownLock.lock()
    if didShutDown {
        shutdownLock.unlock()
        return
    }
    didShutDown = true
    let tapToStop = activeTap
    let streamerToStop = activeStreamer
    shutdownLock.unlock()
    logStderr("stopping")
    // Preserve teardown order: stop PRODUCING first (device -> IO proc -> aggregate ->
    // tap, inside ProcessTap.stop()), THEN drain and flush the writer thread so the
    // frames already buffered in the ring are written out rather than dropped.
    tapToStop?.stop()
    streamerToStop?.stop()
    exit(code.rawValue)
}

// Route SIGTERM/SIGINT through Dispatch sources on a DEDICATED queue that stays
// runnable even while the main thread is parked on the first-run TCC permission
// semaphore. If these were on .main (blocked during the permission wait) an early
// SIGTERM would not be handled until the human answered the dialog.
signal(SIGTERM, SIG_IGN)
signal(SIGINT, SIG_IGN)
let signalQueue = DispatchQueue(label: "com.digiphyte.volksmond.audiotap.signals")
let termSource = DispatchSource.makeSignalSource(signal: SIGTERM, queue: signalQueue)
termSource.setEventHandler { shutdown(.cleanStop) }
termSource.resume()
let intSource = DispatchSource.makeSignalSource(signal: SIGINT, queue: signalQueue)
intSource.setEventHandler { shutdown(.cleanStop) }
intSource.resume()

// Additive shutdown path (CONTRACT.md section 4): a parent that launches the helper
// with its stdin connected to a PIPE can request shutdown simply by closing that pipe.
// A dedicated reader thread blocks on stdin and, on EOF, runs the SAME orderly
// teardown as SIGTERM (exit 0). This complements, and never replaces, the signal
// handling above. Installed early (like the signal sources) so an stdin-close during
// the first-run permission wait still exits cleanly. Guarded to a pipe/socket so a dev
// run from a terminal, or stdin from /dev/null or a redirected file (all of which
// report EOF immediately), cannot self-terminate the helper at launch. Bytes written
// to stdin are ignored; only EOF is meaningful.
func stdinIsShutdownPipe() -> Bool {
    var st = stat()
    guard fstat(0, &st) == 0 else { return false }
    // Normalise to Int: st_mode is mode_t (UInt16) while S_IF* import as Int32, so
    // masking them directly would be a type mismatch.
    let type = Int(st.st_mode) & Int(S_IFMT)
    return type == Int(S_IFIFO) || type == Int(S_IFSOCK)
}

if stdinIsShutdownPipe() {
    let stdinThread = Thread {
        var buffer = [UInt8](repeating: 0, count: 256)
        while true {
            let n = read(0, &buffer, buffer.count)
            if n > 0 { continue }                    // not part of the contract; ignore
            if n < 0 && errno == EINTR { continue }  // interrupted syscall; retry
            logStderr(n == 0
                ? "stdin closed by parent; shutting down"
                : "stdin read error (errno \(errno)); shutting down")
            shutdown(.cleanStop)
            return
        }
    }
    stdinThread.name = "com.digiphyte.volksmond.audiotap.stdin"
    stdinThread.start()
}

let args = Array(CommandLine.arguments.dropFirst())

let config: Config
do {
    guard let parsed = try CLI.parse(args) else {
        // --help / --version already printed to stderr.
        exit(ExitCode.cleanStop.rawValue)
    }
    config = parsed
} catch CLIError.usage(let message) {
    logStderr("usage error: \(message)")
    logStderr(CLI.usageText)
    exit(ExitCode.usage.rawValue)
} catch {
    logStderr("usage error: \(error)")
    exit(ExitCode.usage.rawValue)
}

// Step 1: echo the negotiated format so the consumer knows the exact rate and
// channel count before any audio arrives.
StdoutWriter.shared.writeControlLine(Control.formatLine(rate: config.sampleRate, channels: config.channels))

// Step 2: permission gate. .unknown (private TCC symbols unavailable) proceeds and
// lets tap creation be the effective gate, which is the safest degrade.
switch AudioCapturePermission.ensure() {
case .denied:
    StdoutWriter.shared.writeControlLine(Control.permissionDenied)
    logStderr("exiting: audio-capture permission denied")
    exit(ExitCode.permissionDenied.rawValue)
case .authorized, .unknown:
    break
}

// Step 3: build the tap and resampler. Kept alive for the process lifetime and
// referenced by the signal handler for orderly teardown. Publish it to `activeTap`
// under the lock so a SIGTERM arriving now tears the tap down instead of leaking it.
let tap = ProcessTap()
shutdownLock.lock()
activeTap = tap
shutdownLock.unlock()
do {
    try tap.prepare()
} catch let error as TapError {
    StdoutWriter.shared.writeControlLine(Control.error(code: "tap_failed", message: error.message))
    logStderr("exiting: \(error.message)")
    // prepare() already rolls back its own partial state (finding M2); call stop()
    // here too for an explicit, idempotent teardown at the failure site.
    tap.stop()
    exit(ExitCode.tapFailed.rawValue)
} catch {
    StdoutWriter.shared.writeControlLine(Control.error(code: "tap_failed", message: "\(error)"))
    logStderr("exiting: \(error)")
    tap.stop()
    exit(ExitCode.tapFailed.rawValue)
}

guard let inputFormat = tap.inputFormat,
      let resampler = Resampler(inputFormat: inputFormat, sampleRate: config.sampleRate, mono: config.mono) else {
    StdoutWriter.shared.writeControlLine(Control.error(code: "tap_failed", message: "could not build resampler for the device format"))
    logStderr("exiting: resampler init failed")
    tap.stop()
    exit(ExitCode.tapFailed.rawValue)
}

// The frame streamer decouples the realtime audio callback from stdout: the callback
// only copies converted PCM into its bounded ring buffer, and a dedicated writer thread
// performs the (possibly blocking) framed stdout writes (finding H2). Publish it under
// the lock so a SIGTERM / stdin-close arriving now tears it down cleanly as well. It is
// started before begin(), but no PCM reaches stdout before "started": the resampler's
// gate (opened below) keeps process() from enqueuing anything until then.
let streamer = FrameStreamer(outputChannels: config.channels)
shutdownLock.lock()
activeStreamer = streamer
shutdownLock.unlock()
resampler.sink = streamer
streamer.start()

tap.onInput = { buffer in
    resampler.process(buffer)
}

// Step 4: begin audio, then announce. begin() first so a start failure exits 3
// WITHOUT ever emitting "started"; the resampler's gate stays closed so no PCM
// frame escapes during the window between begin() and the "started" line.
do {
    try tap.begin()
} catch let error as TapError {
    StdoutWriter.shared.writeControlLine(Control.error(code: "tap_failed", message: error.message))
    logStderr("exiting: \(error.message)")
    tap.stop()
    streamer.stop()
    exit(ExitCode.tapFailed.rawValue)
}
// "started" MUST precede the first PCM frame; after it, stdout carries only binary
// frames until we stop. Opening the gate immediately after lets frames flow.
StdoutWriter.shared.writeControlLine(Control.started)
resampler.open()
logStderr("streaming: \(config.sampleRate) Hz, \(config.channels) ch, f32le")

// Block until shutdown is requested: a SIGTERM/SIGINT handler exits directly on the
// signal queue, and an EPIPE on stdout (possibly from the realtime audio thread) wakes
// us here so the tap is torn down on THIS thread, never from the audio callback.
let requestedCode = ShutdownCoordinator.shared.wait()
shutdown(ExitCode(rawValue: requestedCode) ?? .cleanStop)
