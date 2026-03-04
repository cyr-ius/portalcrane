/**
 * Portalcrane - AuthService
 * Manages the local authentication session:
 *   - login() / logout() with local credentials
 *   - JWT token storage (localStorage)
 *   - reactive user and authentication state (signals)
 *   - Docker Hub account settings
 *
 * OIDC-specific logic (config fetch, redirect, callback) lives in OidcService.
 */

import { HttpClient } from "@angular/common/http";
import { computed, inject, Injectable, signal } from "@angular/core";
import { Router } from "@angular/router";
import { tap } from "rxjs";

import {
  DockerHubAccountSettings,
  LoginResponse,
  UpdateDockerHubAccountSettingsRequest,
  UserInfo,
} from "../models/auth.models";
import { OidcService } from "./oidc.service";

@Injectable({ providedIn: "root" })
export class AuthService {
  private readonly http = inject(HttpClient);
  private readonly router = inject(Router);
  private readonly oidcService = inject(OidcService);

  private readonly TOKEN_KEY = "pc_token";
  private readonly USER_KEY = "pc_user";

  // ── Reactive state ────────────────────────────────────────────────────────

  private readonly _token = signal<string | null>(
    typeof window !== "undefined" ? localStorage.getItem(this.TOKEN_KEY) : null,
  );

  private readonly _user = signal<UserInfo | null>(
    JSON.parse(localStorage.getItem(this.USER_KEY) ?? "null"),
  );

  /** True when a valid token is present in memory. */
  readonly isAuthenticated = computed(() => !!this._token());

  /** Currently authenticated user (null before first load). */
  readonly currentUser = this._user.asReadonly();

  // ── Local authentication ──────────────────────────────────────────────────

  /** Authenticate with username and password, store the token, load user info. */
  login(username: string, password: string) {
    return this.http
      .post<LoginResponse>("/api/auth/login", { username, password })
      .pipe(
        tap((response) => {
          this._setToken(response.access_token);
          this.loadUserInfo();
        }),
      );
  }

  /** Fetch /api/auth/me and refresh the user signal. */
  loadUserInfo(): void {
    this.http.get<UserInfo>("/api/auth/me").subscribe({
      next: (user) => {
        this._user.set(user);
        localStorage.setItem(this.USER_KEY, JSON.stringify(user));
      },
    });
  }

  /**
   * Clear the local session then redirect to the login page.
   * When OIDC is active and an end-session endpoint is configured the browser
   * is redirected to the provider's logout URL instead.
   */
  logout(): void {
    this.clearSession();

    this.oidcService.getPublicConfig().subscribe({
      next: (config) => {
        if (config.enabled && config.end_session_endpoint) {
          const url = new URL(config.end_session_endpoint);
          if (config.post_logout_redirect_uri) {
            url.searchParams.set(
              "post_logout_redirect_uri",
              config.post_logout_redirect_uri,
            );
          }
          window.location.href = url.toString();
          return;
        }
        this.router.navigate(["/auth"]);
      },
      error: () => this.router.navigate(["/auth"]),
    });
  }

  /** Remove token and user from memory and localStorage. */
  clearSession(): void {
    this._token.set(null);
    this._user.set(null);
    localStorage.removeItem(this.TOKEN_KEY);
    localStorage.removeItem(this.USER_KEY);
  }

  /** Return the current JWT token string (or null when not authenticated). */
  getToken(): string | null {
    return this._token();
  }

  // ── Docker Hub account settings ───────────────────────────────────────────

  /** Fetch Docker Hub credentials for the authenticated user. */
  getDockerHubAccountSettings() {
    return this.http.get<DockerHubAccountSettings>(
      "/api/auth/account/dockerhub",
    );
  }

  /** Create or update Docker Hub credentials for the authenticated user. */
  updateDockerHubAccountSettings(
    payload: UpdateDockerHubAccountSettingsRequest,
  ) {
    return this.http.put<DockerHubAccountSettings>(
      "/api/auth/account/dockerhub",
      payload,
    );
  }

  /**
   * Store a JWT access token in memory and localStorage.
   * Called by OidcCallbackComponent after a successful code exchange.
   */
  storeToken(token: string): void {
    this._token.set(token);
    localStorage.setItem(this.TOKEN_KEY, token);
  }

  // ── Private helpers ───────────────────────────────────────────────────────

  private _setToken(token: string): void {
    this.storeToken(token);
  }
}
