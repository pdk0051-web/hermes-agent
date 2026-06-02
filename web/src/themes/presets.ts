import type { DashboardTheme, ThemeTypography, ThemeLayout } from "./types";

/**
 * Built-in dashboard themes.
 *
 * Each theme defines its own palette, typography, and layout so switching
 * themes produces visible changes beyond just color: fonts, density, and
 * corner-radius all shift to match the theme's personality.
 *
 * Theme names must stay in sync with the backend's
 * `_BUILTIN_DASHBOARD_THEMES` list in `hermes_cli/web_server.py`.
 */

// ---------------------------------------------------------------------------
// Shared typography / layout presets
// ---------------------------------------------------------------------------

/** Default system stack: neutral, safe fallback for every platform. */
const SYSTEM_SANS =
  'system-ui, -apple-system, "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif';
const SYSTEM_MONO =
  'ui-monospace, "SF Mono", "Cascadia Mono", Menlo, Consolas, monospace';
const VALLEY_SANS =
  '"Pretendard Variable", Pretendard, -apple-system, BlinkMacSystemFont, "Segoe UI", ' +
  'Roboto, "Helvetica Neue", Arial, sans-serif';

const DEFAULT_TYPOGRAPHY: ThemeTypography = {
  fontSans: SYSTEM_SANS,
  fontMono: SYSTEM_MONO,
  baseSize: "15px",
  lineHeight: "1.55",
  letterSpacing: "0",
};

const DEFAULT_LAYOUT: ThemeLayout = {
  radius: "0.5rem",
  density: "comfortable",
};

// ---------------------------------------------------------------------------
// Themes
// ---------------------------------------------------------------------------

const VALLEY_CONSOLE_CSS = `
.hermes-dashboard-shell[data-theme-name="default"] {
  background: #fcfcfd;
  color: #202124;
  text-transform: none;
}

.hermes-dashboard-shell[data-theme-name="default"] #app-sidebar {
  width: 302px;
  border-right-color: #e7e7ea;
  background: rgba(255, 255, 255, 0.96);
  color: #202124;
  box-shadow: none;
}

.hermes-dashboard-shell[data-theme-name="default"] #app-sidebar > div:first-child {
  height: 58px;
  border-bottom-color: #e7e7ea;
}

.hermes-dashboard-shell[data-theme-name="default"] #app-sidebar nav {
  border-top-color: transparent;
  padding: 14px 12px;
}

.hermes-dashboard-shell[data-theme-name="default"] #app-sidebar a {
  margin: 2px 0;
  border-radius: 8px;
  padding: 9px 12px;
  font-family: var(--theme-font-sans);
  font-size: 0.92rem;
  font-weight: 700;
  letter-spacing: 0;
}

.hermes-dashboard-shell[data-theme-name="default"] #app-sidebar a:hover,
.hermes-dashboard-shell[data-theme-name="default"] #app-sidebar a[aria-current="page"] {
  background: #f1f2f3;
  opacity: 1;
}

.hermes-dashboard-shell[data-theme-name="default"] #app-sidebar a[aria-current="page"]::before {
  content: "";
  position: absolute;
  left: 4px;
  top: 8px;
  bottom: 8px;
  width: 3px;
  border-radius: 999px;
  background: #58bf67;
}

.hermes-dashboard-shell[data-theme-name="default"] #app-sidebar a > span:not([aria-hidden]) {
  color: #202124;
}

.hermes-dashboard-shell[data-theme-name="default"] [id$="heading"],
.hermes-dashboard-shell[data-theme-name="default"] #app-sidebar span[id],
.hermes-dashboard-shell[data-theme-name="default"] #app-sidebar .font-mondwest {
  font-family: var(--theme-font-sans);
  letter-spacing: 0;
  text-transform: none;
}

.hermes-dashboard-shell[data-theme-name="default"] .blend-lighter {
  mix-blend-mode: normal;
}

.hermes-dashboard-shell[data-theme-name="default"] h1,
.hermes-dashboard-shell[data-theme-name="default"] h2,
.hermes-dashboard-shell[data-theme-name="default"] h3,
.hermes-dashboard-shell[data-theme-name="default"] .font-expanded,
.hermes-dashboard-shell[data-theme-name="default"] .font-mondwest,
.hermes-dashboard-shell[data-theme-name="default"] .font-sans {
  font-family: var(--theme-font-sans);
  letter-spacing: 0;
  text-transform: none;
}

.hermes-dashboard-shell[data-theme-name="default"] .bg-card\\/80,
.hermes-dashboard-shell[data-theme-name="default"] .bg-card {
  background-color: #ffffff;
}

.hermes-dashboard-shell[data-theme-name="default"] .border-border {
  border-color: #e7e7ea;
}

.hermes-dashboard-shell[data-theme-name="default"] .shadow-\\[0_12px_32px_-8px_rgba\\(0\\,0\\,0\\,0\\.6\\)\\] {
  box-shadow: 0 8px 20px rgba(17, 24, 39, 0.055);
}
`;

