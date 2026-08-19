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
import { OIDC_STATE_KEY, OIDC_VERIFIER_KEY } from "../constants/oidc.constants";
import {
  LoginResponse,
  OidcAdminSettings,
  OidcPublicConfig,
  OidcTestResult,
} from "../models/auth.models";

@Injectable({ providedIn: "root" })
export class OidcService {
  private readonly http = inject(HttpClient);

  // ── Public (unauthenticated) ──────────────────────────────────────────────

  /** Fetch the public OIDC configuration used on the login page. */
  getPublicConfig() {
    return this.http.get<OidcPublicConfig>("/api/oidc/config");
  }

  /**
   * Generate a CSRF state for the OIDC flow.
   *
   * crypto.randomUUID() is only guaranteed in secure contexts; deployments that
   * expose Portalcrane over plain HTTP on a non-local hostname must still be
   * able to start an OIDC redirect. getRandomValues() remains available in
   * those browsers and provides enough entropy for this nonce.
   */
  private generateState(): string {
    if (globalThis.crypto?.randomUUID) {
      return globalThis.crypto.randomUUID();
    }

    const bytes = new Uint8Array(16);
    globalThis.crypto.getRandomValues(bytes);
    return Array.from(bytes, (byte) => byte.toString(16).padStart(2, "0")).join(
      "",
    );
  }

  /**
   * Generate a PKCE (RFC 7636) code_verifier: a high-entropy random string,
   * base64url-encoded from 32 random bytes (43 chars, well within the
   * 43-128 range required by the spec).
   */
  private generateCodeVerifier(): string {
    const bytes = new Uint8Array(32);
    globalThis.crypto.getRandomValues(bytes);
    return this.base64UrlEncode(bytes);
  }

  /**
   * Derive the PKCE code_challenge from a code_verifier using S256, as
   * required by RFC 7636 / OAuth 2.1 whenever a secure hashing primitive is
   * available. crypto.subtle is only exposed in secure contexts (HTTPS or
   * localhost); on a plain-HTTP non-local deployment it falls back to the
   * "plain" method (challenge == verifier) so the OIDC redirect still works.
   */
  private async generateCodeChallenge(
    verifier: string,
  ): Promise<{ challenge: string; method: string }> {
    if (!globalThis.crypto?.subtle) {
      return { challenge: verifier, method: "plain" };
    }

    const digest = await globalThis.crypto.subtle.digest(
      "SHA-256",
      new TextEncoder().encode(verifier),
    );
    return {
      challenge: this.base64UrlEncode(new Uint8Array(digest)),
      method: "S256",
    };
  }

  private base64UrlEncode(bytes: Uint8Array): string {
    let binary = "";
    for (const byte of bytes) {
      binary += String.fromCharCode(byte);
    }
    return btoa(binary)
      .replace(/\+/g, "-")
      .replace(/\//g, "_")
      .replace(/=+$/, "");
  }

  /** Build the authorization URL and redirect the browser to the OIDC provider. */
  async redirectToProvider(config: OidcPublicConfig): Promise<boolean> {
    if (!config.authorization_endpoint) return false;

    const state = this.generateState();
    sessionStorage.setItem(OIDC_STATE_KEY, state);

    const codeVerifier = this.generateCodeVerifier();
    sessionStorage.setItem(OIDC_VERIFIER_KEY, codeVerifier);
    const { challenge, method } = await this.generateCodeChallenge(codeVerifier);

    const params = new URLSearchParams({
      response_type: config.response_type,
      client_id: config.client_id,
      redirect_uri: config.redirect_uri,
      scope: config.scope,
      state,
      code_challenge: challenge,
      code_challenge_method: method,
    });

    window.location.href = `${config.authorization_endpoint}?${params}`;
    return true;
  }

  /** Exchange an authorization code for a local Portalcrane JWT. */
  exchangeCode(code: string, state: string, codeVerifier: string) {
    const params = new URLSearchParams({
      code,
      state,
      code_verifier: codeVerifier,
    });
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

  /**
   * Run a live connectivity test against the OIDC provider with the given
   * (possibly unsaved) settings. Admin-only — the backend enforces
   * require_admin. An empty client_secret falls back to the stored value.
   */
  testConnection(payload: OidcAdminSettings) {
    return this.http.post<OidcTestResult>("/api/oidc/test", payload);
  }
}
