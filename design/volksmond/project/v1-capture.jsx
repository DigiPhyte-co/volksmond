// v1-capture.jsx — third-pass additions to the capture side:
// 1. Start screen now offers three first-class entries: live, import, record-only.
// 2. The record-only state and its handoff to "transcribe this recording now".
// 3. Transcribing an imported file (uses the live view).
// 4. Three Stop options + the prominent option under the struggling banner.

// ─── Local icons ───────────────────────────────────────────────────────────
const CIcon = {
  Upload: (p) => <svg viewBox="0 0 16 16" width={p.size||14} height={p.size||14}>
    <path d="M8 11 V3 M4.5 6 L8 2.5 L11.5 6" fill="none" stroke="currentColor" strokeWidth="1.3" strokeLinecap="round" strokeLinejoin="round"/>
    <path d="M2.5 13 H13.5" stroke="currentColor" strokeWidth="1.3" strokeLinecap="round"/>
  </svg>,
  Disk: (p) => <svg viewBox="0 0 16 16" width={p.size||14} height={p.size||14}>
    <rect x="2.5" y="2.5" width="11" height="11" rx="1.5" fill="none" stroke="currentColor" strokeWidth="1.3"/>
    <path d="M4.5 2.5 V6 H10.5 V2.5" fill="none" stroke="currentColor" strokeWidth="1.3" strokeLinejoin="round"/>
    <circle cx="8" cy="10" r="1.5" fill="none" stroke="currentColor" strokeWidth="1.3"/>
  </svg>,
  Wave: (p) => <svg viewBox="0 0 24 16" width={p.size||24} height={p.size||16}>
    <path d="M2 8 L2 8 M5 5 V11 M8 3 V13 M11 6 V10 M14 2 V14 M17 5 V11 M20 7 V9 M22 8 V8"
      stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" fill="none"/>
  </svg>,
};

// ════════════════════════════════════════════════════════════════════════════
// 1 · NEW SESSION HUB · three first-class entries
// ════════════════════════════════════════════════════════════════════════════
function NewSessionScreen() {
  return (
    <div style={{ flex: 1, display: "flex", overflow: "hidden" }}>
      <Sidebar active="home" />
      <div style={{ flex: 1, padding: "36px 40px", overflow: "auto",
        display: "flex", flexDirection: "column", gap: 28 }}>
        <div>
          <div className="eyebrow" style={{ marginBottom: 8 }}>Ready when you are</div>
          <h1 style={{ fontSize: 30 }}>Start a session</h1>
          <p style={{ color: "var(--ink-2)", fontSize: 13.5, marginTop: 6, maxWidth: 540 }}>
            Three ways in. Pick the one that fits the moment.
          </p>
        </div>

        <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr 1fr", gap: 14 }}>
          <SessionEntryCard
            primary
            badge="Most common"
            icon={<Icon.Mic size={20} />}
            title="Start a live meeting"
            body="Transcribe what you and others are saying right now, on this computer. Optionally record the audio too."
            cta="Begin"
          />
          <SessionEntryCard
            icon={<CIcon.Upload size={20} />}
            title="Upload a recording to transcribe"
            body="Drop in an audio or video file you already have. Volksmond will transcribe it locally, just like a live meeting."
            cta="Choose a file…"
            extras={["mp3", "m4a", "wav", "mp4", "mov", "ogg"]}
          />
          <SessionEntryCard
            icon={<CIcon.Disk size={20} />}
            title="Record only, transcribe later"
            body="For machines that can't keep up live. Volksmond records the audio cleanly, and you transcribe it when you're back at a desk."
            cta="Start recording"
            note="Quietest on your CPU. Good for old laptops, long sessions, or both."
          />
        </div>

        {/* Drop zone for the second card */}
        <div style={{
          border: "1.5px dashed var(--line-2)",
          borderRadius: 14,
          padding: "22px 24px",
          background: "var(--surface)",
          display: "flex", alignItems: "center", gap: 16,
        }}>
          <div style={{
            width: 38, height: 38, borderRadius: 10,
            background: "var(--accent-soft)", color: "var(--accent)",
            display: "inline-flex", alignItems: "center", justifyContent: "center",
          }}>
            <CIcon.Upload size={18} />
          </div>
          <div style={{ flex: 1 }}>
            <div style={{ fontSize: 13.5, fontWeight: 600 }}>
              Or drop an audio or video file anywhere on this window
            </div>
            <div style={{ fontSize: 11.5, color: "var(--ink-3)", marginTop: 3, lineHeight: 1.5 }}>
              Up to several hours. The file stays on this computer. We never upload it.
            </div>
          </div>
          <button className="btn">Browse…</button>
        </div>
      </div>
    </div>
  );
}