export const defaultTheme: DashboardTheme = {
  name: "default",
  label: "Valley Console",
  description: "Light Valley-inspired operator console",
  palette: {
    background: { hex: "#fcfcfd", alpha: 1 },
    midground: { hex: "#202124", alpha: 1 },
    foreground: { hex: "#58bf67", alpha: 1 },
    warmGlow: "rgba(88, 191, 103, 0.10)",
    noiseOpacity: 0,
  },
  typography: {
    ...DEFAULT_TYPOGRAPHY,
    fontSans: VALLEY_SANS,
    baseSize: "14px",
    lineHeight: "1.5",
  },
  layout: {
    ...DEFAULT_LAYOUT,
    radius: "8px",
  },
  componentStyles: {
    backdrop: {
      baseBlendMode: "normal",
      baseOpacity: "1",
      fillerBlendMode: "normal",
      fillerOpacity: "0",
      warmOpacity: "0",
      noiseOpacity: "0",
    },
    card: {
      background: "#ffffff",
      boxShadow: "0 8px 20px rgba(17, 24, 39, 0.055)",
    },
    header: {
      background: "rgba(255, 255, 255, 0.96)",
      titleBlendMode: "normal",
    },
    sidebar: {
      background: "rgba(255, 255, 255, 0.96)",
    },
  },
  colorOverrides: {
    card: "#ffffff",
    cardForeground: "#202124",
    popover: "#ffffff",
    popoverForeground: "#202124",
    primary: "#2f9c46",
    primaryForeground: "#ffffff",
    secondary: "#f7f7f8",
    secondaryForeground: "#202124",
    muted: "#f1f2f3",
    mutedForeground: "#777982",
    accent: "#f1f2f3",
    accentForeground: "#202124",
    destructive: "#d14b48",
    destructiveForeground: "#ffffff",
    success: "#58bf67",
    warning: "#e8ad39",
    border: "#e7e7ea",
    input: "#d4d4d8",
    ring: "#58bf67",
  },
  customCSS: VALLEY_CONSOLE_CSS,
  terminalBackground: "#000000",
};

export const midnightTheme: DashboardTheme = {
  name: "midnight",
  label: "Midnight",
  description: "Deep blue-violet with cool accents",
  palette: {
    background: { hex: "#0a0a1f", alpha: 1 },
    midground: { hex: "#d4c8ff", alpha: 1 },
    foreground: { hex: "#ffffff", alpha: 0 },
    warmGlow: "rgba(167, 139, 250, 0.32)",
    noiseOpacity: 0.8,
  },
  typography: {
    ...DEFAULT_TYPOGRAPHY,
    fontSans: `"Inter", ${SYSTEM_SANS}`,
    fontMono: `"JetBrains Mono", ${SYSTEM_MONO}`,
    fontUrl:
      "https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&family=JetBrains+Mono:wght@400;500;700&display=swap",
    letterSpacing: "-0.005em",
  },
  layout: {
    ...DEFAULT_LAYOUT,
    radius: "0.75rem",
  },
};

export const emberTheme: DashboardTheme = {
  name: "ember",
  label: "Ember",
  description: "Warm crimson and bronze — forge vibes",
  palette: {
    background: { hex: "#1a0a06", alpha: 1 },
    midground: { hex: "#ffd8b0", alpha: 1 },
    foreground: { hex: "#ffffff", alpha: 0 },
    warmGlow: "rgba(249, 115, 22, 0.38)",
    noiseOpacity: 1,
  },
  typography: {
    ...DEFAULT_TYPOGRAPHY,
    fontSans: `"Spectral", Georgia, "Times New Roman", serif`,
    fontMono: `"IBM Plex Mono", ${SYSTEM_MONO}`,
    fontUrl:
      "https://fonts.googleapis.com/css2?family=Spectral:wght@400;500;600;700&family=IBM+Plex+Mono:wght@400;500;700&display=swap",
  },
  layout: {
    ...DEFAULT_LAYOUT,
    radius: "0.25rem",
  },
  colorOverrides: {
    destructive: "#c92d0f",
    warning: "#f97316",
  },
};

