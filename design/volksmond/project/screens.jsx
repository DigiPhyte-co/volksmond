// screens.jsx — all the non-Live screens, plus error/empty states.
// Welcome / device setup / model download / pre-meeting start / finish-save /
// history / settings / errors / offline.

// ─── Small icon set (inline, no externals) ──────────────────────────────────
const Icon = {
  Mic: (p) => <svg viewBox="0 0 16 16" width={p.size||14} height={p.size||14}><rect x="6" y="2" width="4" height="8" rx="2" fill="none" stroke="currentColor" strokeWidth="1.3"/><path d="M3.5 8 V8.5 a4.5 4.5 0 0 0 9 0 V8" fill="none" stroke="currentColor" strokeWidth="1.3" strokeLinecap="round"/><path d="M8 13 V15" stroke="currentColor" strokeWidth="1.3" strokeLinecap="round"/></svg>,
  Speaker: (p) => <svg viewBox="0 0 16 16" width={p.size||14} height={p.size||14}><path d="M2 6 H4.5 L8 3 V13 L4.5 10 H2 Z" fill="none" stroke="currentColor" strokeWidth="1.3" strokeLinejoin="round"/><path d="M10.5 6 a2.5 2.5 0 0 1 0 4" fill="none" stroke="currentColor" strokeWidth="1.3" strokeLinecap="round"/><path d="M12 4.5 a4.5 4.5 0 0 1 0 7" fill="none" stroke="currentColor" strokeWidth="1.3" strokeLinecap="round"/></svg>,
  Lock: (p) => <svg viewBox="0 0 16 16" width={p.size||14} height={p.size||14}><rect x="3" y="7" width="10" height="7" rx="1.2" fill="none" stroke="currentColor" strokeWidth="1.3"/><path d="M5.5 7 V5 a2.5 2.5 0 0 1 5 0 V7" fill="none" stroke="currentColor" strokeWidth="1.3"/></svg>,
  Wifi: (p) => <svg viewBox="0 0 16 16" width={p.size||14} height={p.size||14}><path d="M2 6 a8 8 0 0 1 12 0" fill="none" stroke="currentColor" strokeWidth="1.3" strokeLinecap="round"/><path d="M4 8.5 a5 5 0 0 1 8 0" fill="none" stroke="currentColor" strokeWidth="1.3" strokeLinecap="round"/><path d="M6 11 a2.4 2.4 0 0 1 4 0" fill="none" stroke="currentColor" strokeWidth="1.3" strokeLinecap="round"/><circle cx="8" cy="13" r="0.8" fill="currentColor"/></svg>,
  WifiOff: (p) => <svg viewBox="0 0 16 16" width={p.size||14} height={p.size||14}><path d="M2 6 a8 8 0 0 1 12 0" fill="none" stroke="currentColor" strokeWidth="1.3" strokeLinecap="round" opacity="0.4"/><path d="M4 8.5 a5 5 0 0 1 8 0" fill="none" stroke="currentColor" strokeWidth="1.3" strokeLinecap="round" opacity="0.4"/><path d="M6 11 a2.4 2.4 0 0 1 4 0" fill="none" stroke="currentColor" strokeWidth="1.3" strokeLinecap="round"/><circle cx="8" cy="13" r="0.8" fill="currentColor"/><path d="M2 2 L14 14" stroke="currentColor" strokeWidth="1.3" strokeLinecap="round"/></svg>,
  Cal: (p) => <svg viewBox="0 0 16 16" width={p.size||14} height={p.size||14}><rect x="2.5" y="3.5" width="11" height="10" rx="1.5" fill="none" stroke="currentColor" strokeWidth="1.3"/><path d="M2.5 6.5 H13.5" stroke="currentColor" strokeWidth="1.3"/><path d="M5 2 V4.5 M11 2 V4.5" stroke="currentColor" strokeWidth="1.3" strokeLinecap="round"/></svg>,
  Folder: (p) => <svg viewBox="0 0 16 16" width={p.size||14} height={p.size||14}><path d="M2 4 V12.5 a1 1 0 0 0 1 1 H13 a1 1 0 0 0 1 -1 V6 a1 1 0 0 0 -1 -1 H8 L6.5 3.5 H3 a1 1 0 0 0 -1 0.5 Z" fill="none" stroke="currentColor" strokeWidth="1.3" strokeLinejoin="round"/></svg>,
  Check: (p) => <svg viewBox="0 0 16 16" width={p.size||14} height={p.size||14}><path d="M3 8.5 L7 12 L13 5" fill="none" stroke="currentColor" strokeWidth={p.strokeWidth||1.6} strokeLinecap="round" strokeLinejoin="round"/></svg>,
  Plus: (p) => <svg viewBox="0 0 16 16" width={p.size||14} height={p.size||14}><path d="M8 3 V13 M3 8 H13" stroke="currentColor" strokeWidth="1.4" strokeLinecap="round"/></svg>,
  Search: (p) => <svg viewBox="0 0 16 16" width={p.size||14} height={p.size||14}><circle cx="7" cy="7" r="4" fill="none" stroke="currentColor" strokeWidth="1.3"/><path d="M10 10 L13.5 13.5" stroke="currentColor" strokeWidth="1.3" strokeLinecap="round"/></svg>,
  Settings: (p) => <svg viewBox="0 0 16 16" width={p.size||14} height={p.size||14}><circle cx="8" cy="8" r="2" fill="none" stroke="currentColor" strokeWidth="1.3"/><path d="M8 2 V3.5 M8 12.5 V14 M2 8 H3.5 M12.5 8 H14 M3.7 3.7 L4.8 4.8 M11.2 11.2 L12.3 12.3 M3.7 12.3 L4.8 11.2 M11.2 4.8 L12.3 3.7" stroke="currentColor" strokeWidth="1.3" strokeLinecap="round"/></svg>,
  History: (p) => <svg viewBox="0 0 16 16" width={p.size||14} height={p.size||14}><path d="M2.5 8 a5.5 5.5 0 1 0 1.6 -3.9" fill="none" stroke="currentColor" strokeWidth="1.3" strokeLinecap="round"/><path d="M2 2.5 V5 H4.5" fill="none" stroke="currentColor" strokeWidth="1.3" strokeLinecap="round" strokeLinejoin="round"/><path d="M8 5 V8 L10 9.5" fill="none" stroke="currentColor" strokeWidth="1.3" strokeLinecap="round"/></svg>,
  Sparkle: (p) => <svg viewBox="0 0 16 16" width={p.size||14} height={p.size||14}><path d="M8 2 L9.2 6.8 L14 8 L9.2 9.2 L8 14 L6.8 9.2 L2 8 L6.8 6.8 Z" fill="currentColor" opacity="0.85"/></svg>,
  Alert: (p) => <svg viewBox="0 0 16 16" width={p.size||14} height={p.size||14}><path d="M8 2.5 L14.5 13.5 H1.5 Z" fill="none" stroke="currentColor" strokeWidth="1.3" strokeLinejoin="round"/><path d="M8 6 V9.5" stroke="currentColor" strokeWidth="1.4" strokeLinecap="round"/><circle cx="8" cy="11.5" r="0.8" fill="currentColor"/></svg>,
  Cloud: (p) => <svg viewBox="0 0 16 16" width={p.size||14} height={p.size||14}><path d="M4 11 a3 3 0 0 1 0.5 -5.95 a3.5 3.5 0 0 1 6.8 0.5 a2.5 2.5 0 0 1 0.7 4.95 Z" fill="none" stroke="currentColor" strokeWidth="1.3" strokeLinejoin="round"/></svg>,
  Sun: (p) => <svg viewBox="0 0 16 16" width={p.size||14} height={p.size||14}><circle cx="8" cy="8" r="3" fill="none" stroke="currentColor" strokeWidth="1.3"/><path d="M8 1.5 V3 M8 13 V14.5 M1.5 8 H3 M13 8 H14.5 M3.3 3.3 L4.4 4.4 M11.6 11.6 L12.7 12.7 M3.3 12.7 L4.4 11.6 M11.6 4.4 L12.7 3.3" stroke="currentColor" strokeWidth="1.3" strokeLinecap="round"/></svg>,
  Moon: (p) => <svg viewBox="0 0 16 16" width={p.size||14} height={p.size||14}><path d="M13 9.5 A5 5 0 1 1 6.5 3 a5 5 0 0 0 6.5 6.5 Z" fill="none" stroke="currentColor" strokeWidth="1.3" strokeLinejoin="round"/></svg>,
  Auto: (p) => <svg viewBox="0 0 16 16" width={p.size||14} height={p.size||14}><circle cx="8" cy="8" r="5" fill="none" stroke="currentColor" strokeWidth="1.3"/><path d="M8 3 A5 5 0 0 1 8 13 Z" fill="currentColor"/></svg>,
  Key: (p) => <svg viewBox="0 0 16 16" width={p.size||14} height={p.size||14}><circle cx="5" cy="11" r="2.5" fill="none" stroke="currentColor" strokeWidth="1.3"/><path d="M7 9 L13 3 M11 5 L13 7 M9.5 6.5 L11.5 8.5" stroke="currentColor" strokeWidth="1.3" strokeLinecap="round" strokeLinejoin="round"/></svg>,
  Crown: (p) => <svg viewBox="0 0 16 16" width={p.size||14} height={p.size||14}><path d="M2 5 L4.5 9 L8 4 L11.5 9 L14 5 L13 12 H3 Z" fill="currentColor" opacity="0.95"/></svg>,
  Chart: (p) => <svg viewBox="0 0 16 16" width={p.size||14} height={p.size||14}><path d="M2.5 13 V3 M2.5 13 H13.5" stroke="currentColor" strokeWidth="1.3" strokeLinecap="round"/><rect x="5" y="8" width="2" height="4" fill="currentColor"/><rect x="8" y="5.5" width="2" height="6.5" fill="currentColor"/><rect x="11" y="9.5" width="2" height="2.5" fill="currentColor"/></svg>,
};

