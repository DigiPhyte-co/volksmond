import Foundation
import os

// Monotonic host clock in milliseconds, used only for the STATS diagnostics line.
// Backed by mach_absolute_time scaled through the timebase (numer/denom is 1/1 on
// Apple Silicon, so ticks are already nanoseconds; the scaling keeps it correct if
// this ever runs under Rosetta or on Intel). Dividing by denom before multiplying by
// numer keeps the intermediate small so realistic uptimes cannot overflow.
enum HostClock {
    private static let timebase: mach_timebase_info_data_t = {
        var info = mach_timebase_info_data_t()
        mach_timebase_info(&info)
        // Cannot fail on real hardware, but never let a zero denom trap millis().
        if info.denom == 0 { info.numer = 1; info.denom = 1 }
        return info
    }()

    static func millis() -> Int {
        let ticks = mach_absolute_time()
        let nanos = ticks / UInt64(timebase.denom) &* UInt64(timebase.numer)
        return Int(nanos / 1_000_000)
    }
}

// FrameStreamer decouples the realtime Core Audio IO callback from the (possibly
// slow) stdout writes the wire contract requires (findings H2 and M5).
//
// Producer side (Resampler.process, on the realtime IO thread): copies each already
// converted PCM block into a bounded, preallocated ring of frame slots and returns
// immediately. No allocation, no write(), and the only lock it takes (an
// os_unfair_lock) is held solely for the memcpy plus a few index updates, NEVER
// across a blocking write. So a slow reader on the far end of the pipe can never
// stall the audio thread.
//
// Consumer side: a dedicated writer thread drains the ring, copying each frame out
// under the lock and then performing the framed stdout write with the lock RELEASED,
// so slow I/O is never held against the producer.
//
// Overflow policy: DROP OLDEST. Under sustained backpressure (the parent draining
// stdout slower than audio arrives) the ring fills; the producer then evicts the
// oldest unwritten frame to make room for the freshest one and bumps a dropped-frame
// counter. This bounds latency and memory and keeps the delivered audio as fresh as
// possible; the loss is surfaced out of band on the STATS line, never on stdout.
final class FrameStreamer {
    // One preallocated frame slot: a fixed-capacity raw buffer plus the used length.
    // Raw storage keeps the producer's hot path free of any Array machinery.
    private final class Slot {
        let bytes: UnsafeMutableRawPointer
        var count: Int = 0
        init(capacity: Int) {
            bytes = UnsafeMutableRawPointer.allocate(
                byteCount: capacity, alignment: MemoryLayout<Float>.alignment)
        }
        deinit { bytes.deallocate() }
    }

    private let slotCount: Int
    private let slotCapacity: Int
    private let slots: [Slot]

    // Ring indices and diagnostics counters, all guarded by `lock`.
    private var head = 0        // next slot to read (writer thread)
    private var tail = 0        // next slot to write (producer)
    private var filled = 0      // number of readable slots
    private var seq = 0         // frames produced (enqueued) since start; monotonic
    private var dropped = 0     // frames dropped on overflow since start
    private var overflowPending = false  // set on overflow so the writer emits STATS at once
    private var finishing = false        // shutdown requested; writer drains then exits

    // os_unfair_lock: taken only for the brief memcpy and index updates, NEVER across
    // a blocking write, so the realtime producer never contends with slow I/O. This
    // is the "lock the IO callback never contends on with slow I/O" the review allows.
    private let lock = OSAllocatedUnfairLock()

    // Writer-thread coordination. `work` is signalled when the ring goes empty->non
    // empty (and on stop); its 1 s wait timeout also drives the periodic STATS line.
    // `done` is signalled once the writer loop exits, so stop() can join it (bounded).
    private let work = DispatchSemaphore(value: 0)
    private let done = DispatchSemaphore(value: 0)
    private var writerThread: Thread?
    private var started = false

    // Writer-thread-owned scratch: one frame is copied here under the lock, then
    // written to stdout with the lock released. Sized to the largest possible frame.
    private let scratch: UnsafeMutableRawPointer
    private var lastStatsMs = 0

    // Default ring depth of 32 slots: at a typical ~45-50 Core Audio blocks/sec that is
    // roughly 0.6-0.7 s of buffered audio before drop-oldest kicks in, enough to ride
    // out a scheduling hiccup in the writer thread or the parent without unbounded
    // memory. Each slot is sized to the largest frame the resampler can emit.
    init(outputChannels: Int, slotCount: Int = 32) {
        // A slot must always hold the largest frame the resampler can emit. Its output
        // buffer is capped at 16384 frames, so the ceiling is 16384 * channels * 4 B.
        let maxFrameBytes = 16384 * max(1, outputChannels) * MemoryLayout<Float>.size
        self.slotCapacity = maxFrameBytes
        self.slotCount = max(2, slotCount)
        self.slots = (0..<self.slotCount).map { _ in Slot(capacity: maxFrameBytes) }
        self.scratch = UnsafeMutableRawPointer.allocate(
            byteCount: maxFrameBytes, alignment: MemoryLayout<Float>.alignment)
    }

