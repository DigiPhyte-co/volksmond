// prototype.jsx — the click-through prototype.
// A single artboard with bottom nav pills that flip between screens. Picks up
// its palette/typography/state from the global tweaks.

function ScreenBody({ screen, recording, variant, transcriptState, tier }) {
  switch (screen) {
    case "welcome": return <WelcomeScreen />;
    case "setup-devices": return <DeviceSetupScreen />;
    case "setup-download": return <ModelDownloadScreen progress={0.42} state="downloading" />;
    case "setup-done": return <ModelDownloadScreen progress={1} state="done" />;
    case "warming": return <ModelLoadingScreen />;
    case "pre-meeting": return <PreMeetingScreen recordingDefault={recording} />;
    case "live": return <LiveScreen variant={variant} state={transcriptState} recording={recording} />;
    case "finish": return <FinishSaveScreen cleanState="offer" />;
    case "history": return <HistoryScreen />;
    case "settings": return <SettingsScreen tier={tier} />;
    case "error": return <ErrorScreen kind="no-sys" />;
    default: return <PreMeetingScreen />;
  }
}

const FLOW = [
  { id: "welcome", label: "Welcome" },
  { id: "setup-devices", label: "Devices" },
  { id: "setup-download", label: "Download" },
  { id: "warming", label: "Warming up" },
  { id: "pre-meeting", label: "Start" },
  { id: "live", label: "Live" },
  { id: "finish", label: "Finish" },
  { id: "history", label: "History" },
  { id: "settings", label: "Settings" },
];

function PrototypeBar({ screen, setScreen }) {
  return (
    <div style={{
      position: "absolute", left: "50%", bottom: -56, transform: "translateX(-50%)",
      display: "flex", gap: 4, padding: 5, borderRadius: 999,
      background: "rgba(20,15,10,0.88)",
      backdropFilter: "blur(20px)", WebkitBackdropFilter: "blur(20px)",
      boxShadow: "0 12px 36px rgba(0,0,0,0.25)",
      fontFamily: "var(--font-sans)",
    }}>
      {FLOW.map((s) => (
        <button key={s.id} onClick={() => setScreen(s.id)}
          style={{
            appearance: "none", border: 0,
            padding: "8px 14px", borderRadius: 999,
            background: s.id === screen ? "rgba(255,255,255,0.18)" : "transparent",
            color: s.id === screen ? "#fff" : "rgba(255,255,255,0.6)",
            fontSize: 11.5, fontWeight: s.id === screen ? 600 : 500,
            letterSpacing: 0.01, cursor: "pointer",
            transition: "background .12s, color .12s",
          }}
        >{s.label}</button>
      ))}
    </div>
  );
}

function Prototype({ tweaks }) {
  const [screen, setScreen] = React.useState("pre-meeting");
  return (
    <div style={{ position: "relative" }}>
      <div
        className="vm"
        data-palette={tweaks.palette}
        data-dark={tweaks.dark ? "true" : "false"}
        style={{ "--font-transcript": fontFor(tweaks.transcriptFont) }}
      >
        <WindowChrome
          kind={tweaks.chrome}
          width={1200} height={760}
          title={screen === "live" ? "Q3 strategy review · Volksmond" : "Volksmond"}
          recording={screen === "live" && tweaks.recording}
        >
          <ScreenBody
            screen={screen}
            recording={tweaks.recording}
            variant={tweaks.variant}
            transcriptState={tweaks.fallingBehind ? "falling-behind" : "live"}
            tier={tweaks.tier}
          />
        </WindowChrome>
      </div>
      <PrototypeBar screen={screen} setScreen={setScreen} />
    </div>
  );
}

function fontFor(kind) {
  if (kind === "serif") return "var(--font-serif)";
  if (kind === "mono") return "var(--font-mono)";
  return "var(--font-sans)";
}

// Surface — wraps an artboard's contents and applies a palette/dark/font set.
// Lets a single artboard pin itself to a specific palette regardless of tweaks
// (used in the "Visual direction" section where we want the three side-by-side).
function VMSurface({ palette, dark, transcriptFont, children, style }) {
  return (
    <div
      className="vm"
      data-palette={palette}
      data-dark={dark ? "true" : "false"}
      style={{
        height: "100%",
        "--font-transcript": fontFor(transcriptFont || "sans"),
        ...style,
      }}
    >
      {children}
    </div>
  );
}

Object.assign(window, { Prototype, VMSurface, fontFor, FLOW });