// ─── Pro badge ──────────────────────────────────────────────────────────────
function ProBadge() {
  return (
    <span style={{
      display: "inline-flex", alignItems: "center", gap: 4,
      padding: "2px 7px", borderRadius: 999,
      fontSize: 9.5, fontWeight: 700, letterSpacing: 0.08,
      textTransform: "uppercase",
      background: "color-mix(in oklch, var(--accent) 18%, var(--surface))",
      color: "var(--accent)",
      border: "1px solid color-mix(in oklch, var(--accent) 30%, transparent)",
      verticalAlign: "1px",
    }}>
      <Icon.Crown size={9} /> Pro
    </span>
  );
}

// ─── Select-row (custom-styled native-ish select) ───────────────────────────
function SelectRow({ icon, label, value, hint, footer }) {
  return (
    <label style={{
      display: "flex", alignItems: "center", gap: 12,
      padding: "12px 14px", border: "1px solid var(--line)",
      borderRadius: 10, background: "var(--surface)",
    }}>
      {icon && <span style={{ color: "var(--ink-3)" }}>{icon}</span>}
      <div style={{ flex: 1, minWidth: 0 }}>
        <div style={{ fontSize: 12, color: "var(--ink-3)", marginBottom: 2 }}>{label}</div>
        <div style={{ fontSize: 13.5, fontWeight: 500, color: "var(--ink)" }}>
          {value}
        </div>
        {footer}
      </div>
      <svg width="10" height="10" viewBox="0 0 10 10" style={{ color: "var(--ink-3)" }}>
        <path d="M2 4 L5 7 L8 4" fill="none" stroke="currentColor" strokeWidth="1.3" strokeLinecap="round" strokeLinejoin="round" />
      </svg>
    </label>
  );
}

// ════════════════════════════════════════════════════════════════════════════
// 1 · FIRST-RUN WELCOME
// ════════════════════════════════════════════════════════════════════════════
function WelcomeScreen() {
  return (
    <div style={{
      flex: 1, display: "flex", alignItems: "center", justifyContent: "center",
      padding: 48, background: "var(--bg)",
    }}>
      <div style={{
        width: 540, display: "flex", flexDirection: "column", gap: 24,
      }}>
        <Wordmark showProvisional />
        <div style={{ display: "flex", flexDirection: "column", gap: 14 }}>
          <h1 style={{ fontSize: 32, lineHeight: 1.1, letterSpacing: -0.025 }}>
            A calm, private transcript of any meeting on your computer.
          </h1>
          <p style={{ color: "var(--ink-2)", fontSize: 15, lineHeight: 1.55, maxWidth: 480 }}>
            Volksmond listens to your microphone and the audio coming out of your computer,
            and writes it down as people talk. Built for Afrikaans, English, and the way
            people actually switch between them.
          </p>
        </div>

        {/* Privacy promise — the centrepiece */}
        <div style={{
          padding: "18px 20px",
          border: "1px solid var(--line)",
          borderRadius: 14,
          background: "var(--surface)",
          display: "flex", gap: 14, alignItems: "flex-start",
        }}>
          <div style={{
            width: 36, height: 36, borderRadius: 10,
            background: "var(--accent-soft)", color: "var(--accent)",
            display: "flex", alignItems: "center", justifyContent: "center",
            flex: "0 0 auto",
          }}>
            <Icon.Lock size={18} />
          </div>
          <div style={{ flex: 1 }}>
            <div style={{ fontSize: 14, fontWeight: 600, marginBottom: 4 }}>
              Your audio never leaves this computer.
            </div>
            <div style={{ fontSize: 12.5, color: "var(--ink-2)", lineHeight: 1.5 }}>
              No cloud, no third-party servers, no telemetry. Everything is transcribed locally,
              on your machine. You can use Volksmond completely offline.
            </div>
          </div>
        </div>

        <div style={{ display: "flex", gap: 10, alignItems: "center" }}>
          <button className="btn primary tall" style={{ flex: 1 }}>Get started</button>
          <button className="btn tall ghost">Learn how it works</button>
        </div>

        <div style={{ fontSize: 11.5, color: "var(--ink-3)", lineHeight: 1.5 }}>
          The first time you start a meeting, Volksmond will download a language model (about 1.5 GB).
          After that, it works without an internet connection.
        </div>
      </div>
    </div>
  );
}

// ════════════════════════════════════════════════════════════════════════════
// 2 · DEVICE SETUP
// ════════════════════════════════════════════════════════════════════════════
function DeviceSetupScreen() {
  return (
    <div style={{ flex: 1, display: "flex", justifyContent: "center", padding: "48px 32px", overflow: "auto" }}>
      <div style={{ width: 540, display: "flex", flexDirection: "column", gap: 24 }}>
        <div>
          <div className="eyebrow" style={{ marginBottom: 10 }}>Setup · 1 of 2</div>
          <h1 style={{ fontSize: 26 }}>Choose what Volksmond should listen to.</h1>
          <p style={{ color: "var(--ink-2)", fontSize: 14, marginTop: 8, lineHeight: 1.55 }}>
            Your voice comes from the microphone. Everyone else comes from your computer's own audio.
            Pick what works for this machine.
          </p>
        </div>

        <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
          <SelectRow
            icon={<Icon.Mic size={16} />}
            label="Microphone (your voice)"
            value="MacBook Pro Microphone"
            footer={
              <div style={{ marginTop: 8 }}>
                <Meter label="" level={0.32} />
              </div>
            }
          />
          <SelectRow
            icon={<Icon.Speaker size={16} />}
            label="System audio (everyone else)"
            value="VB-Audio Cable (loopback)"
            footer={
              <div style={{ marginTop: 8 }}>
                <Meter label="" level={0.55} />
              </div>
            }
          />
        </div>

        <div style={{
          padding: "14px 16px", borderRadius: 10,
          background: "var(--surface)", border: "1px solid var(--line)",
          display: "flex", gap: 12, alignItems: "flex-start",
        }}>
          <div style={{ color: "var(--ink-3)", marginTop: 2 }}><Icon.Speaker size={14} /></div>
          <div style={{ fontSize: 12.5, color: "var(--ink-2)", lineHeight: 1.5 }}>
            <div style={{ color: "var(--ink)", fontWeight: 500, marginBottom: 2 }}>
              Can't see system audio?
            </div>
            On Windows, enable "Stereo Mix" in your sound settings, or install a free loopback driver.
            On macOS, BlackHole and Loopback both work. Volksmond will guide you through it.
          </div>
        </div>

        <div style={{ display: "flex", gap: 10, justifyContent: "space-between", alignItems: "center", marginTop: 4 }}>
          <button className="btn ghost">Back</button>
          <button className="btn primary tall">Continue</button>
        </div>
      </div>
    </div>
  );
}