function SessionEntryCard({ icon, title, body, cta, primary, badge, note, extras }) {
  return (
    <div style={{
      padding: "20px 18px",
      borderRadius: 14,
      border: `1px solid ${primary ? "color-mix(in oklch, var(--accent) 30%, var(--line))" : "var(--line)"}`,
      background: primary ? "color-mix(in oklch, var(--accent) 6%, var(--surface))" : "var(--surface)",
      display: "flex", flexDirection: "column", gap: 12,
      minHeight: 240,
    }}>
      <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
        <span style={{
          width: 36, height: 36, borderRadius: 10,
          background: primary ? "var(--accent)" : "var(--surface-2)",
          color: primary ? "var(--accent-ink)" : "var(--accent)",
          display: "inline-flex", alignItems: "center", justifyContent: "center",
        }}>{icon}</span>
        {badge && (
          <span className="chip" style={{ height: 18, fontSize: 10, color: "var(--accent)",
            background: "var(--accent-soft)", border: "none" }}>{badge}</span>
        )}
      </div>
      <div style={{ fontSize: 15, fontWeight: 600, lineHeight: 1.25 }}>{title}</div>
      <div style={{ fontSize: 12.5, color: "var(--ink-2)", lineHeight: 1.55, flex: 1 }}>{body}</div>
      {extras && (
        <div style={{ display: "flex", flexWrap: "wrap", gap: 4 }}>
          {extras.map((e) => (
            <span key={e} className="mono" style={{
              fontSize: 10, padding: "2px 6px", borderRadius: 4,
              color: "var(--ink-3)", background: "var(--surface-2)",
              border: "1px solid var(--line)",
            }}>.{e}</span>
          ))}
        </div>
      )}
      {note && (
        <div style={{ fontSize: 11, color: "var(--ink-3)", lineHeight: 1.45 }}>{note}</div>
      )}
      <button className={`btn ${primary ? "primary" : ""} tall`} style={{ marginTop: "auto" }}>
        {cta}
      </button>
    </div>
  );
}

