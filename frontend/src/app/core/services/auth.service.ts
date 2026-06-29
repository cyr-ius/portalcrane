/**
 * Portalcrane - AuthService
 * Manages the authentication session:
 *   - login() / logout() with local credentials
 *   - reactive user and authentication state (signals)
 *
 * The session JWT lives in an HttpOnly cookie set by the backend and is never
 * accessible to JavaScript (no localStorage), which neutralises XSS token theft.
 * Auth state is therefore derived from the loaded user, restored on startup via
 * bootstrap() (a /me probe that relies on the cookie).
 *
 * OIDC-specific logic (config fetch, redirect, callback) lives in OidcService.
 *
 * Session isolation fix:
 *   clearSession() now calls jobService.clearState() and transferService.clearState()
 *   so that singleton services that cache user-scoped data are flushed on every logout.
 *   Without this, a second user logging in after a logout would briefly
 *   see the previous user's staging jobs (the ~200 ms before the first
 *   polling cycle fires).
 */

import { HttpClient } from "@angular/common/http";
import { computed, inject, Injectable, signal } from "@angular/core";
import { Router } from "@angular/router";
import { firstValueFrom } from "rxjs";

import { UserInfo } from "../models/auth.models";
import { JobService } from "./job.service";
import { OidcService } from "./oidc.service";
import { TransferService } from "./transfer.service";

@Injectable({ providedIn: "root" })
export class AuthService {
  private readonly http = inject(HttpClient);
  private readonly router = inject(Router);
  private readonly oidcService = inject(OidcService);
  private readonly jobService = inject(JobService);
  private readonly transferService = inject(TransferService);

  // ── Reactive state ────────────────────────────────────────────────────────

  // The session JWT lives in an HttpOnly cookie; auth state is derived from the
  // loaded user. No token is ever held in JavaScript.
  private readonly _user = signal<UserInfo | null>(null);

  /** True when an authenticated user has been loaded. */
  readonly isAuthenticated = computed(() => !!this._user());

  /** Currently authenticated user (null before first load). */
  readonly currentUser = this._user.asReadonly();

  // ── Session bootstrap ──────────────────────────────────────────────────────

  /**
   * Restore the session on startup from the HttpOnly cookie, if present.
   * Called by an app initializer before the auth guard runs so that a page
   * reload keeps the user authenticated. Never throws.
   */
  async bootstrap(): Promise<void> {
    try {
      this._user.set(
        await firstValueFrom(this.http.get<UserInfo>("/api/auth/me")),
      );
    } catch {
      this._user.set(null);
    }
  }

  // ── Local authentication ──────────────────────────────────────────────────

  /** Authenticate with username and password (backend sets the auth cookie). */
  async login(username: string, password: string): Promise<void> {
    await firstValueFrom(
      this.http.post("/api/auth/login", { username, password }),
    );
    await this.loadUserInfo();
  }

  /** Fetch /api/auth/me and refresh the user signal. */
  async loadUserInfo(): Promise<void> {
    this._user.set(
      await firstValueFrom(this.http.get<UserInfo>("/api/auth/me")),
    );
  }

  /**
   * Clear the session (server cookie + local state) then redirect to login.
   * When OIDC is active and an end-session endpoint is configured the browser
   * is redirected to the provider's logout URL instead.
   */
  async logout(): Promise<void> {
    // Clear the HttpOnly cookie server-side; ignore network errors since
    // wiping local state is enough to log the user out of the UI.
    try {
      await firstValueFrom(this.http.post("/api/auth/logout", {}));
    } catch {
      // Ignore — local cleanup below is sufficient.
    }
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
   * Clear the in-memory user state.
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
    this._user.set(null);

    // Reset singleton service caches that contain user-scoped data.
    this.jobService.clearState();
    this.transferService.clearState();
  }
}
