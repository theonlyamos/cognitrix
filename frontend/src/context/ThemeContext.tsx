import { createContext, useCallback, useContext, useEffect, useState, type ReactNode } from 'react';

type Theme = 'dark' | 'light';
/** What the user picked: a fixed theme, or follow the system. */
export type ThemeMode = Theme | 'system';

interface ThemeCtx {
  /** The theme on screen right now. */
  theme: Theme;
  mode: ThemeMode;
  toggle: () => void;
  setTheme: (t: Theme) => void;
  setMode: (m: ThemeMode) => void;
}

const Ctx = createContext<ThemeCtx | null>(null);

/** Read the theme the pre-paint boot script already applied to <html>. */
function currentTheme(): Theme {
  return typeof document !== 'undefined' && document.documentElement.classList.contains('dark')
    ? 'dark'
    : 'light';
}

function storedMode(): ThemeMode {
  try {
    const saved = localStorage.getItem('darkMode');
    return saved === 'true' ? 'dark' : saved === 'false' ? 'light' : 'system';
  } catch {
    return 'system';
  }
}

const systemDark = () => typeof window !== 'undefined' && !!window.matchMedia?.('(prefers-color-scheme: dark)').matches;

export function ThemeProvider({ children }: { children: ReactNode }) {
  const [theme, setThemeState] = useState<Theme>(currentTheme);
  const [mode, setModeState] = useState<ThemeMode>(storedMode);

  const apply = useCallback((t: Theme) => {
    document.documentElement.classList.toggle('dark', t === 'dark');
    setThemeState(t);
  }, []);

  const setMode = useCallback((m: ThemeMode) => {
    try {
      if (m === 'system') localStorage.removeItem('darkMode');
      else localStorage.setItem('darkMode', String(m === 'dark'));
    } catch { /* storage blocked: lasts for this page only */ }
    setModeState(m);
    apply(m === 'system' ? (systemDark() ? 'dark' : 'light') : m);
  }, [apply]);

  const setTheme = useCallback((t: Theme) => setMode(t), [setMode]);
  const toggle = useCallback(() => setMode(currentTheme() === 'dark' ? 'light' : 'dark'), [setMode]);

  // Following the system: switch when the OS does.
  useEffect(() => {
    if (mode !== 'system' || !window.matchMedia) return;
    const query = window.matchMedia('(prefers-color-scheme: dark)');
    const onChange = () => apply(query.matches ? 'dark' : 'light');
    query.addEventListener?.('change', onChange);
    return () => query.removeEventListener?.('change', onChange);
  }, [mode, apply]);

  return <Ctx.Provider value={{ theme, mode, toggle, setTheme, setMode }}>{children}</Ctx.Provider>;
}

export function useTheme(): ThemeCtx {
  const ctx = useContext(Ctx);
  if (!ctx) throw new Error('useTheme must be used within ThemeProvider');
  return ctx;
}
