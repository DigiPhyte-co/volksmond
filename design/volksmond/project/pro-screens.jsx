// pro-screens.jsx — second-pass additions:
// upgrade, offline activation, defaults / AI instructions, language pickers,
// donate, plus the lock / tier / add-on / instruction-selector primitives.
//
// Wireframe-fidelity. Same tokens as the rest of the app, but a slightly
// thinner, more schematic treatment so the system reads as work-in-progress.

// ─── Tiny extra icons (reuse Icon.* where possible) ─────────────────────────
const PIcon = {
  LockSm: (p) => <svg viewBox="0 0 16 16" width={p.size||12} height={p.size||12}>
    <rect x="4" y="7.5" width="8" height="6" rx="1" fill="none" stroke="currentColor" strokeWidth="1.2"/>
    <path d="M6 7.5 V5.5 a2 2 0 0 1 4 0 V7.5" fill="none" stroke="currentColor" strokeWidth="1.2"/>
  </svg>,
  Heart: (p) => <svg viewBox="0 0 16 16" width={p.size||12} height={p.size||12}>
    <path d="M8 13 C 3 9.5 2 6 4 4.5 a2.6 2.6 0 0 1 4 0.6 a2.6 2.6 0 0 1 4 -0.6 C 14 6 13 9.5 8 13 Z"
      fill="currentColor" opacity="0.85"/>
  </svg>,
  Globe: (p) => <svg viewBox="0 0 16 16" width={p.size||14} height={p.size||14}>
    <circle cx="8" cy="8" r="5.5" fill="none" stroke="currentColor" strokeWidth="1.3"/>
    <path d="M2.5 8 H13.5 M8 2.5 a8 8 0 0 1 0 11 a8 8 0 0 1 0 -11" fill="none" stroke="currentColor" strokeWidth="1.3"/>
  </svg>,
  Note: (p) => <svg viewBox="0 0 16 16" width={p.size||14} height={p.size||14}>
    <rect x="3" y="2.5" width="10" height="11" rx="1.5" fill="none" stroke="currentColor" strokeWidth="1.3"/>
    <path d="M5.5 6 H10.5 M5.5 8.5 H10.5 M5.5 11 H8.5" stroke="currentColor" strokeWidth="1.3" strokeLinecap="round"/>
  </svg>,
  Tag: (p) => <svg viewBox="0 0 16 16" width={p.size||13} height={p.size||13}>
    <path d="M2.5 8 L8 13.5 L13.5 8 L8 2.5 H4 a1.5 1.5 0 0 0 -1.5 1.5 Z" fill="none" stroke="currentColor" strokeWidth="1.3" strokeLinejoin="round"/>
    <circle cx="5.5" cy="5.5" r="0.9" fill="currentColor"/>
  </svg>,
};

// ─── Lock chip · how a Pro feature looks to a Free user ─────────────────────
function LockChip({ inline = false }) {
  return (
    <span style={{
      display: "inline-flex", alignItems: "center", gap: 4,
      padding: inline ? "1px 6px" : "2px 7px",
      borderRadius: 999,
      fontSize: 10, fontWeight: 600, letterSpacing: 0.04,
      background: "var(--surface-2)", color: "var(--ink-3)",
      border: "1px solid var(--line)",
    }}>
      <PIcon.LockSm size={9} /> Part of Pro
    </span>
  );
}

// ─── Tier chip (more honest than a logo) ────────────────────────────────────
function TierChip({ tier = "free" }) {
  if (tier === "free") {
    return (
      <span className="chip" style={{ height: 18, fontSize: 10, color: "var(--ink-3)" }}>Free</span>
    );
  }
  if (tier === "trial") {
    return (
      <span style={{
        display: "inline-flex", alignItems: "center", gap: 4,
        height: 18, padding: "0 7px", borderRadius: 999,
        fontSize: 10, fontWeight: 600, letterSpacing: 0.04,
        background: "color-mix(in oklch, var(--accent) 16%, var(--surface))",
        color: "var(--accent)",
        border: "1px solid color-mix(in oklch, var(--accent) 26%, transparent)",
      }}>Pro · trial</span>
    );
  }
  return (
    <span style={{
      display: "inline-flex", alignItems: "center", gap: 4,
      height: 18, padding: "0 7px", borderRadius: 999,
      fontSize: 10, fontWeight: 700, letterSpacing: 0.06,
      background: "var(--accent)", color: "var(--accent-ink)",
    }}>
      <Icon.Crown size={9} /> Pro
    </span>
  );
}

