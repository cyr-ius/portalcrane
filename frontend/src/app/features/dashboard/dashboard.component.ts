import { CommonModule } from "@angular/common";
import { Component, inject, OnDestroy, OnInit, signal } from "@angular/core";
import { RouterLink } from "@angular/router";
import {
  DashboardService,
  DashboardStats,
} from "../../core/services/dashboard.service";
import {
  GCStatus,
  RegistryService,
} from "../../core/services/registry.service";

@Component({
  selector: "app-dashboard",
  imports: [CommonModule, RouterLink],
  templateUrl: "./dashboard.component.html",
  styleUrl: "./dashboard.component.css",
})
export class DashboardComponent implements OnInit, OnDestroy {
  private dashboardService = inject(DashboardService);
  private registryService = inject(RegistryService);

  stats = signal<DashboardStats | null>(null);
  loading = signal(false);
  gcStatus = signal<GCStatus | null>(null);
  ghostRepos = signal<string[]>([]);
  ghostCount = signal(0);
  ghostsChecked = signal(false);
  purgingGhosts = signal(false);
  private gcPollInterval: ReturnType<typeof setInterval> | null = null;

  ngOnInit() {
    this.loadStats();
    this.registryService.getGCStatus().subscribe({
      next: (s) => this.gcStatus.set(s),
    });
    this.checkGhostRepos();
  }

  ngOnDestroy() {
    this.stopGCPoll();
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

  checkGhostRepos() {
    this.registryService.getEmptyRepositories().subscribe({
      next: (res) => {
        this.ghostRepos.set(res.empty_repositories);
        this.ghostCount.set(res.count);
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
