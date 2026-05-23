// v1-summary-action.jsx — the Summarise button states, the resulting
// summary panel, and the Settings · Summaries inset.

// ─── The Summarise button. All five states it can wear. ────────────────────
function SummariseButton({ state = "ready", onClick }) {
  // state:
  //   'disabled-incomplete' — transcript still running
  //   'needs-download'      — summaries are on, but the model isn't here yet
  //   'downloading'         — model is downloading right now
  //   'ready'               — go
  //   'running'             — summarisation in progress
  //   'done'                — summary exists, button opens it
  //
  // (Summaries are local. Not gated by Pro. Pro is only for things that
  //  actually need an online connection.)
  const map = {
    "disabled-incomplete": {
      label: "Summarise",
      sub: "Available once the transcript is complete.",
      disabled: true, kind: "ghost", icon: <SIcon.Note size={13} />,
    },
    "needs-download": {
      label: "Set up summaries",
      sub: "The summary model is not on this computer yet. About 0.6 GB.",
      kind: "primary", icon: <SIcon.Download size={13} />,
    },
    "downloading": {
      label: "Downloading model…",
      sub: "62% · about 90 seconds left. You can keep working.",
      disabled: true, kind: "ghost", icon: <span className="spinner" style={{ width: 12, height: 12, borderWidth: 2 }} />,
    },
    "ready": {
      label: "Summarise",
      sub: "Runs on this computer, using the Quiet model. About 20 seconds for this transcript.",
      kind: "primary", icon: <SIcon.Note size={13} />,
    },
    "running": {
      label: "Summarising…",
      sub: "Reading the full transcript. About 14 seconds left.",
      disabled: true, kind: "ghost", icon: <span className="spinner" style={{ width: 12, height: 12, borderWidth: 2 }} />,
    },
    "done": {
      label: "Open summary",
      sub: "Saved alongside this transcript.",
      kind: "", icon: <Icon.Check size={13} strokeWidth={2.2} />,
    },
  }[state];

  return (
    <div style={{
      padding: 16, borderRadius: 12,
      border: `1px solid ${map.locked ? "var(--line)" :
        state === "ready" || state === "needs-download" ? "color-mix(in oklch, var(--accent) 26%, var(--line))" :
        "var(--line)"}`,
      background: state === "ready" || state === "needs-download"
        ? "color-mix(in oklch, var(--accent) 5%, var(--surface))"
        : map.locked ? "color-mix(in oklch, var(--surface) 60%, var(--surface-2))"
        : "var(--surface)",
      display: "flex", gap: 14, alignItems: "flex-start",
    }}>
      <span style={{
        width: 30, height: 30, borderRadius: 8,
        background: map.locked ? "var(--surface-2)" : "var(--accent-soft)",
        color: map.locked ? "var(--ink-4)" : "var(--accent)",
        display: "inline-flex", alignItems: "center", justifyContent: "center",
        flex: "0 0 auto",
      }}>
        {map.locked ? <Icon.Lock size={14} /> : <Icon.Sparkle size={14} />}
      </span>
      <div style={{ flex: 1, minWidth: 0 }}>
        <div style={{ fontSize: 13.5, fontWeight: 600,
          color: map.disabled || map.locked ? "var(--ink-2)" : "var(--ink)" }}>
          {state === "disabled-incomplete" ? "Summarise" :
           state === "needs-download" ? "Summarise this transcript" :
           state === "ready" ? "Summarise this transcript" :
           state === "downloading" ? "Setting up summaries" :
           state === "running" ? "Working on your summary" :
           "Summary ready"}
          {map.locked && <span style={{ marginLeft: 8 }}><LockChip /></span>}
        </div>
        <div style={{ fontSize: 12, color: "var(--ink-2)", marginTop: 3, lineHeight: 1.5 }}>
          {map.sub}
        </div>
        {state === "downloading" && (
          <div style={{
            marginTop: 10, height: 4, borderRadius: 2,
            background: "var(--surface-2)", overflow: "hidden",
          }}>
            <div style={{ width: "62%", height: "100%", background: "var(--accent)" }} />
          </div>
        )}
        {state === "running" && (
          <div style={{
            marginTop: 10, height: 4, borderRadius: 2,
            background: "var(--surface-2)", overflow: "hidden",
          }}>
            <div style={{ width: "38%", height: "100%", background: "var(--accent)" }} />
          </div>
        )}
      </div>
      <div style={{ display: "flex", flexDirection: "column", gap: 6, alignItems: "flex-end" }}>
        <button
          className={`btn ${map.kind}`}
          style={map.disabled ? { opacity: 0.55, cursor: "default" } : {}}
          onClick={onClick}
        >
          {map.icon} {map.label}
        </button>
        {state === "downloading" && (
          <button className="btn ghost" style={{ height: 26, fontSize: 11.5 }}>Cancel</button>
        )}
        {state === "running" && (
          <button className="btn ghost" style={{ height: 26, fontSize: 11.5 }}>Cancel</button>
        )}
      </div>
    </div>
  );
}

