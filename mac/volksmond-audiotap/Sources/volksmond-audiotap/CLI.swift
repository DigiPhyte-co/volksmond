import Foundation

// Version string reported by --version. Bump alongside meaningful behaviour
// changes; the Python consumer does not read this, it is a human convenience.
let kHelperVersion = "1.0.0"

// Process exit codes. The frozen contract (CONTRACT.md, plan section 2.2) fixes
// 0 / 2 / 3. 64 (sysexits EX_USAGE) is an added, documented code for argument
// errors that only occur before any streaming and that the Python consumer never
// triggers because it always passes a fixed, valid argv.
enum ExitCode: Int32 {
    case cleanStop = 0        // SIGTERM / SIGINT received, drained, exited
    case permissionDenied = 2 // TCC audio-capture not granted
    case tapFailed = 3        // tap / aggregate / IO-proc creation failed, or pre-14.4 API
    case usage = 64           // bad command-line arguments (EX_USAGE)
}

// Parsed, validated invocation. Matches the frozen CLI: `--sample-rate <int>`
// and `--mono`. `--help` / `--version` are human conveniences only.
struct Config {
    var sampleRate: Int = 16000
    var mono: Bool = false

    // Channel count that ends up in the stdout format line and PCM frames.
    var channels: Int { mono ? 1 : 2 }
}

enum CLI {
    static let usageText = """
    volksmond-audiotap \(kHelperVersion)
    Capture the macOS system audio mix via a Core Audio process tap and stream it
    to stdout under the Volksmond binary contract (see CONTRACT.md).

    Usage:
      volksmond-audiotap [--sample-rate <hz>] [--mono]
      volksmond-audiotap --help | --version

    Options:
      --sample-rate <hz>  Output sample rate in Hz (default 16000). The helper
                          resamples the system rate down to this rate.
      --mono              Downmix to a single channel (default is stereo).
      --help              Print this help to stderr and exit 0.
      --version           Print the version to stderr and exit 0.

    stdout is a machine protocol: one JSON format line, then JSON control events,
    then length-prefixed float32 PCM frames. stderr carries human-readable logs.
    Exit codes: 0 clean stop, 2 permission denied, 3 tap failure, 64 bad arguments.
    """

    // Parse argv (excluding argv[0]). Returns a Config, or nil after printing
    // help/version (caller exits 0), or throws a usage message (caller exits 64).
    static func parse(_ args: [String]) throws -> Config? {
        var config = Config()
        var index = 0
        while index < args.count {
            let arg = args[index]
            switch arg {
            case "--help", "-h":
                logStderr(usageText)
                return nil
            case "--version", "-V":
                logStderr("volksmond-audiotap \(kHelperVersion)")
                return nil
            case "--mono":
                config.mono = true
            case "--sample-rate":
                index += 1
                guard index < args.count else {
                    throw CLIError.usage("--sample-rate requires a value")
                }
                guard let rate = Int(args[index]), rate > 0 else {
                    throw CLIError.usage("--sample-rate must be a positive integer, got '\(args[index])'")
                }
                config.sampleRate = rate
            default:
                // Also accept the joined form --sample-rate=16000 for convenience.
                if let value = joinedValue(arg, flag: "--sample-rate") {
                    guard let rate = Int(value), rate > 0 else {
                        throw CLIError.usage("--sample-rate must be a positive integer, got '\(value)'")
                    }
                    config.sampleRate = rate
                } else {
                    throw CLIError.usage("unknown argument '\(arg)'")
                }
            }
            index += 1
        }
        return config
    }

    private static func joinedValue(_ arg: String, flag: String) -> String? {
        let prefix = flag + "="
        guard arg.hasPrefix(prefix) else { return nil }
        return String(arg.dropFirst(prefix.count))
    }
}

enum CLIError: Error {
    case usage(String)
}