// ════════════════════════════════════════════════════════════════════════════
// 2 · RECORD-ONLY STATE · calm, doesn't pretend to transcribe
// ════════════════════════════════════════════════════════════════════════════
function RecordOnlyScreen({ elapsed = "00:17:42", stage = "recording" }) {
  // stage: 'recording' | 'stopped' (handoff card)
  return (
    <div style={{ flex: 1, display: "flex", overflow: "hidden" }}>
      <Sidebar active="home" />
      <div style={{ flex: 1, display: "flex", flexDirection: "column", overflow: "hidden" }}>

        {/* Header */}
        <div className="hairline-b" style={{
          padding: "14px 24px", display: "flex", alignItems: "center", gap: 14,
          background: "var(--surface)",
        }}>
          <div style={{ flex: 1, display: "flex", flexDirection: "column", gap: 4 }}>
            <div style={{ fontSize: 13, fontWeight: 600 }}>
              Eindgesprek met Pieter
            </div>
            <div style={{ fontSize: 11.5, color: "var(--ink-3)",
              display: "flex", alignItems: "center", gap: 8 }}>
              <span className="mono" style={{ fontFeatureSettings: "'tnum'" }}>{elapsed}</span>
              <span style={{ width: 3, height: 3, borderRadius: "50%", background: "var(--ink-4)" }} />
              <span>Recording only · not transcribing yet</span>
              <span style={{ width: 3, height: 3, borderRadius: "50%", background: "var(--ink-4)" }} />
              <span>Local only</span>
            </div>
          </div>
          <span className="chip live"><span className="dot" />Recording</span>
        </div>

        {/* Centre · calm record-only state */}
        {stage === "recording" && (
          <div style={{
            flex: 1, display: "flex", flexDirection: "column",
            alignItems: "center", justifyContent: "center", padding: 32, gap: 22,
          }}>
            <div style={{
              width: 120, height: 120, borderRadius: "50%",
              background: "var(--record-soft)",
              display: "flex", alignItems: "center", justifyContent: "center",
              position: "relative",
            }}>
              <span style={{
                position: "absolute", inset: 0, borderRadius: "50%",
                background: "var(--record-soft)", opacity: 0.6,
                animation: "vm-pulse 2.4s ease-in-out infinite",
              }} />
              <span style={{
                width: 64, height: 64, borderRadius: "50%",
                background: "var(--record)",
                display: "flex", alignItems: "center", justifyContent: "center",
                position: "relative",
              }}>
                <span style={{
                  width: 14, height: 14, borderRadius: 3, background: "#fff",
                }} />
              </span>
            </div>

            <div style={{ textAlign: "center", maxWidth: 440 }}>
              <div className="mono" style={{
                fontSize: 32, color: "var(--ink)", letterSpacing: 0.02,
                fontFeatureSettings: "'tnum'", fontWeight: 500,
              }}>{elapsed}</div>
              <div style={{ fontSize: 13, color: "var(--ink-2)", marginTop: 10, lineHeight: 1.55 }}>
                Recording cleanly. No transcript is being made right now.
                When you stop, you can transcribe it here.
              </div>
            </div>

            {/* Level meter */}
            <div style={{
              padding: "14px 20px", borderRadius: 12,
              background: "var(--surface)", border: "1px solid var(--line)",
              display: "flex", flexDirection: "column", gap: 12, width: 380,
            }}>
              <Meter label="Microphone" level={0.42} />
              <Meter label="System audio" level={0.28} />
              <div style={{ fontSize: 11, color: "var(--ink-3)", marginTop: 2 }}>
                Saving to <span className="mono">~/Volksmond/recordings/2026-05-22.wav</span>
              </div>
            </div>

            <div style={{ display: "flex", gap: 10, marginTop: 4 }}>
              <button className="btn">
                <svg width="13" height="13" viewBox="0 0 13 13">
                  <rect x="3" y="2.5" width="2.5" height="8" rx="0.5" fill="currentColor" />
                  <rect x="7.5" y="2.5" width="2.5" height="8" rx="0.5" fill="currentColor" />
                </svg>
                Pause
              </button>
              <button className="btn" style={{ color: "var(--record)" }}>
                <svg width="11" height="11" viewBox="0 0 11 11">
                  <rect x="2" y="2" width="7" height="7" rx="1" fill="currentColor" />
                </svg>
                Stop recording
              </button>
            </div>
          </div>
        )}

        {/* Handoff · stopped, offer to transcribe now */}
        {stage === "stopped" && (
          <div style={{
            flex: 1, padding: "32px 40px", overflow: "auto",
            display: "flex", justifyContent: "center",
          }}>
            <div style={{ width: 560, display: "flex", flexDirection: "column", gap: 18 }}>
              <div style={{ display: "flex", alignItems: "center", gap: 14 }}>
                <span style={{
                  width: 36, height: 36, borderRadius: "50%",
                  background: "var(--ok-soft)", color: "var(--ok)",
                  display: "inline-flex", alignItems: "center", justifyContent: "center",
                }}>
                  <Icon.Check size={16} strokeWidth={2.2} />
                </span>
                <div>
                  <h1 style={{ fontSize: 22 }}>Recording saved.</h1>
                  <div className="mono" style={{ fontSize: 12, color: "var(--ink-3)", marginTop: 2 }}>
                    ~/Volksmond/recordings/2026-05-22.wav · 17m 42s · 12.4 MB
                  </div>
                </div>
              </div>

              {/* Big handoff card */}
              <div style={{
                padding: 20, borderRadius: 14,
                background: "color-mix(in oklch, var(--accent) 6%, var(--surface))",
                border: "1px solid color-mix(in oklch, var(--accent) 28%, var(--line))",
                display: "flex", flexDirection: "column", gap: 14,
              }}>
                <div style={{ display: "flex", alignItems: "flex-start", gap: 14 }}>
                  <span style={{
                    width: 32, height: 32, borderRadius: 8,
                    background: "var(--accent)", color: "var(--accent-ink)",
                    display: "inline-flex", alignItems: "center", justifyContent: "center",
                    flex: "0 0 auto",
                  }}>
                    <Icon.Mic size={15} />
                  </span>
                  <div style={{ flex: 1 }}>
                    <div style={{ fontSize: 14.5, fontWeight: 600 }}>
                      Transcribe this recording now?
                    </div>
                    <div style={{ fontSize: 12.5, color: "var(--ink-2)", marginTop: 4, lineHeight: 1.55 }}>
                      Volksmond will read the file and write it out. Slower than live, but more accurate.
                      You can keep working on your machine while it runs. Stays on this computer.
                    </div>

                    {/* tiny pre-flight */}
                    <div style={{
                      marginTop: 12, display: "flex", flexDirection: "column", gap: 6,
                      padding: "10px 12px", borderRadius: 8,
                      background: "var(--surface)", border: "1px solid var(--line)",
                      fontSize: 12, color: "var(--ink-2)",
                    }}>
                      <div style={{ display: "flex", justifyContent: "space-between" }}>
                        <span>Estimated time</span>
                        <span className="mono">about 7 minutes</span>
                      </div>
                      <div style={{ display: "flex", justifyContent: "space-between" }}>
                        <span>Quality</span>
                        <span>Balanced</span>
                      </div>
                      <div style={{ display: "flex", justifyContent: "space-between" }}>
                        <span>Language</span>
                        <span>Auto-detect (Afrikaans, English)</span>
                      </div>
                    </div>
                  </div>
                </div>
                <div style={{ display: "flex", gap: 8, justifyContent: "flex-end" }}>
                  <button className="btn">Transcribe later</button>
                  <button className="btn primary">Transcribe this recording now</button>
                </div>
              </div>

              <div style={{ fontSize: 11.5, color: "var(--ink-3)", lineHeight: 1.55 }}>
                You can transcribe a recording any time from <b style={{ color: "var(--ink-2)" }}>History</b>.
                Recordings are kept until you delete them; you can change where they live in Settings.
              </div>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}

// ════════════════════════════════════════════════════════════════════════════
// 3 · IMPORT IN PROGRESS · uses the live view, with a different chrome
// ════════════════════════════════════════════════════════════════════════════
function ImportingScreen({ progress = 0.38 }) {
  const pct = Math.round(progress * 100);
  return (
    <div style={{ flex: 1, display: "flex", flexDirection: "column", overflow: "hidden" }}>

      {/* Header */}
      <div className="hairline-b" style={{
        padding: "14px 24px", display: "flex", alignItems: "center", gap: 14,
        background: "var(--surface)",
      }}>
        <span style={{
          width: 22, height: 22, borderRadius: 6,
          background: "var(--accent-soft)", color: "var(--accent)",
          display: "inline-flex", alignItems: "center", justifyContent: "center",
          flex: "0 0 auto",
        }}>
          <CIcon.Upload size={11} />
        </span>
        <div style={{ flex: 1, display: "flex", flexDirection: "column", gap: 3, minWidth: 0 }}>
          <div style={{ fontSize: 13, fontWeight: 600,
            whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis" }}>
            Transcribing: client-intake-may-21.m4a
          </div>
          <div style={{ display: "flex", alignItems: "center", gap: 8, fontSize: 11.5, color: "var(--ink-3)" }}>
            <span className="mono">{pct}%</span>
            <span style={{ width: 3, height: 3, borderRadius: "50%", background: "var(--ink-4)" }} />
            <span>about 4 minutes left</span>
            <span style={{ width: 3, height: 3, borderRadius: "50%", background: "var(--ink-4)" }} />
            <span>Local only</span>
          </div>
        </div>
        <button className="btn ghost" style={{ height: 30, fontSize: 12 }}>Cancel</button>
      </div>

      {/* Slim progress strip */}
      <div style={{ height: 3, background: "var(--surface-2)" }}>
        <div style={{ height: "100%", width: `${pct}%`,
          background: "var(--accent)", transition: "width .3s" }} />
      </div>

      {/* Body · the running transcript */}
      <TranscriptDocument state="live" />

      {/* Footer · simpler than live (no add-term, no meters; just save target) */}
      <div className="hairline-t" style={{
        flex: "0 0 auto", padding: "14px 24px",
        background: "var(--surface)",
        display: "flex", alignItems: "center", gap: 12,
      }}>
        <div className="spinner" style={{ width: 14, height: 14, borderWidth: 2 }} />
        <span style={{ fontSize: 12, color: "var(--ink-2)" }}>
          Reading the file. You can leave this open or come back later.
        </span>
        <div style={{ flex: 1 }} />
        <span style={{ fontSize: 11, color: "var(--ink-3)" }}>
          Will save to <span className="mono">~/Volksmond/client-intake-may-21.txt</span>
        </span>
      </div>
    </div>
  );
}

Object.assign(window, {
  CIcon, NewSessionScreen, SessionEntryCard,
  RecordOnlyScreen, ImportingScreen,
});