// ─── The summary, once it exists. Saved alongside the transcript. ──────────
function SummaryResult({ saved = true }) {
  return (
    <div style={{
      borderRadius: 14, border: "1px solid var(--line)",
      background: "var(--surface)", overflow: "hidden",
    }}>
      <div style={{
        padding: "12px 16px",
        borderBottom: "1px solid var(--line)",
        background: "color-mix(in oklch, var(--accent) 5%, var(--surface))",
        display: "flex", alignItems: "center", gap: 10,
      }}>
        <span style={{
          width: 22, height: 22, borderRadius: 6,
          background: "var(--accent-soft)", color: "var(--accent)",
          display: "inline-flex", alignItems: "center", justifyContent: "center",
        }}>
          <Icon.Sparkle size={12} />
        </span>
        <div style={{ flex: 1 }}>
          <div style={{ fontSize: 13, fontWeight: 600 }}>Summary</div>
          <div style={{ fontSize: 11, color: "var(--ink-3)", marginTop: 1 }}>
            Quiet model · ran on this computer · {saved ? "saved next to the transcript" : "not saved yet"}
          </div>
        </div>
        <button className="btn ghost" style={{ height: 26, fontSize: 11.5 }}>Copy</button>
        <button className="btn ghost" style={{ height: 26, fontSize: 11.5 }}>Regenerate</button>
      </div>

      <div style={{ padding: "16px 20px", display: "flex", flexDirection: "column", gap: 16 }}>
        <SumSection title="Decisions" items={[
          ["Move the Cape Town launch to 14 August.",
            "Thandi confirmed; Lebo to update the timeline doc."],
          ["Keep the existing pricing tier names for now.",
            "Revisit after the September pipeline review."],
        ]} />
        <SumSection title="Action items" items={[
          ["Lebo: send the revised supplier list by Friday."],
          ["Thandi: confirm the venue deposit for the 14th."],
          ["You: write up the consultant brief from the EBITDA conversation."],
        ]} />
        <SumSection title="Open questions" items={[
          ["Who owns the Pretoria broadcast slot once Sipho rotates off?"],
          ["Do we keep go-to-market language in the Afrikaans deck, or translate it?"],
        ]} />

        {saved && (
          <div style={{
            padding: "10px 12px", borderRadius: 8,
            background: "var(--ok-soft)", border: "1px solid color-mix(in oklch, var(--ok) 30%, var(--line))",
            display: "flex", alignItems: "center", gap: 10,
            fontSize: 12, color: "var(--ink-2)",
          }}>
            <Icon.Check size={12} strokeWidth={2.2} />
            <span>
              Saved as <span className="mono">Q3-review.summary.txt</span>, next to the transcript.
              Nothing was sent off this computer.
            </span>
          </div>
        )}
      </div>
    </div>
  );
}

function SumSection({ title, items }) {
  return (
    <div>
      <div style={{
        fontSize: 10.5, fontWeight: 600, letterSpacing: 0.08, textTransform: "uppercase",
        color: "var(--ink-3)", marginBottom: 8,
      }}>{title}</div>
      <ul style={{ margin: 0, padding: 0, listStyle: "none",
        display: "flex", flexDirection: "column", gap: 8 }}>
        {items.map(([head, sub], i) => (
          <li key={i} style={{
            display: "grid", gridTemplateColumns: "14px 1fr",
            gap: 10, fontSize: 13, lineHeight: 1.55, textWrap: "pretty",
          }}>
            <span style={{
              width: 6, height: 6, marginTop: 8, borderRadius: "50%",
              background: "var(--accent)",
            }} />
            <div>
              <div>{head}</div>
              {sub && <div style={{ fontSize: 11.5, color: "var(--ink-3)", marginTop: 2 }}>{sub}</div>}
            </div>
          </li>
        ))}
      </ul>
    </div>
  );
}

