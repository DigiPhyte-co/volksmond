// live-screen.jsx — the main "listening" screen.
// Composes a header, a transcript region (3 swappable variants), and a footer
// with mic + system-audio meters, the mid-meeting term input, and controls.
//
// Props: { variant, recording, state, dense, paletteName }
//   variant: 'document' | 'chat' | 'codeswitch'
//   recording: bool (changes chrome accent)
//   state: 'live' | 'paused' | 'falling-behind' | 'stopped'

// ─── Bits ────────────────────────────────────────────────────────────────────
function SpeakerDot({ s, size = 22 }) {
  const sp = SPEAKERS[s] || { short: "?", label: "?" };
  const bg = s === 0 ? "var(--accent)" : "var(--surface-2)";
  const fg = s === 0 ? "var(--accent-ink)" : "var(--ink-2)";
  return (
    <span style={{
      width: size, height: size, borderRadius: "50%",
      background: bg, color: fg,
      display: "inline-flex", alignItems: "center", justifyContent: "center",
      font: `600 ${Math.round(size * 0.45)}px/1 var(--font-sans)`,
      flex: "0 0 auto",
      border: s === 0 ? "none" : "1px solid var(--line-2)",
      letterSpacing: 0,
    }}>{sp.short}</span>
  );
}

function Meter({ label, level = 0.4, muted = false, missing = false }) {
  const N = 20;
  return (
    <div style={{ display: "flex", alignItems: "center", gap: 10, minWidth: 0 }}>
      <span style={{
        fontSize: 11, fontWeight: 500, color: "var(--ink-3)",
        letterSpacing: 0.04, textTransform: "uppercase", minWidth: 70,
      }}>{label}</span>
      <div className="meter" style={{ height: 14, opacity: missing ? 0.35 : 1 }}>
        {Array.from({ length: N }).map((_, i) => {
          const on = i / N < level && !muted && !missing;
          const isPeak = i / N > 0.85;
          return (
            <span key={i} className="bar" style={{
              height: 4 + (i / N) * 10,
              background: on ? (isPeak ? "var(--warn)" : "var(--accent)") : "var(--line-2)",
              opacity: on ? 1 : 0.6,
            }} />
          );
        })}
      </div>
      {missing && <span style={{ fontSize: 11, color: "var(--warn)" }}>not detected</span>}
      {muted && !missing && <span style={{ fontSize: 11, color: "var(--ink-3)" }}>muted</span>}
    </div>
  );
}

function StatusChip({ state }) {
  if (state === "paused") return <span className="chip muted"><span className="dot" />Paused</span>;
  if (state === "falling-behind") return <span className="chip warn"><span className="dot" />Switched to fast mode</span>;
  if (state === "stopped") return <span className="chip ok"><span className="dot" />Saved</span>;
  return <span className="chip live"><span className="dot" />Listening</span>;
}

function QualityChip({ state }) {
  const label = state === "falling-behind" ? "Fast model · live preview" : "Balanced model · live preview";
  return <span className="chip">{label}</span>;
}

// ─── Header ─────────────────────────────────────────────────────────────────
function LiveHeader({ title, state, elapsed = "00:02:38" }) {
  return (
    <div className="hairline-b" style={{
      flex: "0 0 auto",
      padding: "14px 24px",
      display: "flex", alignItems: "center", gap: 14,
      background: "var(--surface)",
    }}>
      <div style={{ display: "flex", flexDirection: "column", gap: 4, minWidth: 0, flex: 1 }}>
        <div style={{
          fontSize: 13, fontWeight: 600, color: "var(--ink)",
          whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis",
        }}>{title}</div>
        <div style={{ display: "flex", alignItems: "center", gap: 8, fontSize: 11.5, color: "var(--ink-3)" }}>
          <span className="mono" style={{ fontFeatureSettings: "'tnum'" }}>{elapsed}</span>
          <span style={{ width: 3, height: 3, borderRadius: "50%", background: "var(--ink-4)" }} />
          <span>Afrikaans, auto-detect</span>
          <span style={{ width: 3, height: 3, borderRadius: "50%", background: "var(--ink-4)" }} />
          <span>Local only</span>
        </div>
      </div>
      <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
        <QualityChip state={state} />
        <StatusChip state={state} />
      </div>
    </div>
  );
}

