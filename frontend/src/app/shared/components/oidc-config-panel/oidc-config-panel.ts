import { HttpClient } from "@angular/common/http";
import {
  ChangeDetectionStrategy,
  Component,
  inject,
  OnInit,
  signal,
} from "@angular/core";

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
  imports: [],
  templateUrl: "./oidc-config-panel.html",
  styleUrl: "./oidc-config-panel.css",
  changeDetection: ChangeDetectionStrategy.OnPush,
})
export class OidcConfigPanel implements OnInit {
  private http = inject(HttpClient);

  // ── State signals ──────────────────────────────────────────────────────────
  readonly loading = signal(false);
  readonly saving = signal(false);
  readonly saved = signal(false);
  readonly error = signal<string | null>(null);

  // ── Form signals (each field is its own signal) ────────────────────────────
  readonly enabled = signal(false);
  readonly issuer = signal("");
  readonly clientId = signal("");
  readonly clientSecret = signal("");
  readonly redirectUri = signal("");
  readonly postLogoutRedirectUri = signal("");
  readonly responseType = signal("code");
  readonly scope = signal("openid profile email");

  /** Whether the client secret field is revealed in plaintext. */
  readonly showSecret = signal(false);

  ngOnInit(): void {
    this.load();
  }

  /** Fetch current OIDC settings from the backend. */
  load(): void {
    this.loading.set(true);
    this.error.set(null);
    this.http.get<OidcSettings>("/api/auth/oidc-settings").subscribe({
      next: (s) => {
        this.enabled.set(s.enabled);
        this.issuer.set(s.issuer);
        this.clientId.set(s.client_id);
        this.clientSecret.set(s.client_secret);
        this.redirectUri.set(s.redirect_uri);
        this.postLogoutRedirectUri.set(s.post_logout_redirect_uri);
        this.responseType.set(s.response_type || "code");
        this.scope.set(s.scope || "openid profile email");
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

    const payload: OidcSettings = {
      enabled: this.enabled(),
      issuer: this.issuer().trim(),
      client_id: this.clientId().trim(),
      client_secret: this.clientSecret(),
      redirect_uri: this.redirectUri().trim(),
      post_logout_redirect_uri: this.postLogoutRedirectUri().trim(),
      response_type: this.responseType().trim() || "code",
      scope: this.scope().trim() || "openid profile email",
    };

    this.http.put<OidcSettings>("/api/auth/oidc-settings", payload).subscribe({
      next: () => {
        this.saving.set(false);
        this.saved.set(true);
        // Clear the success banner after 3 seconds
        setTimeout(() => this.saved.set(false), 3000);
      },
      error: (err) => {
        this.error.set(
          err?.error?.detail ?? "Failed to save OIDC configuration",
        );
        this.saving.set(false);
      },
    });
  }
}