// ════════════════════════════════════════════════════════════════════════════
// 3 · MODEL DOWNLOAD (3 sub-states: idle/downloading/done)
// ════════════════════════════════════════════════════════════════════════════
function ModelDownloadScreen({ progress = 0.42, state = "downloading" }) {
  const pct = Math.round(progress * 100);
  return (
    <div style={{ flex: 1, display: "flex", justifyContent: "center", padding: "48px 32px" }}>
      <div style={{ width: 540, display: "flex", flexDirection: "column", gap: 24 }}>
        <div>
          <div className="eyebrow" style={{ marginBottom: 10 }}>Setup · 2 of 2</div>
          <h1 style={{ fontSize: 26 }}>One download, once.</h1>
          <p style={{ color: "var(--ink-2)", fontSize: 14, marginTop: 8, lineHeight: 1.55, maxWidth: 480 }}>
            We're fetching the Afrikaans-and-English language model. After this,
            Volksmond works fully offline. The model stays on your machine.
          </p>
        </div>

        <div style={{
          padding: "20px 22px", borderRadius: 14,
          background: "var(--surface)", border: "1px solid var(--line)",
          display: "flex", flexDirection: "column", gap: 14,
        }}>
          <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between" }}>
            <div style={{ fontSize: 13, fontWeight: 600 }}>
              volksmond-af-en-balanced.bin
            </div>
            <div style={{ fontSize: 12, color: "var(--ink-3)" }} className="mono">
              {state === "done" ? "1.52 GB · done" : `${(progress * 1.52).toFixed(2)} of 1.52 GB`}
            </div>
          </div>
          <div style={{
            height: 6, borderRadius: 3,
            background: "var(--surface-2)", overflow: "hidden",
            border: "1px solid var(--line)",
          }}>
            <div style={{
              height: "100%",
              width: state === "done" ? "100%" : `${pct}%`,
              background: state === "done" ? "var(--ok)" : "var(--accent)",
              transition: "width .3s",
            }} />
          </div>
          <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
            <div style={{ fontSize: 12, color: "var(--ink-3)" }}>
              {state === "done" ? (
                <span style={{ display: "inline-flex", alignItems: "center", gap: 6, color: "var(--ok)" }}>
                  <Icon.Check size={12} strokeWidth={2} /> Ready to use
                </span>
              ) : (
                <>About 2 minutes left on your connection</>
              )}
            </div>
            {state === "downloading" && (
              <button className="btn ghost" style={{ height: 26, fontSize: 11.5 }}>Pause</button>
            )}
          </div>
        </div>

        <div style={{ fontSize: 12, color: "var(--ink-3)", lineHeight: 1.55 }}>
          Once downloaded, Volksmond starts each meeting in a few seconds. The model loads into
          memory the first time you press "Begin", and stays warm for the rest of your session.
        </div>

        <div style={{ display: "flex", justifyContent: "flex-end" }}>
          <button className="btn primary tall" disabled={state !== "done"}
                  style={state !== "done" ? { opacity: 0.45, cursor: "default" } : {}}>
            Finish setup
          </button>
        </div>
      </div>
    </div>
  );
}

