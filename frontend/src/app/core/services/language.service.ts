import { inject, Injectable, signal } from "@angular/core";
import { TranslateService } from "@ngx-translate/core";

export type Language = "en" | "fr" | "es";

interface LanguageOption {
  value: Language;
  label: string;
  flag: string;
}

/**
 * Gère la langue de l'interface. Les traductions sont chargées à l'exécution
 * par ngx-translate depuis /i18n/{lang}.json (voir app.config.ts). La langue
 * choisie est persistée dans localStorage et réappliquée au démarrage.
 */
@Injectable({ providedIn: "root" })
export class LanguageService {
  private readonly LANG_KEY = "pc_lang";
  private readonly translate = inject(TranslateService);

  /** Langues proposées dans le sélecteur. */
  readonly languages: readonly LanguageOption[] = [
    { value: "en", label: "English", flag: "🇬🇧" },
    { value: "fr", label: "Français", flag: "🇫🇷" },
    { value: "es", label: "Español", flag: "🇪🇸" },
  ];

  private _language = signal<Language>(this.resolveInitialLanguage());
  readonly language = this._language.asReadonly();

  constructor() {
    this.translate.use(this._language());
  }

  setLanguage(lang: Language): void {
    this._language.set(lang);
    this.translate.use(lang);
    localStorage.setItem(this.LANG_KEY, lang);
  }

  private resolveInitialLanguage(): Language {
    const stored = localStorage.getItem(this.LANG_KEY);
    const candidate = stored ?? navigator.language.split("-")[0];
    const supported = this.languages.map((l) => l.value) as string[];
    return supported.includes(candidate) ? (candidate as Language) : "en";
  }
}
