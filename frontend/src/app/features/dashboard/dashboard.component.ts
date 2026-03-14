/**
 * Portalcrane - Dashboard Component
 *
 * Change: visual refresh feedback
 *   - New `refreshing` signal: true while a manual Refresh is in flight.
 *     Unlike `loading` (which shows the full-page spinner on first load),
 *     `refreshing` keeps the dashboard visible and only animates the button
 *     icon + fades the stat cards via CSS class `refreshing-overlay`.
 *   - refresh() now resets ghostsChecked / orphanOciChecked to false before
 *     calling checkGhostRepos() / checkOrphanOci(), so their individual card
 *     spinners restart visually on every manual refresh.
 *   - refreshing is cleared only after ALL concurrent requests have settled
 *     (stats + gcStatus + ghost + orphan), using a simple counter tracked by
 *     _refreshPending signal.
 */
import { DatePipe, SlicePipe } from "@angular/common";
import {
  Component,
  computed,
  DestroyRef,
  inject,
  OnInit,
  signal
} from "@angular/core";
import { takeUntilDestroyed } from "@angular/core/rxjs-interop";
import { RouterLink } from "@angular/router";
import { Subject, switchMap, takeWhile, timer } from "rxjs";
import { formatBytes } from "../../core/helpers/storage";
import { AuthService } from "../../core/services/auth.service";
import {
  DashboardService,
  DashboardStats,
} from "../../core/services/dashboard.service";
import {
  GCStatus,
  RegistryService,
} from "../../core/services/registry.service";
import { StagingService } from "../../core/services/staging.service";

@Component({
  selector: "app-dashboard",
  imports: [SlicePipe, DatePipe, RouterLink],
  templateUrl: "./dashboard.component.html",
  styleUrl: "./dashboard.component.css",
})
export class DashboardComponent implements OnInit {
  private dashboardService = inject(DashboardService);
  private registryService = inject(RegistryService);
  private stagingService = inject(StagingService);
  private destroyRef = inject(DestroyRef);
  readonly authService = inject(AuthService);

  stats = signal<DashboardStats | null>(null);
  loading = signal(false);

  readonly formatBytes = formatBytes

  /**
   * True while a manual Refresh is in progress.
   * Controls: button spin animation, stat-card overlay fade, button disabled state.
   * Does NOT hide the dashboard — the existing data stays visible.
   */
  refreshing = signal(false);

  /**
   * Internal counter tracking how many concurrent refresh sub-requests are
   * still in flight. When it reaches 0, `refreshing` is cleared.
   */
  private _refreshPending = signal(0);

  gcStatus = signal<GCStatus | null>(null);
  gcDryStatus = signal(false);

  // Ghost repositories
  ghostRepos = signal<string[]>([]);
  readonly ghostCount = computed(() => this.ghostRepos().length);
  ghostsChecked = signal(false);
  purgingGhosts = signal(false);

  // Orphan OCI layout directories in staging
  orphanOciDirs = signal<string[]>([]);
  readonly orphanOciCount = computed(() => this.orphanOciDirs().length);
  orphanOciSize = signal("");
  orphanOciChecked = signal(false);
  purgingOrphanOci = signal(false);
  orphanOciRefreshing = signal(false);

  private gcPollTrigger$ = new Subject<void>();

  ngOnInit(): void {
    this.setupGCPolling();
    this.loadStats(this.stats() === null);
    this.registryService.getGCStatus().subscribe({ next: (s) => this.gcStatus.set(s) });
    this.checkGhostRepos();
    this.checkOrphanOci();
  }

  /**
   * Setup garbage collection status polling.
   * When gcPollTrigger$ emits, start polling getGCStatus() every 2 seconds
   * until status changes from "running" to "done" or "failed".
   * On completion, reload stats and recheck for ghost repositories.
   */
  private setupGCPolling(): void {
    this.gcPollTrigger$
      .pipe(
        switchMap(() =>
          timer(0, 2000).pipe(
            switchMap(() => this.registryService.getGCStatus()),
            takeWhile((s) => s.status === "running", /* inclusive */ true),
          ),
        ),
        takeUntilDestroyed(this.destroyRef),
      )
      .subscribe((s) => {
        this.gcStatus.set(s);
        if (s.status === "done" && !this.gcDryStatus()) {
          this.loadStats(false);
          this.purgeGhostRepos();
          this.checkGhostRepos();
        }
      });
  }

  // ── Refresh pending counter helpers ──────────────────────────────────────

  /**
   * Increment the pending counter and activate the refreshing state.
   * Call once per sub-request started during a manual refresh.
   */
  private _refreshStart(): void {
    this._refreshPending.update((n) => n + 1);
    this.refreshing.set(true);
  }

  /**
   * Decrement the pending counter.
   * When it reaches 0, all sub-requests have settled and refreshing is cleared.
   */
  private _refreshDone(): void {
    this._refreshPending.update((n) => Math.max(0, n - 1));
    if (this._refreshPending() === 0) {
      this.refreshing.set(false);
    }
  }