// ════════════════════════════════════════════════════════════════════════════
// 4 · PRE-MEETING START (the "home" screen)
// ════════════════════════════════════════════════════════════════════════════
function PreMeetingScreen({ recordingDefault = false }) {
  return (
    <div style={{ flex: 1, display: "flex", overflow: "hidden" }}>
      <Sidebar active="home" />
      <div style={{ flex: 1, padding: "36px 40px", overflow: "auto", display: "flex", flexDirection: "column", gap: 28 }}>
        <div style={{ display: "flex", alignItems: "flex-end", justifyContent: "space-between", gap: 16 }}>
          <div>
            <div className="eyebrow" style={{ marginBottom: 8 }}>Ready when you are</div>
            <h1 style={{ fontSize: 30 }}>Start a meeting</h1>
            <p style={{ color: "var(--ink-2)", fontSize: 13.5, marginTop: 6, maxWidth: 460 }}>
              Press begin when the meeting starts. You can add names and jargon while it's running.
            </p>
          </div>
          <span className="chip ok">
            <Icon.Check size={11} strokeWidth={2} /> Model ready · balanced
          </span>
        </div>

        <div style={{ display: "grid", gridTemplateColumns: "1fr 320px", gap: 24, alignItems: "start" }}>
          {/* Left column · the form */}
          <div style={{ display: "flex", flexDirection: "column", gap: 16 }}>
            <div>
              <label style={{ fontSize: 12, color: "var(--ink-3)", display: "block", marginBottom: 6 }}>
                Meeting title <span style={{ color: "var(--ink-4)" }}>(optional)</span>
              </label>
              <input className="field tall" defaultValue="Q3 strategy review · Thandi & Lebo" />
            </div>

            <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 12 }}>
              <div>
                <label style={{ fontSize: 12, color: "var(--ink-3)", display: "block", marginBottom: 6 }}>
                  Language
                </label>
                <div style={{
                  display: "flex", padding: 3, borderRadius: 8,
                  border: "1px solid var(--line)", background: "var(--surface-2)",
                }}>
                  {["Afrikaans", "English", "Auto-detect"].map((l, i) => (
                    <button key={l} className="btn" style={{
                      flex: 1, height: 30, padding: 0, fontSize: 12,
                      background: i === 2 ? "var(--surface)" : "transparent",
                      border: i === 2 ? "1px solid var(--line)" : "1px solid transparent",
                      color: i === 2 ? "var(--ink)" : "var(--ink-3)",
                      fontWeight: i === 2 ? 600 : 500,
                    }}>{l}</button>
                  ))}
                </div>
              </div>
              <div>
                <label style={{ fontSize: 12, color: "var(--ink-3)", display: "block", marginBottom: 6 }}>
                  Quality
                </label>
                <div style={{
                  display: "flex", padding: 3, borderRadius: 8,
                  border: "1px solid var(--line)", background: "var(--surface-2)",
                }}>
                  {["Fast", "Balanced", "Best"].map((l, i) => (
                    <button key={l} className="btn" style={{
                      flex: 1, height: 30, padding: 0, fontSize: 12,
                      background: i === 1 ? "var(--surface)" : "transparent",
                      border: i === 1 ? "1px solid var(--line)" : "1px solid transparent",
                      color: i === 1 ? "var(--ink)" : "var(--ink-3)",
                      fontWeight: i === 1 ? 600 : 500,
                    }}>{l}</button>
                  ))}
                </div>
              </div>
            </div>

            <div>
              <label style={{ fontSize: 12, color: "var(--ink-3)", display: "block", marginBottom: 6 }}>
                Names and jargon <span style={{ color: "var(--ink-4)" }}>(optional, helps accuracy)</span>
              </label>
              <div style={{
                padding: 10, borderRadius: 10, border: "1px solid var(--line)",
                background: "var(--surface)", display: "flex", flexWrap: "wrap", gap: 6, minHeight: 64,
                alignItems: "flex-start",
              }}>
                {["Thandi Mokoena", "Lebo van Wyk", "EBITDA", "go-to-market", "Volksmond"].map((t) => (
                  <span key={t} style={{
                    display: "inline-flex", alignItems: "center", gap: 6,
                    padding: "4px 8px", borderRadius: 999,
                    background: "var(--accent-soft)", color: "var(--accent)",
                    fontSize: 12, fontWeight: 500,
                  }}>
                    {t}
                    <span style={{ opacity: 0.5, fontSize: 13 }}>×</span>
                  </span>
                ))}
                <input
                  style={{
                    border: 0, outline: 0, background: "transparent",
                    fontSize: 12.5, fontFamily: "var(--font-sans)", color: "var(--ink)",
                    minWidth: 120, padding: "4px 6px",
                  }}
                  placeholder="Add a term…"
                />
              </div>
              <div style={{ fontSize: 11.5, color: "var(--ink-3)", marginTop: 6 }}>
                You can keep adding terms during the meeting; new terms apply from that moment onwards.
              </div>
            </div>

            {/* Pull from calendar */}
            <div style={{
              padding: "12px 14px", borderRadius: 10,
              border: "1px solid var(--line)", background: "var(--surface)",
              display: "flex", alignItems: "center", gap: 12,
            }}>
              <span style={{ color: "var(--ink-3)" }}><Icon.Cal size={16} /></span>
              <div style={{ flex: 1 }}>
                <div style={{ fontSize: 13, fontWeight: 500 }}>Pull attendee names from my calendar</div>
                <div style={{ fontSize: 11.5, color: "var(--ink-3)", marginTop: 2 }}>
                  Reads the next event on your local calendar only. Nothing is sent anywhere.
                </div>
              </div>
              <div className={`toggle`}><i /></div>
            </div>

            {/* Recording toggle */}
            <div style={{
              padding: "14px 16px", borderRadius: 10,
              border: `1px solid ${recordingDefault ? "color-mix(in oklch, var(--record) 30%, var(--line))" : "var(--line)"}`,
              background: recordingDefault ? "var(--record-soft)" : "var(--surface)",
              display: "flex", alignItems: "flex-start", gap: 12,
            }}>
              <span style={{ color: recordingDefault ? "var(--record)" : "var(--ink-3)", marginTop: 2 }}>
                <svg width="16" height="16" viewBox="0 0 16 16"><circle cx="8" cy="8" r="4" fill="currentColor" /></svg>
              </span>
              <div style={{ flex: 1 }}>
                <div style={{ fontSize: 13, fontWeight: 600 }}>Record the audio</div>
                <div style={{ fontSize: 12, color: "var(--ink-2)", marginTop: 3, lineHeight: 1.5 }}>
                  Keeps the audio on this machine until you stop. Lets us produce a slower, more accurate
                  clean version after the meeting, with named speakers.
                </div>
                {recordingDefault && (
                  <div style={{
                    marginTop: 10, padding: "8px 10px", borderRadius: 8,
                    background: "var(--surface)", border: "1px solid var(--line)",
                    fontSize: 11.5, color: "var(--ink-2)", lineHeight: 1.5,
                  }}>
                    <div style={{ color: "var(--ink-3)", fontWeight: 600, fontSize: 10.5, letterSpacing: 0.06, textTransform: "uppercase", marginBottom: 4 }}>
                      Courtesy line you could say
                    </div>
                    "Just a heads-up, I'm running a tool on my machine that's taking a private
                    transcript for my own notes. The audio doesn't leave my computer."
                  </div>
                )}
              </div>
              <div className={`toggle danger ${recordingDefault ? "on" : ""}`}><i /></div>
            </div>
          </div>

          {/* Right column · what's happening */}
          <div style={{ display: "flex", flexDirection: "column", gap: 12 }}>
            <div className="card" style={{ padding: 16, display: "flex", flexDirection: "column", gap: 12 }}>
              <div style={{ fontSize: 12, fontWeight: 600, color: "var(--ink-3)", letterSpacing: 0.06, textTransform: "uppercase" }}>
                On this machine
              </div>
              <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
                <div style={{ display: "flex", alignItems: "center", gap: 8, fontSize: 12.5 }}>
                  <span style={{ width: 8, height: 8, borderRadius: "50%", background: "var(--ok)" }} />
                  <span style={{ flex: 1 }}>Microphone ready</span>
                  <span style={{ color: "var(--ink-3)" }} className="mono">MacBook Pro</span>
                </div>
                <div style={{ display: "flex", alignItems: "center", gap: 8, fontSize: 12.5 }}>
                  <span style={{ width: 8, height: 8, borderRadius: "50%", background: "var(--ok)" }} />
                  <span style={{ flex: 1 }}>System audio ready</span>
                  <span style={{ color: "var(--ink-3)" }} className="mono">Loopback</span>
                </div>
                <div style={{ display: "flex", alignItems: "center", gap: 8, fontSize: 12.5 }}>
                  <span style={{ width: 8, height: 8, borderRadius: "50%", background: "var(--ok)" }} />
                  <span style={{ flex: 1 }}>Hardware fits balanced model</span>
                  <span style={{ color: "var(--ink-3)" }} className="mono">M2, 16 GB</span>
                </div>
                <div style={{ marginTop: 4, fontSize: 11.5, color: "var(--ink-3)", lineHeight: 1.5 }}>
                  Expected delay on each line: under one second.
                </div>
              </div>
            </div>

            <div className="card" style={{ padding: 14, display: "flex", flexDirection: "column", gap: 8 }}>
              <div style={{ display: "flex", alignItems: "center", gap: 8, fontSize: 12.5, fontWeight: 500 }}>
                <Icon.Lock size={13} /> Stays on this computer
              </div>
              <div style={{ fontSize: 11.5, color: "var(--ink-2)", lineHeight: 1.5 }}>
                No audio, no transcript, and no metadata is sent anywhere. Offline-safe.
              </div>
            </div>
          </div>
        </div>

        {/* Begin */}
        <div style={{ display: "flex", alignItems: "center", gap: 12, marginTop: "auto", paddingTop: 8 }}>
          <button className="btn primary tall" style={{ padding: "0 28px", height: 48, fontSize: 14 }}>
            <svg width="12" height="12" viewBox="0 0 12 12"><circle cx="6" cy="6" r="3" fill="currentColor" /></svg>
            Begin
          </button>
          <span style={{ fontSize: 11.5, color: "var(--ink-3)" }}>
            or press <kbd>⌘</kbd> <kbd>↵</kbd>
          </span>
        </div>
      </div>
    </div>
  );
}