// ─── Settings · Summaries section ─────────────────────────────────────────
function SummariesSettingsSection({ enabled = true, installed = "quiet" }) {
  return (
    <div className="card" style={{ overflow: "hidden" }}>
      <div style={{ padding: "12px 16px", fontSize: 11, letterSpacing: 0.08,
        textTransform: "uppercase", color: "var(--ink-3)", fontWeight: 600,
        borderBottom: "1px solid var(--line)",
        display: "flex", alignItems: "center", justifyContent: "space-between" }}>
        <span>Summaries</span>
        <span style={{ fontSize: 10.5, color: "var(--ink-4)", textTransform: "none",
          letterSpacing: 0, fontWeight: 500 }}>
          Runs on this machine
        </span>
      </div>

      {/* On/off */}
      <div style={{
        display: "grid", gridTemplateColumns: "auto 1fr auto",
        alignItems: "center", gap: 14, padding: "14px 16px",
        borderBottom: "1px solid var(--line)",
      }}>
        <span style={{ color: "var(--ink-3)" }}><SIcon.Note size={14} /></span>
        <div>
          <div style={{ fontSize: 13.5, fontWeight: 500 }}>Generate summaries after a meeting</div>
          <div style={{ fontSize: 11.5, color: "var(--ink-3)", marginTop: 2, lineHeight: 1.5 }}>
            The Summarise button appears once a transcript is complete. Nothing runs without you asking.
          </div>
        </div>
        <div className={`toggle ${enabled ? "on" : ""}`}><i /></div>
      </div>

      {/* Installed model */}
      <div style={{
        display: "grid", gridTemplateColumns: "auto 1fr auto",
        alignItems: "center", gap: 14, padding: "14px 16px",
        borderBottom: "1px solid var(--line)",
      }}>
        <span style={{ color: "var(--ink-3)" }}><SIcon.Cpu size={14} /></span>
        <div>
          <div style={{ fontSize: 13.5, fontWeight: 500 }}>Installed model</div>
          <div style={{ fontSize: 11.5, color: "var(--ink-3)", marginTop: 2, lineHeight: 1.5 }}>
            {installed === "quiet" && "Quiet · 0.6 GB · works on 8 GB of RAM."}
            {installed === "considered" && "Considered · 1.4 GB · works best with 16 GB of RAM."}
            {installed === "none" && "None yet. Download a model to turn summaries on."}
          </div>
        </div>
        <button className="btn ghost" style={{ height: 28, fontSize: 12 }}>
          {installed === "none" ? "Download…" : "Switch model…"}
        </button>
      </div>

      {/* Model picker, inline expanded view */}
      <div style={{ padding: "12px 16px", borderBottom: "1px solid var(--line)" }}>
        <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 8 }}>
          <MiniModelCard
            title="Quiet"
            size="0.6 GB"
            ram="8 GB"
            installed={installed === "quiet"}
          />
          <MiniModelCard
            title="Considered"
            size="1.4 GB"
            ram="16 GB"
            note="more accurate, needs more RAM"
            installed={installed === "considered"}
          />
        </div>
      </div>

      {/* Output language */}
      <div style={{
        display: "grid", gridTemplateColumns: "auto 1fr auto",
        alignItems: "center", gap: 14, padding: "14px 16px",
      }}>
        <span style={{ color: "var(--ink-3)" }}><svg viewBox="0 0 16 16" width="14" height="14"><circle cx="8" cy="8" r="5.5" fill="none" stroke="currentColor" strokeWidth="1.3"/><path d="M2.5 8 H13.5 M8 2.5 a8 8 0 0 1 0 11 a8 8 0 0 1 0 -11" fill="none" stroke="currentColor" strokeWidth="1.3"/></svg></span>
        <div>
          <div style={{ fontSize: 13.5, fontWeight: 500 }}>Summary language</div>
          <div style={{ fontSize: 11.5, color: "var(--ink-3)", marginTop: 2, lineHeight: 1.5 }}>
            What the summary is written in. The transcript itself stays in whatever language was spoken.
          </div>
        </div>
        <button className="btn ghost" style={{ height: 28 }}>Match transcript ▾</button>
      </div>
    </div>
  );
}

function MiniModelCard({ title, size, ram, note, installed }) {
  return (
    <div style={{
      padding: "10px 12px", borderRadius: 8,
      border: `1px solid ${installed ? "var(--accent)" : "var(--line)"}`,
      background: installed ? "color-mix(in oklch, var(--accent) 6%, var(--surface))" : "var(--surface)",
      display: "flex", flexDirection: "column", gap: 4,
    }}>
      <div style={{ display: "flex", alignItems: "center", gap: 6 }}>
        <span style={{ fontSize: 12.5, fontWeight: 600 }}>{title}</span>
        {installed && (
          <span className="chip ok" style={{ height: 16, fontSize: 9.5 }}>
            <Icon.Check size={9} strokeWidth={2.4} /> Installed
          </span>
        )}
      </div>
      <div className="mono" style={{ fontSize: 11, color: "var(--ink-3)" }}>
        {size} · {ram} RAM
      </div>
      {note && <div style={{ fontSize: 11, color: "var(--ink-3)", lineHeight: 1.45 }}>{note}</div>}
      {!installed && (
        <button className="btn ghost" style={{ height: 24, fontSize: 11, padding: "0 8px", alignSelf: "flex-start", marginTop: 2 }}>
          Download…
        </button>
      )}
    </div>
  );
}

Object.assign(window, {
  SummariseButton, SummaryResult, SumSection,
  SummariesSettingsSection, MiniModelCard,
});
