import Foundation
import CoreAudio
import AVFoundation

// Errors raised while standing up the Core Audio process tap. All map to exit
// code 3 (tap failure) at the call site.
enum TapError: Error {
    case createTap(OSStatus)
    case tapUID(OSStatus)
    case createAggregate(OSStatus)
    case streamFormat(OSStatus)
    case badStreamFormat
    case createIOProc(OSStatus)
    case start(OSStatus)

    var message: String {
        switch self {
        case .createTap(let s): return "AudioHardwareCreateProcessTap failed (OSStatus \(s))"
        case .tapUID(let s): return "reading tap UID failed (OSStatus \(s))"
        case .createAggregate(let s): return "AudioHardwareCreateAggregateDevice failed (OSStatus \(s))"
        case .streamFormat(let s): return "reading aggregate stream format failed (OSStatus \(s))"
        case .badStreamFormat: return "aggregate stream format is not float32 PCM"
        case .createIOProc(let s): return "AudioDeviceCreateIOProcIDWithBlock failed (OSStatus \(s))"
        case .start(let s): return "AudioDeviceStart failed (OSStatus \(s))"
        }
    }
}

// ProcessTap owns the lifecycle of a system-wide Core Audio process tap plus the
// private aggregate device that surfaces the tapped audio to an IO proc. It hands
// each captured block, wrapped as a no-copy AVAudioPCMBuffer, to `onInput`.
final class ProcessTap {
    private var tapID: AudioObjectID = AudioObjectID(kAudioObjectUnknown)
    private var aggregateID: AudioObjectID = AudioObjectID(kAudioObjectUnknown)
    private var ioProcID: AudioDeviceIOProcID?

    // The device-side input format (system rate, channel count, float32). Valid
    // only after start() succeeds. The consumer's converter is built from this.
    private(set) var inputFormat: AVAudioFormat!

    // Called on the Core Audio IO thread for each captured block. Set before start().
    // The buffer is a no-copy view valid only for the duration of the call.
    var onInput: ((AVAudioPCMBuffer) -> Void)?

    // prepare() builds the tap, aggregate and IO proc and reads inputFormat, but
    // does NOT start audio. The caller then builds its converter from inputFormat,
    // sets onInput, emits the "started" control line, and finally calls begin().
    // Splitting the two guarantees no captured block is dropped before onInput is
    // wired and no PCM frame is emitted before the "started" event.
    func prepare() throws {
        // 1. Describe a private, global stereo tap that excludes no processes, so
        //    it captures the full system output mix. Downmix to mono, if requested,
        //    happens later in the resampling converter, not here.
        // TODO(mac-hw): confirm an empty exclude list yields the whole-system mix
        //    (every app) and that a bundled helper is permitted to create the tap.
        let tapDescription = CATapDescription(stereoGlobalTapButExcludeProcesses: [])
        tapDescription.name = "Volksmond system audio tap"
        tapDescription.isPrivate = true
        tapDescription.muteBehavior = .unmuted // never mute what the user is hearing

        var newTapID = AudioObjectID(kAudioObjectUnknown)
        let tapStatus = AudioHardwareCreateProcessTap(tapDescription, &newTapID)
        guard tapStatus == noErr, newTapID != AudioObjectID(kAudioObjectUnknown) else {
            throw TapError.createTap(tapStatus)
        }
        tapID = newTapID

        // Transactional from here on (finding M2): the tap now exists, so if ANY later
        // step throws (reading the UID, creating the aggregate, validating the stream
        // format, creating the IO proc) we must tear down what we have already acquired
        // before propagating, or the tap/aggregate would leak in the OS. stop() undoes
        // the tap, aggregate and IO proc in the correct reverse order and is idempotent,
        // so the caller and the shutdown path may still call it again harmlessly.
        var committed = false
        defer { if !committed { stop() } }

        // 2. Read the tap's UID so we can reference it from the aggregate device.
        let tapUID = try readTapUID(tapID)

        // 3. Create a private aggregate device whose only member is our tap. Auto-
        //    starting the tap means audio flows as soon as the device starts.
        let aggregateUID = UUID().uuidString
        let description: [String: Any] = [
            kAudioAggregateDeviceNameKey: "Volksmond Aggregate Tap",
            kAudioAggregateDeviceUIDKey: aggregateUID,
            kAudioAggregateDeviceIsPrivateKey: true,
            kAudioAggregateDeviceIsStackedKey: false,
            kAudioAggregateDeviceTapAutoStartKey: true,
            kAudioAggregateDeviceSubDeviceListKey: [[String: Any]](),
            kAudioAggregateDeviceTapListKey: [
                [
                    kAudioSubTapUIDKey: tapUID,
                    kAudioSubTapDriftCompensationKey: true,
                ]
            ],
        ]
        var newAggregateID = AudioObjectID(kAudioObjectUnknown)
        let aggStatus = AudioHardwareCreateAggregateDevice(description as CFDictionary, &newAggregateID)
        guard aggStatus == noErr, newAggregateID != AudioObjectID(kAudioObjectUnknown) else {
            throw TapError.createAggregate(aggStatus)
        }
        aggregateID = newAggregateID

        // 4. Read the aggregate's input stream format so we can build a matching
        //    AVAudioFormat for the no-copy wrap and the downstream converter.
        // TODO(mac-hw): confirm the runtime ASBD is interleaved float32 as assumed
        //    (channel count and sample rate are read dynamically, so those adapt).
        let asbd = try readInputStreamFormat(aggregateID)
        guard asbd.mFormatID == kAudioFormatLinearPCM,
              (asbd.mFormatFlags & kAudioFormatFlagIsFloat) != 0 else {
            throw TapError.badStreamFormat
        }
        var mutableASBD = asbd
        guard let format = AVAudioFormat(streamDescription: &mutableASBD) else {
            throw TapError.badStreamFormat
        }
        inputFormat = format
        logStderr("tap: input format \(Int(asbd.mSampleRate)) Hz, \(asbd.mChannelsPerFrame) ch, "
            + ((asbd.mFormatFlags & kAudioFormatFlagIsNonInterleaved) != 0 ? "non-interleaved" : "interleaved"))

        // 5. Install the IO proc. Passing nil for the queue lets Core Audio drive
        //    it on its own realtime thread; the block is serialised (one at a time).
        var newProcID: AudioDeviceIOProcID?
        let ioStatus = AudioDeviceCreateIOProcIDWithBlock(&newProcID, aggregateID, nil) {
            [weak self] (_, inInputData, _, _, _) in
            guard let self = self, let handler = self.onInput else { return }
            guard let buffer = AVAudioPCMBuffer(pcmFormat: self.inputFormat, bufferListNoCopy: inInputData, deallocator: nil) else {
                return
            }
            handler(buffer)
        }
        guard ioStatus == noErr, let procID = newProcID else {
            throw TapError.createIOProc(ioStatus)
        }
        ioProcID = procID

        // All resources acquired: commit so the rollback defer above becomes a no-op.
        committed = true
    }