// ════════════════════════════════════════════════════════════════════════════
// 5 · FINISH AND SAVE
// ════════════════════════════════════════════════════════════════════════════
function FinishSaveScreen({ cleanState = "offer" }) {
  // cleanState: 'offer' | 'running' | 'done'
  return (
    <div style={{ flex: 1, display: "flex", overflow: "hidden" }}>
      <Sidebar active="home" />
      <div style={{ flex: 1, padding: "48px 40px", overflow: "auto", display: "flex", justifyContent: "center" }}>
        <div style={{ width: 620, display: "flex", flexDirection: "column", gap: 22 }}>
          <div style={{
            display: "flex", alignItems: "center", gap: 14,
          }}>
            <span style={{
              width: 36, height: 36, borderRadius: "50%",
              background: "var(--ok-soft)", color: "var(--ok)",
              display: "inline-flex", alignItems: "center", justifyContent: "center",
            }}>
              <Icon.Check size={18} strokeWidth={2.2} />
            </span>
            <div>
              <h1 style={{ fontSize: 24 }}>Saved.</h1>
              <div style={{ fontSize: 13, color: "var(--ink-3)", marginTop: 2 }}>
                <span className="mono">~/Documents/Volksmond/Q3-review.txt</span>
              </div>
            </div>
          </div>

          <div className="card" style={{ padding: "16px 18px", display: "flex", flexDirection: "column", gap: 12 }}>
            <div style={{ display: "flex", alignItems: "baseline", justifyContent: "space-between" }}>
              <div style={{ fontSize: 13.5, fontWeight: 600 }}>Q3 strategy review · Thandi &amp; Lebo</div>
              <div className="mono" style={{ fontSize: 11.5, color: "var(--ink-3)" }}>22 May 2026 · 38m 12s</div>
            </div>
            <div style={{ display: "flex", gap: 18, fontSize: 12, color: "var(--ink-2)" }}>
              <span>3 speakers</span>
              <span>1,284 words</span>
              <span>4 second gap marked</span>
              <span style={{ color: "var(--record)" }}>Audio kept</span>
            </div>
            <div style={{ display: "flex", gap: 8, marginTop: 4 }}>
              <button className="btn">
                <Icon.Folder size={13} /> Open folder
              </button>
              <button className="btn">Open transcript</button>
              <button className="btn ghost">Copy</button>
            </div>
          </div>

          {/* Clean version offer */}
          <div style={{
            padding: 18, borderRadius: 14,
            border: "1px solid var(--line)", background: "var(--surface)",
            display: "flex", flexDirection: "column", gap: 14,
          }}>
            <div style={{ display: "flex", alignItems: "flex-start", gap: 14 }}>
              <span style={{
                width: 32, height: 32, borderRadius: 8,
                background: "var(--accent-soft)", color: "var(--accent)",
                display: "inline-flex", alignItems: "center", justifyContent: "center",
                flex: "0 0 auto",
              }}>
                <Icon.Sparkle size={16} />
              </span>
              <div style={{ flex: 1 }}>
                <div style={{ fontSize: 14.5, fontWeight: 600 }}>
                  {cleanState === "done" ? "Clean version is ready." :
                   cleanState === "running" ? "Making the clean version…" :
                   "Make a clean version?"}
                </div>
                <div style={{ fontSize: 12.5, color: "var(--ink-2)", marginTop: 3, lineHeight: 1.5 }}>
                  We'll run the audio again with the slower, more accurate model and try to put names
                  to each voice. Fills the 4-second gap. Stays on this machine.
                </div>

                {cleanState === "running" && (
                  <div style={{ marginTop: 14, display: "flex", flexDirection: "column", gap: 8 }}>
                    <div style={{
                      height: 5, borderRadius: 2.5, background: "var(--surface-2)",
                      border: "1px solid var(--line)", overflow: "hidden",
                    }}>
                      <div style={{ height: "100%", width: "62%", background: "var(--accent)" }} />
                    </div>
                    <div style={{ fontSize: 11.5, color: "var(--ink-3)", display: "flex", justifyContent: "space-between" }}>
                      <span>Second pass · about 90 seconds left</span>
                      <span className="mono">62%</span>
                    </div>
                  </div>
                )}

                {cleanState === "done" && (
                  <div style={{
                    marginTop: 12, display: "flex", flexDirection: "column", gap: 6,
                    padding: 10, borderRadius: 8, background: "var(--ok-soft)",
                    border: "1px solid color-mix(in oklch, var(--ok) 30%, var(--line))",
                    fontSize: 12.5, color: "var(--ink-2)",
                  }}>
                    <div style={{ fontWeight: 500, color: "var(--ink)" }}>
                      ✓ Saved as <span className="mono">Q3-review.clean.txt</span>
                    </div>
                    Named 3 speakers, fixed the 4-second gap, corrected 14 likely-wrong words.
                  </div>
                )}
              </div>
            </div>
            <div style={{ display: "flex", gap: 8, justifyContent: "flex-end" }}>
              {cleanState === "offer" && <>
                <button className="btn ghost">Skip</button>
                <button className="btn primary">Make clean version</button>
              </>}
              {cleanState === "running" && <>
                <button className="btn">Cancel</button>
              </>}
              {cleanState === "done" && <>
                <button className="btn">Open clean version</button>
              </>}
            </div>
          </div>

          <div style={{
            display: "flex", justifyContent: "space-between", alignItems: "center",
            fontSize: 12, color: "var(--ink-3)",
          }}>
            <span>Audio is not kept by default. To delete it now, choose Delete audio in the menu.</span>
            <button className="btn ghost" style={{ height: 28, fontSize: 12 }}>Done</button>
          </div>
        </div>
      </div>
    </div>
  );
}

// ════════════════════════════════════════════════════════════════════════════
// 6 · HISTORY
// ════════════════════════════════════════════════════════════════════════════
const HISTORY = [
  { date: "Today · 14:02", title: "Q3 strategy review · Thandi & Lebo", dur: "38m", lang: "af · en", clean: true, audio: true },
  { date: "Today · 09:30", title: "1:1 with Sipho", dur: "22m", lang: "af", clean: false, audio: false },
  { date: "Yesterday · 16:15", title: "Client intake · M. Botha", dur: "55m", lang: "af", clean: true, audio: true },
  { date: "Yesterday · 11:00", title: "Counselling session 14", dur: "48m", lang: "af", clean: false, audio: false },
  { date: "20 May · 10:30", title: "Eindgesprek met Pieter", dur: "31m", lang: "af", clean: true, audio: false },
  { date: "19 May · 15:45", title: "Pipeline review", dur: "42m", lang: "en", clean: false, audio: false },
  { date: "18 May · 13:30", title: "Onboarding kick-off", dur: "27m", lang: "en", clean: false, audio: false },
];

