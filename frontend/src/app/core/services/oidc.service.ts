/**
 * Portalcrane - OidcService
 * Encapsulates every OIDC-specific interaction:
 *   - getPublicConfig()       → GET /api/oidc/config  (login page, unauthenticated)
 *   - redirectToProvider()    → build authorization URL and redirect the browser
 *   - exchangeCode()          → POST /api/oidc/callback (exchange code for JWT)
 *   - getAdminSettings()      → GET /api/oidc/settings  (settings page, admin only)
 *   - saveAdminSettings()     → PUT /api/oidc/settings  (settings page, admin only)
 *
 * Session state (token, user signal) remains in AuthService.
 *
 * Security note: getAdminSettings() and saveAdminSettings() must only be called
 * from admin-gated components. The backend enforces require_admin on these
 * endpoints, but the frontend should also guard access to avoid unnecessary
 * 403 errors for regular users.
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

  // ── Public (unauthenticated) ──────────────────────────────────────────────

  /** Fetch the public OIDC configuration used on the login page. */
  getPublicConfig() {
    return this.http.get<OidcPublicConfig>("/api/oidc/config");
  }

  /** Build the authorization URL and redirect the browser to the OIDC provider. */
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

  /** Exchange an authorization code for a local Portalcrane JWT. */
  exchangeCode(code: string, state: string) {
    const params = new URLSearchParams({ code, state });
    return this.http.post<LoginResponse>(
      `/api/oidc/callback?${params.toString()}`,
      {},
    );
  }

  // ── Admin-only endpoints ──────────────────────────────────────────────────

  /**
   * Fetch full OIDC settings including client_secret.
   * Admin-only — the backend enforces require_admin (403 for regular users).
   * Only call this from admin-gated components (e.g. OidcConfigPanel).
   */
  getAdminSettings() {
    return this.http.get<OidcAdminSettings>("/api/oidc/settings");
  }

  /**
   * Persist OIDC settings overrides.
   * Admin-only — the backend enforces require_admin (403 for regular users).
   * Only call this from admin-gated components (e.g. OidcConfigPanel).
   */
  saveAdminSettings(payload: OidcAdminSettings) {
    return this.http.put<OidcAdminSettings>("/api/oidc/settings", payload);
  }
}