// ─── Licence-key field ─────────────────────────────────────────────────────
function LicenceKeyField({ state = "empty", value = "", onChange }) {
  // state: 'empty' | 'typing' | 'success' | 'error-bad' | 'error-version'
  const tones = {
    success:        { ring: "var(--ok)", soft: "var(--ok-soft)" },
    "error-bad":    { ring: "var(--danger)", soft: "var(--record-soft)" },
    "error-version":{ ring: "var(--warn)", soft: "var(--warn-soft)" },
  };
  const tone = tones[state];
  const display = state === "success"
    ? "VM1-K4F7-2K9R-XQ8N-PROV"
    : state === "error-bad"
      ? "VM1-K4F7-XXXX-XQ8N-XXXX"
      : state === "error-version"
        ? "VM2-K4F7-2K9R-XQ8N-PROV"
        : value;
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
      <div style={{
        display: "flex", alignItems: "center", gap: 8,
        padding: "0 10px", height: 42, borderRadius: 8,
        background: "var(--surface)",
        border: `1px solid ${tone ? tone.ring : "var(--line-2)"}`,
        boxShadow: tone ? `inset 0 0 0 2px ${tone.soft}` : "none",
        fontFamily: "var(--font-mono)",
      }}>
        <Icon.Key size={13} />
        <input style={{
          flex: 1, border: 0, outline: 0, background: "transparent",
          fontFamily: "var(--font-mono)", fontSize: 13, letterSpacing: 0.04,
          color: "var(--ink)",
        }}
        placeholder="Paste your licence key, e.g. VM1-XXXX-XXXX-XXXX-XXXX"
        value={display}
        onChange={onChange || (() => {})}
        readOnly={state !== "empty" && state !== "typing"} />
        {state === "typing" && (
          <button className="btn" style={{ height: 28, fontSize: 12 }}>Activate</button>
        )}
        {state === "success" && (
          <span style={{ color: "var(--ok)", display: "inline-flex", alignItems: "center", gap: 4, fontSize: 11.5 }}>
            <Icon.Check size={11} strokeWidth={2.2} /> Verified
          </span>
        )}
        {state === "error-bad" && (
          <span style={{ color: "var(--danger)", fontSize: 11.5 }}>Invalid</span>
        )}
        {state === "error-version" && (
          <span style={{ color: "var(--warn)", fontSize: 11.5 }}>Wrong version</span>
        )}
      </div>
      {state === "success" && (
        <div style={{ fontSize: 11.5, color: "var(--ink-2)" }}>
          Activated offline. Your licence is valid for version 1 of Volksmond on this computer.
        </div>
      )}
      {state === "error-bad" && (
        <div style={{ fontSize: 11.5, color: "var(--danger)" }}>
          That key did not match. Check for a typo, or paste it directly from your purchase email.
          You can also activate from a file (see Settings).
        </div>
      )}
      {state === "error-version" && (
        <div style={{ fontSize: 11.5, color: "var(--warn)" }}>
          That key is for version 2. This app is version 1. You can either install version 2,
          or use your earlier version 1 key here.
        </div>
      )}
    </div>
  );
}

// ─── Add-on row ────────────────────────────────────────────────────────────
function AddOnRow({ title, sub, price, owned = false, danger = false }) {
  return (
    <div style={{
      display: "grid", gridTemplateColumns: "1fr auto",
      gap: 16, padding: "14px 16px",
      borderBottom: "1px solid var(--line)",
      alignItems: "center",
    }}>
      <div>
        <div style={{ fontSize: 13.5, fontWeight: 600, display: "flex", alignItems: "center", gap: 8 }}>
          {title}
          {danger && (
            <span style={{
              fontSize: 10, fontWeight: 700, letterSpacing: 0.04,
              color: "var(--record)",
            }}>· sends data off device</span>
          )}
        </div>
        <div style={{ fontSize: 12, color: "var(--ink-2)", marginTop: 3, lineHeight: 1.5 }}>{sub}</div>
      </div>
      <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
        <div className="mono" style={{ fontSize: 12, color: "var(--ink-3)" }}>{price}</div>
        {owned
          ? <span className="chip ok" style={{ height: 22, fontSize: 10.5 }}>
              <Icon.Check size={10} strokeWidth={2.2} /> Added
            </span>
          : <button className="btn" style={{ height: 28, fontSize: 12 }}>Add</button>}
      </div>
    </div>
  );
}

