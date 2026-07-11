/**
 * Portalcrane - Auth models
 * Single source of truth for all authentication and OIDC TypeScript interfaces.
 * Imported by AuthService, OidcService, and any component that needs them.
 */

// ── Local auth ────────────────────────────────────────────────────────────────

/** JWT token response returned by /api/auth/login and /api/oidc/callback. */
export interface LoginResponse {
  access_token: string;
  token_type: string;
  expires_in: number;
}

/** Authenticated user information returned by /api/auth/me. */
export interface UserInfo {
  username: string;
  is_admin: boolean;
  /** Whether the Personal Access Token feature is enabled (API_KEYS_ENABLED). */
  api_keys_enabled?: boolean;
  /** Whether this account has a local password it can change (false for OIDC). */
  can_change_password?: boolean;
}

// ── OIDC ──────────────────────────────────────────────────────────────────────

/**
 * Public OIDC configuration served by GET /api/oidc/config.
 * Used by the login page to build the authorization URL.
 * Does NOT contain the client_secret.
 */
export interface OidcPublicConfig {
  enabled: boolean;
  client_id: string;
  issuer: string;
  redirect_uri: string;
  post_logout_redirect_uri: string;
  authorization_endpoint: string;
  end_session_endpoint: string;
  response_type: string;
  scope: string;
  /** When true, local credential login is disabled (OIDC-only mode). */
  oidc_only: boolean;
}

/**
 * Full OIDC settings served by GET/PUT /api/oidc/settings.
 * Used by the settings page (admin only). Contains the client_secret.
 */
export interface OidcAdminSettings {
  enabled: boolean;
  issuer: string;
  client_id: string;
  client_secret: string;
  redirect_uri: string;
  post_logout_redirect_uri: string;
  response_type: string;
  scope: string;
  /** Disable every local login (env-admin included) and rely solely on OIDC. */
  oidc_only: boolean;
  /** Name of the OIDC claim carrying the user's groups/roles. */
  admin_group_claim: string;
  /** Group/role value that grants admin when present in admin_group_claim. */
  admin_group: string;
  /** Name of the OIDC claim carrying the user's groups/roles (regular users). */
  user_group_claim: string;
  /** Group/role value that grants regular-user access when present in user_group_claim. */
  user_group: string;
  /** Restrict access to mapped groups: deny (and never create) users matching neither the admin nor the user group. */
  restrict_to_groups: boolean;
}

/** Single diagnostic step returned by POST /api/oidc/test. */
export interface OidcTestStep {
  name: string;
  ok: boolean;
  detail: string;
}

/** Result of the OIDC connectivity test (POST /api/oidc/test). */
export interface OidcTestResult {
  success: boolean;
  steps: OidcTestStep[];
}
