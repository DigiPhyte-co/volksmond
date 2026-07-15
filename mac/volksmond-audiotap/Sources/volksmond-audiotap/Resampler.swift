import Foundation
import AVFoundation
import os

// Resampler converts each captured block from the device format (system rate,
// stereo, float32) to the contracted output format (target rate, mono or stereo,
// interleaved float32 LE) and writes it as one PCM frame on stdout.
//
// It runs synchronously on the Core Audio IO thread. That keeps the helper simple
// (no ring buffer, no cross-thread hand-off): the only consumer is a pipe that the
// Python side drains continuously, and since we are capturing rather than playing,
// a brief stall would delay data, never glitch anything the user hears.
final class Resampler {
    private let converter: AVAudioConverter
    private let outputChannels: Int
    private let outputBuffer: AVAudioPCMBuffer

    // Start gate. Audio callbacks begin the instant AudioDeviceStart runs, which is
    // before we have emitted the "started" control line. Blocks captured before the
    // gate opens are dropped so no PCM frame ever precedes "started" on stdout.
    // OSAllocatedUnfairLock manages a heap-stable lock (safe from the IO thread).
    private let gate = OSAllocatedUnfairLock(initialState: false)

    init?(inputFormat: AVAudioFormat, sampleRate: Int, mono: Bool) {
        outputChannels = mono ? 1 : 2
        guard let outFormat = AVAudioFormat(
            commonFormat: .pcmFormatFloat32,
            sampleRate: Double(sampleRate),
            channels: AVAudioChannelCount(outputChannels),
            interleaved: true) else {
            return nil
        }
        guard let conv = AVAudioConverter(from: inputFormat, to: outFormat) else {
            return nil
        }
        converter = conv

        // Pre-allocate one reusable output buffer sized well above any realistic
        // per-callback block (48 kHz -> 16 kHz turns a ~1024-frame input into ~340
        // output frames; 16384 leaves generous headroom). Reused because the IO
        // callback is serialised, so there is never a concurrent user.
        guard let outBuf = AVAudioPCMBuffer(pcmFormat: outFormat, frameCapacity: 16384) else {
            return nil
        }
        outputBuffer = outBuf
    }

    // Open the gate once "started" has been written. After this, captured blocks
    // are converted and streamed.
    func open() {
        gate.withLock { $0 = true }
    }

    private func isOpen() -> Bool {
        return gate.withLock { $0 }
    }

    // Convert one input block and emit it as a single stdout frame. Called per IO
    // callback. Silently skips empty output (e.g. the converter buffering input
    // across calls during rate conversion).
    func process(_ input: AVAudioPCMBuffer) {
        guard isOpen() else { return }
        guard input.frameLength > 0 else { return }

        outputBuffer.frameLength = outputBuffer.frameCapacity
        var suppliedOnce = false
        var conversionError: NSError?
        let outStatus = converter.convert(to: outputBuffer, error: &conversionError) { _, statusOut in
            if suppliedOnce {
                statusOut.pointee = .noDataNow
                return nil
            }
            suppliedOnce = true
            statusOut.pointee = .haveData
            return input
        }

        if outStatus == .error {
            if let err = conversionError {
                logStderr("resample: conversion error \(err.localizedDescription)")
            }
            return
        }

        let frames = Int(outputBuffer.frameLength)
        guard frames > 0 else { return }

        // Interleaved single-buffer layout: one contiguous run of
        // frames * channels float32 samples. Copy the raw bytes for the frame.
        let byteCount = frames * outputChannels * MemoryLayout<Float>.size
        guard let mData = outputBuffer.audioBufferList.pointee.mBuffers.mData else { return }
        let payload = Data(bytes: mData, count: byteCount)
        StdoutWriter.shared.writeFrame(payload)
    }
}