function HistoryScreen() {
  return (
    <div style={{ flex: 1, display: "flex", overflow: "hidden" }}>
      <Sidebar active="history" />
      <div style={{ flex: 1, display: "flex", flexDirection: "column", overflow: "hidden" }}>
        <div style={{
          padding: "20px 32px 14px",
          display: "flex", alignItems: "center", gap: 12,
          borderBottom: "1px solid var(--line)",
        }}>
          <h2 style={{ fontSize: 18, flex: "0 0 auto" }}>Past meetings</h2>
          <div style={{
            display: "flex", alignItems: "center", gap: 8,
            padding: "0 10px", height: 32, borderRadius: 8,
            background: "var(--surface-2)", border: "1px solid var(--line)",
            flex: 1, maxWidth: 340, marginLeft: 18,
          }}>
            <Icon.Search size={13} />
            <input style={{
              border: 0, outline: 0, background: "transparent",
              flex: 1, fontSize: 12.5, color: "var(--ink)",
              fontFamily: "var(--font-sans)",
            }} placeholder="Search transcripts…" />
            <kbd>⌘K</kbd>
          </div>
          <div style={{ flex: 1 }} />
          <button className="btn"><Icon.Plus size={12} /> New meeting</button>
        </div>

        <div style={{ flex: 1, overflow: "auto", padding: "8px 16px 24px" }}>
          {HISTORY.map((h, i) => (
            <div key={i} style={{
              display: "grid", gridTemplateColumns: "120px 1fr 130px 200px",
              alignItems: "center", gap: 16, padding: "12px 16px",
              borderRadius: 8,
              background: i === 0 ? "var(--accent-soft)" : "transparent",
              borderBottom: "1px solid var(--line)",
            }}>
              <div className="mono" style={{ fontSize: 11.5, color: "var(--ink-3)" }}>{h.date}</div>
              <div style={{
                fontSize: 13.5, fontWeight: 500,
                color: "var(--ink)",
                whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis",
              }}>
                {h.title}
              </div>
              <div style={{ display: "flex", gap: 6, alignItems: "center" }}>
                <span className="chip" style={{ height: 18, fontSize: 10 }}>{h.lang}</span>
                <span className="mono" style={{ fontSize: 11.5, color: "var(--ink-3)" }}>{h.dur}</span>
              </div>
              <div style={{ display: "flex", gap: 6, justifyContent: "flex-end" }}>
                {h.clean && (
                  <span className="chip ok" style={{ height: 20, fontSize: 10.5 }}>
                    <Icon.Sparkle size={10} /> Clean
                  </span>
                )}
                {h.audio && (
                  <span className="chip" style={{ height: 20, fontSize: 10.5, color: "var(--record)" }}>
                    Audio kept
                  </span>
                )}
                <button className="btn ghost" style={{ height: 22, padding: "0 8px", fontSize: 11 }}>Open</button>
              </div>
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}

// ════════════════════════════════════════════════════════════════════════════
// 7 · SETTINGS
// ════════════════════════════════════════════════════════════════════════════
function SettingsRow({ icon, title, sub, control }) {
  return (
    <div style={{
      display: "grid", gridTemplateColumns: "auto 1fr auto",
      alignItems: "center", gap: 14,
      padding: "14px 16px",
      borderBottom: "1px solid var(--line)",
    }}>
      <span style={{ color: "var(--ink-3)" }}>{icon}</span>
      <div>
        <div style={{ fontSize: 13.5, fontWeight: 500 }}>{title}</div>
        {sub && <div style={{ fontSize: 11.5, color: "var(--ink-3)", marginTop: 2, lineHeight: 1.5 }}>{sub}</div>}
      </div>
      <div>{control}</div>
    </div>
  );
}

// ─── Licence card · top of settings ────────────────────────────────────────
function LicenceCard({ tier = "free" }) {
  // tier: 'free' | 'trial' | 'pro' — perpetual per-major-version model
  const isFree = tier === "free";
  const isTrial = tier === "trial";
  const isPro = tier === "pro";

  return (
    <div style={{
      padding: 20, borderRadius: 14,
      background: isPro
        ? "color-mix(in oklch, var(--accent) 8%, var(--surface))"
        : "var(--surface)",
      border: `1px solid ${isPro
        ? "color-mix(in oklch, var(--accent) 28%, var(--line))"
        : "var(--line)"}`,
      display: "flex", flexDirection: "column", gap: 14,
    }}>
      <div style={{ display: "flex", alignItems: "flex-start", gap: 14 }}>
        <div style={{
          width: 38, height: 38, borderRadius: 10,
          background: isPro
            ? "var(--accent)"
            : "color-mix(in oklch, var(--accent) 14%, var(--surface-2))",
          color: isPro ? "var(--accent-ink)" : "var(--accent)",
          display: "flex", alignItems: "center", justifyContent: "center",
          flex: "0 0 auto",
        }}>
          <Icon.Crown size={18} />
        </div>
        <div style={{ flex: 1 }}>
          <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 3 }}>
            <span style={{ fontSize: 14.5, fontWeight: 600 }}>
              {isFree && "Free"}
              {isTrial && "Pro · trial"}
              {isPro && "Pro · activated"}
            </span>
            {isTrial && (
              <span className="chip" style={{ height: 18, fontSize: 10, color: "var(--accent)" }}>
                12 days left
              </span>
            )}
            {isPro && (
              <span className="chip" style={{ height: 18, fontSize: 10 }}>
                Version 1 · perpetual
              </span>
            )}
          </div>
          <div style={{ fontSize: 12.5, color: "var(--ink-2)", lineHeight: 1.5 }}>
            {isFree && "Unlimited local transcription, forever. Upgrade for named speakers, summaries, saved instructions, and other quiet upgrades."}
            {isTrial && "All Pro features unlocked while you try it. Paste a licence key any time to activate, fully offline."}
            {isPro && (
              <>Owned by <b style={{ color: "var(--ink)" }}>jaco@digiphyte.com</b>. Volksmond never phones home; the key was checked on this computer at launch.</>
            )}
          </div>
        </div>
        <div style={{ display: "flex", flexDirection: "column", gap: 6, alignItems: "flex-end" }}>
          {isFree && <button className="btn primary" style={{ height: 32 }}>Upgrade</button>}
          {isTrial && <button className="btn primary" style={{ height: 32 }}>Buy or paste key</button>}
          {isPro && <button className="btn ghost" style={{ height: 28, fontSize: 12 }}>Manage</button>}
        </div>
      </div>

      {/* Licence key row (Pro only) */}
      {isPro && (
        <div style={{
          display: "flex", alignItems: "center", gap: 10,
          padding: "10px 12px", borderRadius: 8,
          background: "var(--surface-2)", border: "1px solid var(--line)",
        }}>
          <Icon.Key size={13} />
          <div className="mono" style={{ fontSize: 12, color: "var(--ink-2)", flex: 1, letterSpacing: 0.02 }}>
            VM1-K4F7-2K9R-XQ8N-PROV
          </div>
          <button className="btn ghost" style={{ height: 24, fontSize: 11, padding: "0 8px" }}>Copy</button>
          <button className="btn ghost" style={{ height: 24, fontSize: 11, padding: "0 8px" }}>Deactivate</button>
        </div>
      )}

      {/* Free → Pro feature peek */}
      {isFree && (
        <div style={{
          display: "grid", gridTemplateColumns: "1fr 1fr",
          gap: 8, borderTop: "1px solid var(--line)",
          marginTop: 4, paddingTop: 14,
        }}>
          {[
            ["Meeting summaries", "Local summary of decisions, actions, open questions."],
            ["Named speakers", "Clean pass puts real names to each voice."],
            ["Saved instructions", "A standing prompt per kind of meeting."],
            ["Calendar attendees", "Auto-fill names from your next event, all local."],
          ].map(([t, d]) => (
            <div key={t} style={{ display: "flex", gap: 8, alignItems: "flex-start" }}>
              <Icon.Sparkle size={11} />
              <div>
                <div style={{ fontSize: 12, fontWeight: 600 }}>{t}</div>
                <div style={{ fontSize: 11, color: "var(--ink-3)", lineHeight: 1.45, marginTop: 1 }}>{d}</div>
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

// ─── Theme picker (segmented) ──────────────────────────────────────────────
function ThemePicker({ value = "system" }) {
  const opts = [
    { id: "system", label: "System", icon: <Icon.Auto size={12} /> },
    { id: "light",  label: "Light",  icon: <Icon.Sun size={12} /> },
    { id: "dark",   label: "Dark",   icon: <Icon.Moon size={12} /> },
  ];
  return (
    <div style={{
      display: "inline-flex", padding: 3, borderRadius: 8,
      border: "1px solid var(--line)", background: "var(--surface-2)",
    }}>
      {opts.map((o) => {
        const active = o.id === value;
        return (
          <button key={o.id} className="btn" style={{
            height: 26, padding: "0 10px", fontSize: 11.5,
            background: active ? "var(--surface)" : "transparent",
            border: active ? "1px solid var(--line)" : "1px solid transparent",
            color: active ? "var(--ink)" : "var(--ink-3)",
            fontWeight: active ? 600 : 500,
          }}>
            {o.icon} {o.label}
          </button>
        );
      })}
    </div>
  );
}

function SettingsScreen({ tier = "free", theme = "system" }) {
  const isPro = tier === "pro" || tier === "trial";
  const resolvedTheme = theme;

  return (
    <div style={{ flex: 1, display: "flex", overflow: "hidden" }}>
      <Sidebar active="settings" />
      <div style={{ flex: 1, overflow: "auto", padding: "28px 40px 40px" }}>
        <div style={{ maxWidth: 720, display: "flex", flexDirection: "column", gap: 24 }}>
          <h2 style={{ fontSize: 22 }}>Settings</h2>

          {/* Licence */}
          <LicenceCard tier={tier} />

          {/* Donate · only on free tier, dismissible */}
          {tier === "free" && typeof DonateCard !== "undefined" && <DonateCard compact />}

          {/* Appearance */}
          <div className="card">
            <div style={{ padding: "12px 16px", fontSize: 11, letterSpacing: 0.08,
              textTransform: "uppercase", color: "var(--ink-3)", fontWeight: 600,
              borderBottom: "1px solid var(--line)" }}>
              Appearance
            </div>
            <SettingsRow title="Theme"
              sub="System follows your OS. Dark uses the same palette, just inverted."
              control={<ThemePicker value={resolvedTheme} />} />
            <SettingsRow title="Interface size"
              sub="Slightly larger text and hit targets across the app."
              control={<div className="toggle"><i /></div>} />
          </div>

          {/* Devices */}
          <div className="card">
            <div style={{ padding: "12px 16px", fontSize: 11, letterSpacing: 0.08,
              textTransform: "uppercase", color: "var(--ink-3)", fontWeight: 600,
              borderBottom: "1px solid var(--line)" }}>
              Devices
            </div>
            <SettingsRow icon={<Icon.Mic size={14} />} title="Microphone"
              sub="MacBook Pro Microphone · default"
              control={<button className="btn ghost" style={{ height: 28 }}>Change</button>} />
            <SettingsRow icon={<Icon.Speaker size={14} />} title="System audio"
              sub="VB-Audio Cable (loopback)"
              control={<button className="btn ghost" style={{ height: 28 }}>Change</button>} />
          </div>

          {/* Transcription */}
          <div className="card">
            <div style={{ padding: "12px 16px", fontSize: 11, letterSpacing: 0.08,
              textTransform: "uppercase", color: "var(--ink-3)", fontWeight: 600,
              borderBottom: "1px solid var(--line)" }}>
              Transcription
            </div>
            <SettingsRow title="Default language" sub="Used unless you change it for a meeting."
              control={<button className="btn ghost" style={{ height: 28 }}>Auto-detect ▾</button>} />
            <SettingsRow title="Quality" sub="Balanced is recommended on your machine."
              control={<button className="btn ghost" style={{ height: 28 }}>Balanced ▾</button>} />
            <SettingsRow title="Named speakers in clean pass"
              sub="The clean second pass labels each voice with a person's name."
              control={<div className="toggle on"><i /></div>} />
            <SettingsRow title="Meeting summaries"
              sub="After saving, generate a local summary of decisions, actions, and open questions."
              control={<div className="toggle on"><i /></div>} />
            <SettingsRow title={<>Read attendees from calendar <ProBadge /></>}
              sub="Reads the next event from your local calendar only. Nothing is sent anywhere."
              control={isPro
                ? <div className="toggle"><i /></div>
                : <button className="btn ghost" style={{ height: 28, color: "var(--accent)" }}>Unlock</button>} />
            <SettingsRow title="Save transcripts to"
              sub="~/Documents/Volksmond"
              control={<button className="btn ghost" style={{ height: 28 }}>Change…</button>} />
          </div>

          {/* Data */}
          <div className="card">
            <div style={{ padding: "12px 16px", fontSize: 11, letterSpacing: 0.08,
              textTransform: "uppercase", color: "var(--ink-3)", fontWeight: 600,
              borderBottom: "1px solid var(--line)" }}>
              Data and privacy
            </div>
            <SettingsRow icon={<Icon.Lock size={14} />} title="Keep audio after a meeting"
              sub="Off by default. Audio is only kept when you turn it on for a meeting, and deleted when you choose."
              control={<div className="toggle"><i /></div>} />
            <SettingsRow icon={<Icon.Folder size={14} />} title="Open data folder"
              sub="See everything Volksmond has stored on this computer."
              control={<button className="btn ghost" style={{ height: 28 }}>Open</button>} />
            <SettingsRow title="Automatic updates"
              sub="Volksmond will check for updates on launch. The model itself never auto-updates."
              control={<div className="toggle on"><i /></div>} />
          </div>

          {/* ── DANGER ZONE ── */}
          <DangerZone tier={tier} />

          <div style={{ fontSize: 11.5, color: "var(--ink-3)", lineHeight: 1.6 }}>
            Volksmond version 0.4.2 (provisional name) · all transcription happens on this machine
            unless you explicitly opt in above.
          </div>
        </div>
      </div>
    </div>
  );
}

// ─── Danger zone ───────────────────────────────────────────────────────────
function DangerZone({ tier = "free" }) {
  const isPro = tier === "pro" || tier === "trial";
  return (
    <div style={{
      border: "1px solid color-mix(in oklch, var(--record) 30%, var(--line))",
      borderRadius: 14,
      background: "color-mix(in oklch, var(--record) 4%, var(--surface))",
      overflow: "hidden",
    }}>
      <div style={{
        display: "flex", alignItems: "center", gap: 10,
        padding: "12px 18px",
        background: "color-mix(in oklch, var(--record) 12%, transparent)",
        borderBottom: "1px solid color-mix(in oklch, var(--record) 25%, var(--line))",
      }}>
        <Icon.Alert size={14} />
        <div style={{ fontSize: 12, fontWeight: 700, letterSpacing: 0.08, textTransform: "uppercase",
          color: "var(--record)" }}>
          Danger zone · these settings send data off your computer
        </div>
      </div>

      <div style={{ padding: "4px 6px" }}>
        {/* 1 · cloud API */}
        <div style={{
          display: "grid", gridTemplateColumns: "auto 1fr auto",
          alignItems: "flex-start", gap: 14, padding: "16px 14px",
          borderBottom: "1px solid color-mix(in oklch, var(--record) 18%, var(--line))",
        }}>
          <span style={{ color: "var(--record)", marginTop: 2 }}><Icon.Cloud size={16} /></span>
          <div>
            <div style={{ fontSize: 13.5, fontWeight: 600, display: "flex", alignItems: "center", gap: 6 }}>
              Connect an online API for faster transcription
              <ProBadge />
            </div>
            <div style={{ fontSize: 12, color: "var(--ink-2)", marginTop: 4, lineHeight: 1.55 }}>
              For weak machines that can't keep up. When this is on, audio is streamed to the provider
              you choose. <b style={{ color: "var(--ink)" }}>Your audio leaves this computer.</b> The
              privacy promise no longer applies, and Volksmond will say so on every screen.
            </div>
            <div style={{ marginTop: 6, fontSize: 11.5, color: "var(--ink-3)", lineHeight: 1.5 }}>
              Not recommended for counselling, legal, or any confidential context.
            </div>
            {isPro && (
              <div style={{
                marginTop: 12, display: "flex", gap: 8, alignItems: "center",
                padding: "8px 10px", borderRadius: 8,
                background: "var(--surface)", border: "1px solid var(--line)",
              }}>
                <Icon.Key size={12} />
                <input className="field" style={{ height: 28, fontSize: 12, padding: "0 8px",
                  background: "transparent", border: 0, flex: 1, fontFamily: "var(--font-mono)" }}
                  placeholder="sk-•••• your provider key" />
                <button className="btn ghost" style={{ height: 24, fontSize: 11, padding: "0 8px" }}>
                  Test
                </button>
              </div>
            )}
          </div>
          <div className={`toggle danger`}><i /></div>
        </div>

        {/* 2 · help improve */}
        <div style={{
          display: "grid", gridTemplateColumns: "auto 1fr auto",
          alignItems: "flex-start", gap: 14, padding: "16px 14px",
        }}>
          <span style={{ color: "var(--record)", marginTop: 2 }}><Icon.Chart size={16} /></span>
          <div>
            <div style={{ fontSize: 13.5, fontWeight: 600 }}>
              Help us improve Volksmond for everyone
            </div>
            <div style={{ fontSize: 12, color: "var(--ink-2)", marginTop: 4, lineHeight: 1.55 }}>
              When you correct a word in a transcript, share the original snippet (about
              5 seconds of audio plus the words on either side) with the Volksmond team. We use
              these to make the Afrikaans-and-English model better. <b style={{ color: "var(--ink)" }}>
              These snippets leave your computer</b> and are kept on our servers.
            </div>
            <div style={{
              marginTop: 10, padding: "10px 12px", borderRadius: 8,
              background: "var(--surface)", border: "1px solid var(--line)",
              fontSize: 11.5, color: "var(--ink-2)", lineHeight: 1.55,
            }}>
              <div style={{ fontWeight: 600, color: "var(--ink)", marginBottom: 4 }}>
                What we share
              </div>
              <ul style={{ margin: 0, paddingLeft: 16, display: "flex", flexDirection: "column", gap: 2 }}>
                <li>The 5-second audio clip you corrected</li>
                <li>The wrong words and your fix</li>
                <li>The language Volksmond detected (af / en)</li>
              </ul>
              <div style={{ fontWeight: 600, color: "var(--ink)", margin: "10px 0 4px" }}>
                What we never share
              </div>
              <ul style={{ margin: 0, paddingLeft: 16, display: "flex", flexDirection: "column", gap: 2 }}>
                <li>The rest of the meeting</li>
                <li>Names, calendar info, or who you are</li>
                <li>Anything from meetings where you don't correct a word</li>
              </ul>
            </div>
            <div style={{ marginTop: 8, fontSize: 11.5, color: "var(--ink-3)" }}>
              Off by default. Turn off any time — past contributions stay on our servers.
            </div>
          </div>
          <div className={`toggle danger`}><i /></div>
        </div>
      </div>
    </div>
  );
}

// ─── Danger zone ───────────────────────────────────────────────────────────

// ════════════════════════════════════════════════════════════════════════════
// SIDEBAR (shared by home/history/settings)
// ════════════════════════════════════════════════════════════════════════════
function Sidebar({ active = "home" }) {
  const items = [
    { id: "home", label: "Meeting", icon: <Icon.Mic size={14} /> },
    { id: "history", label: "History", icon: <Icon.History size={14} /> },
    { id: "defaults", label: "Defaults", icon: <svg viewBox="0 0 16 16" width={14} height={14}><rect x="3" y="2.5" width="10" height="11" rx="1.5" fill="none" stroke="currentColor" strokeWidth="1.3"/><path d="M5.5 6 H10.5 M5.5 8.5 H10.5 M5.5 11 H8.5" stroke="currentColor" strokeWidth="1.3" strokeLinecap="round"/></svg> },
    { id: "settings", label: "Settings", icon: <Icon.Settings size={14} /> },
  ];
  return (
    <div style={{
      flex: "0 0 220px", width: 220,
      borderRight: "1px solid var(--line)",
      background: "var(--surface)",
      padding: "20px 14px",
      display: "flex", flexDirection: "column", gap: 14,
    }}>
      <div style={{ padding: "0 6px 12px", borderBottom: "1px solid var(--line)" }}>
        <Wordmark />
      </div>
      <nav style={{ display: "flex", flexDirection: "column", gap: 2 }}>
        {items.map((it) => (
          <button key={it.id} className="btn ghost" style={{
            justifyContent: "flex-start",
            height: 34, padding: "0 10px",
            background: it.id === active ? "var(--accent-soft)" : "transparent",
            color: it.id === active ? "var(--accent)" : "var(--ink-2)",
            fontWeight: it.id === active ? 600 : 500,
            border: "1px solid transparent",
          }}>
            {it.icon}
            <span>{it.label}</span>
          </button>
        ))}
      </nav>
      <div style={{ marginTop: "auto", display: "flex", flexDirection: "column", gap: 8 }}>
        <div style={{
          padding: 10, borderRadius: 8,
          background: "var(--ok-soft)", color: "var(--ok)",
          fontSize: 11.5, display: "flex", alignItems: "center", gap: 8,
          fontWeight: 500,
        }}>
          <Icon.Lock size={12} /> Local only · no internet
        </div>
        <button className="btn ghost" style={{
          justifyContent: "flex-start", height: 30, padding: "0 10px",
          fontSize: 12, color: "var(--ink-3)",
        }}>Quit Volksmond</button>
      </div>
    </div>
  );
}

// ════════════════════════════════════════════════════════════════════════════
// ERROR / EMPTY / OFFLINE states
// ════════════════════════════════════════════════════════════════════════════
function ErrorScreen({ kind = "no-mic" }) {
  const k = {
    "no-mic": {
      icon: <Icon.Mic size={22} />,
      title: "Volksmond can't hear your microphone.",
      body: "We can't access the microphone you picked. Check that it's plugged in and that Volksmond has permission to use it.",
      cta: "Open sound settings", secondary: "Pick a different microphone",
      tone: "warn",
    },
    "no-sys": {
      icon: <Icon.Speaker size={22} />,
      title: "We can't hear the other side of the meeting.",
      body: "Volksmond needs a loopback source to pick up the audio coming out of your computer. We'll walk you through it.",
      cta: "Set up loopback", secondary: "Continue with mic only",
      tone: "warn",
    },
    "model": {
      icon: <Icon.Alert size={22} />,
      title: "The language model didn't load.",
      body: "The model file looks incomplete. We can re-download it (about 1.5 GB) without losing your settings or past transcripts.",
      cta: "Re-download model", secondary: "Try again",
      tone: "danger",
    },
    "offline": {
      icon: <Icon.WifiOff size={22} />,
      title: "You're offline. That's fine.",
      body: "Volksmond works completely offline. You can start a meeting and transcribe normally. We'll skip the model update for now.",
      cta: "Start a meeting", secondary: "Try again later",
      tone: "ok",
    },
    "empty": {
      icon: <Icon.History size={22} />,
      title: "No meetings yet.",
      body: "Once you've transcribed a meeting, it'll show up here. Nothing is uploaded; your past meetings live in your data folder.",
      cta: "Start your first meeting", secondary: "Open data folder",
      tone: "muted",
    },
  }[kind];
  const tones = {
    warn: { bg: "var(--warn-soft)", fg: "var(--warn)" },
    danger: { bg: "var(--record-soft)", fg: "var(--danger)" },
    ok: { bg: "var(--ok-soft)", fg: "var(--ok)" },
    muted: { bg: "var(--surface-2)", fg: "var(--ink-3)" },
  };
  const tone = tones[k.tone];
  return (
    <div style={{ flex: 1, display: "flex", alignItems: "center", justifyContent: "center", padding: 48 }}>
      <div style={{ width: 460, textAlign: "center", display: "flex", flexDirection: "column", alignItems: "center", gap: 16 }}>
        <div style={{
          width: 52, height: 52, borderRadius: 14,
          background: tone.bg, color: tone.fg,
          display: "flex", alignItems: "center", justifyContent: "center",
        }}>{k.icon}</div>
        <h2 style={{ fontSize: 20, lineHeight: 1.2 }}>{k.title}</h2>
        <p style={{ fontSize: 13.5, color: "var(--ink-2)", lineHeight: 1.55 }}>{k.body}</p>
        <div style={{ display: "flex", gap: 8, marginTop: 4 }}>
          <button className="btn primary">{k.cta}</button>
          <button className="btn">{k.secondary}</button>
        </div>
      </div>
    </div>
  );
}

// ════════════════════════════════════════════════════════════════════════════
// MODEL-LOADING state (shown when a meeting starts and model warms up)
// ════════════════════════════════════════════════════════════════════════════
function ModelLoadingScreen() {
  return (
    <div style={{ flex: 1, display: "flex", overflow: "hidden" }}>
      <Sidebar active="home" />
      <div style={{ flex: 1, display: "flex", alignItems: "center", justifyContent: "center", padding: 48 }}>
        <div style={{ width: 420, textAlign: "center", display: "flex", flexDirection: "column", alignItems: "center", gap: 14 }}>
          <div className="spinner" style={{ width: 28, height: 28, borderWidth: 3 }} />
          <h2 style={{ fontSize: 19 }}>Warming up the model…</h2>
          <p style={{ fontSize: 13, color: "var(--ink-2)", lineHeight: 1.55 }}>
            This takes a few seconds the first time each session. After that, every meeting starts instantly.
          </p>
          <div className="mono" style={{ fontSize: 11, color: "var(--ink-3)", marginTop: 4 }}>
            Loading balanced.bin · 2.1 s
          </div>
        </div>
      </div>
    </div>
  );
}

Object.assign(window, {
  WelcomeScreen, DeviceSetupScreen, ModelDownloadScreen,
  PreMeetingScreen, FinishSaveScreen,
  HistoryScreen, SettingsScreen, Sidebar,
  ErrorScreen, ModelLoadingScreen, Icon,
  ProBadge, LicenceCard, DangerZone, ThemePicker,
});
