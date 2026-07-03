/**
 * Portalcrane - OidcConfigPanel
 * Settings panel that lets admins view and persist OIDC configuration.
 * Delegates all HTTP calls to OidcService.
 */

import { Component, computed, inject, OnInit, signal } from "@angular/core";
import { form, FormField, required, submit } from "@angular/forms/signals";
import { firstValueFrom } from "rxjs";

import {
  OidcAdminSettings,
  OidcTestResult,
} from "../../../core/models/auth.models";
import { OidcService } from "../../../core/services/oidc.service";

@Component({
  selector: "app-oidc-config-panel",
  imports: [FormField],
  templateUrl: "./oidc-config-panel.html",
  styleUrl: "./oidc-config-panel.css",
})
export class OidcConfigPanel implements OnInit {
  private readonly oidc = inject(OidcService);

  readonly loading = signal(false);
  readonly saved = signal(false);
  readonly error = signal<string | null>(null);
  readonly showSecret = signal(false);
  readonly testing = signal(false);
  readonly testResult = signal<OidcTestResult | null>(null);
  readonly oidcModel = signal<OidcAdminSettings>({
    enabled: false,
    issuer: "",
    client_id: "",
    client_secret: "",
    redirect_uri: "",
    post_logout_redirect_uri: "",
    response_type: "code",
    scope: "openid profile email",
    oidc_only: false,
    admin_group_claim: "",
    admin_group: "",
    user_group_claim: "",
    user_group: "",
    restrict_to_groups: false,
  });

  /**
   * True when OIDC-only mode is requested but no admin group mapping is
   * configured. Mirrors the backend anti-lockout guard so the user is warned
   * before the save call is rejected with a 400.
   */
  readonly oidcOnlyMissingAdmin = computed(() => {
    const m = this.oidcModel();
    if (!m.oidc_only) return false;
    const hasAdminGroup =
      m.admin_group_claim.trim().length > 0 && m.admin_group.trim().length > 0;
    return !hasAdminGroup;
  });

  oidcForm = form(this.oidcModel, (p) => {
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
  onEnabledToggle(event: Event): void {
    const checked = (event.target as HTMLInputElement).checked;
    this.oidcModel.update((m) => ({ ...m, enabled: checked }));
    queueMicrotask(() => this.save());
  }

  /**
   * Run a live connectivity test against the OIDC provider using the current
   * (possibly unsaved) form values, without persisting anything.
   */
  testConnection(): void {
    this.error.set(null);
    this.testResult.set(null);
    this.testing.set(true);

    const payload = this.oidcForm().value() as OidcAdminSettings;
    this.oidc.testConnection(payload).subscribe({
      next: (result) => {
        this.testResult.set(result);
        this.testing.set(false);
      },
      error: (err) => {
        this.error.set(err?.error?.detail ?? "OIDC connection test failed");
        this.testing.set(false);
      },
    });
  }
}
