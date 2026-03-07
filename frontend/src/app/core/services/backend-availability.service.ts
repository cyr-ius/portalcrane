/**
 * Portalcrane - BackendAvailabilityService
 *
 * Detects when the backend becomes unreachable and redirects the user to the
 * /backend-unavailable page. Automatically polls /api/health until the backend
 * recovers, then restores the previous route.
 *
 * Key change: a proactive health-check is started immediately at service
 * creation so that a backend that is already down when the app loads is
 * detected even before the first /api/ request is made.
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
  tap,
} from "rxjs";

@Injectable({ providedIn: "root" })
export class BackendAvailabilityService {
  private readonly http = inject(HttpClient);
  private readonly router = inject(Router);

  private healthCheckSubscription: Subscription | null = null;

  /**
   * URL to restore after the backend comes back online.
   * Defaults to "/" and is updated the first time the backend goes down.
   */
  private restoreUrl = "/";

  /** True while the backend is considered unreachable. */
  readonly backendUnavailable = signal(false);

  constructor() {
    // Perform a single health-check immediately after the service is created.
    // This catches the case where the backend is already down when the app
    // first loads (before any /api/ request is dispatched by the interceptor).
    this.probeOnStartup();
  }

  // ── Public API ─────────────────────────────────────────────────────────────

  /**
   * Called by the HTTP interceptor when a request to /api/ returns a network
   * or gateway error (status 0, 502, 503, 504).
   */
  markBackendUnavailable(): void {
    // Persist the current URL so we can navigate back after recovery.
    if (!this.backendUnavailable()) {
      const currentUrl = this.router.url;
      if (currentUrl && currentUrl !== "/backend-unavailable") {
        this.restoreUrl = currentUrl;
      }
    }

    this.backendUnavailable.set(true);

    if (this.router.url !== "/backend-unavailable") {
      this.router.navigateByUrl("/backend-unavailable");
    }

    // Ensure the recovery poll is running.
    this.startHealthChecks();
  }

  // ── Private helpers ────────────────────────────────────────────────────────

  /**
   * Perform startup health-checks with a short grace period.
   *
   * This avoids false positives during app bootstrap (proxy warm-up,
   * backend cold start, transient network hiccup) where the first probe can
   * fail even though the backend becomes reachable a moment later.
   */
  private probeOnStartup(): void {
    let recoveredDuringGrace = false;

    interval(1200)
      .pipe(
        startWith(0),
        take(3),
        switchMap(() =>
          this.http.get<{ status: string }>("/api/health").pipe(
            map((response) => response.status === "healthy"),
            catchError(() => of(false)),
          ),
        ),
        tap((isHealthy) => {
          if (isHealthy) {
            recoveredDuringGrace = true;
          }
        }),
      )
      .subscribe({
        complete: () => {
          if (!recoveredDuringGrace) {
            // Backend appears genuinely unavailable after grace probes.
            this.markBackendUnavailable();
          }
        },
      });
  }

  /**
   * Start a periodic poll against /api/health.
   * Only one poll can run at a time — subsequent calls are no-ops.
   * When the backend recovers, the poll stops and the user is redirected back.
   */
  private startHealthChecks(): void {
    if (this.healthCheckSubscription) return;

    this.healthCheckSubscription = interval(5000)
      .pipe(
        startWith(0),
        switchMap(() =>
          this.http.get<{ status: string }>("/api/health").pipe(
            map((response) => response.status === "healthy"),
            catchError(() => of(false)),
          ),
        ),
      )
      .subscribe((isHealthy) => {
        if (!isHealthy) return;

        // Backend is back — clear the flag and restore the previous page.
        this.backendUnavailable.set(false);
        this.stopHealthChecks();
        this.router.navigateByUrl(this.restoreUrl || "/");
      });
  }

  /** Cancel the recovery poll. */
  private stopHealthChecks(): void {
    this.healthCheckSubscription?.unsubscribe();
    this.healthCheckSubscription = null;
  }
}
