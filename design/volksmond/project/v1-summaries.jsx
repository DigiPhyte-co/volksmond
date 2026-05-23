// v1-summaries.jsx — third-pass additions: local AI summaries.
// First-run setup question, model picker, the Summarise action with all its
// states, the result panel, and the Settings · Summaries section.
//
// Voice: warm, calm, no em or en dashes. Pro is a quiet upgrade, never a wall.

// ─── Tiny icons local to these screens ─────────────────────────────────────
const SIcon = {
  Note: (p) => <svg viewBox="0 0 16 16" width={p.size||14} height={p.size||14}>
    <path d="M3 2.5 H10 L13 5.5 V13.5 H3 Z" fill="none" stroke="currentColor" strokeWidth="1.3" strokeLinejoin="round"/>
    <path d="M10 2.5 V5.5 H13" fill="none" stroke="currentColor" strokeWidth="1.3" strokeLinejoin="round"/>
    <path d="M5 8 H11 M5 10 H11 M5 12 H9" stroke="currentColor" strokeWidth="1.2" strokeLinecap="round"/>
  </svg>,
  Cpu: (p) => <svg viewBox="0 0 16 16" width={p.size||14} height={p.size||14}>
    <rect x="3.5" y="3.5" width="9" height="9" rx="1.2" fill="none" stroke="currentColor" strokeWidth="1.3"/>
    <rect x="5.5" y="5.5" width="5" height="5" fill="none" stroke="currentColor" strokeWidth="1.1"/>
    <path d="M6 1.5 V3.5 M8 1.5 V3.5 M10 1.5 V3.5 M6 12.5 V14.5 M8 12.5 V14.5 M10 12.5 V14.5 M1.5 6 H3.5 M1.5 8 H3.5 M1.5 10 H3.5 M12.5 6 H14.5 M12.5 8 H14.5 M12.5 10 H14.5" stroke="currentColor" strokeWidth="1.1" strokeLinecap="round"/>
  </svg>,
  Download: (p) => <svg viewBox="0 0 16 16" width={p.size||14} height={p.size||14}>
    <path d="M8 2 V10 M4.5 7 L8 10.5 L11.5 7" fill="none" stroke="currentColor" strokeWidth="1.3" strokeLinecap="round" strokeLinejoin="round"/>
    <path d="M2.5 13 H13.5" stroke="currentColor" strokeWidth="1.3" strokeLinecap="round"/>
  </svg>,
};

// ════════════════════════════════════════════════════════════════════════════
// SETUP · the summarise question + model picker
// Two states stitched into one screen: the question, and (if yes) the picker.
// ════════════════════════════════════════════════════════════════════════════
function SummariesSetupScreen({ stage = "ask", picked = "quiet" }) {
  // stage: 'ask' | 'pick'
  const isPick = stage === "pick";
  return (
    <div style={{ flex: 1, display: "flex", justifyContent: "center", padding: "44px 32px", overflow: "auto" }}>
      <div style={{ width: 560, display: "flex", flexDirection: "column", gap: 22 }}>
        <div>
          <div className="eyebrow" style={{ marginBottom: 10 }}>Setup · summaries</div>
          <h1 style={{ fontSize: 26, letterSpacing: -0.02 }}>
            {isPick
              ? "Pick the summary model that fits your machine."
              : "Do you want to just transcribe, or also summarise on your machine?"}
          </h1>
          <p style={{ color: "var(--ink-2)", fontSize: 14, marginTop: 8, lineHeight: 1.55, maxWidth: 500 }}>
            {isPick
              ? "Both run entirely on your computer. The bigger one is more accurate and reads the room better. The quieter one is faster and asks less of your hardware."
              : "Summaries condense a finished transcript into the decisions, the to-dos, and what stayed unresolved. They run on a small model on your machine, separate from the one that does the transcribing. Off by default."}
          </p>
        </div>

        {!isPick && (
          <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
            <SetupChoiceCard
              recommended
              icon={<Icon.Mic size={18} />}
              title="Just transcribe"
              body="The original promise. Live transcripts, clean second pass, history, all of it. No extra download, no extra RAM."
              note="Default. You can turn summaries on later in Settings."
            />
            <SetupChoiceCard
              icon={<SIcon.Note size={18} />}
              title="Transcribe and summarise"
              body="Adds a Summarise button at the end of every meeting. We will pick a model with you next, and you can download it now or later."
              note="Optional download, between 0.6 GB and 1.4 GB."
            />
          </div>
        )}

        {isPick && (
          <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
            <ModelPickRow
              id="quiet"
              picked={picked === "quiet"}
              title="Quiet"
              size="0.6 GB"
              ram="8 GB recommended"
              one="Fast on most machines, including older laptops."
              two="Good for tight bullet summaries and action items. Reads names and jargon reliably."
            />
            <ModelPickRow
              id="considered"
              picked={picked === "considered"}
              title="Considered"
              size="1.4 GB"
              ram="16 GB recommended"
              one="Slower. More careful. Better with long transcripts and nuance."
              two="Catches more decisions, reads tone, summarises code-switched conversations cleanly."
              accurate
            />

            <div style={{
              padding: "10px 12px", borderRadius: 10,
              background: "var(--surface)", border: "1px solid var(--line)",
              fontSize: 11.5, color: "var(--ink-2)", lineHeight: 1.5,
              display: "flex", gap: 10, alignItems: "flex-start",
            }}>
              <span style={{ color: "var(--ink-3)", marginTop: 1 }}><Icon.Lock size={12} /></span>
              <span>
                The larger model is more accurate but needs more RAM, so it can slow down a small
                machine if other apps are open. Both models stay on your computer once downloaded.
              </span>
            </div>
          </div>
        )}

        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginTop: 4 }}>
          <button className="btn ghost">{isPick ? "Back" : "Skip for now"}</button>
          <div style={{ display: "flex", gap: 8, alignItems: "center" }}>
            {isPick && (
              <button className="btn">Download later</button>
            )}
            <button className="btn primary tall" style={{ padding: "0 22px" }}>
              {isPick ? "Download now" : "Continue"}
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}

