/**
 * Theme state: dark by default, persisted in one localStorage key.
 *
 * The chosen theme has to be applied before the first paint, or the page renders light and then flips -
 * so the decision lives in a tiny script injected into <head>, and everything else here reuses the same
 * key and the same rule. Previously the script and the toggle each had their own copy of that rule, which
 * is the kind of duplication that silently drifts.
 *
 * Stored value, not the DOM, is the source of truth. The server renders <html> with no class, so the
 * `dark` class exists only because the pre-paint script added it - and anything that re-renders or
 * reconciles that element can drop it. When the class went missing the page fell back to the `:root`
 * light variables AND the app believed it was in light mode, because it asked the DOM. Reading storage
 * first makes that recoverable, and `ensureThemeApplied` puts the class back.
 */

export type Theme = "dark" | "light";

export const THEME_KEY = "nse.theme";

/** The theme used when nothing has been chosen, or when what was stored cannot be understood. */
export const DEFAULT_THEME: Theme = "dark";

/**
 * Runs before first paint, so it must be synchronous, dependency-free and never throw: a throw here would
 * leave the page unstyled. Anything other than an explicit "light" means dark, so a missing or corrupt
 * value lands on the default rather than on light.
 *
 * It also records the resolved theme on a data attribute. The class is what CSS keys off, but the
 * attribute says *why* - it distinguishes "dark because dark was stored" from a class that something else
 * happened to add, and it is what `ensureThemeApplied` checks against after hydration.
 */
export const themeBootstrapScript =
  `try{var t=localStorage.getItem('${THEME_KEY}');var d=t!=='light';` +
  `document.documentElement.classList.toggle('dark',d);` +
  `document.documentElement.setAttribute('data-theme',d?'dark':'light')}` +
  `catch(e){document.documentElement.classList.add('dark');` +
  `document.documentElement.setAttribute('data-theme','dark')}`;

/**
 * Fired on the window after the theme changes. Anything that reads a CSS custom property into JavaScript -
 * canvas charts, for instance - cannot be restyled by CSS alone and has to rebuild itself when this fires.
 */
export const THEME_EVENT = "nse:themechange";

/** The theme that has been chosen, from storage, falling back to the rendered class and then the default. */
export function readStoredTheme(): Theme {
  try {
    const stored = window.localStorage.getItem(THEME_KEY);
    if (stored === "light" || stored === "dark") return stored;
  } catch {
    // Private browsing or blocked site data: fall through to whatever is on the document.
  }
  // Nothing readable was stored, so trust what is rendered - the pre-paint script put it there.
  const el = document.documentElement;
  const attr = el.getAttribute("data-theme");
  if (attr === "light" || attr === "dark") return attr;
  return el.classList.contains("dark") ? "dark" : DEFAULT_THEME;
}

/** Write `theme` to the document without touching storage or notifying listeners. */
function paint(theme: Theme): void {
  document.documentElement.classList.toggle("dark", theme === "dark");
  document.documentElement.setAttribute("data-theme", theme);
}

/** Apply `theme` to the document and remember it. Storage may be unavailable; the class still changes. */
export function applyTheme(theme: Theme): void {
  paint(theme);
  try {
    window.localStorage.setItem(THEME_KEY, theme);
  } catch {
    // Private browsing or blocked site data: the theme still applies for this page view.
  }
  window.dispatchEvent(new CustomEvent(THEME_EVENT, { detail: theme }));
}

/**
 * Re-assert the stored theme on the document, and return it.
 *
 * Called once after mount. The pre-paint script already did this, but the class it added lives on an
 * element the server rendered without one, so hydration or a later re-render of <html> can drop it. This
 * is idempotent and writes nothing when the document already agrees, so it costs a class check in the
 * normal case and repairs the page in the broken one. It deliberately does not write to storage: a repair
 * must not be able to invent a preference the user never expressed.
 */
export function ensureThemeApplied(): Theme {
  const theme = readStoredTheme();
  const el = document.documentElement;
  if (el.classList.contains("dark") !== (theme === "dark") || el.getAttribute("data-theme") !== theme) {
    paint(theme);
  }
  return theme;
}
