import Foundation

// Coordinates orderly shutdown from any thread. The realtime audio (tap) thread and
// the write path must NEVER stop the tap or call exit() themselves: stopping Core
// Audio from inside its own IO callback can deadlock. Instead they request shutdown
// here; the main thread blocks on wait() and performs teardown + exit on itself.
final class ShutdownCoordinator {
    static let shared = ShutdownCoordinator()

    private let semaphore = DispatchSemaphore(value: 0)
    private let lock = NSLock()
    private var requested = false
    private var code: Int32 = ExitCode.cleanStop.rawValue

    private init() {}

    // Request shutdown with an exit code. Idempotent; the first request wins the code.
    // Safe from a signal handler or the realtime audio thread (no tap teardown here).
    func request(_ exitCode: Int32) {
        lock.lock()
        if requested {
            lock.unlock()
            return
        }
        requested = true
        code = exitCode
        lock.unlock()
        semaphore.signal()
    }

    // Block the calling (main) thread until shutdown is requested; return the code.
    func wait() -> Int32 {
        semaphore.wait()
        lock.lock()
        let c = code
        lock.unlock()
        return c
    }
}

// Human-readable diagnostics go to stderr, one line at a time. stdout is reserved
// entirely for the machine protocol (StdoutWriter), so nothing here ever touches it.
func logStderr(_ message: String) {
    let line = "[volksmond-audiotap] " + message + "\n"
    if let data = line.data(using: .utf8) {
        FileHandle.standardError.write(data)
    }
}

// StdoutWriter owns file descriptor 1 and enforces the frozen wire contract:
//   Phase 1 (control): newline-terminated JSON objects. The format line first,
//     then control events. Ends at exactly one terminal event: "started" (proceed
//     to phase 2), "permission_denied", or "error".
//   Phase 2 (data): length-prefixed PCM frames, [uint32 LE byte count][bytes],
//     until EOF / clean stop. No JSON is ever emitted on stdout after "started".
//
// The helper targets Apple Silicon (arm64, little-endian), so host-order UInt32
// and Float bytes are already little-endian; we still write UInt32 via its
// littleEndian representation to make the contract explicit and byte-order safe.
final class StdoutWriter {
    static let shared = StdoutWriter()

    private let lock = NSLock()
    private let fd: Int32 = 1

    private init() {}

    // Emit a control-phase JSON line. Built as an explicit string so key order and
    // formatting match CONTRACT.md verbatim; the consumer parses JSON either way.
    func writeControlLine(_ json: String) {
        lock.lock()
        defer { lock.unlock() }
        var line = json
        line.append("\n")
        writeAllLocked(Array(line.utf8))
    }

    // Emit one PCM frame: a uint32 LE byte count followed by that many bytes of
    // interleaved float32 LE samples.
    func writeFrame(_ payload: Data) {
        lock.lock()
        defer { lock.unlock() }
        var header = UInt32(payload.count).littleEndian
        withUnsafeBytes(of: &header) { headerBytes in
            writeAllLocked(Array(headerBytes))
        }
        payload.withUnsafeBytes { (raw: UnsafeRawBufferPointer) in
            if let base = raw.baseAddress {
                writeAllRawLocked(base, raw.count)
            }
        }
    }

    // Robust write-all over an array of bytes. Retries on EINTR. If the reader has
    // gone away (EPIPE), the consumer closed the pipe, which we treat as a clean
    // stop: this can run on the realtime audio thread, so we REQUEST shutdown and
    // let the main thread tear down, never exit() from here.
    private func writeAllLocked(_ bytes: [UInt8]) {
        bytes.withUnsafeBytes { raw in
            if let base = raw.baseAddress {
                writeAllRawLocked(base, raw.count)
            }
        }
    }

    private func writeAllRawLocked(_ base: UnsafeRawPointer, _ count: Int) {
        var offset = 0
        while offset < count {
            let n = write(fd, base.advanced(by: offset), count - offset)
            if n > 0 {
                offset += n
                continue
            }
            if n < 0 {
                let err = errno
                if err == EINTR { continue }
                if err == EPIPE {
                    // Consumer closed the read end. Do NOT exit() or stop the tap from
                    // here: this may be the realtime audio thread, where that can
                    // deadlock. Request an orderly shutdown; the main loop tears down
                    // the tap and exits 0. SIGPIPE itself is ignored in main().
                    ShutdownCoordinator.shared.request(ExitCode.cleanStop.rawValue)
                    return
                }
                // Any other write error is unrecoverable for a streaming helper. Route
                // through the same request path (no exit() from a possible audio thread).
                logStderr("fatal: stdout write failed (errno \(err))")
                ShutdownCoordinator.shared.request(ExitCode.cleanStop.rawValue)
                return
            }
            // n == 0: nothing written and no error; avoid a busy spin.
            break
        }
    }
}

// Control-line builders. Kept as explicit strings to match the contract exactly.
enum Control {
    static func formatLine(rate: Int, channels: Int) -> String {
        return "{\"format\":\"f32le\",\"rate\":\(rate),\"channels\":\(channels)}"
    }

    static let started = "{\"event\":\"started\"}"
    static let permissionDenied = "{\"event\":\"permission_denied\"}"
    // Optional pre-started event: emitted immediately before the helper blocks on the
    // first-run TCC dialog, so the consumer can extend its handshake deadline instead
    // of degrading to mic-only when a human is slow to grant permission (CONTRACT.md 2.1).
    static let waitingPermission = "{\"event\":\"waiting_permission\"}"

    static func error(code: String, message: String) -> String {
        return "{\"event\":\"error\",\"code\":\"\(escape(code))\",\"message\":\"\(escape(message))\"}"
    }

    // Minimal JSON string escaper: enough for our short, ASCII diagnostic messages.
    private static func escape(_ s: String) -> String {
        var out = ""
        out.reserveCapacity(s.count)
        for ch in s.unicodeScalars {
            switch ch {
            case "\"": out += "\\\""
            case "\\": out += "\\\\"
            case "\n": out += "\\n"
            case "\r": out += "\\r"
            case "\t": out += "\\t"
            default:
                if ch.value < 0x20 {
                    out += String(format: "\\u%04x", ch.value)
                } else {
                    out.unicodeScalars.append(ch)
                }
            }
        }
        return out
    }
}