    // Start audio flowing. Callbacks begin immediately, so onInput must be set and
    // the "started" control line emitted before this returns.
    func begin() throws {
        guard aggregateID != AudioObjectID(kAudioObjectUnknown), let procID = ioProcID else {
            throw TapError.start(OSStatus(kAudioHardwareNotRunningError))
        }
        let startStatus = AudioDeviceStart(aggregateID, procID)
        guard startStatus == noErr else {
            throw TapError.start(startStatus)
        }
    }

    // Tear down in reverse order. Safe to call more than once and safe if start()
    // only partially completed (each id is guarded).
    func stop() {
        if aggregateID != AudioObjectID(kAudioObjectUnknown), let procID = ioProcID {
            AudioDeviceStop(aggregateID, procID)
            AudioDeviceDestroyIOProcID(aggregateID, procID)
            ioProcID = nil
        }
        if aggregateID != AudioObjectID(kAudioObjectUnknown) {
            AudioHardwareDestroyAggregateDevice(aggregateID)
            aggregateID = AudioObjectID(kAudioObjectUnknown)
        }
        if tapID != AudioObjectID(kAudioObjectUnknown) {
            AudioHardwareDestroyProcessTap(tapID)
            tapID = AudioObjectID(kAudioObjectUnknown)
        }
    }

    // MARK: - Property reads

    private func readTapUID(_ tap: AudioObjectID) throws -> CFString {
        var address = AudioObjectPropertyAddress(
            mSelector: kAudioTapPropertyUID,
            mScope: kAudioObjectPropertyScopeGlobal,
            mElement: kAudioObjectPropertyElementMain)
        var size = UInt32(MemoryLayout<CFString>.size)
        var uid: CFString = "" as CFString
        let status = AudioObjectGetPropertyData(tap, &address, 0, nil, &size, &uid)
        guard status == noErr else { throw TapError.tapUID(status) }
        return uid
    }

    private func readInputStreamFormat(_ device: AudioObjectID) throws -> AudioStreamBasicDescription {
        var address = AudioObjectPropertyAddress(
            mSelector: kAudioDevicePropertyStreamFormat,
            mScope: kAudioObjectPropertyScopeInput,
            mElement: 0)
        var asbd = AudioStreamBasicDescription()
        var size = UInt32(MemoryLayout<AudioStreamBasicDescription>.size)
        let status = AudioObjectGetPropertyData(device, &address, 0, nil, &size, &asbd)
        guard status == noErr else { throw TapError.streamFormat(status) }
        return asbd
    }
}
