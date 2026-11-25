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
  readStoredTheme,
  THEME_EVENT,
  THEME_KEY,
  themeBootstrapScript,
  type Theme,
} from "@/lib/theme";

/** Run the shipped pre-paint script exactly as the browser would on a cold load. */
function coldLoad() {
  document.documentElement.classList.remove("dark");
  // eslint-disable-next-line no-new-func
  new Function(themeBootstrapScript)();
}

const isDark = () => document.documentElement.classList.contains("dark");

beforeEach(() => {
  window.localStorage.clear();
  document.documentElement.classList.remove("dark");
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
  it("reports the live DOM state, not a guess", () => {
    coldLoad();
    expect(readStoredTheme()).toBe("dark");
    applyTheme("light");
    expect(readStoredTheme()).toBe("light");
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