// ════════════════════════════════════════════════════════════════════════════
// UPGRADE SCREEN
// ════════════════════════════════════════════════════════════════════════════
function UpgradeScreen({ keyState = "empty" }) {
  // keyState forwarded to <LicenceKeyField/>
  return (
    <div style={{ flex: 1, display: "flex", justifyContent: "center", overflow: "auto",
      padding: "36px 32px" }}>
      <div style={{ width: 640, display: "flex", flexDirection: "column", gap: 24 }}>
        <div>
          <div className="eyebrow" style={{ marginBottom: 10 }}>Upgrade</div>
          <h1 style={{ fontSize: 26 }}>Pro adds the polish, not the privacy.</h1>
          <p style={{ color: "var(--ink-2)", fontSize: 13.5, marginTop: 8, lineHeight: 1.55 }}>
            Free is the real thing: unlimited live transcription, fully on this machine,
            forever. Pro adds the small, professional features that turn a transcript into
            something you can hand someone.
          </p>
        </div>

        {/* Two-column comparison */}
        <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 12 }}>
          {/* Free */}
          <div style={{
            padding: 18, borderRadius: 12,
            border: "1px solid var(--line)", background: "var(--surface)",
            display: "flex", flexDirection: "column", gap: 12,
          }}>
            <div style={{ display: "flex", alignItems: "baseline", justifyContent: "space-between" }}>
              <div style={{ fontSize: 14, fontWeight: 600 }}>Free</div>
              <div className="mono" style={{ fontSize: 12, color: "var(--ink-3)" }}>R 0</div>
            </div>
            <ul style={{ margin: 0, padding: 0, listStyle: "none",
              display: "flex", flexDirection: "column", gap: 8 }}>
              {[
                "Unlimited local live transcription",
                "Afrikaans, English, code-switch",
                "Save transcripts as plain text",
                "Works fully offline",
              ].map((t) => (
                <li key={t} style={{ display: "flex", gap: 8, alignItems: "flex-start", fontSize: 12.5, color: "var(--ink-2)" }}>
                  <Icon.Check size={12} strokeWidth={2.2} /> {t}
                </li>
              ))}
            </ul>
          </div>

          {/* Pro */}
          <div style={{
            padding: 18, borderRadius: 12,
            border: "1px solid color-mix(in oklch, var(--accent) 30%, var(--line))",
            background: "color-mix(in oklch, var(--accent) 6%, var(--surface))",
            display: "flex", flexDirection: "column", gap: 12,
          }}>
            <div style={{ display: "flex", alignItems: "baseline", justifyContent: "space-between" }}>
              <div style={{ display: "flex", alignItems: "center", gap: 8, fontSize: 14, fontWeight: 600 }}>
                Pro
                <TierChip tier="pro" />
              </div>
              <div className="mono" style={{ fontSize: 12, color: "var(--ink-3)" }}>R 599 · one-time</div>
            </div>
            <ul style={{ margin: 0, padding: 0, listStyle: "none",
              display: "flex", flexDirection: "column", gap: 8 }}>
              {[
                ["Pull attendee names from your calendar", "Reads your local calendar so the clean pass can label each voice with a real name."],
                ["Use an online transcription API for weak machines", "Optional. Audio leaves your computer only if you turn it on, per meeting."],
                ["Use an online summary API for harder transcripts", "Optional. Sends the transcript to a provider you choose. Off by default."],
              ].map(([t, d]) => (
                <li key={t} style={{ display: "flex", gap: 8, alignItems: "flex-start", fontSize: 12.5, color: "var(--ink-2)" }}>
                  <Icon.Check size={12} strokeWidth={2.2} />
                  <span><span style={{ color: "var(--ink)", fontWeight: 500 }}>{t}.</span> <span style={{ color: "var(--ink-3)" }}>{d}</span></span>
                </li>
              ))}
            </ul>
            <div style={{
              padding: "8px 10px", borderRadius: 6,
              background: "var(--surface)", border: "1px solid var(--line)",
              fontSize: 11, color: "var(--ink-3)", lineHeight: 1.5,
            }}>
              Pro covers the things that need an online connection. Everything that runs on this
              computer (live transcription, the clean second pass, local summaries, history,
              exports) stays in Free. Perpetual licence: you own version 1 forever, version 2
              would be a separate purchase with an existing-owner discount.
            </div>
          </div>
        </div>

        {/* Buy + activate */}
        <div style={{
          padding: 18, borderRadius: 12, border: "1px solid var(--line)",
          background: "var(--surface)", display: "flex", flexDirection: "column", gap: 14,
        }}>
          <div style={{ display: "flex", alignItems: "center", gap: 12 }}>
            <button className="btn primary tall" style={{ padding: "0 22px" }}>
              Buy Pro for R 599
            </button>
            <div style={{ fontSize: 12, color: "var(--ink-3)" }}>
              Opens your browser. We will email you a licence key.
            </div>
          </div>
          <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
            <div style={{ flex: 1, height: 1, background: "var(--line)" }} />
            <div style={{ fontSize: 10.5, letterSpacing: 0.08, textTransform: "uppercase",
              color: "var(--ink-3)" }}>Already bought</div>
            <div style={{ flex: 1, height: 1, background: "var(--line)" }} />
          </div>
          <LicenceKeyField state={keyState} />
          <div style={{ fontSize: 11, color: "var(--ink-3)", lineHeight: 1.5 }}>
            Activation is fully offline. Your key is checked on this computer, never on a server.
            No account, no email login, no phone-home.
          </div>
        </div>

        <div style={{ fontSize: 11, color: "var(--ink-3)" }}>
          Prices in South African Rand, inclusive of VAT. Other currencies on the website.
        </div>
      </div>
    </div>
  );
}

