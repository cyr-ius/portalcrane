/**
 * Portalcrane - OidcService
 * Encapsulates every OIDC-specific interaction:
 *   - getPublicConfig()       → GET /api/oidc/config  (login page)
 *   - redirectToProvider()    → build authorization URL and redirect the browser
 *   - handleCallback()        → POST /api/oidc/callback (exchange code for JWT)
 *   - getAdminSettings()      → GET /api/oidc/settings  (settings page, admin)
 *   - saveAdminSettings()     → PUT /api/oidc/settings  (settings page, admin)
 *
 * Session state (token, user signal) remains in AuthService.
 */

import { HttpClient } from "@angular/common/http";
import { inject, Injectable } from "@angular/core";
import { OIDC_STATE_KEY } from "../constants/oidc.constants";
import {
  LoginResponse,
  OidcAdminSettings,
  OidcPublicConfig,
} from "../models/auth.models";

@Injectable({ providedIn: "root" })
export class OidcService {
  private readonly http = inject(HttpClient);

  // ── Public config (used by the login page) ────────────────────────────────

  /** Fetch the public OIDC configuration from the backend (no auth required). */
  getPublicConfig() {
    return this.http.get<OidcPublicConfig>("/api/oidc/config");
  }

  // ── Authorization redirect ────────────────────────────────────────────────

  /**
   * Build the OIDC authorization URL from *config* and redirect the browser.
   * A random CSRF state parameter is stored in sessionStorage so the callback
   * component can verify it.
   */
  redirectToProvider(config: OidcPublicConfig): void {
    if (!config.authorization_endpoint) return;

    const state = crypto.randomUUID();
    sessionStorage.setItem(OIDC_STATE_KEY, state);

    const params = new URLSearchParams({
      response_type: config.response_type,
      client_id: config.client_id,
      redirect_uri: config.redirect_uri,
      scope: config.scope,
      state,
    });

    window.location.href = `${config.authorization_endpoint}?${params}`;
  }

  // ── Callback (code → local JWT) ───────────────────────────────────────────

  /**
   * Exchange the authorization code for a Portalcrane JWT.
   * Returns an Observable<LoginResponse> that callers must subscribe to.
   * The caller (OidcCallbackComponent) is responsible for storing the token
   * via AuthService.
   */
  exchangeCode(code: string, state: string) {
    const params = new URLSearchParams({ code, state });
    return this.http.post<LoginResponse>(
      `/api/oidc/callback?${params.toString()}`,
      {},
    );
  }

  // ── Admin settings (settings page) ───────────────────────────────────────

  /** Fetch the full OIDC settings (including client_secret) for the settings page. */
  getAdminSettings() {
    return this.http.get<OidcAdminSettings>("/api/oidc/settings");
  }

  /** Persist OIDC settings overrides to the backend. */
  saveAdminSettings(payload: OidcAdminSettings) {
    return this.http.put<OidcAdminSettings>("/api/oidc/settings", payload);
  }
}
