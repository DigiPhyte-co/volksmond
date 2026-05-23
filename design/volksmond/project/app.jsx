// app.jsx — main entry. Builds the design canvas, the prototype, and wires
// the tweaks panel. Loads after all component files via Babel.

const TWEAK_DEFAULTS = /*EDITMODE-BEGIN*/{
  "palette": "clinical",
  "dark": false,
  "transcriptFont": "sans",
  "variant": "document",
  "recording": false,
  "fallingBehind": false,
  "chrome": "win11",
  "tier": "free",
  "keyState": "empty",
  "versionNote": "current",
  "defaultsState": "set",
  "donate": "shown",
  "setupStage": "ask",
  "summariseState": "ready",
  "recordOnlyStage": "recording",
  "stopRecommended": "all",
  "installedSummaryModel": "quiet"
}/*EDITMODE-END*/;

// ─── Artboard helper ────────────────────────────────────────────────────────
// Renders children inside a VMSurface that picks up either the global tweaks
// or a pinned palette/dark/font override.
function Board({ pin, tweaks, children, padded = false }) {
  const palette = pin?.palette ?? tweaks.palette;
  const dark = pin?.dark ?? tweaks.dark;
  const transcriptFont = pin?.transcriptFont ?? tweaks.transcriptFont;
  return (
    <VMSurface palette={palette} dark={dark} transcriptFont={transcriptFont}
      style={{ display: "flex", flexDirection: "column", padding: padded ? 0 : 0 }}>
      {children}
    </VMSurface>
  );
}

// ─── Chromed artboard helper ────────────────────────────────────────────────
// Wraps a screen body in the window chrome so each artboard looks like the
// real app. Inner content height ≈ artboard height − chrome bar.
function ChromedBoard({ pin, tweaks, kind, title = "Volksmond", recording = false, width, height, children }) {
  return (
    <Board pin={pin} tweaks={tweaks}>
      <WindowChrome
        kind={kind ?? tweaks.chrome}
        width={width} height={height}
        title={title}
        recording={recording}
      >
        {children}
      </WindowChrome>
    </Board>
  );
}