export const monoTheme: DashboardTheme = {
  name: "mono",
  label: "Mono",
  description: "Clean grayscale — minimal and focused",
  palette: {
    background: { hex: "#0e0e0e", alpha: 1 },
    midground: { hex: "#eaeaea", alpha: 1 },
    foreground: { hex: "#ffffff", alpha: 0 },
    warmGlow: "rgba(255, 255, 255, 0.1)",
    noiseOpacity: 0.6,
  },
  typography: {
    ...DEFAULT_TYPOGRAPHY,
    fontSans: `"IBM Plex Sans", ${SYSTEM_SANS}`,
    fontMono: `"IBM Plex Mono", ${SYSTEM_MONO}`,
    fontUrl:
      "https://fonts.googleapis.com/css2?family=IBM+Plex+Sans:wght@400;500;600&family=IBM+Plex+Mono:wght@400;500&display=swap",
  },
  layout: {
    ...DEFAULT_LAYOUT,
    radius: "0",
  },
};

export const cyberpunkTheme: DashboardTheme = {
  name: "cyberpunk",
  label: "Cyberpunk",
  description: "Neon green on black — matrix terminal",
  palette: {
    background: { hex: "#040608", alpha: 1 },
    midground: { hex: "#9bffcf", alpha: 1 },
    foreground: { hex: "#ffffff", alpha: 0 },
    warmGlow: "rgba(0, 255, 136, 0.22)",
    noiseOpacity: 1.2,
  },
  typography: {
    ...DEFAULT_TYPOGRAPHY,
    fontSans: `"Share Tech Mono", "JetBrains Mono", ${SYSTEM_MONO}`,
    fontMono: `"Share Tech Mono", "JetBrains Mono", ${SYSTEM_MONO}`,
    fontUrl:
      "https://fonts.googleapis.com/css2?family=Share+Tech+Mono&family=JetBrains+Mono:wght@400;700&display=swap",
  },
  layout: {
    ...DEFAULT_LAYOUT,
    radius: "0",
  },
  colorOverrides: {
    success: "#00ff88",
    warning: "#ffd700",
    destructive: "#ff0055",
  },
};

export const roseTheme: DashboardTheme = {
  name: "rose",
  label: "Rosé",
  description: "Soft pink and warm ivory — easy on the eyes",
  palette: {
    background: { hex: "#1a0f15", alpha: 1 },
    midground: { hex: "#ffd4e1", alpha: 1 },
    foreground: { hex: "#ffffff", alpha: 0 },
    warmGlow: "rgba(249, 168, 212, 0.3)",
    noiseOpacity: 0.9,
  },
  typography: {
    ...DEFAULT_TYPOGRAPHY,
    fontSans: `"Fraunces", Georgia, serif`,
    fontMono: `"DM Mono", ${SYSTEM_MONO}`,
    fontUrl:
      "https://fonts.googleapis.com/css2?family=Fraunces:opsz,wght@9..144,400;9..144,500;9..144,600&family=DM+Mono:wght@400;500&display=swap",
  },
  layout: {
    ...DEFAULT_LAYOUT,
    radius: "1rem",
  },
};

/**
 * Same look as ``defaultTheme`` but with a larger root font size, looser
 * line-height, and ``spacious`` density so every rem-based size in the
 * dashboard scales up. For users who find the default 14px UI too dense.
 */
export const defaultLargeTheme: DashboardTheme = {
  name: "default-large",
  label: "Valley Console (Large)",
  description: "Valley Console with bigger fonts and roomier spacing",
  palette: defaultTheme.palette,
  typography: {
    ...defaultTheme.typography,
    baseSize: "18px",
    lineHeight: "1.65",
  },
  layout: {
    ...DEFAULT_LAYOUT,
    radius: "8px",
    density: "spacious",
  },
  componentStyles: defaultTheme.componentStyles,
  colorOverrides: defaultTheme.colorOverrides,
  customCSS: defaultTheme.customCSS,
  terminalBackground: defaultTheme.terminalBackground,
};

export const BUILTIN_THEMES: Record<string, DashboardTheme> = {
  default: defaultTheme,
  "default-large": defaultLargeTheme,
  midnight: midnightTheme,
  ember: emberTheme,
  mono: monoTheme,
  cyberpunk: cyberpunkTheme,
  rose: roseTheme,
};
