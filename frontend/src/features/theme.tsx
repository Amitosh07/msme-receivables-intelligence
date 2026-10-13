import { createContext, useCallback, useContext, useState } from "react";

export type Theme = "light" | "dark";
type ThemeContextValue = { theme: Theme; setTheme: (theme: Theme) => void; toggleTheme: () => void };

// Must match the key read by the pre-paint script in index.html.
export const THEME_STORAGE_KEY = "receivables-theme";

const ThemeContext = createContext<ThemeContextValue | undefined>(undefined);

function initialTheme(): Theme {
  const applied = document.documentElement.dataset.theme;
  if (applied === "dark" || applied === "light") return applied;
  try {
    return localStorage.getItem(THEME_STORAGE_KEY) === "dark" ? "dark" : "light";
  } catch {
    return "light";
  }
}

function applyTheme(theme: Theme) {
  const root = document.documentElement;
  // Switch every surface in the same frame instead of letting per-element transitions animate.
  root.setAttribute("data-theme-switching", "");
  root.dataset.theme = theme;
  root.style.colorScheme = theme;
  requestAnimationFrame(() => requestAnimationFrame(() => root.removeAttribute("data-theme-switching")));
  try {
    localStorage.setItem(THEME_STORAGE_KEY, theme);
  } catch {
    /* storage unavailable (private mode): theme still applies for this session */
  }
}

export function ThemeProvider({ children }: { children: React.ReactNode }) {
  const [theme, setThemeState] = useState<Theme>(initialTheme);

  const setTheme = useCallback((next: Theme) => {
    applyTheme(next);
    setThemeState(next);
  }, []);
  const toggleTheme = useCallback(() => setTheme(document.documentElement.dataset.theme === "dark" ? "light" : "dark"), [setTheme]);

  return <ThemeContext.Provider value={{ theme, setTheme, toggleTheme }}>{children}</ThemeContext.Provider>;
}

export function useTheme() {
  const value = useContext(ThemeContext);
  if (!value) throw new Error("useTheme must be used inside ThemeProvider");
  return value;
}