// ════════════════════════════════════════════════════════════════════════════
// LICENCE STATUS panel · settings inset, supersedes the simple licence card
// when the user opens "Manage" or "Licence" from Settings.
// ════════════════════════════════════════════════════════════════════════════
function LicenceStatusPanel({ tier = "pro", versionNote = "current" }) {
  // versionNote: 'current' | 'v2-available'
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 14 }}>
      <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
        <h3 style={{ fontSize: 16 }}>Your licence</h3>
        <TierChip tier={tier} />
      </div>

      <div className="card" style={{ padding: 0 }}>
        <div style={{
          display: "grid", gridTemplateColumns: "160px 1fr",
          padding: "12px 16px", borderBottom: "1px solid var(--line)",
          fontSize: 12.5,
        }}>
          <div style={{ color: "var(--ink-3)" }}>Owned by</div>
          <div>jaco@digiphyte.com</div>
        </div>
        <div style={{
          display: "grid", gridTemplateColumns: "160px 1fr",
          padding: "12px 16px", borderBottom: "1px solid var(--line)",
          fontSize: 12.5,
        }}>
          <div style={{ color: "var(--ink-3)" }}>Covers version</div>
          <div>Version 1 (1.x), perpetual</div>
        </div>
        <div style={{
          display: "grid", gridTemplateColumns: "160px 1fr",
          padding: "12px 16px", borderBottom: "1px solid var(--line)",
          fontSize: 12.5,
        }}>
          <div style={{ color: "var(--ink-3)" }}>Activated on</div>
          <div>14 May 2026, this computer (offline)</div>
        </div>
        <div style={{
          display: "grid", gridTemplateColumns: "160px 1fr",
          padding: "12px 16px",
          fontSize: 12.5,
        }}>
          <div style={{ color: "var(--ink-3)" }}>Licence key</div>
          <div className="mono" style={{ letterSpacing: 0.04 }}>VM1-K4F7-2K9R-XQ8N-PROV</div>
        </div>
      </div>

      <div style={{ fontSize: 12, color: "var(--ink-3)", lineHeight: 1.5 }}>
        Volksmond never contacts our servers to check this. The key is verified locally each
        time the app starts.
      </div>

      {/* Version-2 note */}
      {versionNote === "v2-available" && (
        <div style={{
          padding: 16, borderRadius: 12,
          border: "1px solid var(--line)",
          background: "var(--surface)",
          display: "flex", gap: 14, alignItems: "flex-start",
        }}>
          <div style={{
            width: 32, height: 32, borderRadius: 8,
            background: "color-mix(in oklch, var(--accent) 14%, var(--surface-2))",
            color: "var(--accent)",
            display: "inline-flex", alignItems: "center", justifyContent: "center",
            flex: "0 0 auto",
          }}><Icon.Sparkle size={15} /></div>
          <div style={{ flex: 1 }}>
            <div style={{ fontSize: 13.5, fontWeight: 600 }}>
              Volksmond 2 is available
            </div>
            <div style={{ fontSize: 12, color: "var(--ink-2)", marginTop: 4, lineHeight: 1.55 }}>
              Your licence covers version 1. Version 2 adds on-device translation between
              Afrikaans and English, and a slower, more accurate base model. It is a separate
              purchase: R 349 with your existing licence, R 599 otherwise.
            </div>
            <div style={{ marginTop: 10, display: "flex", gap: 8 }}>
              <button className="btn">See what's new</button>
              <button className="btn ghost">Stay on version 1</button>
            </div>
          </div>
        </div>
      )}

      {/* Add-ons */}
      <div className="card" style={{ padding: 0, marginTop: 6 }}>
        <div style={{
          padding: "10px 16px", fontSize: 11, letterSpacing: 0.08, textTransform: "uppercase",
          color: "var(--ink-3)", fontWeight: 600, borderBottom: "1px solid var(--line)",
          display: "flex", justifyContent: "space-between", alignItems: "center",
        }}>
          <span>Add-ons</span>
          <span style={{ fontSize: 10.5, color: "var(--ink-4)", textTransform: "none",
            letterSpacing: 0, fontWeight: 500 }}>
            Optional, separate from Pro
          </span>
        </div>
        <AddOnRow
          title="Cloud transcription fallback"
          sub="Connect your own API key (OpenAI Whisper, Deepgram, others). Lets Volksmond fall back to the cloud on weak machines."
          price="Free · BYO key"
          danger
        />
        <AddOnRow
          title="Multi-seat licence"
          sub="Activate Volksmond on up to 5 computers under one licence. Hand-off, swap, no reactivation hassle."
          price="R 1 499 · one-time"
        />
        <AddOnRow
          title="Priority support"
          sub="Email reply within one business day, direct line to the engineer."
          price="R 199 / year"
          owned
        />
      </div>
    </div>
  );
}