// ─── Footer ─────────────────────────────────────────────────────────────────
function LiveFooter({ state, recording, onAddTerm }) {
  return (
    <div className="hairline-t" style={{
      flex: "0 0 auto",
      padding: "14px 24px",
      background: "var(--surface)",
      display: "flex", flexDirection: "column", gap: 10,
    }}>
      {/* Mid-meeting term input + meters */}
      <div style={{ display: "flex", alignItems: "center", gap: 18 }}>
        <div style={{ flex: 1, minWidth: 0, display: "flex", alignItems: "center", gap: 8 }}>
          <span style={{ fontSize: 11, color: "var(--ink-3)", whiteSpace: "nowrap" }}>Add term</span>
          <div style={{ position: "relative", flex: 1, minWidth: 0 }}>
            <input
              className="field"
              placeholder="e.g. EBITDA, Volksmond, Thandi Mokoena"
              style={{
                paddingLeft: 12, paddingRight: 80, height: 32, fontSize: 12.5,
              }}
              defaultValue=""
            />
            <span style={{
              position: "absolute", right: 8, top: "50%", transform: "translateY(-50%)",
              display: "flex", alignItems: "center", gap: 4,
              fontSize: 10, color: "var(--ink-3)",
            }}>
              <kbd>↵</kbd>
            </span>
          </div>
        </div>
        <Meter label="Microphone" level={0.32} />
        <Meter label="System audio" level={0.55} />
      </div>

      {/* Controls */}
      <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
        <button className="btn ghost" style={{ padding: "0 12px", height: 32 }}>
          <svg width="13" height="13" viewBox="0 0 13 13"><rect x="3" y="2.5" width="2.5" height="8" rx="0.5" fill="currentColor" /><rect x="7.5" y="2.5" width="2.5" height="8" rx="0.5" fill="currentColor" /></svg>
          {state === "paused" ? "Resume" : "Pause"}
        </button>
        <button className="btn" style={{ padding: "0 14px", height: 32 }}>
          <svg width="11" height="11" viewBox="0 0 11 11"><rect x="2" y="2" width="7" height="7" rx="1" fill="currentColor" /></svg>
          Stop and save
        </button>
        <div style={{ flex: 1 }} />
        {recording && (
          <span style={{ display: "inline-flex", alignItems: "center", gap: 6,
            fontSize: 11, color: "var(--record)", fontWeight: 500 }}>
            <i style={{
              width: 8, height: 8, borderRadius: "50%", background: "var(--record)",
              animation: "vm-pulse 1.6s ease-in-out infinite",
            }} />
            Recording audio
          </span>
        )}
        <span style={{ fontSize: 11, color: "var(--ink-3)" }}>
          Saving to <span className="mono">~/Volksmond/Q3-review.txt</span>
        </span>
      </div>
    </div>
  );
}

// ─── Falling-behind banner ──────────────────────────────────────────────────
function FallingBehindBanner() {
  return (
    <div style={{
      margin: "12px 24px 0",
      padding: "10px 14px",
      borderRadius: 10,
      background: "var(--warn-soft)",
      border: "1px solid color-mix(in oklch, var(--warn) 40%, var(--line))",
      display: "flex", alignItems: "flex-start", gap: 10,
      fontSize: 12.5, color: "var(--ink-2)",
    }}>
      <svg width="14" height="14" viewBox="0 0 14 14" style={{ flex: "0 0 auto", marginTop: 2, color: "var(--warn)" }}>
        <circle cx="7" cy="7" r="6.5" fill="none" stroke="currentColor" strokeWidth="1" />
        <path d="M7 4 V 7.5" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" />
        <circle cx="7" cy="9.8" r="0.7" fill="currentColor" />
      </svg>
      <div style={{ flex: 1 }}>
        <div style={{ fontWeight: 500, color: "var(--ink)" }}>
          Your machine is working hard. Switched to the fast model to keep up.
        </div>
        <div style={{ marginTop: 2, color: "var(--ink-3)" }}>
          The live preview may miss a word here and there. We'll mark any gap, and the clean version after the meeting will still be accurate.
        </div>
      </div>
      <button className="btn ghost" style={{ height: 24, padding: "0 8px", fontSize: 11.5 }}>Dismiss</button>
    </div>
  );
}

