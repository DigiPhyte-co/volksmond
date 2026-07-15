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
// referenced by the signal handler for orderly teardown.
let tap = ProcessTap()
do {
    try tap.prepare()
} catch let error as TapError {
    StdoutWriter.shared.writeControlLine(Control.error(code: "tap_failed", message: error.message))
    logStderr("exiting: \(error.message)")
    exit(ExitCode.tapFailed.rawValue)
} catch {
    StdoutWriter.shared.writeControlLine(Control.error(code: "tap_failed", message: "\(error)"))
    logStderr("exiting: \(error)")
    exit(ExitCode.tapFailed.rawValue)
}

guard let inputFormat = tap.inputFormat,
      let resampler = Resampler(inputFormat: inputFormat, sampleRate: config.sampleRate, mono: config.mono) else {
    StdoutWriter.shared.writeControlLine(Control.error(code: "tap_failed", message: "could not build resampler for the device format"))
    logStderr("exiting: resampler init failed")
    tap.stop()
    exit(ExitCode.tapFailed.rawValue)
}

tap.onInput = { buffer in
    resampler.process(buffer)
}

// Orderly shutdown, idempotent. Wired to SIGTERM/SIGINT below.
var didShutDown = false
let shutdownLock = NSLock()
func shutdown(_ code: ExitCode) {
    shutdownLock.lock()
    if didShutDown {
        shutdownLock.unlock()
        return
    }
    didShutDown = true
    shutdownLock.unlock()
    logStderr("stopping")
    tap.stop()
    exit(code.rawValue)
}

// Default disposition would kill us before teardown; ignore it and route through
// Dispatch sources so the tap and aggregate device are always destroyed.
signal(SIGTERM, SIG_IGN)
signal(SIGINT, SIG_IGN)
let termSource = DispatchSource.makeSignalSource(signal: SIGTERM, queue: .main)
termSource.setEventHandler { shutdown(.cleanStop) }
termSource.resume()
let intSource = DispatchSource.makeSignalSource(signal: SIGINT, queue: .main)
intSource.setEventHandler { shutdown(.cleanStop) }
intSource.resume()

// Step 4: begin audio, then announce. begin() first so a start failure exits 3
// WITHOUT ever emitting "started"; the resampler's gate stays closed so no PCM
// frame escapes during the window between begin() and the "started" line.
do {
    try tap.begin()
} catch let error as TapError {
    StdoutWriter.shared.writeControlLine(Control.error(code: "tap_failed", message: error.message))
    logStderr("exiting: \(error.message)")
    tap.stop()
    exit(ExitCode.tapFailed.rawValue)
}
// "started" MUST precede the first PCM frame; after it, stdout carries only binary
// frames until we stop. Opening the gate immediately after lets frames flow.
StdoutWriter.shared.writeControlLine(Control.started)
resampler.open()
logStderr("streaming: \(config.sampleRate) Hz, \(config.channels) ch, f32le")

// Run until a signal handler calls shutdown().
dispatchMain()