// ════════════════════════════════════════════════════════════════════════════
// LOCKED FEATURE ROW · inline lock affordance in a settings row
// ════════════════════════════════════════════════════════════════════════════
function LockedSettingsRow({ icon, title, sub }) {
  return (
    <div style={{
      display: "grid", gridTemplateColumns: "auto 1fr auto",
      alignItems: "center", gap: 14,
      padding: "14px 16px",
      borderBottom: "1px solid var(--line)",
      background: "color-mix(in oklch, var(--surface) 50%, var(--surface-2))",
    }}>
      <span style={{ color: "var(--ink-4)" }}>{icon}</span>
      <div>
        <div style={{ fontSize: 13.5, fontWeight: 500, display: "flex", alignItems: "center", gap: 8, color: "var(--ink-2)" }}>
          {title} <LockChip />
        </div>
        {sub && <div style={{ fontSize: 11.5, color: "var(--ink-3)", marginTop: 2, lineHeight: 1.5 }}>{sub}</div>}
      </div>
      <button className="btn ghost" style={{ height: 28, fontSize: 12, color: "var(--accent)" }}>
        Learn more
      </button>
    </div>
  );
}

// ════════════════════════════════════════════════════════════════════════════
// DONATE affordance · dismissible, non-blocking
// ════════════════════════════════════════════════════════════════════════════
function DonateCard({ dismissed = false, compact = false }) {
  if (dismissed) return null;
  if (compact) {
    return (
      <div style={{
        display: "flex", alignItems: "center", gap: 10,
        padding: "8px 10px", borderRadius: 8,
        border: "1px dashed var(--line-2)",
        background: "transparent",
        fontSize: 11.5, color: "var(--ink-3)",
      }}>
        <PIcon.Heart size={11} />
        <span style={{ flex: 1 }}>Donate to keep Volksmond free.</span>
        <button className="btn ghost" style={{ height: 22, fontSize: 11, padding: "0 6px" }}>
          Donate
        </button>
        <button className="btn ghost" style={{ height: 22, fontSize: 11, padding: "0 6px", color: "var(--ink-4)" }}>
          ×
        </button>
      </div>
    );
  }
  return (
    <div style={{
      padding: 16, borderRadius: 12,
      border: "1px solid var(--line)", background: "var(--surface)",
      display: "flex", gap: 14, alignItems: "flex-start", position: "relative",
    }}>
      <div style={{
        width: 32, height: 32, borderRadius: 8,
        background: "color-mix(in oklch, var(--accent) 14%, var(--surface-2))",
        color: "var(--accent)",
        display: "inline-flex", alignItems: "center", justifyContent: "center",
        flex: "0 0 auto",
      }}><PIcon.Heart size={14} /></div>
      <div style={{ flex: 1 }}>
        <div style={{ fontSize: 13.5, fontWeight: 600 }}>
          Volksmond is free. If it's useful, you can support it.
        </div>
        <div style={{ fontSize: 12, color: "var(--ink-2)", marginTop: 4, lineHeight: 1.55 }}>
          The core app is free for everyone, forever. A small donation funds the
          Afrikaans model and keeps the lights on. Any amount, once or monthly, in any
          currency. No login, no receipt-chasing.
        </div>
        <div style={{ marginTop: 10, display: "flex", gap: 8 }}>
          <button className="btn">Donate once</button>
          <button className="btn ghost">Set up monthly</button>
        </div>
      </div>
      <button className="btn ghost" style={{
        position: "absolute", top: 8, right: 8, height: 24, padding: "0 8px",
        fontSize: 11, color: "var(--ink-4)",
      }}>
        Don't ask again
      </button>
    </div>
  );
}

// ════════════════════════════════════════════════════════════════════════════
// LANGUAGE PICKERS
// ════════════════════════════════════════════════════════════════════════════

