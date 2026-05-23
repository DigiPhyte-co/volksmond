// chrome.jsx — Windows 11 + macOS window frames, and the Volksmond wordmark.
// Pure presentational components; the screen content goes inside.

const VMMark = ({ size = 22 }) => (
  <svg width={size} height={size} viewBox="0 0 22 22" fill="none"
       xmlns="http://www.w3.org/2000/svg" aria-hidden="true">
    {/* A small mouth/sound-glyph: lower lip arc + three rising sound levels */}
    <path d="M3 12.5 C 6 16.5, 16 16.5, 19 12.5"
          stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" fill="none" />
    <path d="M7.5 8.5 L 7.5 6"
          stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" />
    <path d="M11 8.5 L 11 4"
          stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" />
    <path d="M14.5 8.5 L 14.5 6"
          stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" />
  </svg>
);

const Wordmark = ({ subtle = false, showProvisional = false }) => (
  <div className="wordmark" style={{ opacity: subtle ? 0.75 : 1 }}>
    <span className="mark"><VMMark size={20} /></span>
    <span>Volksmond</span>
    {showProvisional && (
      <span style={{
        fontSize: 10, letterSpacing: 0.08, textTransform: "uppercase",
        color: "var(--ink-3)", fontWeight: 500, marginLeft: 6,
        padding: "2px 6px", border: "1px solid var(--line)", borderRadius: 4,
      }}>working name</span>
    )}
  </div>
);

// ─── Windows 11 frame ─────────────────────────────────────────────────────────
function Win11Frame({ title = "Volksmond", width = 1200, height = 760, children, recording = false }) {
  return (
    <div style={{
      width, height,
      borderRadius: 8,
      overflow: "hidden",
      background: "var(--bg)",
      boxShadow: "0 0 0 1px rgba(0,0,0,.10), 0 30px 60px -20px rgba(0,0,0,.30), 0 10px 20px -10px rgba(0,0,0,.15)",
      display: "flex", flexDirection: "column",
      position: "relative",
      fontFamily: "var(--font-sans)",
      color: "var(--ink)",
    }}>
      {/* Title bar */}
      <div style={{
        height: 36, flex: "0 0 36px",
        display: "flex", alignItems: "stretch",
        background: "var(--surface)",
        borderBottom: "1px solid var(--line)",
        userSelect: "none",
      }}>
        {/* App identity (drag region) */}
        <div style={{
          display: "flex", alignItems: "center", gap: 10,
          padding: "0 14px", flex: 1, minWidth: 0,
        }}>
          <div style={{ color: "var(--accent)", display: "flex" }}>
            <VMMark size={15} />
          </div>
          <div style={{
            fontSize: 12, fontWeight: 500, color: "var(--ink-2)",
            whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis",
            letterSpacing: -0.01,
          }}>
            {title}
            {recording && (
              <span style={{
                marginLeft: 10, padding: "1px 7px", borderRadius: 999,
                background: "var(--record-soft)", color: "var(--record)",
                fontWeight: 600, fontSize: 10.5, letterSpacing: 0.04,
                display: "inline-flex", alignItems: "center", gap: 5, verticalAlign: "1px",
              }}>
                <i style={{
                  width: 6, height: 6, borderRadius: "50%", background: "var(--record)",
                  display: "inline-block", animation: "vm-pulse 1.6s ease-in-out infinite",
                }} />
                Recording
              </span>
            )}
          </div>
        </div>
        {/* Win11 controls — minimize, maximize, close */}
        <div style={{ display: "flex" }}>
          {[
            <svg key="m" width="10" height="10" viewBox="0 0 10 10"><path d="M0 5h10" stroke="currentColor" strokeWidth="1" /></svg>,
            <svg key="x" width="10" height="10" viewBox="0 0 10 10"><rect x="0.5" y="0.5" width="9" height="9" fill="none" stroke="currentColor" strokeWidth="1" /></svg>,
            <svg key="c" width="10" height="10" viewBox="0 0 10 10"><path d="M0 0 L10 10 M10 0 L0 10" stroke="currentColor" strokeWidth="1" /></svg>,
          ].map((g, i) => (
            <div key={i} style={{
              width: 46, display: "flex", alignItems: "center", justifyContent: "center",
              color: "var(--ink-2)",
              background: i === 2 ? "transparent" : "transparent",
            }}>{g}</div>
          ))}
        </div>
      </div>
      {/* Content */}
      <div style={{ flex: 1, minHeight: 0, display: "flex", flexDirection: "column", background: "var(--bg)" }}>
        {children}
      </div>
    </div>
  );
}

// ─── macOS frame ──────────────────────────────────────────────────────────────
function MacFrame({ title = "Volksmond", width = 1200, height = 760, children, recording = false }) {
  return (
    <div style={{
      width, height,
      borderRadius: 10,
      overflow: "hidden",
      background: "var(--bg)",
      boxShadow: "0 0 0 0.5px rgba(0,0,0,.15), 0 30px 60px -20px rgba(0,0,0,.30), 0 10px 20px -10px rgba(0,0,0,.15)",
      display: "flex", flexDirection: "column",
      position: "relative",
      fontFamily: "var(--font-sans)",
      color: "var(--ink)",
    }}>
      <div style={{
        height: 38, flex: "0 0 38px",
        display: "grid", gridTemplateColumns: "120px 1fr 120px",
        alignItems: "center",
        background: "var(--surface)",
        borderBottom: "1px solid var(--line)",
        userSelect: "none",
      }}>
        <div style={{ display: "flex", gap: 8, padding: "0 14px" }}>
          {["#ff5f57", "#febc2e", "#28c840"].map((c) => (
            <span key={c} style={{
              width: 12, height: 12, borderRadius: "50%", background: c,
              boxShadow: "inset 0 0 0 0.5px rgba(0,0,0,.15)",
            }} />
          ))}
        </div>
        <div style={{
          textAlign: "center", fontSize: 12.5, fontWeight: 500, color: "var(--ink-2)",
          letterSpacing: -0.005,
          display: "inline-flex", justifyContent: "center", alignItems: "center", gap: 10,
        }}>
          <span>{title}</span>
          {recording && (
            <span style={{
              padding: "1px 7px", borderRadius: 999,
              background: "var(--record-soft)", color: "var(--record)",
              fontWeight: 600, fontSize: 10.5, letterSpacing: 0.04,
              display: "inline-flex", alignItems: "center", gap: 5,
            }}>
              <i style={{
                width: 6, height: 6, borderRadius: "50%", background: "var(--record)",
                display: "inline-block", animation: "vm-pulse 1.6s ease-in-out infinite",
              }} />
              Recording
            </span>
          )}
        </div>
        <div />
      </div>
      <div style={{ flex: 1, minHeight: 0, display: "flex", flexDirection: "column", background: "var(--bg)" }}>
        {children}
      </div>
    </div>
  );
}

// Generic chrome chooser
function WindowChrome({ kind = "win11", ...rest }) {
  return kind === "mac" ? <MacFrame {...rest} /> : <Win11Frame {...rest} />;
}

Object.assign(window, { Win11Frame, MacFrame, WindowChrome, Wordmark, VMMark });