// ─── App / Canvas ───────────────────────────────────────────────────────────
function App() {
  const [t, setTweak] = useTweaks(TWEAK_DEFAULTS);

  // Inject one runtime style for the editable inputs so they don't look
  // washed out inside the dark palette mirrors.
  React.useEffect(() => {
    document.body.style.background = "#f0eee9";
  }, []);

  return (
    <>
      <DesignCanvas minScale={0.1} maxScale={2}>

        {/* ── HERO PROTOTYPE ────────────────────────────────────────────── */}
        <DCSection
          id="prototype"
          title="Click-through prototype"
          subtitle="Use the pill bar under the window. Tweaks panel (top-right) drives palette, font, transcript layout, recording, falling-behind, and Win/Mac chrome.">
          <DCArtboard id="proto" label="Volksmond · main flow" width={1200} height={760}>
            <Prototype tweaks={t} />
          </DCArtboard>
        </DCSection>

        {/* ── VISUAL DIRECTION SIDE-BY-SIDE ─────────────────────────────── */}
        <DCSection
          id="directions"
          title="Visual direction"
          subtitle="Three palettes, same screen. The tweaks panel cycles all other artboards through these too.">
          {[
            { id: "clinical", label: "A · Clinical Quiet · near-monochrome with a cool muted accent" },
            { id: "paper",    label: "B · Warm Paper · ink on warm cream, terracotta accent" },
            { id: "veld",     label: "C · Veld Neutrals · stone and clay, deep umber ink, dusty teal" },
          ].map((p) => (
            <DCArtboard key={p.id} id={`dir-${p.id}`} label={p.label} width={1100} height={720}>
              <ChromedBoard pin={{ palette: p.id, dark: false }} tweaks={t}
                kind={t.chrome} width={1100} height={720}
                title="Q3 strategy review · Volksmond">
                <LiveScreen variant="document" state="live" />
              </ChromedBoard>
            </DCArtboard>
          ))}
        </DCSection>

        {/* ── FIRST RUN ──────────────────────────────────────────────────── */}
        <DCSection
          id="first-run"
          title="First run"
          subtitle="Welcome, device setup, the one-time model download, and the per-session model load.">

          <DCArtboard id="welcome" label="1. Welcome · privacy promise is the centrepiece"
            width={960} height={680}>
            <ChromedBoard tweaks={t} width={960} height={680} title="Welcome · Volksmond">
              <WelcomeScreen />
            </ChromedBoard>
          </DCArtboard>

          <DCArtboard id="devices" label="2. Pick the microphone and the system-audio source"
            width={960} height={680}>
            <ChromedBoard tweaks={t} width={960} height={680} title="Setup · Volksmond">
              <DeviceSetupScreen />
            </ChromedBoard>
          </DCArtboard>

          <DCArtboard id="download" label="3a. Model downloading · honest progress"
            width={960} height={680}>
            <ChromedBoard tweaks={t} width={960} height={680} title="Setup · Volksmond">
              <ModelDownloadScreen progress={0.42} state="downloading" />
            </ChromedBoard>
          </DCArtboard>

          <DCArtboard id="download-done" label="3b. Model ready" width={960} height={680}>
            <ChromedBoard tweaks={t} width={960} height={680} title="Setup · Volksmond">
              <ModelDownloadScreen progress={1} state="done" />
            </ChromedBoard>
          </DCArtboard>

          <DCArtboard id="warming" label="4. Per-session warm-up · a few seconds"
            width={1100} height={720}>
            <ChromedBoard tweaks={t} width={1100} height={720} title="Volksmond">
              <ModelLoadingScreen />
            </ChromedBoard>
          </DCArtboard>
        </DCSection>

        {/* ── PRE-MEETING ────────────────────────────────────────────────── */}
        <DCSection
          id="pre-meeting"
          title="Pre-meeting start"
          subtitle="The 'home' screen. Designed so a hurried user can hit Begin in one click with good defaults.">
          <DCArtboard id="pre-empty" label="Defaults · recording off · everything ready"
            width={1200} height={760}>
            <ChromedBoard tweaks={t} width={1200} height={760} title="Volksmond">
              <PreMeetingScreen recordingDefault={false} />
            </ChromedBoard>
          </DCArtboard>
          <DCArtboard id="pre-record" label="Recording on · the courtesy line surfaces"
            width={1200} height={760}>
            <ChromedBoard tweaks={t} width={1200} height={760} title="Volksmond" recording={false}>
              <PreMeetingScreen recordingDefault={true} />
            </ChromedBoard>
          </DCArtboard>
        </DCSection>

        {/* ── LIVE TRANSCRIPT VARIANTS ──────────────────────────────────── */}
        <DCSection
          id="variants"
          title="Live transcript · 3 layout variants"
          subtitle="Same data, three layouts. Toggle from the tweaks panel to swap the prototype between them.">
          <DCArtboard id="v-doc" label="A · Document · serif, ink on paper, marginal timestamps"
            width={1200} height={760}>
            <ChromedBoard tweaks={t} width={1200} height={760} title="Q3 strategy review">
              <LiveScreen variant="document" state="live" />
            </ChromedBoard>
          </DCArtboard>
          <DCArtboard id="v-chat" label="B · Chat · bubbles, You on the right, others on the left"
            width={1200} height={760}>
            <ChromedBoard tweaks={t} width={1200} height={760} title="Q3 strategy review">
              <LiveScreen variant="chat" state="live" />
            </ChromedBoard>
          </DCArtboard>
          <DCArtboard id="v-codeswitch"
            label="C · Code-switch stream · inline language ribbons, sells the Afrikaans angle"
            width={1200} height={760}>
            <ChromedBoard tweaks={t} width={1200} height={760} title="Q3 strategy review">
              <LiveScreen variant="codeswitch" state="live" />
            </ChromedBoard>
          </DCArtboard>
        </DCSection>

        {/* ── LIVE STATES ───────────────────────────────────────────────── */}
        <DCSection
          id="live-states"
          title="Live · states"
          subtitle="Paused, falling-behind (with the calm 'we switched to keep up' notice), and recording-on chrome.">
          <DCArtboard id="s-paused" label="Paused" width={1200} height={760}>
            <ChromedBoard tweaks={t} width={1200} height={760} title="Q3 strategy review">
              <LiveScreen variant="document" state="paused" />
            </ChromedBoard>
          </DCArtboard>
          <DCArtboard id="s-fb" label="Falling behind · switched to fast mode, gap will be filled later"
            width={1200} height={760}>
            <ChromedBoard tweaks={t} width={1200} height={760} title="Q3 strategy review">
              <LiveScreen variant="document" state="falling-behind" />
            </ChromedBoard>
          </DCArtboard>
          <DCArtboard id="s-rec" label="Recording on · the chrome wears it openly"
            width={1200} height={760}>
            <ChromedBoard tweaks={t} width={1200} height={760}
              title="Q3 strategy review" recording={true}>
              <LiveScreen variant="document" state="live" recording={true} />
            </ChromedBoard>
          </DCArtboard>
        </DCSection>

        {/* ── FINISH & SAVE ─────────────────────────────────────────────── */}
        <DCSection
          id="finish"
          title="Finish & save · the clean second pass"
          subtitle="The live preview is fast and honest about it. The slower clean version is the accurate record.">
          <DCArtboard id="f-offer" label="Saved · clean pass offered" width={1200} height={760}>
            <ChromedBoard tweaks={t} width={1200} height={760} title="Volksmond">
              <FinishSaveScreen cleanState="offer" />
            </ChromedBoard>
          </DCArtboard>
          <DCArtboard id="f-running" label="Clean pass running · honest progress"
            width={1200} height={760}>
            <ChromedBoard tweaks={t} width={1200} height={760} title="Volksmond">
              <FinishSaveScreen cleanState="running" />
            </ChromedBoard>
          </DCArtboard>
          <DCArtboard id="f-done" label="Clean pass done" width={1200} height={760}>
            <ChromedBoard tweaks={t} width={1200} height={760} title="Volksmond">
              <FinishSaveScreen cleanState="done" />
            </ChromedBoard>
          </DCArtboard>
        </DCSection>

        {/* ── HISTORY & SETTINGS ────────────────────────────────────────── */}
        <DCSection id="library" title="History">
          <DCArtboard id="history" label="History · searchable list of past meetings"
            width={1200} height={760}>
            <ChromedBoard tweaks={t} width={1200} height={760} title="Volksmond">
              <HistoryScreen />
            </ChromedBoard>
          </DCArtboard>
        </DCSection>

        {/* SETTINGS · freemium variations */}
        <DCSection id="settings"
          title="Settings · freemium"
          subtitle="Licence states top to bottom: free → trial → pro. Theme picker drives in-app dark mode. Two off-device toggles sit in the danger zone at the bottom, fenced off in red so they can't be flipped by accident.">
          <DCArtboard id="settings-free" label="Free plan · Pro features peeked, 'Unlock' on each gated row"
            width={1200} height={1320}>
            <ChromedBoard tweaks={t} width={1200} height={1320} title="Volksmond">
              <SettingsScreen tier="free" />
            </ChromedBoard>
          </DCArtboard>
          <DCArtboard id="settings-trial" label="Pro trial · 12 days left, all features on, 'Add payment' CTA"
            width={1200} height={1320}>
            <ChromedBoard tweaks={t} width={1200} height={1320} title="Volksmond">
              <SettingsScreen tier="trial" />
            </ChromedBoard>
          </DCArtboard>
          <DCArtboard id="settings-pro" label="Pro · active · licence key visible, API-key field exposed in danger zone"
            width={1200} height={1320}>
            <ChromedBoard tweaks={t} width={1200} height={1320} title="Volksmond">
              <SettingsScreen tier="pro" />
            </ChromedBoard>
          </DCArtboard>
        </DCSection>

        {/* ── COMMERCIAL · upgrade / activation / status ─────────────── */}
        <DCSection id="commerce"
          title="Commercial · perpetual licence, offline activation"
          subtitle="One purchase, version locked. The upgrade screen sells without pressure; activation is a paste-in-the-box affair that runs entirely on this machine.">

          <DCArtboard id="upgrade-empty" label="Upgrade screen · key field empty"
            width={1100} height={1100}>
            <ChromedBoard tweaks={t} width={1100} height={1100} title="Upgrade · Volksmond">
              <UpgradeScreen keyState="empty" />
            </ChromedBoard>
          </DCArtboard>

          <DCArtboard id="upgrade-typing" label="Upgrade · pasted, ready to activate"
            width={1100} height={1100}>
            <ChromedBoard tweaks={t} width={1100} height={1100} title="Upgrade · Volksmond">
              <UpgradeScreen keyState="typing" />
            </ChromedBoard>
          </DCArtboard>

          <DCArtboard id="upgrade-success" label="Activation success · offline-verified"
            width={1100} height={1100}>
            <ChromedBoard tweaks={t} width={1100} height={1100} title="Upgrade · Volksmond">
              <UpgradeScreen keyState="success" />
            </ChromedBoard>
          </DCArtboard>

          <DCArtboard id="upgrade-bad" label="Activation failure · bad key, plain help"
            width={1100} height={1100}>
            <ChromedBoard tweaks={t} width={1100} height={1100} title="Upgrade · Volksmond">
              <UpgradeScreen keyState="error-bad" />
            </ChromedBoard>
          </DCArtboard>

          <DCArtboard id="upgrade-version" label="Activation failure · key is for v2"
            width={1100} height={1100}>
            <ChromedBoard tweaks={t} width={1100} height={1100} title="Upgrade · Volksmond">
              <UpgradeScreen keyState="error-version" />
            </ChromedBoard>
          </DCArtboard>

          <DCArtboard id="licence-status" label="Licence status panel · settings inset"
            width={1100} height={900}>
            <ChromedBoard tweaks={t} width={1100} height={900} title="Settings · Volksmond">
              <div className="vm" data-palette={t.palette} data-dark={t.dark ? "true" : "false"}
                style={{ flex: 1, padding: "32px 40px", overflow: "auto" }}>
                <div style={{ maxWidth: 720 }}>
                  <LicenceStatusPanel tier="pro" versionNote="current" />
                </div>
              </div>
            </ChromedBoard>
          </DCArtboard>

          <DCArtboard id="licence-v2" label="Licence status · version 2 available, calm upgrade note"
            width={1100} height={1100}>
            <ChromedBoard tweaks={t} width={1100} height={1100} title="Settings · Volksmond">
              <div className="vm" data-palette={t.palette} data-dark={t.dark ? "true" : "false"}
                style={{ flex: 1, padding: "32px 40px", overflow: "auto" }}>
                <div style={{ maxWidth: 720 }}>
                  <LicenceStatusPanel tier="pro" versionNote="v2-available" />
                </div>
              </div>
            </ChromedBoard>
          </DCArtboard>

          <DCArtboard id="donate-card" label="Donate card · dismissible, free tier"
            width={760} height={300}>
            <ChromedBoard tweaks={t} width={760} height={300} title="Volksmond">
              <div className="vm" data-palette={t.palette} data-dark={t.dark ? "true" : "false"}
                style={{ flex: 1, padding: 32 }}>
                <DonateCard />
              </div>
            </ChromedBoard>
          </DCArtboard>

          <DCArtboard id="donate-compact" label="Donate · compact, lives at the bottom of the sidebar"
            width={760} height={180}>
            <ChromedBoard tweaks={t} width={760} height={180} title="Volksmond">
              <div className="vm" data-palette={t.palette} data-dark={t.dark ? "true" : "false"}
                style={{ flex: 1, padding: 32, display: "flex", alignItems: "center" }}>
                <div style={{ width: 280 }}><DonateCard compact /></div>
              </div>
            </ChromedBoard>
          </DCArtboard>
        </DCSection>

        {/* ── INTERNATIONAL · language pickers ────────────────────────── */}
        <DCSection id="languages"
          title="Language · interface vs transcription"
          subtitle="Two separate concepts now, clearly. Interface language sits in Settings. Transcription language sits in the pre-meeting screen where it actually matters.">

          <DCArtboard id="iface-lang" label="Interface language · SA English baseline, Afrikaans translated by the community"
            width={780} height={620}>
            <ChromedBoard tweaks={t} width={780} height={620} title="Settings · Language">
              <div className="vm" data-palette={t.palette} data-dark={t.dark ? "true" : "false"}
                style={{ flex: 1, padding: "28px 32px", overflow: "auto" }}>
                <div style={{ maxWidth: 540 }}>
                  <InterfaceLanguagePicker value="en-za" />
                </div>
              </div>
            </ChromedBoard>
          </DCArtboard>

          <DCArtboard id="trans-lang" label="Transcription language · af/en first-class, others tucked behind a disclosure"
            width={780} height={780}>
            <ChromedBoard tweaks={t} width={780} height={780} title="Start a meeting · Language">
              <div className="vm" data-palette={t.palette} data-dark={t.dark ? "true" : "false"}
                style={{ flex: 1, padding: "28px 32px", overflow: "auto" }}>
                <div style={{ maxWidth: 520 }}>
                  <TranscriptionLanguagePicker value="auto" />
                </div>
              </div>
            </ChromedBoard>
          </DCArtboard>
        </DCSection>

        {/* ── DEFAULTS · context terms + AI instruction profiles ──────── */}
        <DCSection id="defaults"
          title="Defaults · set once, applied everywhere"
          subtitle="Two related but separate ideas: persistent context terms (free), and saved AI-instruction profiles (Pro). Free users see the locked treatment in place.">

          <DCArtboard id="def-empty" label="Defaults · empty, first-time" width={1100} height={900}>
            <ChromedBoard tweaks={t} width={1100} height={900} title="Defaults · Volksmond">
              <DefaultsScreen tier="free" state="empty" />
            </ChromedBoard>
          </DCArtboard>

          <DCArtboard id="def-free" label="Defaults · set, free tier · AI section locked"
            width={1100} height={900}>
            <ChromedBoard tweaks={t} width={1100} height={900} title="Defaults · Volksmond">
              <DefaultsScreen tier="free" state="set" />
            </ChromedBoard>
          </DCArtboard>

          <DCArtboard id="def-pro" label="Defaults · Pro · saved instruction profiles, switchable"
            width={1100} height={1180}>
            <ChromedBoard tweaks={t} width={1100} height={1180} title="Defaults · Volksmond">
              <DefaultsScreen tier="pro" state="set" />
            </ChromedBoard>
          </DCArtboard>
        </DCSection>

        {/* DARK MODE */}
        <DCSection id="dark"
          title="Dark mode"
          subtitle="Each palette has a calibrated dark mirror. The theme picker in Settings drives this; the tweaks panel does the same.">
          <DCArtboard id="dark-live" label="Live transcript · dark" width={1200} height={760}>
            <ChromedBoard pin={{ palette: t.palette, dark: true }} tweaks={t}
              width={1200} height={760} title="Q3 strategy review">
              <LiveScreen variant="document" state="live" />
            </ChromedBoard>
          </DCArtboard>
          <DCArtboard id="dark-settings" label="Settings · dark, free tier" width={1200} height={1320}>
            <ChromedBoard pin={{ palette: t.palette, dark: true }} tweaks={t}
              width={1200} height={1320} title="Volksmond">
              <SettingsScreen tier="free" theme="dark" />
            </ChromedBoard>
          </DCArtboard>
          <DCArtboard id="dark-history" label="History · dark" width={1200} height={760}>
            <ChromedBoard pin={{ palette: t.palette, dark: true }} tweaks={t}
              width={1200} height={760} title="Volksmond">
              <HistoryScreen />
            </ChromedBoard>
          </DCArtboard>
        </DCSection>

        {/* ── ERRORS & EDGE CASES ───────────────────────────────────────── */}
        <DCSection id="states"
          title="Errors, empty, offline"
          subtitle="Calm, plain, helpful. No tech-talk, no fear, no exclamation marks.">
          <DCArtboard id="e-mic" label="No microphone" width={960} height={620}>
            <ChromedBoard tweaks={t} width={960} height={620} title="Volksmond">
              <ErrorScreen kind="no-mic" />
            </ChromedBoard>
          </DCArtboard>
          <DCArtboard id="e-sys" label="No system audio · the most common Win-Setup issue"
            width={960} height={620}>
            <ChromedBoard tweaks={t} width={960} height={620} title="Volksmond">
              <ErrorScreen kind="no-sys" />
            </ChromedBoard>
          </DCArtboard>
          <DCArtboard id="e-model" label="Model failed to load · re-download" width={960} height={620}>
            <ChromedBoard tweaks={t} width={960} height={620} title="Volksmond">
              <ErrorScreen kind="model" />
            </ChromedBoard>
          </DCArtboard>
          <DCArtboard id="e-offline" label="Offline · still fully functional"
            width={960} height={620}>
            <ChromedBoard tweaks={t} width={960} height={620} title="Volksmond">
              <ErrorScreen kind="offline" />
            </ChromedBoard>
          </DCArtboard>
          <DCArtboard id="e-empty" label="Empty history · first-time user"
            width={960} height={620}>
            <ChromedBoard tweaks={t} width={960} height={620} title="Volksmond">
              <ErrorScreen kind="empty" />
            </ChromedBoard>
          </DCArtboard>
        </DCSection>

        {/* ── V1 ADDITIONS · summaries, import, record-only, stop options ─ */}
        <DCSection id="v1-summaries"
          title="V1 · Local AI summaries"
          subtitle="Opt-in at setup, opt-in per transcript. The model is a separate, optional download with two sizes. The Summarise button wears six honest states.">

          <DCArtboard id="setup-ask" label="Setup question · 'just transcribe, or also summarise?'"
            width={960} height={680}>
            <ChromedBoard tweaks={t} width={960} height={680} title="Setup · Volksmond">
              <SummariesSetupScreen stage="ask" />
            </ChromedBoard>
          </DCArtboard>

          <DCArtboard id="setup-pick" label="Model picker · two sizes, RAM guidance, more-accurate note"
            width={960} height={780}>
            <ChromedBoard tweaks={t} width={960} height={780} title="Setup · Volksmond">
              <SummariesSetupScreen stage="pick" picked="considered" />
            </ChromedBoard>
          </DCArtboard>

          <DCArtboard id="summarise-states" label="Summarise button · five states (summaries are local, not Pro)"
            width={760} height={960}>
            <ChromedBoard tweaks={t} width={760} height={960} title="Volksmond">
              <div className="vm" data-palette={t.palette} data-dark={t.dark ? "true" : "false"}
                style={{ flex: 1, padding: 24, overflow: "auto", display: "flex", flexDirection: "column", gap: 16 }}>
                <div style={{ fontSize: 11, letterSpacing: 0.08, textTransform: "uppercase", color: "var(--ink-3)", fontWeight: 600 }}>
                  Disabled · transcript still running
                </div>
                <SummariseButton state="disabled-incomplete" />
                <div style={{ fontSize: 11, letterSpacing: 0.08, textTransform: "uppercase", color: "var(--ink-3)", fontWeight: 600 }}>
                  Model not on this machine yet
                </div>
                <SummariseButton state="needs-download" />
                <div style={{ fontSize: 11, letterSpacing: 0.08, textTransform: "uppercase", color: "var(--ink-3)", fontWeight: 600 }}>
                  Model downloading
                </div>
                <SummariseButton state="downloading" />
                <div style={{ fontSize: 11, letterSpacing: 0.08, textTransform: "uppercase", color: "var(--ink-3)", fontWeight: 600 }}>
                  Ready to summarise
                </div>
                <SummariseButton state="ready" />
                <div style={{ fontSize: 11, letterSpacing: 0.08, textTransform: "uppercase", color: "var(--ink-3)", fontWeight: 600 }}>
                  Running · honest progress
                </div>
                <SummariseButton state="running" />
              </div>
            </ChromedBoard>
          </DCArtboard>

          <DCArtboard id="summary-result" label="Summary result · saved alongside the transcript"
            width={760} height={820}>
            <ChromedBoard tweaks={t} width={760} height={820} title="Q3 strategy review · Volksmond">
              <div className="vm" data-palette={t.palette} data-dark={t.dark ? "true" : "false"}
                style={{ flex: 1, padding: 24, overflow: "auto" }}>
                <SummaryResult saved={true} />
              </div>
            </ChromedBoard>
          </DCArtboard>

          <DCArtboard id="summaries-settings" label="Settings · Summaries section · on/off, model, language"
            width={760} height={780}>
            <ChromedBoard tweaks={t} width={760} height={780} title="Settings · Volksmond">
              <div className="vm" data-palette={t.palette} data-dark={t.dark ? "true" : "false"}
                style={{ flex: 1, padding: 24, overflow: "auto" }}>
                <SummariesSettingsSection enabled={true} installed={t.installedSummaryModel || "quiet"} />
              </div>
            </ChromedBoard>
          </DCArtboard>
        </DCSection>

        {/* ── V1 · CAPTURE · new session hub + import + record-only ───────── */}
        <DCSection id="v1-capture"
          title="V1 · Three ways into a session"
          subtitle="Live, import a file, or record only. The record-only mode is for machines that can't keep up live; it hands off cleanly to a calm 'transcribe this now?' once you stop.">

          <DCArtboard id="new-session" label="Start screen · three first-class entries"
            width={1200} height={760}>
            <ChromedBoard tweaks={t} width={1200} height={760} title="Volksmond">
              <NewSessionScreen />
            </ChromedBoard>
          </DCArtboard>

          <DCArtboard id="importing" label="Importing a recording · reuses the live transcript view"
            width={1200} height={760}>
            <ChromedBoard tweaks={t} width={1200} height={760} title="Importing · Volksmond">
              <ImportingScreen progress={0.38} />
            </ChromedBoard>
          </DCArtboard>

          <DCArtboard id="record-only" label="Record only · calm, doesn't pretend to transcribe"
            width={1200} height={760}>
            <ChromedBoard tweaks={t} width={1200} height={760}
              title="Eindgesprek met Pieter · Volksmond" recording={true}>
              <RecordOnlyScreen stage="recording" elapsed="00:17:42" />
            </ChromedBoard>
          </DCArtboard>

          <DCArtboard id="record-handoff" label="Record only · stopped · 'transcribe this recording now?'"
            width={1200} height={760}>
            <ChromedBoard tweaks={t} width={1200} height={760}
              title="Eindgesprek met Pieter · Volksmond">
              <RecordOnlyScreen stage="stopped" elapsed="00:17:42" />
            </ChromedBoard>
          </DCArtboard>
        </DCSection>

        {/* ── V1 · STOP CONTROLS · single, three-way, and the calm fallback ─ */}
        <DCSection id="v1-stops"
          title="V1 · Stop controls and the struggling banner"
          subtitle="A single Stop when there's no recording. When recording is on, three options. The honest 'your machine is struggling' banner gets a primary CTA for the calm fallback.">

          <DCArtboard id="stop-single" label="Stop menu · no recording (single choice)"
            width={560} height={220}>
            <ChromedBoard tweaks={t} width={560} height={220} title="Q3 strategy review">
              <div className="vm" data-palette={t.palette} data-dark={t.dark ? "true" : "false"}
                style={{ flex: 1, display: "flex", alignItems: "center", justifyContent: "center", padding: 20 }}>
                <StopMenu recording={false} />
              </div>
            </ChromedBoard>
          </DCArtboard>

          <DCArtboard id="stop-three" label="Stop menu · recording on · three options"
            width={560} height={420}>
            <ChromedBoard tweaks={t} width={560} height={420} title="Q3 strategy review">
              <div className="vm" data-palette={t.palette} data-dark={t.dark ? "true" : "false"}
                style={{ flex: 1, display: "flex", alignItems: "center", justifyContent: "center", padding: 20 }}>
                <StopMenu recording={true} recommended="all" />
              </div>
            </ChromedBoard>
          </DCArtboard>

          <DCArtboard id="stop-prominent" label="Stop menu · 'keep recording' surfaced under the struggling banner"
            width={560} height={420}>
            <ChromedBoard tweaks={t} width={560} height={420} title="Q3 strategy review">
              <div className="vm" data-palette={t.palette} data-dark={t.dark ? "true" : "false"}
                style={{ flex: 1, display: "flex", alignItems: "center", justifyContent: "center", padding: 20 }}>
                <StopMenu recording={true} recommended="transcript-only" />
              </div>
            </ChromedBoard>
          </DCArtboard>

          <DCArtboard id="struggle-banner" label="Live screen · struggling banner with the calm primary CTA"
            width={1200} height={760}>
            <ChromedBoard tweaks={t} width={1200} height={760}
              title="Q3 strategy review · Volksmond" recording={true}>
              <LiveScreenWithStruggleV1 recording={true} />
            </ChromedBoard>
          </DCArtboard>
        </DCSection>

        {/* ── DESIGN-DOC POST-ITS ──────────────────────────────────────── */}
        <DCPostIt top={140} left={40} rotate={-3} width={220}>
          <b>Hi.</b> Three palettes side-by-side at the top, then every state below. Click the prototype's pill bar to walk a meeting end-to-end.
        </DCPostIt>

        <DCPostIt top={1500} left={40} rotate={2} width={260}>
          <b>Second pass.</b> Below this point: upgrade screen, offline paste-a-key activation, licence status with the v2 note, donate, language pickers (interface vs transcription), and the Defaults area with saved AI-instruction profiles.
        </DCPostIt>

        <DCPostIt top={3000} left={40} rotate={-2} width={260}>
          <b>Third pass · V1.</b> Below: the summarise-too question and the model picker; the Summarise button in every honest state; the start screen with three first-class entries (live, import, record only); the calm record-only state and its handoff; and the three-way Stop, including the prominent calm fallback under the struggling banner.
        </DCPostIt>

        <DCPostIt top={3260} left={40} rotate={2} width={280}>
          <b>Pro principle.</b> Pro is now only for things that need an online connection: calendar attendees (confirmed), and optionally the online transcription or summary APIs for weak machines. Everything local (live transcription, summaries, clean pass, history, exports) stays in Free. The rest of the paywall is TBD.
        </DCPostIt>
      </DesignCanvas>

      <TweaksPanel title="Tweaks">
        <TweakSection label="Visual direction" />
        <TweakRadio label="Palette" value={t.palette}
          options={["clinical", "paper", "veld"]}
          onChange={(v) => setTweak("palette", v)} />
        <TweakToggle label="Dark mode" value={t.dark}
          onChange={(v) => setTweak("dark", v)} />

        <TweakSection label="Typography" />
        <TweakRadio label="Transcript font" value={t.transcriptFont}
          options={["sans", "serif", "mono"]}
          onChange={(v) => setTweak("transcriptFont", v)} />

        <TweakSection label="Live transcript" />
        <TweakSelect label="Layout variant" value={t.variant}
          options={[
            { value: "document", label: "Document (serif, paragraphs)" },
            { value: "chat", label: "Chat (bubbles)" },
            { value: "codeswitch", label: "Code-switch stream (novel)" },
          ]}
          onChange={(v) => setTweak("variant", v)} />
        <TweakToggle label="Falling-behind state" value={t.fallingBehind}
          onChange={(v) => setTweak("fallingBehind", v)} />
        <TweakToggle label="Recording audio" value={t.recording}
          onChange={(v) => setTweak("recording", v)} />

        <TweakSection label="Platform" />
        <TweakRadio label="Window chrome" value={t.chrome}
          options={[
            { value: "win11", label: "Windows" },
            { value: "mac", label: "macOS" },
          ]}
          onChange={(v) => setTweak("chrome", v)} />

        <TweakSection label="Licence" />
        <TweakRadio label="Tier" value={t.tier}
          options={[
            { value: "free", label: "Free" },
            { value: "trial", label: "Trial" },
            { value: "pro", label: "Pro" },
          ]}
          onChange={(v) => setTweak("tier", v)} />
        <TweakSelect label="Activation key state" value={t.keyState}
          options={[
            { value: "empty", label: "Empty (placeholder)" },
            { value: "typing", label: "Typing (Activate button)" },
            { value: "success", label: "Success (verified)" },
            { value: "error-bad", label: "Error: invalid key" },
            { value: "error-version", label: "Error: wrong major version" },
          ]}
          onChange={(v) => setTweak("keyState", v)} />
        <TweakRadio label="Version note" value={t.versionNote}
          options={[
            { value: "current", label: "Current" },
            { value: "v2-available", label: "V2 available" },
          ]}
          onChange={(v) => setTweak("versionNote", v)} />

        <TweakSection label="V1 · summaries" />
        <TweakRadio label="Installed summary model" value={t.installedSummaryModel}
          options={[
            { value: "none", label: "None" },
            { value: "quiet", label: "Quiet" },
            { value: "considered", label: "Considered" },
          ]}
          onChange={(v) => setTweak("installedSummaryModel", v)} />
      </TweaksPanel>
    </>
  );
}

const root = ReactDOM.createRoot(document.getElementById("root"));
root.render(<App />);