// — Interface language (the app's chrome) — Settings inset.
function InterfaceLanguagePicker({ value = "en-za" }) {
  const langs = [
    { id: "en-za", label: "English (South Africa)", note: "Baseline" },
    { id: "en-gb", label: "English (United Kingdom)" },
    { id: "en-us", label: "English (United States)" },
    { id: "af",    label: "Afrikaans", note: "Translated by the community, 92% complete" },
    { id: "nl",    label: "Nederlands", note: "Coming soon" },
    { id: "de",    label: "Deutsch", note: "Coming soon" },
  ];
  return (
    <div className="card" style={{ padding: 0 }}>
      <div style={{
        padding: "12px 16px", display: "flex", alignItems: "center", gap: 10,
        borderBottom: "1px solid var(--line)",
      }}>
        <PIcon.Globe size={14} />
        <div style={{ flex: 1 }}>
          <div style={{ fontSize: 13.5, fontWeight: 600 }}>Interface language</div>
          <div style={{ fontSize: 11.5, color: "var(--ink-3)", marginTop: 2 }}>
            The language the app shows you. Doesn't change what gets transcribed.
          </div>
        </div>
      </div>
      <div style={{ display: "flex", flexDirection: "column" }}>
        {langs.map((l) => {
          const active = l.id === value;
          const disabled = l.note === "Coming soon";
          return (
            <label key={l.id} style={{
              display: "flex", alignItems: "center", gap: 12,
              padding: "10px 16px",
              borderBottom: "1px solid var(--line)",
              opacity: disabled ? 0.5 : 1,
              background: active ? "color-mix(in oklch, var(--accent) 8%, var(--surface))" : "transparent",
            }}>
              <span style={{
                width: 14, height: 14, borderRadius: "50%",
                border: `1.5px solid ${active ? "var(--accent)" : "var(--line-2)"}`,
                background: active ? "var(--accent)" : "transparent",
                boxShadow: active ? "inset 0 0 0 3px var(--surface)" : "none",
                flex: "0 0 auto",
              }} />
              <div style={{ flex: 1 }}>
                <div style={{ fontSize: 13, fontWeight: active ? 600 : 500 }}>{l.label}</div>
                {l.note && (
                  <div style={{ fontSize: 11, color: "var(--ink-3)", marginTop: 1 }}>{l.note}</div>
                )}
              </div>
            </label>
          );
        })}
      </div>
      <div style={{ padding: "10px 16px", fontSize: 11.5, color: "var(--ink-3)" }}>
        Help translate Volksmond at volksmond.app/translate. Strings are plain English so
        anyone literate in their language can contribute.
      </div>
    </div>
  );
}

// — Transcription language picker (what's being spoken). Afrikaans first-class.
function TranscriptionLanguagePicker({ value = "auto" }) {
  const headline = [
    { id: "auto", label: "Auto-detect", note: "Picks Afrikaans, English, or a mix as people speak. Recommended." },
    { id: "af",   label: "Afrikaans", note: "Best accuracy for South African Afrikaans." },
    { id: "en-za", label: "English", note: "South African English first; American and British also recognised." },
    { id: "af-en", label: "Afrikaans + English (code-switch)", note: "For meetings that switch in the same sentence." },
  ];
  const others = [
    "Zulu", "Xhosa", "Sesotho", "Setswana",
    "Nederlands", "Deutsch", "Português", "Français",
    "Español", "Italiano", "Polski",
  ];
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 14 }}>
      <div>
        <label style={{ fontSize: 12, color: "var(--ink-3)", display: "block", marginBottom: 6 }}>
          Transcription language
        </label>
        <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
          {headline.map((l) => {
            const active = l.id === value;
            return (
              <label key={l.id} style={{
                display: "flex", alignItems: "flex-start", gap: 12,
                padding: "10px 12px", borderRadius: 8,
                border: `1px solid ${active ? "var(--accent)" : "var(--line)"}`,
                background: active ? "color-mix(in oklch, var(--accent) 8%, var(--surface))" : "var(--surface)",
              }}>
                <span style={{
                  width: 14, height: 14, marginTop: 2, borderRadius: "50%",
                  border: `1.5px solid ${active ? "var(--accent)" : "var(--line-2)"}`,
                  background: active ? "var(--accent)" : "transparent",
                  boxShadow: active ? "inset 0 0 0 3px var(--surface)" : "none",
                  flex: "0 0 auto",
                }} />
                <div style={{ flex: 1 }}>
                  <div style={{ fontSize: 13, fontWeight: active ? 600 : 500 }}>{l.label}</div>
                  <div style={{ fontSize: 11.5, color: "var(--ink-3)", marginTop: 2, lineHeight: 1.5 }}>{l.note}</div>
                </div>
              </label>
            );
          })}
        </div>
      </div>

      <details style={{
        border: "1px solid var(--line)", borderRadius: 8,
        background: "var(--surface)",
      }}>
        <summary style={{
          padding: "10px 14px", fontSize: 12, color: "var(--ink-2)",
          cursor: "pointer", listStyle: "none", display: "flex",
          alignItems: "center", gap: 8,
        }}>
          <svg width="10" height="10" viewBox="0 0 10 10" style={{ color: "var(--ink-3)" }}>
            <path d="M2 4 L5 7 L8 4" fill="none" stroke="currentColor" strokeWidth="1.3"
              strokeLinecap="round" strokeLinejoin="round" />
          </svg>
          Other languages (slower model, varying accuracy)
        </summary>
        <div style={{
          padding: "4px 14px 14px",
          display: "grid", gridTemplateColumns: "1fr 1fr 1fr",
          gap: 6, fontSize: 12, color: "var(--ink-2)",
        }}>
          {others.map((l) => (
            <label key={l} style={{ display: "flex", alignItems: "center", gap: 6, padding: "4px 0" }}>
              <input type="radio" name="other-lang" style={{ accentColor: "var(--accent)" }} /> {l}
            </label>
          ))}
        </div>
      </details>
    </div>
  );
}

