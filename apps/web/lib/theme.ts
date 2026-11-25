/**
 * Theme state: dark by default, persisted in one localStorage key.
 *
 * The chosen theme has to be applied before the first paint, or the page renders light and then flips -
 * so the decision lives in a tiny script injected into <head>, and everything else here reuses the same
 * key and the same rule. Previously the script and the toggle each had their own copy of that rule, which
 * is the kind of duplication that silently drifts.
 */

export type Theme = "dark" | "light";

export const THEME_KEY = "nse.theme";

/**
 * Runs before first paint, so it must be synchronous, dependency-free and never throw: a throw here would
 * leave the page unstyled. Anything other than an explicit "light" means dark, so a missing or corrupt
 * value lands on the default rather than on light.
 */
export const themeBootstrapScript =
  `try{var t=localStorage.getItem('${THEME_KEY}');` +
  `if(t!=='light')document.documentElement.classList.add('dark')}` +
  `catch(e){document.documentElement.classList.add('dark')}`;

/**
 * Fired on the window after the theme changes. Anything that reads a CSS custom property into JavaScript -
 * canvas charts, for instance - cannot be restyled by CSS alone and has to rebuild itself when this fires.
 */
export const THEME_EVENT = "nse:themechange";

/** Apply `theme` to the document and remember it. Storage may be unavailable; the class still changes. */
export function applyTheme(theme: Theme): void {
  document.documentElement.classList.toggle("dark", theme === "dark");
  try {
    window.localStorage.setItem(THEME_KEY, theme);
  } catch {
    // Private browsing or blocked site data: the theme still applies for this page view.
  }
  window.dispatchEvent(new CustomEvent(THEME_EVENT, { detail: theme }));
}

/** The theme currently rendered, read from the document rather than assumed. */
export function readStoredTheme(): Theme {
  return document.documentElement.classList.contains("dark") ? "dark" : "light";
}
