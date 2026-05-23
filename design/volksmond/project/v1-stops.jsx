// v1-stops.jsx — third-pass additions to the stop controls:
// 1. The three-way Stop menu when both recording and transcription are running.
// 2. A simpler single-Stop when there's no recording.
// 3. A richer falling-behind banner that surfaces "stop transcription, keep
//    recording" prominently, so the user can fall back to recording.

// ─── The three-way stop menu (popover under the Stop button) ──────────────
function StopMenu({ recording = true, recommended = "all" }) {
  // recommended: 'transcript-only' (surfaced under struggling banner) | 'all'
  if (!recording) {
    return (
      <div style={{
        width: 280, padding: 6, borderRadius: 10,
        background: "var(--surface)",
        border: "1px solid var(--line)",
        boxShadow: "0 10px 30px rgba(0,0,0,0.10), 0 1px 2px rgba(0,0,0,0.05)",
      }}>
        <StopRow
          tone="default"
          title="Stop and save"
          sub="Save the transcript and close the session."
          kbd="⌘ ↵"
        />
      </div>
    );
  }
  return (
    <div style={{
      width: 360, padding: 8, borderRadius: 12,
      background: "var(--surface)",
      border: "1px solid var(--line)",
      boxShadow: "0 12px 36px rgba(0,0,0,0.12), 0 1px 3px rgba(0,0,0,0.06)",
      display: "flex", flexDirection: "column", gap: 4,
    }}>
      <div style={{
        padding: "8px 10px 6px", fontSize: 10.5, fontWeight: 600,
        letterSpacing: 0.08, textTransform: "uppercase", color: "var(--ink-3)",
      }}>
        You have recording and transcription on
      </div>

      <StopRow
        tone={recommended === "transcript-only" ? "recommended" : "default"}
        title="Stop transcription, keep recording"
        sub="Falls back to a quiet recording. Transcribe and summarise it after the meeting."
        kbd="T"
        recommended={recommended === "transcript-only"}
      />
      <StopRow
        tone="default"
        title="Stop recording, keep transcribing"
        sub="The live transcript continues. Nothing more is saved as audio."
        kbd="R"
      />
      <div style={{ height: 1, background: "var(--line)", margin: "6px 0" }} />
      <StopRow
        tone="finish"
        title="Stop recording and transcription"
        sub="End the session. Saves what you have. A clean second pass becomes available."
        kbd="⌘ ↵"
      />
    </div>
  );
}

function StopRow({ title, sub, kbd, tone, recommended }) {
  // tone: 'default' | 'recommended' | 'finish'
  const borderColor = recommended
    ? "var(--accent)"
    : "transparent";
  const bg = recommended
    ? "color-mix(in oklch, var(--accent) 8%, var(--surface))"
    : "transparent";
  return (
    <div style={{
      padding: "10px 12px",
      borderRadius: 8,
      border: `1px solid ${borderColor}`,
      background: bg,
      display: "grid", gridTemplateColumns: "auto 1fr auto",
      columnGap: 12, alignItems: "flex-start",
      cursor: "default",
    }}>
      <span style={{ marginTop: 3 }}>
        {tone === "finish" ? (
          <svg width="14" height="14" viewBox="0 0 14 14" style={{ color: "var(--ink-2)" }}>
            <rect x="3" y="3" width="8" height="8" rx="1.2" fill="currentColor" />
          </svg>
        ) : tone === "recommended" ? (
          <svg width="14" height="14" viewBox="0 0 14 14" style={{ color: "var(--accent)" }}>
            <circle cx="7" cy="7" r="6" fill="none" stroke="currentColor" strokeWidth="1.4" />
            <path d="M4 7 L6.4 9.4 L10 5" fill="none" stroke="currentColor" strokeWidth="1.6"
              strokeLinecap="round" strokeLinejoin="round" />
          </svg>
        ) : (
          <svg width="14" height="14" viewBox="0 0 14 14" style={{ color: "var(--ink-3)" }}>
            <rect x="3" y="3" width="8" height="8" rx="1.2" fill="none" stroke="currentColor" strokeWidth="1.3"/>
          </svg>
        )}
      </span>
      <div>
        <div style={{ fontSize: 13, fontWeight: 600, color: "var(--ink)",
          display: "flex", alignItems: "center", gap: 8 }}>
          {title}
          {recommended && (
            <span className="chip" style={{ height: 16, fontSize: 9.5,
              background: "var(--accent-soft)", color: "var(--accent)",
              border: "none" }}>Recommended</span>
          )}
        </div>
        <div style={{ fontSize: 11.5, color: "var(--ink-3)", marginTop: 3, lineHeight: 1.5 }}>
          {sub}
        </div>
      </div>
      <span style={{ display: "inline-flex", gap: 2, marginTop: 4 }}>
        {kbd.split(" ").map((k) => <kbd key={k}>{k}</kbd>)}
      </span>
    </div>
  );
}

