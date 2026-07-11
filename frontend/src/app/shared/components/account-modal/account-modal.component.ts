/**
 * Portalcrane - Account Modal
 * User profile panel: account info, personal access tokens, preferences
 * (language + theme) and personal external registries.
 * Accessible to ALL authenticated users via the sidebar user zone.
 *
 * The registry add/edit form + list is provided by the shared
 * RegistryFormPanelComponent (personal scope, compact layout).
 */
import { Component, inject, output, signal } from "@angular/core";
import {
  form,
  FormField,
  minLength,
  required,
  submit,
} from "@angular/forms/signals";
import { TranslatePipe, TranslateService } from "@ngx-translate/core";
import { AuthService } from "../../../core/services/auth.service";
import {
  Language,
  LanguageService,
} from "../../../core/services/language.service";
import { Theme, ThemeService } from "../../../core/services/theme.service";
import { PersonalTokensPanelComponent } from "../personal-tokens-panel/personal-tokens-panel.component";
import { RegistryFormPanelComponent } from "../registry-form-panel/registry-form-panel.component";

/** Shape of the change-password form model. */
interface PasswordFormModel {
  currentPassword: string;
  newPassword: string;
  confirmPassword: string;
}

@Component({
  selector: "app-account-modal",
  imports: [
    PersonalTokensPanelComponent,
    RegistryFormPanelComponent,
    FormField,
    TranslatePipe,
  ],
  templateUrl: "./account-modal.component.html",
  styleUrl: "./account-modal.component.css",
})
export class AccountModalComponent {
  readonly close = output<void>();
  readonly authService = inject(AuthService);
  readonly languageService = inject(LanguageService);
  readonly themeService = inject(ThemeService);
  private readonly translate = inject(TranslateService);

  readonly currentUser = this.authService.currentUser;

  // ── Change password ─────────────────────────────────────────────────────────
  readonly changingPassword = signal(false);
  readonly passwordError = signal<string | null>(null);
  readonly passwordSuccess = signal(false);

  private readonly passwordInit: PasswordFormModel = {
    currentPassword: "",
    newPassword: "",
    confirmPassword: "",
  };

  readonly passwordModel = signal<PasswordFormModel>({ ...this.passwordInit });

  readonly passwordForm = form(this.passwordModel, (p) => {
    required(p.currentPassword);
    required(p.newPassword);
    minLength(p.newPassword, 8, {
      message: this.translate.instant("ACCOUNT.PASSWORD_MIN"),
    });
    required(p.confirmPassword);
  });

  /** Submit the change-password form. */
  submitPassword(): void {
    submit(this.passwordForm, async (f) => {
      const { currentPassword, newPassword, confirmPassword } = f().value();
      this.passwordError.set(null);
      this.passwordSuccess.set(false);
      // Cross-field check kept out of the schema for API-version portability.
      if (newPassword !== confirmPassword) {
        this.passwordError.set(
          this.translate.instant("ACCOUNT.PASSWORD_MISMATCH"),
        );
        return;
      }
      this.changingPassword.set(true);
      try {
        await this.authService.changePassword(currentPassword!, newPassword!);
        this.passwordSuccess.set(true);
        f().reset({ ...this.passwordInit });
      } catch (err: unknown) {
        const httpErr = err as { error?: { detail?: string } };
        this.passwordError.set(
          httpErr?.error?.detail ??
            this.translate.instant("ACCOUNT.PASSWORD_ERROR"),
        );
      } finally {
        this.changingPassword.set(false);
      }
    });
  }

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
