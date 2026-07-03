/**
 * Portalcrane - LoginComponent
 * Handles both local credential login and the initial OIDC redirect.
 * OIDC callback handling is done in OidcCallbackComponent.
 */

import { SlicePipe } from "@angular/common";
import { Component, computed, inject, OnInit, signal } from "@angular/core";
import { form, FormField, required, submit } from "@angular/forms/signals";
import { Router } from "@angular/router";
import { TranslatePipe, TranslateService } from "@ngx-translate/core";

import { OidcPublicConfig } from "../../../core/models/auth.models";
import { AuthService } from "../../../core/services/auth.service";
import { OidcService } from "../../../core/services/oidc.service";
import { ThemeService } from "../../../core/services/theme.service";
import { AppLogo } from "../../../shared/components/app-logo/app-logo";

@Component({
  selector: "app-login",
  imports: [SlicePipe, FormField, AppLogo, TranslatePipe],
  templateUrl: "./login.component.html",
  styleUrl: "./login.component.css",
})
export class LoginComponent implements OnInit {
  private readonly auth = inject(AuthService);
  private readonly oidc = inject(OidcService);
  private readonly router = inject(Router);
  private readonly translate = inject(TranslateService);
  readonly themeService = inject(ThemeService);

  // ── Local login form ──────────────────────────────────────────────────────
  login = { username: "", password: "" };
  readonly loginModel = signal({ ...this.login });
  readonly loginForm = form(this.loginModel, (p) => {
    required(p.username);
    required(p.password);
  });

  readonly loading = signal(false);
  readonly error = signal("");
  readonly showPassword = signal(false);

  // ── OIDC ──────────────────────────────────────────────────────────────────

  readonly oidcConfig = signal<OidcPublicConfig | null>(null);

  /**
   * Whether the local credential form may be shown. It is hidden only when
   * OIDC is enabled AND OIDC-only mode is active (no local login allowed).
   */
  readonly localLoginEnabled = computed(() => {
    const config = this.oidcConfig();
    return !(config?.enabled && config?.oidc_only);
  });

  /**
   * True when OIDC is enabled but the backend could not resolve the provider
   * authorization endpoint from its discovery document.
   */
  readonly oidcAuthorizationUnavailable = computed(() => {
    const config = this.oidcConfig();
    return Boolean(config?.enabled && !config.authorization_endpoint);
  });

  ngOnInit(): void {
    // Load the OIDC public config to show/hide the SSO button
    this.oidc.getPublicConfig().subscribe({
      next: (config) => this.oidcConfig.set(config),
    });
  }

  // ── Handlers ──────────────────────────────────────────────────────────────

  onSubmit(event: Event): void {
    event.preventDefault();
    this.loading.set(true);
    this.error.set("");

    submit(this.loginForm, async (f) => {
      const { username, password } = f().value();
      try {
        await this.auth.login(username!, password!);
        this.router.navigate(["/"]);
        f().reset({ ...this.login });
      } catch (err: unknown) {
        const httpErr = err as { error?: { detail?: string } };
        this.error.set(
          httpErr.error?.detail ?? this.translate.instant("AUTH.LOGIN_FAILED"),
        );
      } finally {
        this.loading.set(false);
      }
    });
  }

  /** Delegate the OIDC authorization redirect to OidcService. */
  loginWithOidc(): void {
    this.error.set("");
    const config = this.oidcConfig();
    if (!config) {
      return;
    }

    const redirected = this.oidc.redirectToProvider(config);
    if (!redirected) {
      this.error.set(this.translate.instant("AUTH.OIDC_ENDPOINT_UNAVAILABLE"));
    }
  }
}
