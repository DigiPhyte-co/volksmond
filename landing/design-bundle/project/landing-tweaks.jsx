// landing-tweaks.jsx — review-only Tweaks panel for the Volksmond landing page.
// Does NOT ship: it drives the same window.VM setters the header controls use,
// so logo / theme / palette / heading-font stay a single source of truth.

const TWEAK_DEFAULTS = /*EDITMODE-BEGIN*/{
  "logo": "speaker",
  "palette": "clinical",
  "dark": false,
  "headingFont": "serif"
}/*EDITMODE-END*/;

function LandingTweaks() {
  // Seed from whatever the page already restored from localStorage, so the
  // panel opens reflecting the live state rather than the static defaults.
  const seed = (window.VM && window.VM.state) || {};
  const [t, setTweak] = useTweaks({
    logo: seed.logo || TWEAK_DEFAULTS.logo,
    palette: seed.palette || TWEAK_DEFAULTS.palette,
    dark: typeof seed.dark === "boolean" ? seed.dark : TWEAK_DEFAULTS.dark,
    headingFont: seed.hfont || TWEAK_DEFAULTS.headingFont,
  });

  const apply = (key, val) => {
    setTweak(key, val);
    if (!window.VM) return;
    if (key === "logo") window.VM.setLogo(val);
    if (key === "palette") window.VM.setPalette(val);
    if (key === "dark") window.VM.setDark(val);
    if (key === "headingFont") window.VM.setHeadingFont(val);
  };

  return (
    <TweaksPanel title="Tweaks">
      <TweakSection label="Logo" />
      <TweakRadio
        label="Mark"
        value={t.logo}
        options={[
          { value: "speaker", label: "Speaker" },
          { value: "wave", label: "Wave" },
          { value: "bubble", label: "Bubble" },
        ]}
        onChange={(v) => apply("logo", v)}
      />

      <TweakSection label="Colour" />
      <TweakRadio
        label="Palette"
        value={t.palette}
        options={[
          { value: "clinical", label: "Clinical" },
          { value: "paper", label: "Paper" },
          { value: "veld", label: "Veld" },
        ]}
        onChange={(v) => apply("palette", v)}
      />
      <TweakToggle label="Dark theme" value={t.dark} onChange={(v) => apply("dark", v)} />

      <TweakSection label="Type" />
      <TweakRadio
        label="Headline"
        value={t.headingFont}
        options={[
          { value: "serif", label: "Serif" },
          { value: "sans", label: "Sans" },
        ]}
        onChange={(v) => apply("headingFont", v)}
      />
    </TweaksPanel>
  );
}

(function mount() {
  function go() {
    var el = document.getElementById("tweaks-root");
    if (!el) { return; }
    ReactDOM.createRoot(el).render(<LandingTweaks />);
  }
  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", go);
  } else { go(); }
})();
