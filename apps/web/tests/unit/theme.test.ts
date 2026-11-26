/**
 * Theme: dark by default, and the choice survives a reload.
 *
 * The pre-paint script is the only thing that decides the theme on a cold load, so it is tested as the
 * literal string the app ships rather than a re-implementation of it - a copy that drifted from the real
 * one would pass while the app was broken.
 */
import { beforeEach, describe, expect, it } from "vitest";

import {
  applyTheme,
  ensureThemeApplied,
  readStoredTheme,
  THEME_EVENT,
  THEME_KEY,
  themeBootstrapScript,
  type Theme,
} from "@/lib/theme";

/** Run the shipped pre-paint script exactly as the browser would on a cold load. */
function coldLoad() {
  document.documentElement.classList.remove("dark");
  document.documentElement.removeAttribute("data-theme");
  // eslint-disable-next-line no-new-func
  new Function(themeBootstrapScript)();
}

/**
 * What hydration does when it goes wrong: the server rendered <html> without a class, so reconciling that
 * element can strip the class the pre-paint script added, leaving the stored preference intact but the
 * page painted light.
 */
function hydrationDropsTheClass() {
  document.documentElement.classList.remove("dark");
  document.documentElement.removeAttribute("data-theme");
}

const isDark = () => document.documentElement.classList.contains("dark");

beforeEach(() => {
  window.localStorage.clear();
  document.documentElement.classList.remove("dark");
  document.documentElement.removeAttribute("data-theme");
});

describe("default", () => {
  it("is dark when nothing has been stored", () => {
    coldLoad();
    expect(isDark()).toBe(true);
  });

  it("is dark for a stored value that is not 'light'", () => {
    window.localStorage.setItem(THEME_KEY, "dark");
    coldLoad();
    expect(isDark()).toBe(true);
  });

  it("is dark even if a stored value is corrupt, rather than falling back to light", () => {
    window.localStorage.setItem(THEME_KEY, "{}");
    coldLoad();
    expect(isDark()).toBe(true);
  });
});

describe("persistence across reloads", () => {
  it("keeps light once chosen", () => {
    applyTheme("light");
    expect(window.localStorage.getItem(THEME_KEY)).toBe("light");
    coldLoad(); // the reload
    expect(isDark()).toBe(false);
  });

  it("keeps dark once chosen back from light", () => {
    applyTheme("light");
    applyTheme("dark");
    expect(window.localStorage.getItem(THEME_KEY)).toBe("dark");
    coldLoad();
    expect(isDark()).toBe(true);
  });

  it("survives several reloads without drifting", () => {
    applyTheme("light");
    for (let i = 0; i < 3; i += 1) coldLoad();
    expect(isDark()).toBe(false);
  });
});

describe("storage is unavailable", () => {
  it("still renders dark rather than throwing", () => {
    const proto = Object.getPrototypeOf(window.localStorage);
    const original = proto.getItem;
    proto.getItem = () => {
      throw new Error("storage disabled");
    };
    try {
      coldLoad();
      expect(isDark()).toBe(true);
    } finally {
      proto.getItem = original;
    }
  });

  it("applyTheme still switches the class when the write fails", () => {
    const proto = Object.getPrototypeOf(window.localStorage);
    const original = proto.setItem;
    proto.setItem = () => {
      throw new Error("storage disabled");
    };
    try {
      applyTheme("light");
      expect(isDark()).toBe(false); // the page must still respond to the click
    } finally {
      proto.setItem = original;
    }
  });
});

describe("readStoredTheme", () => {
  it("follows the choice through a cold load and a toggle", () => {
    coldLoad();
    expect(readStoredTheme()).toBe("dark");
    applyTheme("light");
    expect(readStoredTheme()).toBe("light");
  });

  it("trusts storage over a class that no longer matches it", () => {
    applyTheme("light");
    document.documentElement.classList.add("dark"); // something restyled the document behind our back
    // Asking the DOM would answer "dark" and the next toggle would then send the user to light again.
    expect(readStoredTheme()).toBe("light");
  });

  it("falls back to the rendered theme when nothing is stored", () => {
    coldLoad(); // no stored value; the script paints the default
    expect(window.localStorage.getItem(THEME_KEY)).toBeNull();
    expect(readStoredTheme()).toBe("dark");
  });
});

describe("surviving hydration", () => {
  it("restores dark after the class is dropped", () => {
    applyTheme("dark");
    hydrationDropsTheClass();
    expect(isDark()).toBe(false); // the bug as the user sees it: light page, dark stored
    expect(ensureThemeApplied()).toBe("dark");
    expect(isDark()).toBe(true);
  });

  it("restores light after the class is dropped, rather than forcing the default", () => {
    applyTheme("light");
    document.documentElement.classList.add("dark");
    document.documentElement.removeAttribute("data-theme");
    expect(ensureThemeApplied()).toBe("light");
    expect(isDark()).toBe(false);
  });

  it("gives a new user dark without inventing a stored preference", () => {
    coldLoad();
    hydrationDropsTheClass();
    expect(ensureThemeApplied()).toBe("dark");
    expect(isDark()).toBe(true);
    // A repair must not record a choice the user never made, or the default could never change.
    expect(window.localStorage.getItem(THEME_KEY)).toBeNull();
  });

  it("changes nothing when the document already agrees", () => {
    applyTheme("light");
    const before = document.documentElement.className;
    expect(ensureThemeApplied()).toBe("light");
    expect(document.documentElement.className).toBe(before);
    expect(window.localStorage.getItem(THEME_KEY)).toBe("light");
  });
});

describe("notifying canvas consumers", () => {
  it("fires an event so charts can rebuild against the new palette", () => {
    const seen: string[] = [];
    const onChange = (e: Event) => seen.push((e as CustomEvent<Theme>).detail);
    window.addEventListener(THEME_EVENT, onChange);
    try {
      applyTheme("light");
      applyTheme("dark");
    } finally {
      window.removeEventListener(THEME_EVENT, onChange);
    }
    // Charts read CSS custom properties into a canvas once, so a class change alone leaves them stale.
    expect(seen).toEqual(["light", "dark"]);
  });
});