// ─── Anchored version · the menu pinned under a "Stop" button in context ─
function StopPopoverDemo({ recommended }) {
  return (
    <div style={{
      flex: 1, padding: "32px", display: "flex",
      alignItems: "center", justifyContent: "center", gap: 32, flexWrap: "wrap",
    }}>
      <div style={{ display: "flex", flexDirection: "column", alignItems: "center", gap: 14 }}>
        <button className="btn primary" style={{ padding: "0 16px", height: 36 }}>
          <svg width="11" height="11" viewBox="0 0 11 11"><rect x="2" y="2" width="7" height="7" rx="1" fill="currentColor" /></svg>
          Stop
          <svg width="10" height="10" viewBox="0 0 10 10" style={{ opacity: 0.7 }}>
            <path d="M2 4 L5 7 L8 4" fill="none" stroke="currentColor" strokeWidth="1.4" strokeLinecap="round" strokeLinejoin="round" />
          </svg>
        </button>
        <StopMenu recording={true} recommended={recommended} />
      </div>
    </div>
  );
}

// ════════════════════════════════════════════════════════════════════════════
// FALLING-BEHIND banner, V1 version · surfaces the fallback action prominently
// ════════════════════════════════════════════════════════════════════════════
function FallingBehindBannerV1({ recording = true }) {
  return (
    <div style={{
      margin: "12px 24px 0",
      padding: "14px 16px",
      borderRadius: 12,
      background: "var(--warn-soft)",
      border: "1px solid color-mix(in oklch, var(--warn) 40%, var(--line))",
      display: "flex", flexDirection: "column", gap: 12,
    }}>
      <div style={{ display: "flex", alignItems: "flex-start", gap: 12 }}>
        <svg width="16" height="16" viewBox="0 0 16 16" style={{ marginTop: 2, color: "var(--warn)", flex: "0 0 auto" }}>
          <circle cx="8" cy="8" r="7" fill="none" stroke="currentColor" strokeWidth="1.1" />
          <path d="M8 4 V 8.5" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" />
          <circle cx="8" cy="11.2" r="0.8" fill="currentColor" />
        </svg>
        <div style={{ flex: 1, fontSize: 13, color: "var(--ink)" }}>
          <div style={{ fontWeight: 600 }}>
            Your machine is struggling to keep up with the live transcript.
          </div>
          <div style={{ marginTop: 3, color: "var(--ink-2)", fontSize: 12.5, lineHeight: 1.55 }}>
            We've switched to the fast model already. If it's still labouring, the calmer option is
            to stop the live transcript and keep recording. You can transcribe and summarise after
            the meeting, when your machine isn't doing two jobs at once.
          </div>
        </div>
        <button className="btn ghost" style={{ height: 24, padding: "0 8px", fontSize: 11.5,
          color: "var(--ink-3)", flex: "0 0 auto" }}>Dismiss</button>
      </div>

      {recording && (
        <div style={{
          display: "flex", alignItems: "center", gap: 10,
          padding: "10px 12px", borderRadius: 8,
          background: "var(--surface)",
          border: "1px solid color-mix(in oklch, var(--warn) 30%, var(--line))",
        }}>
          <span style={{
            width: 26, height: 26, borderRadius: "50%",
            background: "var(--record-soft)", color: "var(--record)",
            display: "inline-flex", alignItems: "center", justifyContent: "center",
            flex: "0 0 auto",
          }}>
            <svg width="11" height="11" viewBox="0 0 11 11"><circle cx="5.5" cy="5.5" r="3.5" fill="currentColor" /></svg>
          </span>
          <div style={{ flex: 1, minWidth: 0 }}>
            <div style={{ fontSize: 12.5, fontWeight: 600 }}>
              Stop the live transcript, keep recording
            </div>
            <div style={{ fontSize: 11, color: "var(--ink-3)", marginTop: 2, lineHeight: 1.45 }}>
              Quietest on your CPU. We will mark the gap and fill it in later from the recording.
            </div>
          </div>
          <button className="btn ghost" style={{ height: 28, fontSize: 12 }}>Not now</button>
          <button className="btn primary" style={{ height: 28, fontSize: 12, padding: "0 12px" }}>
            Stop transcription
          </button>
        </div>
      )}
    </div>
  );
}

// ════════════════════════════════════════════════════════════════════════════
// LIVE SCREEN with V1 banner + 3-way stop affordance · whole-screen demo
// ════════════════════════════════════════════════════════════════════════════
function LiveScreenWithStruggleV1({ recording = true }) {
  return (
    <>
      <LiveHeader title="Q3 strategy review · Thandi & Lebo" state="falling-behind" />
      <FallingBehindBannerV1 recording={recording} />
      <TranscriptDocument state="falling-behind" />
      <LiveFooter state="falling-behind" recording={recording} />
    </>
  );
}

Object.assign(window, {
  StopMenu, StopRow, StopPopoverDemo,
  FallingBehindBannerV1, LiveScreenWithStruggleV1,
});
