import { DatePipe, SlicePipe } from "@angular/common";
import {
  Component,
  computed,
  inject,
  OnDestroy,
  OnInit,
  signal,
} from "@angular/core";
import { RouterLink } from "@angular/router";
import { AppConfigService } from "../../core/services/app-config.service";
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
export class DashboardComponent implements OnInit, OnDestroy {
  private dashboardService = inject(DashboardService);
  private registryService = inject(RegistryService);
  private stagingService = inject(StagingService);
  readonly configService = inject(AppConfigService);

  stats = signal<DashboardStats | null>(null);
  loading = signal(false);
  gcStatus = signal<GCStatus | null>(null);

  // Ghost repositories
  ghostRepos = signal<string[]>([]);
  readonly ghostCount = computed(() => this.ghostRepos().length);
  ghostsChecked = signal(false);
  purgingGhosts = signal(false);

  // Dangling images on the host Docker daemon
  danglingImages = signal<
    {
      id: string;
      repository: string;
      tag: string;
      size: string;
      created: string;
    }[]
  >([]);
  readonly danglingCount = computed(() => this.danglingImages().length);
  danglingChecked = signal(false);
  purgingDangling = signal(false);

  // Orphan .tar files in staging directory
  orphanTarballs = signal<string[]>([]);
  orphanTarballsCount = signal(0);
  orphanTarballsSize = signal("");
  orphanTarballsChecked = signal(false);
  purgingOrphanTarballs = signal(false);

  private gcPollInterval: ReturnType<typeof setInterval> | null = null;

  ngOnInit() {
    this.refresh();
  }

  ngOnDestroy() {
    this.stopGCPoll();
  }

  refresh() {
    this.loadStats();
    this.registryService.getGCStatus().subscribe({
      next: (s) => this.gcStatus.set(s),
    });
    this.checkGhostRepos();
    this.checkDanglingImages();
    this.checkOrphanTarballs();
  }

  loadStats() {
    this.loading.set(true);
    this.dashboardService.getStats().subscribe({
      next: (data) => {
        this.stats.set(data);
        this.loading.set(false);
      },
      error: () => this.loading.set(false),
    });
  }

  startGC() {
    this.registryService.startGarbageCollect().subscribe({
      next: (s) => {
        this.gcStatus.set(s);
        this.startGCPoll();
      },
    });
  }

  private startGCPoll() {
    this.stopGCPoll();
    this.gcPollInterval = setInterval(() => {
      this.registryService.getGCStatus().subscribe({
        next: (s) => {
          this.gcStatus.set(s);
          if (s.status !== "running") {
            this.stopGCPoll();
            if (s.status === "done") {
              this.loadStats();
              this.checkGhostRepos();
            }
          }
        },
      });
    }, 2000);
  }

  private stopGCPoll() {
    if (this.gcPollInterval) {
      clearInterval(this.gcPollInterval);
      this.gcPollInterval = null;
    }
  }

  // ── Ghost repositories ────────────────────────────────────────────────────

  checkGhostRepos() {
    this.registryService.getEmptyRepositories().subscribe({
      next: (res) => {
        this.ghostRepos.set(res.empty_repositories);
        this.ghostsChecked.set(true);
      },
    });
  }

  purgeGhostRepos() {
    if (this.purgingGhosts()) return;
    this.purgingGhosts.set(true);
    this.registryService.purgeEmptyRepositories().subscribe({
      next: () => {
        this.purgingGhosts.set(false);
        this.checkGhostRepos();
        this.loadStats();
      },
      error: () => this.purgingGhosts.set(false),
    });
  }

  // ── Dangling images ───────────────────────────────────────────────────────

  checkDanglingImages() {
    this.stagingService.getDanglingImages().subscribe({
      next: (res) => {
        this.danglingImages.set(res.images);
        this.danglingChecked.set(true);
      },
      error: () => this.danglingChecked.set(true),
    });
  }

  purgeDanglingImages() {
    if (this.purgingDangling()) return;
    this.purgingDangling.set(true);
    this.stagingService.purgeDanglingImages().subscribe({
      next: () => {
        this.purgingDangling.set(false);
        this.checkDanglingImages();
      },
      error: () => this.purgingDangling.set(false),
    });
  }

  // ── Orphan tarballs ───────────────────────────────────────────────────────

  checkOrphanTarballs() {
    this.stagingService.getOrphanTarballs().subscribe({
      next: (res) => {
        this.orphanTarballs.set(res.files);
        this.orphanTarballsCount.set(res.count);
        this.orphanTarballsSize.set(res.total_size_human);
        this.orphanTarballsChecked.set(true);
      },
      error: () => this.orphanTarballsChecked.set(true),
    });
  }

  purgeOrphanTarballs() {
    if (this.purgingOrphanTarballs()) return;
    this.purgingOrphanTarballs.set(true);
    this.stagingService.purgeOrphanTarballs().subscribe({
      next: () => {
        this.purgingOrphanTarballs.set(false);
        this.checkOrphanTarballs();
      },
      error: () => this.purgingOrphanTarballs.set(false),
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