// ─── Variant 1 · Document ───────────────────────────────────────────────────
function TranscriptDocument({ state }) {
  return (
    <div style={{
      flex: 1, minHeight: 0, overflow: "hidden",
      display: "flex", justifyContent: "center",
      padding: "32px 24px 16px",
    }}>
      <div style={{
        width: "100%", maxWidth: 720,
        fontFamily: "var(--font-transcript)",
        fontSize: 17, lineHeight: 1.65,
        color: "var(--ink)",
      }} className="fade-top">
        {TRANSCRIPT.map((row, i) => {
          const isLast = i === TRANSCRIPT.length - 1;
          const sp = SPEAKERS[row.s];
          const faded = i < TRANSCRIPT.length - 6 ? 0.55 : 1;
          return (
            <div key={row.t} style={{
              display: "grid", gridTemplateColumns: "62px 1fr",
              columnGap: 16, rowGap: 4,
              marginBottom: 18, opacity: faded,
            }}>
              <div style={{
                fontFamily: "var(--font-mono)", fontSize: 11.5,
                color: "var(--ink-3)", paddingTop: 6,
                fontFeatureSettings: "'tnum'",
              }}>{row.t.slice(3)}</div>
              <div>
                <div style={{
                  fontFamily: "var(--font-sans)", fontSize: 11,
                  fontWeight: 600, color: speakerColor(row.s),
                  letterSpacing: 0.04, textTransform: "uppercase",
                  marginBottom: 2,
                }}>{sp.label}</div>
                <div style={{ textWrap: "pretty" }}>
                  {row.text}
                  {isLast && row.inProgress && <span className="caret" />}
                </div>
              </div>
            </div>
          );
        })}
        {state === "falling-behind" && (
          <div style={{
            display: "grid", gridTemplateColumns: "62px 1fr",
            columnGap: 16, marginTop: -4, marginBottom: 18,
            color: "var(--ink-3)", fontSize: 13,
          }}>
            <div style={{ fontFamily: "var(--font-mono)", fontSize: 11.5, paddingTop: 4 }}>02:43</div>
            <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
              <span style={{
                display: "inline-block", height: 1, flex: "0 0 24px",
                background: "var(--ink-4)",
              }} />
              <span style={{ fontStyle: "italic" }}>gap of about 4 seconds, will be filled in clean version</span>
              <span style={{
                display: "inline-block", height: 1, flex: 1,
                background: "var(--ink-4)",
              }} />
            </div>
          </div>
        )}
      </div>
    </div>
  );
}

// ─── Variant 2 · Chat ───────────────────────────────────────────────────────
function TranscriptChat({ state }) {
  return (
    <div style={{
      flex: 1, minHeight: 0, overflow: "hidden",
      padding: "20px 24px 12px",
    }}>
      <div className="fade-top" style={{
        display: "flex", flexDirection: "column", gap: 14,
        fontFamily: "var(--font-transcript)",
      }}>
        {TRANSCRIPT.map((row, i) => {
          const isYou = row.s === 0;
          const isLast = i === TRANSCRIPT.length - 1;
          const sp = SPEAKERS[row.s];
          return (
            <div key={row.t} style={{
              display: "flex", flexDirection: isYou ? "row-reverse" : "row",
              gap: 10, alignItems: "flex-start",
            }}>
              <SpeakerDot s={row.s} size={26} />
              <div style={{ maxWidth: "70%", display: "flex", flexDirection: "column", alignItems: isYou ? "flex-end" : "flex-start", gap: 3 }}>
                <div style={{
                  display: "flex", gap: 8, fontSize: 11, color: "var(--ink-3)",
                  flexDirection: isYou ? "row-reverse" : "row",
                }}>
                  <span style={{ fontWeight: 600, color: speakerColor(row.s) }}>{sp.label}</span>
                  <span className="mono" style={{ fontFeatureSettings: "'tnum'" }}>{row.t.slice(3)}</span>
                </div>
                <div style={{
                  padding: "10px 14px",
                  borderRadius: 14,
                  borderTopLeftRadius: isYou ? 14 : 4,
                  borderTopRightRadius: isYou ? 4 : 14,
                  background: isYou ? "var(--accent)" : "var(--surface)",
                  color: isYou ? "var(--accent-ink)" : "var(--ink)",
                  border: isYou ? "none" : "1px solid var(--line)",
                  fontSize: 14, lineHeight: 1.5,
                  textWrap: "pretty",
                }}>
                  {row.text}
                  {isLast && row.inProgress && <span className="caret" />}
                </div>
              </div>
            </div>
          );
        })}
        {state === "falling-behind" && (
          <div style={{
            alignSelf: "center", padding: "6px 12px",
            fontSize: 11, color: "var(--ink-3)", fontStyle: "italic",
            border: "1px dashed var(--line-2)", borderRadius: 999,
          }}>about 4 seconds couldn't be transcribed live</div>
        )}
      </div>
    </div>
  );
}

