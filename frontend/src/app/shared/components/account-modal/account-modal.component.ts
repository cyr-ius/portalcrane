/**
 * Portalcrane - Account Modal
 * User profile panel: account info, personal access tokens, preferences
 * (language + theme) and personal external registries.
 * Accessible to ALL authenticated users via the sidebar user zone.
 *
 * The registry add/edit form + list is provided by the shared
 * RegistryFormPanelComponent (personal scope, compact layout).
 */
import { Component, inject, output } from "@angular/core";
import { TranslatePipe } from "@ngx-translate/core";
import { AuthService } from "../../../core/services/auth.service";
import { Language, LanguageService } from "../../../core/services/language.service";
import { Theme, ThemeService } from "../../../core/services/theme.service";
import { PersonalTokensPanelComponent } from "../personal-tokens-panel/personal-tokens-panel.component";
import { RegistryFormPanelComponent } from "../registry-form-panel/registry-form-panel.component";

@Component({
  selector: "app-account-modal",
  imports: [PersonalTokensPanelComponent, RegistryFormPanelComponent, TranslatePipe],
  templateUrl: "./account-modal.component.html",
  styleUrl: "./account-modal.component.css",
})
export class AccountModalComponent {
  readonly close = output<void>();
  readonly authService = inject(AuthService);
  readonly languageService = inject(LanguageService);
  readonly themeService = inject(ThemeService);

  readonly currentUser = this.authService.currentUser;

  /** Selectable interface themes shown in the account panel. */
  readonly themes: { value: Theme; icon: string; label: string }[] = [
    { value: "light", icon: "bi-sun-fill", label: "THEME.LIGHT" },
    { value: "dark", icon: "bi-moon-fill", label: "THEME.DARK" },
    { value: "auto", icon: "bi-circle-half", label: "THEME.AUTO" },
  ];

  /** Change the interface language from the account panel. */
  setLanguage(lang: Language): void {
    this.languageService.setLanguage(lang);
  }

  /** Change the interface theme from the account panel. */
  setTheme(theme: Theme): void {
    this.themeService.setTheme(theme);
  }
}
