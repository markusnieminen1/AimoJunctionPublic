import { createContext, useCallback, useContext, useEffect, useMemo, useState, type ReactNode } from "react";
import { en, type Strings } from "./en";
import { fi } from "./fi";

export type Locale = "en" | "fi";

const LOCALE_STORAGE_KEY = "aimoannos.locale";
const DICTIONARIES: Record<Locale, Strings> = { en, fi };

function readStoredLocale(): Locale {
  try {
    const raw = window.localStorage.getItem(LOCALE_STORAGE_KEY);
    return raw === "fi" ? "fi" : "en";
  } catch {
    return "en";
  }
}

let activeLocale: Locale = typeof window !== "undefined" ? readStoredLocale() : "en";

export function getLocale(): Locale {
  return activeLocale;
}

export function getStrings(locale: Locale = activeLocale): Strings {
  return DICTIONARIES[locale];
}

interface I18nContextValue {
  locale: Locale;
  setLocale: (next: Locale) => void;
  strings: Strings;
}

const I18nContext = createContext<I18nContextValue>({
  locale: "en",
  setLocale: () => undefined,
  strings: en,
});

export function I18nProvider({ children }: { children: ReactNode }) {
  const [locale, setLocaleState] = useState<Locale>(() => readStoredLocale());

  const setLocale = useCallback((next: Locale) => {
    activeLocale = next;
    setLocaleState(next);
    try {
      window.localStorage.setItem(LOCALE_STORAGE_KEY, next);
    } catch {
      // ignore private mode / storage errors
    }
  }, []);

  useEffect(() => {
    activeLocale = locale;
    if (typeof document !== "undefined") {
      document.documentElement.lang = locale;
    }
  }, [locale]);

  const value = useMemo<I18nContextValue>(
    () => ({ locale, setLocale, strings: DICTIONARIES[locale] }),
    [locale, setLocale],
  );

  return <I18nContext.Provider value={value}>{children}</I18nContext.Provider>;
}

export function useI18n(): I18nContextValue {
  return useContext(I18nContext);
}

export function useStrings(): Strings {
  return useContext(I18nContext).strings;
}

export { en, fi };
export type { Strings };