    deinit { scratch.deallocate() }

    // Start the writer thread. Safe to call once; must precede any enqueue draining.
    func start() {
        lock.lock()
        if started { lock.unlock(); return }
        started = true
        let t = Thread { [weak self] in self?.runWriter() }
        t.name = "com.digiphyte.volksmond.audiotap.writer"
        t.qualityOfService = .userInitiated
        writerThread = t
        lock.unlock()
        t.start()
    }

    // Producer (realtime IO thread). Copy `count` bytes into the ring and return.
    // No allocation, no write; the lock is held only for the memcpy and index math.
    func enqueue(_ src: UnsafeRawPointer, _ count: Int) {
        lock.lock()
        seq += 1
        let wasEmpty = (filled == 0)
        if count > slotCapacity {
            // Cannot happen (slots are sized to the resampler's ceiling) but never
            // overrun a slot: drop this frame and flag the overflow.
            dropped += 1
            overflowPending = true
            lock.unlock()
            // Wake the writer so it reports the drop promptly, even if the ring is idle.
            if wasEmpty { work.signal() }
            return
        }
        if filled == slotCount {
            // Ring full: evict the oldest frame to make room for the freshest one.
            head = (head + 1) % slotCount
            filled -= 1
            dropped += 1
            overflowPending = true
        }
        let slot = slots[tail]
        slot.bytes.copyMemory(from: src, byteCount: count)
        slot.count = count
        tail = (tail + 1) % slotCount
        filled += 1
        lock.unlock()
        // Wake the writer only on the empty->non-empty edge. A sleeping writer went to
        // sleep on an empty ring, so this edge always reaches it; the signal is
        // remembered by the semaphore, so there is no lost-wakeup race with the drain.
        if wasEmpty { work.signal() }
    }

    // Request an orderly stop: drain what is buffered, flush what the pipe will take,
    // and join the writer thread. Bounded so a parent that has stopped reading cannot
    // hang shutdown. Idempotent.
    func stop() {
        lock.lock()
        if finishing { lock.unlock(); return }
        finishing = true
        let hasWriter = (writerThread != nil)
        lock.unlock()
        work.signal()
        if hasWriter {
            _ = done.wait(timeout: .now() + 2.0)
        }
    }

    // Writer thread. Drains frames to stdout off the realtime path and emits STATS.
    private func runWriter() {
        while true {
            _ = work.wait(timeout: .now() + 1.0)   // wake on data or stop; 1 s STATS tick

            // Drain everything currently queued. Copy each frame out under the lock,
            // then write it with the lock RELEASED so the blocking write is never held
            // against the producer.
            while true {
                lock.lock()
                if filled == 0 {
                    lock.unlock()
                    break
                }
                let slot = slots[head]
                let n = slot.count
                scratch.copyMemory(from: slot.bytes, byteCount: n)
                head = (head + 1) % slotCount
                filled -= 1
                lock.unlock()
                StdoutWriter.shared.writeFrameRaw(scratch, n)
            }

            // Snapshot counters and control flags under the lock, emit outside it.
            lock.lock()
            let sSeq = seq
            let sDropped = dropped
            let hadOverflow = overflowPending
            overflowPending = false
            let isFinishing = finishing
            let isEmpty = (filled == 0)
            lock.unlock()

            let nowMs = HostClock.millis()
            if hadOverflow || nowMs - lastStatsMs >= 1000 {
                emitStats(seq: sSeq, dropped: sDropped, hostMs: nowMs)
                lastStatsMs = nowMs
            }

            if isFinishing && isEmpty {
                break
            }
        }
        done.signal()
    }

    // One out-of-band diagnostics line to stderr. Plain ASCII, emitted verbatim
    // (WITHOUT the logStderr "[volksmond-audiotap] " prefix) so the parent can match
    // the exact "STATS " prefix. Never touches stdout (CONTRACT.md 3.1).
    private func emitStats(seq: Int, dropped: Int, hostMs: Int) {
        let line = "STATS seq=\(seq) dropped=\(dropped) host_ms=\(hostMs)\n"
        FileHandle.standardError.write(Data(line.utf8))
    }
}
