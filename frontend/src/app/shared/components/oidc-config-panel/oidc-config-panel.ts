/**
 * Portalcrane - OidcConfigPanel
 * Settings panel that lets admins view and persist OIDC configuration.
 * Delegates all HTTP calls to OidcService.
 */

import { Component, inject, OnInit, signal } from "@angular/core";
import { form, FormField, required, submit } from "@angular/forms/signals";
import { firstValueFrom } from "rxjs";

import { OidcAdminSettings } from "../../../core/models/auth.models";
import { OidcService } from "../../../core/services/oidc.service";

@Component({
  selector: "app-oidc-config-panel",
  imports: [FormField],
  templateUrl: "./oidc-config-panel.html",
  styleUrl: "./oidc-config-panel.css",
})
export class OidcConfigPanel implements OnInit {
  private readonly oidc = inject(OidcService);

  // ── State signals ──────────────────────────────────────────────────────────
  readonly loading = signal(false);
  readonly saved = signal(false);
  readonly error = signal<string | null>(null);

  /** Whether the client_secret field is shown in plaintext. */
  readonly showSecret = signal(false);

  // ── Signal form ────────────────────────────────────────────────────────────

  readonly oidcModel = signal<OidcAdminSettings>({
    enabled: false,
    issuer: "",
    client_id: "",
    client_secret: "",
    redirect_uri: "",
    post_logout_redirect_uri: "",
    response_type: "code",
    scope: "openid profile email",
  });

  readonly oidcForm = form(this.oidcModel, (p) => {
    required(p.enabled);
    required(p.issuer);
    required(p.client_id);
    required(p.redirect_uri);
    required(p.scope);
  });

  ngOnInit(): void {
    this.load();
  }

  /** Fetch the current OIDC settings from the backend. */
  load(): void {
    this.loading.set(true);
    this.error.set(null);
    this.oidc.getAdminSettings().subscribe({
      next: (s) => {
        this.oidcModel.set(s);
        this.loading.set(false);
      },
      error: (err) => {
        this.error.set(
          err?.error?.detail ?? "Failed to load OIDC configuration",
        );
        this.loading.set(false);
      },
    });
  }

  /** Persist the form values to the backend. */
  save(): void {
    this.saved.set(false);
    this.error.set(null);

    submit(this.oidcForm, async (f) => {
      const formData = f().value() as OidcAdminSettings;
      try {
        await firstValueFrom(this.oidc.saveAdminSettings(formData));
        this.saved.set(true);
        setTimeout(() => this.saved.set(false), 3000);
      } catch (err: unknown) {
        const httpErr = err as { error?: { detail?: string } };
        this.error.set(
          httpErr?.error?.detail ?? "Failed to save OIDC configuration",
        );
      }
    });
  }

  /** Persist OIDC enable/disable once the checkbox value has been applied. */
  onEnabledToggle(): void {
    queueMicrotask(() => this.save());
  }
}
