import { HttpClient } from "@angular/common/http";
import { Component, inject, OnInit, signal } from "@angular/core";
import { form, FormField, required, submit } from "@angular/forms/signals";
import { firstValueFrom } from "rxjs";

/** OIDC settings shape returned and accepted by /api/auth/oidc-settings. */
export interface OidcSettings {
  enabled: boolean;
  issuer: string;
  client_id: string;
  client_secret: string;
  redirect_uri: string;
  post_logout_redirect_uri: string;
  response_type: string;
  scope: string;
}

@Component({
  selector: "app-oidc-config-panel",
  imports: [FormField],
  templateUrl: "./oidc-config-panel.html",
  styleUrl: "./oidc-config-panel.css",
})
export class OidcConfigPanel implements OnInit {
  private http = inject(HttpClient);

  // ── State signals ──────────────────────────────────────────────────────────
  readonly loading = signal(false);
  readonly saving = signal(false);
  readonly saved = signal(false);
  readonly error = signal<string | null>(null);

  /** Whether the client secret field is revealed in plaintext. */
  readonly showSecret = signal(false);

  oidcModel = signal<OidcSettings>({
    enabled: false,
    issuer: "",
    client_id: "",
    client_secret: "",
    redirect_uri: "",
    post_logout_redirect_uri: "",
    response_type: "code",
    scope: "openid profile email",
  });

  oidcForm = form(this.oidcModel, (p) => {
    required(p.enabled);
    required(p.issuer);
    required(p.client_id);
    required(p.client_secret);
    required(p.redirect_uri);
    required(p.scope);
  });

  ngOnInit(): void {
    this.load();
  }

  /** Fetch current OIDC settings from the backend. */
  load(): void {
    this.loading.set(true);
    this.error.set(null);
    this.http.get<OidcSettings>("/api/auth/oidc-settings").subscribe({
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

  /** Persist the form values to /api/auth/oidc-settings. */
  save(): void {
    this.saving.set(true);
    this.saved.set(false);
    this.error.set(null);
    submit(this.oidcForm, async (form) => {
      const formData = form().value();
      try {
        await firstValueFrom(
          this.http.put<OidcSettings>("/api/auth/oidc-settings", formData),
        );
        this.saving.set(false);
        this.saved.set(true);
        setTimeout(() => this.saved.set(false), 3000);
      } catch (err: any) {
        this.error.set(
          err?.error?.detail ?? "Failed to save OIDC configuration",
        );
        this.saving.set(false);
      }
    });
  }
}