// ════════════════════════════════════════════════════════════════════════════
// DEFAULTS / CUSTOM INSTRUCTIONS
// ════════════════════════════════════════════════════════════════════════════
function DefaultsScreen({ tier = "free", state = "set" }) {
  // state: 'empty' | 'set'
  const isPro = tier === "pro" || tier === "trial";
  return (
    <div style={{ flex: 1, display: "flex", overflow: "hidden" }}>
      <Sidebar active="defaults" />
      <div style={{ flex: 1, overflow: "auto", padding: "28px 40px 40px" }}>
        <div style={{ maxWidth: 760, display: "flex", flexDirection: "column", gap: 24 }}>
          <div>
            <h2 style={{ fontSize: 22 }}>Defaults</h2>
            <p style={{ color: "var(--ink-2)", fontSize: 13.5, marginTop: 6, lineHeight: 1.55,
              maxWidth: 560 }}>
              Set once, applied to every meeting. You can still override anything per meeting
              from the Start screen.
            </p>
          </div>

          {/* 1 · Context terms */}
          <div className="card">
            <div style={{ padding: "12px 16px", fontSize: 11, letterSpacing: 0.08,
              textTransform: "uppercase", color: "var(--ink-3)", fontWeight: 600,
              borderBottom: "1px solid var(--line)",
              display: "flex", alignItems: "center", justifyContent: "space-between" }}>
              <span>Default context · names and jargon</span>
              <span style={{ fontSize: 10.5, color: "var(--ink-4)", textTransform: "none",
                letterSpacing: 0, fontWeight: 500 }}>
                Helps accuracy
              </span>
            </div>
            <div style={{ padding: 16, display: "flex", flexDirection: "column", gap: 10 }}>
              {state === "empty" ? (
                <div style={{
                  padding: 16, borderRadius: 8, background: "var(--surface-2)",
                  border: "1px dashed var(--line-2)",
                  fontSize: 12.5, color: "var(--ink-3)", textAlign: "center", lineHeight: 1.5,
                }}>
                  No defaults yet. Add the names and terms you use most, like client names,
                  colleagues, internal acronyms, or product names.
                </div>
              ) : (
                <div style={{
                  padding: 10, borderRadius: 8, border: "1px solid var(--line)",
                  background: "var(--surface)", display: "flex", flexWrap: "wrap", gap: 6, minHeight: 64,
                  alignItems: "flex-start",
                }}>
                  {["Thandi Mokoena", "Lebo van Wyk", "Sipho Dlamini", "Pieter Coetzee",
                    "Digiphyte", "Volksmond", "EBITDA", "go-to-market", "OKR",
                    "Q3", "uitkomstgerig", "voldoeningsverklaring"].map((t) => (
                    <span key={t} style={{
                      display: "inline-flex", alignItems: "center", gap: 6,
                      padding: "4px 8px", borderRadius: 999,
                      background: "var(--accent-soft)", color: "var(--accent)",
                      fontSize: 12, fontWeight: 500,
                    }}>
                      {t} <span style={{ opacity: 0.5, fontSize: 13 }}>×</span>
                    </span>
                  ))}
                </div>
              )}
              <div style={{ display: "flex", gap: 8, alignItems: "center" }}>
                <input className="field" placeholder="Add a term and press enter…" />
                <button className="btn" style={{ height: 36 }}>Import from file…</button>
              </div>
              <div style={{ fontSize: 11.5, color: "var(--ink-3)", lineHeight: 1.5 }}>
                Stored on this computer only. Applied to every new meeting; you can still
                add or remove terms per meeting.
              </div>
            </div>
          </div>

          {/* 2 · AI instructions */}
          <div style={{
            border: "1px solid var(--line)", borderRadius: 14,
            background: "var(--surface)",
            opacity: isPro ? 1 : 0.96,
          }}>
            <div style={{
              padding: "12px 16px", fontSize: 11, letterSpacing: 0.08,
              textTransform: "uppercase", color: "var(--ink-3)", fontWeight: 600,
              borderBottom: "1px solid var(--line)",
              display: "flex", alignItems: "center", justifyContent: "space-between",
            }}>
              <span>Default AI instructions</span>
              <span style={{ fontSize: 10.5, color: "var(--ink-4)", textTransform: "none",
                letterSpacing: 0, fontWeight: 500 }}>
                Governs summaries and the clean pass
              </span>
            </div>

            {!isPro ? (
              <div style={{
                padding: 22, display: "flex", flexDirection: "column", gap: 10,
                alignItems: "center", textAlign: "center",
              }}>
                <PIcon.Note size={20} />
                <div style={{ fontSize: 13.5, fontWeight: 600 }}>
                  Tell Volksmond how you want your transcripts processed
                </div>
                <div style={{ fontSize: 12.5, color: "var(--ink-2)", maxWidth: 440, lineHeight: 1.55 }}>
                  Save a few standing instructions (one for client meetings, one for
                  counselling sessions, one for personal notes) and switch between them
                  with a click. Part of Pro.
                </div>
                <button className="btn" style={{ height: 30, marginTop: 4 }}>Learn more</button>
              </div>
            ) : (
              <div style={{ padding: 16, display: "flex", flexDirection: "column", gap: 14 }}>
                {/* Profile selector */}
                <SavedInstructionSelector active="consultant" />

                {/* The currently active prompt */}
                <div>
                  <label style={{ fontSize: 12, color: "var(--ink-3)", display: "block",
                    marginBottom: 6 }}>
                    Instruction · Consultant
                  </label>
                  <textarea
                    style={{
                      width: "100%", minHeight: 130, padding: 12,
                      borderRadius: 8, border: "1px solid var(--line)",
                      background: "var(--surface-2)", color: "var(--ink)",
                      fontFamily: "var(--font-sans)", fontSize: 13, lineHeight: 1.55,
                      resize: "vertical",
                    }}
                    defaultValue={
`Produce a clean summary structured as:
1. Decisions made (with who decided)
2. Action items (with owner and due date if stated)
3. Open questions / parked items
4. Direct quotes worth keeping

Use British English spelling. Keep names exactly as transcribed. Don't editorialise.`
                    }
                  />
                </div>

                {/* Tone / format toggles */}
                <div style={{
                  display: "grid", gridTemplateColumns: "1fr 1fr",
                  gap: 10, fontSize: 12, color: "var(--ink-2)",
                }}>
                  {[
                    "Include direct quotes",
                    "Anonymise speakers",
                    "Detect action items automatically",
                    "Tag follow-ups with a date",
                  ].map((t, i) => (
                    <label key={t} style={{
                      display: "flex", alignItems: "center", gap: 8,
                      padding: "8px 10px", borderRadius: 8,
                      border: "1px solid var(--line)", background: "var(--surface-2)",
                    }}>
                      <span className={`toggle ${i < 2 ? "on" : ""}`} style={{ transform: "scale(0.85)" }}>
                        <i />
                      </span>
                      {t}
                    </label>
                  ))}
                </div>

                <div style={{
                  display: "flex", justifyContent: "space-between", alignItems: "center",
                  paddingTop: 4,
                }}>
                  <div style={{ fontSize: 11.5, color: "var(--ink-3)" }}>
                    Runs locally with the on-device summariser. Your transcripts never leave
                    this machine.
                  </div>
                  <div style={{ display: "flex", gap: 8 }}>
                    <button className="btn ghost">Reset</button>
                    <button className="btn">Save changes</button>
                  </div>
                </div>
              </div>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}

// ─── Saved-instruction selector (segmented + "+" for a new one) ─────────────
function SavedInstructionSelector({ active = "consultant" }) {
  const profiles = [
    { id: "consultant", label: "Consultant", sub: "Decisions, actions, parked items" },
    { id: "counsellor", label: "Counsellor", sub: "Themes, follow-ups, no quotes" },
    { id: "personal",   label: "Personal",   sub: "Loose notes, no structure" },
  ];
  return (
    <div>
      <div style={{
        display: "flex", alignItems: "center", justifyContent: "space-between",
        marginBottom: 8,
      }}>
        <div style={{ fontSize: 12, color: "var(--ink-3)" }}>Active profile</div>
        <button className="btn ghost" style={{ height: 26, fontSize: 11.5, padding: "0 8px" }}>
          <Icon.Plus size={11} /> New profile
        </button>
      </div>
      <div style={{
        display: "grid", gridTemplateColumns: "1fr 1fr 1fr",
        gap: 8,
      }}>
        {profiles.map((p) => {
          const isActive = p.id === active;
          return (
            <button key={p.id} className="btn" style={{
              height: "auto", padding: "10px 12px",
              flexDirection: "column", alignItems: "flex-start",
              gap: 4, textAlign: "left",
              background: isActive ? "color-mix(in oklch, var(--accent) 8%, var(--surface))" : "var(--surface)",
              border: `1px solid ${isActive ? "var(--accent)" : "var(--line)"}`,
            }}>
              <div style={{ display: "flex", alignItems: "center", gap: 6, width: "100%" }}>
                <PIcon.Tag size={11} />
                <span style={{ fontWeight: 600, fontSize: 12.5, flex: 1 }}>{p.label}</span>
                {isActive && <Icon.Check size={11} strokeWidth={2.2} />}
              </div>
              <div style={{ fontSize: 11, color: "var(--ink-3)", lineHeight: 1.4, fontWeight: 400 }}>
                {p.sub}
              </div>
            </button>
          );
        })}
      </div>
    </div>
  );
}

Object.assign(window, {
  PIcon, LockChip, TierChip, LicenceKeyField, AddOnRow,
  UpgradeScreen, LicenceStatusPanel, LockedSettingsRow,
  DonateCard, InterfaceLanguagePicker, TranscriptionLanguagePicker,
  DefaultsScreen, SavedInstructionSelector,
});
