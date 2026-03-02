/**
 * Portalcrane - Dashboard Component
 *
 */
import { DatePipe, SlicePipe } from "@angular/common";
import {
  Component,
  computed,
  DestroyRef,
  inject,
  OnInit,
  signal,
} from "@angular/core";
import { takeUntilDestroyed } from "@angular/core/rxjs-interop";
import { RouterLink } from "@angular/router";
import { Subject, switchMap, takeWhile } from "rxjs";
import { AppConfigService } from "../../core/services/app-config.service";
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
  readonly configService = inject(AppConfigService);
  readonly authService = inject(AuthService);

  stats = signal<DashboardStats | null>(null);
  loading = signal(false);

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

  private gcPollTrigger$ = new Subject<void>();

  ngOnInit() {
    this.setupGCPolling();
    this.refresh();
  }

  private setupGCPolling(): void {
    import("rxjs").then(({ timer }) => {
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
          if (s.status === "done") {
            this.loadStats(false);
            this.checkGhostRepos();
          }
        });
    });
  }

  // ── Public methods ────────────────────────────────────────────────────────

  refresh() {
    // Show the full spinner only when there is no data yet
    this.loadStats(this.stats() === null);

    if (!this.authService.currentUser()?.is_admin) {
      return;
    }
    this.registryService.getGCStatus().subscribe({
      next: (s) => this.gcStatus.set(s),
    });
    this.checkGhostRepos();
    this.checkOrphanOci();
  }

  /**
   * Load dashboard stats from the backend.
   *
   * @param showSpinner - When true (initial load only), the full-page loading
   *   spinner is shown while the request is in-flight.  When false (silent
   *   refresh triggered by GC / Purge), stats are updated in place without
   *   affecting the loading flag, so the rest of the dashboard stays visible.
   */
  loadStats(showSpinner = false) {
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

  startGC(dryRun: boolean) {
    this.registryService.startGarbageCollect(dryRun).subscribe({
      next: (s) => {
        if (dryRun) this.gcDryStatus.set(false);
        this.gcStatus.set(s);
        this.gcPollTrigger$.next();
      },
    });
  }

  // ── Ghost repositories ────────────────────────────────────────────────────

  checkGhostRepos() {
    this.registryService.getEmptyRepositories().subscribe({
      next: (res) => {
        this.ghostRepos.set(res.empty_repositories);
        this.ghostsChecked.set(true);
      },
      error: () => this.ghostsChecked.set(true),
    });
  }

  purgeGhostRepos() {
    if (this.purgingGhosts()) return;
    this.purgingGhosts.set(true);
    this.registryService.purgeEmptyRepositories().subscribe({
      next: () => {
        this.purgingGhosts.set(false);
        this.checkGhostRepos();
        // Silent refresh — no spinner
        this.loadStats(false);
      },
      error: () => this.purgingGhosts.set(false),
    });
  }

  // ── Orphan OCI layouts ───────────────────────────────────────────────────

  checkOrphanOci() {
    this.stagingService.getOrphanOci().subscribe({
      next: (res) => {
        this.orphanOciDirs.set(res.dirs);
        this.orphanOciSize.set(res.total_size_human);
        this.orphanOciChecked.set(true);
      },
      error: () => this.orphanOciChecked.set(true),
    });
  }

  purgeOrphanOci() {
    if (this.purgingOrphanOci()) return;
    this.purgingOrphanOci.set(true);
    this.stagingService.purgeOrphanOci().subscribe({
      next: () => {
        this.purgingOrphanOci.set(false);
        this.checkOrphanOci();
        // No loadStats() here — purging OCI dirs does not affect registry stats
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

  formatBytes(bytes: number): string {
    if (!bytes) return "0 B";
    const units = ["B", "KB", "MB", "GB", "TB"];
    let size = bytes;
    let i = 0;
    while (size >= 1024 && i < units.length - 1) {
      size /= 1024;
      i++;
    }
    return `${size.toFixed(2)} ${units[i]}`;
  }
}
