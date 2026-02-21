import { Injectable, signal, effect } from '@angular/core';

export type Theme = 'light' | 'dark' | 'auto';

@Injectable({ providedIn: 'root' })
export class ThemeService {
  private readonly THEME_KEY = 'pc_theme';

  readonly theme = signal<Theme>(
    (localStorage.getItem(this.THEME_KEY) as Theme) || 'auto'
  );

  constructor() {
    // Apply theme on change
    effect(() => {
      this.applyTheme(this.theme());
    });

    // Listen for system theme changes
    window
      .matchMedia('(prefers-color-scheme: dark)')
      .addEventListener('change', () => {
        if (this.theme() === 'auto') {
          this.applyTheme('auto');
        }
      });
  }

  setTheme(theme: Theme) {
    this.theme.set(theme);
    localStorage.setItem(this.THEME_KEY, theme);
  }

  private applyTheme(theme: Theme) {
    const isDark =
      theme === 'dark' ||
      (theme === 'auto' &&
        window.matchMedia('(prefers-color-scheme: dark)').matches);

    document.documentElement.setAttribute(
      'data-bs-theme',
      isDark ? 'dark' : 'light'
    );
    document.documentElement.setAttribute(
      'data-pc-theme',
      isDark ? 'dark' : 'light'
    );
  }
}
