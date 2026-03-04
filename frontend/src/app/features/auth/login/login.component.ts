/**
 * Portalcrane - LoginComponent
 * Handles both local credential login and the initial OIDC redirect.
 * OIDC callback handling is done in OidcCallbackComponent.
 */

import { SlicePipe } from "@angular/common";
import { Component, inject, OnInit, signal } from "@angular/core";
import { form, FormField, required, submit } from "@angular/forms/signals";
import { Router } from "@angular/router";
import { firstValueFrom } from "rxjs";

import { OidcPublicConfig } from "../../../core/models/auth.models";
import { AuthService } from "../../../core/services/auth.service";
import { OidcService } from "../../../core/services/oidc.service";
import { ThemeService } from "../../../core/services/theme.service";

@Component({
  selector: "app-login",
  imports: [SlicePipe, FormField],
  templateUrl: "./login.component.html",
  styleUrl: "./login.component.css",
})
export class LoginComponent implements OnInit {
  private readonly auth = inject(AuthService);
  private readonly oidc = inject(OidcService);
  private readonly router = inject(Router);
  readonly themeService = inject(ThemeService);

  // ── Local login form ──────────────────────────────────────────────────────

  readonly loginModel = signal({ username: "", password: "" });
  readonly loginForm = form(this.loginModel, (p) => ({
    username: [required(p.username)],
    password: [required(p.password)],
  }));

  readonly loading = signal(false);
  readonly error = signal("");
  readonly showPassword = signal(false);

  // ── OIDC ──────────────────────────────────────────────────────────────────

  readonly oidcConfig = signal<OidcPublicConfig | null>(null);

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
        await firstValueFrom(this.auth.login(username!, password!));
        this.router.navigate(["/"]);
      } catch (err: unknown) {
        const httpErr = err as { error?: { detail?: string } };
        this.error.set(httpErr.error?.detail ?? "Authentication failed");
      } finally {
        this.loading.set(false);
      }
    });
  }

  /** Delegate the OIDC authorization redirect to OidcService. */
  loginWithOidc(): void {
    const config = this.oidcConfig();
    if (config) {
      this.oidc.redirectToProvider(config);
    }
  }
}