// ─── Variant 3 · Code-switch stream (novel) ─────────────────────────────────
// Each line shows inline language ribbons where the speaker switches between
// Afrikaans and English. A subtle waveform gutter sits on the left, giving a
// time-anchored sense of the conversation. This is the variant that visibly
// sells the Afrikaans/code-switch differentiator.
function LangRibbon({ lang }) {
  const isAf = lang === "af";
  return (
    <span style={{
      display: "inline-flex", alignItems: "center", justifyContent: "center",
      padding: "0 5px", height: 14, borderRadius: 3,
      fontSize: 9, fontWeight: 700, letterSpacing: 0.08,
      fontFamily: "var(--font-mono)",
      color: isAf ? "var(--accent)" : "color-mix(in oklch, var(--accent) 50%, var(--ink-3))",
      background: isAf ? "var(--accent-soft)" : "var(--surface-2)",
      border: isAf ? "none" : "1px solid var(--line)",
      textTransform: "uppercase",
      verticalAlign: "0.18em",
      marginRight: 4,
    }}>{lang}</span>
  );
}

function CodeSwitchLine({ row, isLast }) {
  const tokens = CODESWITCH_TOKENS[row.t];
  const sp = SPEAKERS[row.s];
  return (
    <div style={{
      display: "grid",
      gridTemplateColumns: "44px 18px 1fr",
      columnGap: 12,
      padding: "12px 0",
      borderBottom: "1px solid var(--line)",
    }}>
      {/* Time */}
      <div style={{
        fontFamily: "var(--font-mono)", fontSize: 11,
        color: "var(--ink-3)", paddingTop: 6,
        fontFeatureSettings: "'tnum'",
      }}>{row.t.slice(3)}</div>
      {/* Speaker bubble */}
      <div style={{ paddingTop: 4 }}>
        <SpeakerDot s={row.s} size={18} />
      </div>
      <div style={{ minWidth: 0 }}>
        <div style={{
          fontSize: 10.5, fontWeight: 600, color: speakerColor(row.s),
          letterSpacing: 0.04, textTransform: "uppercase",
          marginBottom: 4,
        }}>{sp.label}</div>
        <div style={{
          fontFamily: "var(--font-transcript)",
          fontSize: 15, lineHeight: 1.55,
          textWrap: "pretty",
        }}>
          {tokens ? (
            tokens.map((tok, j) => (
              <React.Fragment key={j}>
                <LangRibbon lang={tok.lang} />
                <span style={{ marginRight: 6 }}>{tok.text}</span>
              </React.Fragment>
            ))
          ) : (
            <><LangRibbon lang={row.lang === "mix" ? "af" : row.lang} />{row.text}</>
          )}
          {isLast && row.inProgress && <span className="caret" />}
        </div>
      </div>
    </div>
  );
}

function TranscriptCodeSwitch({ state }) {
  return (
    <div style={{
      flex: 1, minHeight: 0, overflow: "hidden",
      display: "flex",
      padding: "0 24px",
    }}>
      <div className="fade-top" style={{
        flex: 1, minWidth: 0,
      }}>
        {TRANSCRIPT.map((row, i) => (
          <CodeSwitchLine key={row.t} row={row} isLast={i === TRANSCRIPT.length - 1} />
        ))}
        {state === "falling-behind" && (
          <div style={{
            padding: "10px 0", display: "flex", alignItems: "center", gap: 10,
            color: "var(--ink-3)", fontSize: 12,
          }}>
            <span style={{ height: 1, flex: "0 0 24px", background: "var(--ink-4)" }} />
            <span style={{ fontStyle: "italic" }}>about 4 seconds will be filled in the clean version</span>
            <span style={{ height: 1, flex: 1, background: "var(--ink-4)" }} />
          </div>
        )}
      </div>
    </div>
  );
}

// ─── LiveScreen — composes the above ────────────────────────────────────────
function LiveScreen({ variant = "document", state = "live", recording = false, title = "Q3 strategy review · Thandi & Lebo" }) {
  const Body = {
    document: TranscriptDocument,
    chat: TranscriptChat,
    codeswitch: TranscriptCodeSwitch,
  }[variant] || TranscriptDocument;

  return (
    <>
      <LiveHeader title={title} state={state} />
      {state === "falling-behind" && <FallingBehindBanner />}
      <Body state={state} />
      <LiveFooter state={state} recording={recording} />
    </>
  );
}

Object.assign(window, {
  LiveScreen, LiveHeader, LiveFooter, FallingBehindBanner,
  TranscriptDocument, TranscriptChat, TranscriptCodeSwitch,
  SpeakerDot, Meter, StatusChip, QualityChip,
});
