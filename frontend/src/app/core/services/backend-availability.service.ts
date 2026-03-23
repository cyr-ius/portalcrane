/**
 * Portalcrane - BackendAvailabilityService
 *
 * Continuously monitors the backend health via a background polling loop
 * that runs from app startup until the browser tab is closed.
 *
 * Two-phase polling strategy:
 *   - NORMAL mode  : poll every 10 s to detect backend going down while the
 *                    app is running normally (login page or authenticated).
 *   - RECOVERY mode: poll every 5 s until the backend comes back online,
 *                    then restore the previous URL.
 *
 * Key design rules to avoid navigation side-effects:
 *   - Only ONE subscription is ever active at a time (unsubscribe before
 *     creating a new one).
 *   - Recovery polling does NOT use startWith(0): we wait the first full
 *     interval before probing again. This avoids an immediate "healthy" read
 *     right after switchover when the previous probe had already succeeded.
 *   - Navigation only happens when _backendUnavailable is actually true.
 */

import { HttpClient } from "@angular/common/http";
import { Injectable, inject, signal } from "@angular/core";
import { Router } from "@angular/router";
import { Subscription, catchError, interval, map, of, startWith, switchMap } from "rxjs";

/** Routes that should never be saved as the restore URL after recovery. */
const EXCLUDED_RESTORE_ROUTES = new Set(["/auth/callback"]);

/** Polling interval while the backend is healthy (normal mode). */
const HEALTHY_POLL_INTERVAL_MS = 10_000;

/** Polling interval while the backend is down (recovery mode). */
const RECOVERY_POLL_INTERVAL_MS = 5_000;

@Injectable({ providedIn: "root" })
export class BackendAvailabilityService {
  private readonly http = inject(HttpClient);
  private readonly router = inject(Router);

  /** Single active polling subscription — only one runs at a time. */
  private pollSubscription: Subscription | null = null;

  /**
   * URL to restore after the backend comes back online.
   * "/auth" is a valid restore target so the user lands on the login page.
   */
  private restoreUrl = "/";

  /** True while the backend is considered unreachable. */
  private readonly _backendUnavailable = signal(false);
  readonly backendUnavailable = this._backendUnavailable.asReadonly();

  constructor() {
    // Start normal background polling immediately at service creation.
    // startWith(0) fires the first probe at t=0 so a backend that is already
    // down when the app loads is detected before the login page renders.
    this.startNormalPolling();
  }

  // ── Public API ─────────────────────────────────────────────────────────────

  /**
   * Called by the HTTP interceptor when a request to /api/ returns a network
   * or gateway error (status 0, 502, 503, 504).
   * Switches to recovery polling immediately without waiting for the next
   * normal-mode poll tick.
   */
  markBackendUnavailable(): void {
    // Avoid duplicate state changes
    if (this._backendUnavailable()) return;

    this.saveRestoreUrl();
    this._backendUnavailable.set(true);

    // Switch to faster recovery polling (no startWith — wait first interval)
    this.switchToRecoveryPolling();
  }

  // ── Private helpers ────────────────────────────────────────────────────────

  /** Save the current router URL as the restore target. */
  private saveRestoreUrl(): void {
    const currentUrl = this.router.url;
    if (currentUrl && !EXCLUDED_RESTORE_ROUTES.has(currentUrl)) {
      this.restoreUrl = currentUrl;
    }
  }

  /**
   * Normal-mode polling: detects backend going down during normal operation.
   *
   * Uses startWith(0) for the initial probe at app startup.
   * On failure: saves URL, sets unavailable flag, switches to recovery mode.
   * On success: does nothing (app continues normally).
   */
  private startNormalPolling(): void {
    this.pollSubscription?.unsubscribe();

    this.pollSubscription = interval(HEALTHY_POLL_INTERVAL_MS)
      .pipe(
        startWith(0),
        switchMap(() =>
          this.http.get<{ status: string }>("/api/health").pipe(
            map((r) => r.status === "healthy"),
            catchError(() => of(false)),
          ),
        ),
      )
      .subscribe((isHealthy) => {
        if (!isHealthy) {
          // Guard: only act if not already in unavailable state
          if (!this._backendUnavailable()) {
            this.saveRestoreUrl();
            this._backendUnavailable.set(true);
            this.switchToRecoveryPolling();
          }
        }
        // isHealthy && normal mode → do nothing, keep polling
      });
  }

  /**
   * Recovery-mode polling: waits for the backend to come back online.
   *
   * Does NOT use startWith(0) to avoid immediately reading a stale "healthy"
   * result right after the switchover — we deliberately wait one full interval
   * before the first recovery probe.
   *
   * On recovery: clears the unavailable flag, resumes normal polling,
   * then navigates to the saved restore URL.
   */
  private switchToRecoveryPolling(): void {
    this.pollSubscription?.unsubscribe();

    this.pollSubscription = interval(RECOVERY_POLL_INTERVAL_MS)
      .pipe(
        // No startWith(0) here — intentional, see JSDoc above
        switchMap(() =>
          this.http.get<{ status: string }>("/api/health").pipe(
            map((r) => r.status === "healthy"),
            catchError(() => of(false)),
          ),
        ),
      )
      .subscribe((isHealthy) => {
        if (!isHealthy) return;

        // Guard: only navigate if we were actually in unavailable state
        if (!this._backendUnavailable()) return;

        // Backend recovered — clear flag first to avoid double navigation
        this._backendUnavailable.set(false);

        // Resume normal polling before navigating
        this.startNormalPolling();

        // Restore the previous URL
        this.router.navigateByUrl(this.restoreUrl || "/");
      });
  }
}