function SetupChoiceCard({ icon, title, body, note, recommended }) {
  return (
    <label style={{
      display: "grid", gridTemplateColumns: "auto 1fr auto",
      gap: 14, padding: "16px 18px",
      borderRadius: 12,
      border: `1px solid ${recommended ? "var(--accent)" : "var(--line)"}`,
      background: recommended ? "color-mix(in oklch, var(--accent) 6%, var(--surface))" : "var(--surface)",
      alignItems: "flex-start",
    }}>
      <span style={{
        width: 36, height: 36, borderRadius: 10,
        background: recommended ? "var(--accent)" : "var(--surface-2)",
        color: recommended ? "var(--accent-ink)" : "var(--accent)",
        display: "inline-flex", alignItems: "center", justifyContent: "center",
        flex: "0 0 auto",
      }}>{icon}</span>
      <div style={{ minWidth: 0 }}>
        <div style={{ display: "flex", gap: 8, alignItems: "center", marginBottom: 4 }}>
          <span style={{ fontSize: 14, fontWeight: 600 }}>{title}</span>
          {recommended && (
            <span className="chip" style={{ height: 18, fontSize: 10, color: "var(--accent)",
              background: "var(--accent-soft)", border: "none" }}>Default</span>
          )}
        </div>
        <div style={{ fontSize: 12.5, color: "var(--ink-2)", lineHeight: 1.55 }}>{body}</div>
        {note && <div style={{ fontSize: 11.5, color: "var(--ink-3)", marginTop: 8, lineHeight: 1.5 }}>{note}</div>}
      </div>
      <span style={{
        width: 16, height: 16, borderRadius: "50%",
        border: `1.5px solid ${recommended ? "var(--accent)" : "var(--line-2)"}`,
        background: recommended ? "var(--accent)" : "transparent",
        boxShadow: recommended ? "inset 0 0 0 3px var(--surface)" : "none",
        flex: "0 0 auto", marginTop: 10,
      }} />
    </label>
  );
}

function ModelPickRow({ id, picked, title, size, ram, one, two, accurate }) {
  return (
    <label style={{
      display: "grid", gridTemplateColumns: "auto 1fr",
      gap: 14, padding: "14px 16px",
      borderRadius: 12,
      border: `1px solid ${picked ? "var(--accent)" : "var(--line)"}`,
      background: picked ? "color-mix(in oklch, var(--accent) 6%, var(--surface))" : "var(--surface)",
    }}>
      <span style={{
        width: 16, height: 16, marginTop: 3, borderRadius: "50%",
        border: `1.5px solid ${picked ? "var(--accent)" : "var(--line-2)"}`,
        background: picked ? "var(--accent)" : "transparent",
        boxShadow: picked ? "inset 0 0 0 3px var(--surface)" : "none",
        flex: "0 0 auto",
      }} />
      <div style={{ minWidth: 0 }}>
        <div style={{ display: "flex", alignItems: "baseline", gap: 10, marginBottom: 4, flexWrap: "wrap" }}>
          <span style={{ fontSize: 14, fontWeight: 600 }}>{title}</span>
          {accurate && (
            <span className="chip" style={{ height: 18, fontSize: 10, color: "var(--accent)",
              background: "var(--accent-soft)", border: "none" }}>More accurate</span>
          )}
          <span className="mono" style={{ fontSize: 11.5, color: "var(--ink-3)" }}>
            {size} · {ram}
          </span>
        </div>
        <div style={{ fontSize: 12.5, color: "var(--ink-2)", lineHeight: 1.55 }}>{one}</div>
        <div style={{ fontSize: 12.5, color: "var(--ink-3)", lineHeight: 1.55, marginTop: 4 }}>{two}</div>
      </div>
    </label>
  );
}

Object.assign(window, {
  SIcon, SummariesSetupScreen, SetupChoiceCard, ModelPickRow,
});
