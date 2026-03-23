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
 * Startup behavior:
 *   - The very first availability check uses a short bootstrap retry window
 *     before showing the full-screen offline state. This prevents transient
 *     proxy/backend warm-up delays during a hard browser refresh from causing
 *     a brief false "Connection unavailable" flash.
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
import {
  Subscription,
  catchError,
  interval,
  map,
  of,
  startWith,
  switchMap,
  take,
  timer,
} from "rxjs";

/** Routes that should never be saved as the restore URL after recovery. */
const EXCLUDED_RESTORE_ROUTES = new Set(["/auth/callback"]);

/** Polling interval while the backend is healthy (normal mode). */
const HEALTHY_POLL_INTERVAL_MS = 10_000;

/** Polling interval while the backend is down (recovery mode). */
const RECOVERY_POLL_INTERVAL_MS = 5_000;

/** Retry cadence used only during the initial app bootstrap. */
const BOOTSTRAP_RETRY_INTERVAL_MS = 1_000;

/** Number of bootstrap probes before declaring the backend unavailable. */
const BOOTSTRAP_MAX_ATTEMPTS = 3;

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

  /** True until the startup grace sequence finishes successfully once. */
  private bootstrapPending = true;

  constructor() {
    // Start with a short bootstrap retry window so hard refreshes do not flash
    // the offline page while the API/proxy is still warming up.
    this.startBootstrapPolling();
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

    this.bootstrapPending = false;
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

  /** Probe the health endpoint and emit true only for an explicit healthy reply. */
  private checkBackendHealth() {
    return this.http.get<{ status: string }>("/api/health").pipe(
      map((response) => response.status === "healthy"),
      catchError(() => of(false)),
    );
  }

  /**
   * Bootstrap polling: retries briefly before showing the unavailable overlay.
   *
   * This absorbs transient startup races during browser refresh where the UI is
   * ready a little earlier than the backend/proxy, while still surfacing a real
   * outage quickly when the backend truly stays down.
   */
  private startBootstrapPolling(): void {
    this.pollSubscription?.unsubscribe();

    this.pollSubscription = timer(0, BOOTSTRAP_RETRY_INTERVAL_MS)
      .pipe(
        take(BOOTSTRAP_MAX_ATTEMPTS),
        switchMap(() => this.checkBackendHealth()),
      )
      .subscribe((isHealthy) => {
        if (isHealthy) {
          this.bootstrapPending = false;
          this.startNormalPolling();
          return;
        }

        // Wait for the remaining bootstrap attempts before showing the offline UI.
        // The last failed attempt falls through when the timer completes.
      });

    this.pollSubscription.add(() => {
      if (!this.bootstrapPending || this._backendUnavailable()) return;

      this.saveRestoreUrl();
      this._backendUnavailable.set(true);
      this.switchToRecoveryPolling();
    });
  }

  /**
   * Normal-mode polling: detects backend going down during normal operation.
   *
   * Uses startWith(0) for the initial probe once the bootstrap phase succeeded.
   * On failure: saves URL, sets unavailable flag, switches to recovery mode.
   * On success: does nothing (app continues normally).
   */
  private startNormalPolling(): void {
    this.pollSubscription?.unsubscribe();

    this.pollSubscription = interval(HEALTHY_POLL_INTERVAL_MS)
      .pipe(startWith(0), switchMap(() => this.checkBackendHealth()))
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
        switchMap(() => this.checkBackendHealth()),
      )
      .subscribe((isHealthy) => {
        if (!isHealthy) return;

        // Guard: only navigate if we were actually in unavailable state
        if (!this._backendUnavailable()) return;

        // Backend recovered — clear flag first to avoid double navigation
        this._backendUnavailable.set(false);
        this.bootstrapPending = false;

        // Resume normal polling before navigating
        this.startNormalPolling();

        // Restore the previous URL
        this.router.navigateByUrl(this.restoreUrl || "/");
      });
  }
}
