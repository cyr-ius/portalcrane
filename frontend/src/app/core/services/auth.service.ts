/**
 * Portalcrane - AuthService
 * Manages the local authentication session:
 *   - login() / logout() with local credentials
 *   - JWT token storage (localStorage)
 *   - reactive user and authentication state (signals)
 *
 * OIDC-specific logic (config fetch, redirect, callback) lives in OidcService.
 *
 * Session isolation fix:
 *   clearSession() now calls jobService.clearState() so that singleton
 *   services that cache user-scoped data are flushed on every logout.
 *   Without this, a second user logging in after a logout would briefly
 *   see the previous user's staging jobs (the ~200 ms before the first
 *   polling cycle fires).
 */

import { HttpClient } from "@angular/common/http";
import { computed, inject, Injectable, signal } from "@angular/core";
import { Router } from "@angular/router";
import { tap } from "rxjs";

import { LoginResponse, UserInfo } from "../models/auth.models";
import { JobService } from "./job.service";
import { OidcService } from "./oidc.service";

@Injectable({ providedIn: "root" })
export class AuthService {
  private readonly http = inject(HttpClient);
  private readonly router = inject(Router);
  private readonly oidcService = inject(OidcService);
  private readonly jobService = inject(JobService);

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
          this.storeToken(response.access_token);
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

  /**
   * Remove token and user from memory and localStorage.
   *
   * Also resets all singleton services that hold user-scoped state so that
   * a subsequent login (same browser tab, different user) starts with a clean
   * slate and never leaks data from the previous session.
   *
   * Called by:
   *   - logout()         — explicit user action
   *   - authInterceptor  — on any 401 response (session expired / invalid token)
   */
  clearSession(): void {
    this._token.set(null);
    this._user.set(null);
    localStorage.removeItem(this.TOKEN_KEY);
    localStorage.removeItem(this.USER_KEY);

    // Reset singleton service caches that contain user-scoped data.
    this.jobService.clearState();
  }

  /** Return the current JWT token string (or null when not authenticated). */
  getToken(): string | null {
    return this._token();
  }

  /**
   * Store a JWT access token in memory and localStorage.
   * Called by OidcCallbackComponent after a successful code exchange.
   */
  storeToken(token: string): void {
    this._token.set(token);
    localStorage.setItem(this.TOKEN_KEY, token);
  }

}