  // ── Public methods ────────────────────────────────────────────────────────

  refresh(): void {
    // Reset card-level checked flags so their individual spinners restart.
    this.ghostsChecked.set(false);
    this.orphanOciChecked.set(false);

    // Stats — silent reload (no full-page spinner), but tracked for refreshing.
    this._refreshStart();
    this.dashboardService.getStats().subscribe({
      next: (data) => {
        this.stats.set(data);
        this._refreshDone();
      },
      error: () => this._refreshDone(),
    });

    if (!this.authService.currentUser()?.is_admin) {
      return;
    }

    // GC status
    this._refreshStart();
    this.registryService.getGCStatus().subscribe({
      next: (s) => {
        this.gcStatus.set(s);
        this._refreshDone();
      },
      error: () => this._refreshDone(),
    });

    // Ghost repos
    this._refreshStart();
    this.registryService.getEmptyRepositories().subscribe({
      next: (res) => {
        this.ghostRepos.set(res.empty_repositories);
        this.ghostsChecked.set(true);
        this._refreshDone();
      },
      error: () => {
        this.ghostsChecked.set(true);
        this._refreshDone();
      },
    });

    // Orphan OCI
    this._refreshStart();
    this.orphanOciRefreshing.set(true);
    this.stagingService.getOrphanOci().subscribe({
      next: (res) => {
        this.orphanOciDirs.set(res.dirs);
        this.orphanOciSize.set(res.total_size_human);
        this.orphanOciChecked.set(true);
        this.orphanOciRefreshing.set(false);
        this._refreshDone();
      },
      error: () => {
        this.orphanOciChecked.set(true);
        this.orphanOciRefreshing.set(false);
        this._refreshDone();
      },
    });
  }

  /**
   * Load dashboard stats from the backend.
   *
   * @param showSpinner - When true (initial load only), the full-page loading
   *   spinner is shown while the request is in-flight. When false (silent
   *   refresh triggered by GC / Purge), stats are updated in place without
   *   affecting the loading flag, so the rest of the dashboard stays visible.
   */
  loadStats(showSpinner = false): void {
    if (showSpinner) {
      this.loading.set(true);
    }
    this.dashboardService.getStats().subscribe({
      next: (data) => {
        this.stats.set(data);
        if (showSpinner) {
          this.loading.set(false);
        }
      },
      error: () => {
        if (showSpinner) {
          this.loading.set(false);
        }
      },
    });
  }

  startGC(dryRun: boolean): void {
    this.gcDryStatus.set(dryRun);
    this.registryService.startGarbageCollect(dryRun).subscribe({
      next: (s) => {
        this.gcStatus.set(s);
        this.gcPollTrigger$.next();
      },
    });
  }

  // ── Ghost repositories ────────────────────────────────────────────────────

  checkGhostRepos(): void {
    this.registryService.getEmptyRepositories().subscribe({
      next: (res) => {
        this.ghostRepos.set(res.empty_repositories);
        this.ghostsChecked.set(true);
      },
      error: () => this.ghostsChecked.set(true),
    });
  }

  purgeGhostRepos(): void {
    if (this.purgingGhosts()) return;
    this.purgingGhosts.set(true);
    this.registryService.purgeEmptyRepositories().subscribe({
      next: () => {
        this.purgingGhosts.set(false);
        this.checkGhostRepos();
        this.loadStats(false);
      },
      error: () => this.purgingGhosts.set(false),
    });
  }

  // ── Orphan OCI layouts ───────────────────────────────────────────────────

  checkOrphanOci(): void {
    this.orphanOciRefreshing.set(true);
    this.stagingService.getOrphanOci().subscribe({
      next: (res) => {
        this.orphanOciDirs.set(res.dirs);
        this.orphanOciSize.set(res.total_size_human);
        this.orphanOciChecked.set(true);
        this.orphanOciRefreshing.set(false);
      },
      error: () => {
        this.orphanOciChecked.set(true);
        this.orphanOciRefreshing.set(false);
      },
    });
  }

  purgeOrphanOci(): void {
    if (this.purgingOrphanOci()) return;
    this.purgingOrphanOci.set(true);
    this.stagingService.purgeOrphanOci().subscribe({
      next: () => {
        this.purgingOrphanOci.set(false);
        this.checkOrphanOci();
      },
      error: () => this.purgingOrphanOci.set(false),
    });
  }

  // ── Helpers ───────────────────────────────────────────────────────────────

  getGCBadgeClass(status: string): string {
    const map: Record<string, string> = {
      running: "badge bg-warning-subtle text-warning",
      done: "badge bg-success-subtle text-success",
      failed: "badge bg-danger-subtle text-danger",
      idle: "badge bg-secondary-subtle text-secondary",
    };
    return map[status] ?? "badge bg-secondary-subtle text-secondary";
  }

}
