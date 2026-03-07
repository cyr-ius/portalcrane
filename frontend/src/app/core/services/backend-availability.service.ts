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
} from "rxjs";

@Injectable({ providedIn: "root" })
export class BackendAvailabilityService {
  private readonly http = inject(HttpClient);
  private readonly router = inject(Router);

  private healthCheckSubscription: Subscription | null = null;
  private restoreUrl = "/";

  readonly backendUnavailable = signal(false);

  markBackendUnavailable(): void {
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

    this.startHealthChecks();
  }

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

        this.backendUnavailable.set(false);
        this.stopHealthChecks();
        this.router.navigateByUrl(this.restoreUrl || "/");
      });
  }

  private stopHealthChecks(): void {
    this.healthCheckSubscription?.unsubscribe();
    this.healthCheckSubscription = null;
  }
}
