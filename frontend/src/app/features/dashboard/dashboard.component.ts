import { Component, signal, inject, OnInit, OnDestroy } from "@angular/core";
import { CommonModule } from "@angular/common";
import { RouterLink } from "@angular/router";
import {
  DashboardService,
  DashboardStats,
} from "../../core/services/dashboard.service";
import {
  RegistryService,
  GCStatus,
} from "../../core/services/registry.service";

@Component({
  selector: "app-dashboard",
  imports: [CommonModule, RouterLink],
  template: `
    <div class="p-4">
      <!-- Header -->
      <div class="d-flex align-items-center justify-content-between mb-4">
        <div>
          <h2 class="fw-bold mb-1">Dashboard</h2>
          <p class="text-muted small mb-0">Registry overview and statistics</p>
        </div>
        <div class="d-flex align-items-center gap-2">
          @if (stats()?.registry_status === "ok") {
            <span class="badge bg-success-subtle text-success">
              <i class="bi bi-circle-fill me-1" style="font-size:0.5rem"></i
              >Registry Online
            </span>
          } @else {
            <span class="badge bg-danger-subtle text-danger">
              <i class="bi bi-circle-fill me-1" style="font-size:0.5rem"></i
              >Registry Unreachable
            </span>
          }
          <button
            class="btn btn-sm btn-outline-secondary"
            (click)="loadStats()"
            [disabled]="loading()"
          >
            <i class="bi bi-arrow-clockwise" [class.spin]="loading()"></i>
            Refresh
          </button>
        </div>
      </div>

      @if (loading() && !stats()) {
        <div class="d-flex align-items-center justify-content-center py-5">
          <div class="spinner-border text-primary me-3"></div>
          <span class="text-muted">Loading statistics...</span>
        </div>
      }

      @if (stats(); as s) {
        <!-- Stat cards -->
        <div class="row g-3 mb-4">
          <div class="col-sm-6 col-lg-3">
            <div class="stat-card card border-0 h-100">
              <div class="card-body">
                <div
                  class="d-flex align-items-center justify-content-between mb-2"
                >
                  <span class="text-muted small">Total Images</span>
                  <div class="stat-icon bg-primary-subtle text-primary">
                    <i class="bi bi-layers"></i>
                  </div>
                </div>
                <div class="stat-value">{{ s.total_images }}</div>
                <div class="text-muted small">
                  {{ s.total_tags }} tags total
                </div>
              </div>
            </div>
          </div>

          <div class="col-sm-6 col-lg-3">
            <div class="stat-card card border-0 h-100">
              <div class="card-body">
                <div
                  class="d-flex align-items-center justify-content-between mb-2"
                >
                  <span class="text-muted small">Storage Used</span>
                  <div class="stat-icon bg-warning-subtle text-warning">
                    <i class="bi bi-hdd"></i>
                  </div>
                </div>
                <div class="stat-value">{{ s.total_size_human }}</div>
                <div class="text-muted small">By registry images</div>
              </div>
            </div>
          </div>

          <div class="col-sm-6 col-lg-3">
            <div class="stat-card card border-0 h-100">
              <div class="card-body">
                <div
                  class="d-flex align-items-center justify-content-between mb-2"
                >
                  <span class="text-muted small">Disk Space</span>
                  <div
                    class="stat-icon"
                    [class.bg-success-subtle]="s.disk_usage_percent < 70"
                    [class.text-success]="s.disk_usage_percent < 70"
                    [class.bg-warning-subtle]="
                      s.disk_usage_percent >= 70 && s.disk_usage_percent < 90
                    "
                    [class.text-warning]="
                      s.disk_usage_percent >= 70 && s.disk_usage_percent < 90
                    "
                    [class.bg-danger-subtle]="s.disk_usage_percent >= 90"
                    [class.text-danger]="s.disk_usage_percent >= 90"
                  >
                    <i class="bi bi-pie-chart"></i>
                  </div>
                </div>
                <div class="stat-value">{{ s.disk_usage_percent }}%</div>
                <div class="progress" style="height:4px">
                  <div
                    class="progress-bar"
                    [class.bg-success]="s.disk_usage_percent < 70"
                    [class.bg-warning]="
                      s.disk_usage_percent >= 70 && s.disk_usage_percent < 90
                    "
                    [class.bg-danger]="s.disk_usage_percent >= 90"
                    [style.width.%]="s.disk_usage_percent"
                  ></div>
                </div>
              </div>
            </div>
          </div>

          <div class="col-sm-6 col-lg-3">
            <div class="stat-card card border-0 h-100">
              <div class="card-body">
                <div
                  class="d-flex align-items-center justify-content-between mb-2"
                >
                  <span class="text-muted small">Largest Image</span>
                  <div class="stat-icon bg-info-subtle text-info">
                    <i class="bi bi-box-seam"></i>
                  </div>
                </div>
                <div class="stat-value small">
                  {{ s.largest_image.size_human }}
                </div>
                <div
                  class="text-muted small text-truncate"
                  [title]="s.largest_image.name"
                >
                  {{ s.largest_image.name || "N/A" }}
                </div>
              </div>
            </div>
          </div>
        </div>

        <!-- Registry info & Quick actions -->
        <div class="row g-3">
          <div class="col-lg-6">
            <div class="card border-0">
              <div class="card-header border-0 pb-0">
                <h6 class="fw-semibold mb-0">Registry Information</h6>
              </div>
              <div class="card-body">
                <table class="table table-sm table-borderless mb-0">
                  <tbody>
                    <tr>
                      <td class="text-muted small" style="width:40%">URL</td>
                      <td class="small fw-mono">{{ s.registry_url }}</td>
                    </tr>
                    <tr>
                      <td class="text-muted small">Status</td>
                      <td>
                        <span
                          [class]="
                            s.registry_status === 'ok'
                              ? 'badge bg-success-subtle text-success'
                              : 'badge bg-danger-subtle text-danger'
                          "
                        >
                          {{ s.registry_status }}
                        </span>
                      </td>
                    </tr>
                    <tr>
                      <td class="text-muted small">Disk Total</td>
                      <td class="small">
                        {{ formatBytes(s.disk_total_bytes) }}
                      </td>
                    </tr>
                    <tr>
                      <td class="text-muted small">Disk Free</td>
                      <td class="small">
                        {{ formatBytes(s.disk_free_bytes) }}
                      </td>
                    </tr>
                    <tr>
                      <td class="text-muted small">Advanced Mode</td>
                      <td>
                        <span
                          [class]="
                            s.advanced_mode
                              ? 'badge bg-primary-subtle text-primary'
                              : 'badge bg-secondary-subtle text-secondary'
                          "
                        >
                          {{ s.advanced_mode ? "Enabled" : "Disabled" }}
                        </span>
                      </td>
                    </tr>
                  </tbody>
                </table>
              </div>
            </div>
          </div>

          <div class="col-lg-6">
            <div class="card border-0">
              <div class="card-header border-0 pb-0">
                <h6 class="fw-semibold mb-0">Quick Actions</h6>
              </div>
              <div class="card-body d-flex flex-column gap-2">
                <a
                  routerLink="/images"
                  class="btn btn-outline-primary d-flex align-items-center gap-2"
                >
                  <i class="bi bi-layers"></i>
                  Browse Images
                </a>
                <a
                  routerLink="/staging"
                  class="btn btn-outline-success d-flex align-items-center gap-2"
                >
                  <i class="bi bi-cloud-arrow-down"></i>
                  Pull Image from Docker Hub
                </a>

                <!-- ── Garbage Collect ── -->
                <div class="gc-panel rounded p-3 mt-1">
                  <div
                    class="d-flex align-items-center justify-content-between mb-2"
                  >
                    <div>
                      <div
                        class="fw-semibold small d-flex align-items-center gap-2"
                      >
                        <i class="bi bi-trash3-fill text-warning"></i>
                        Garbage Collection
                      </div>
                      <div class="text-muted" style="font-size:.72rem">
                        Reclaim disk space from deleted layers
                      </div>
                    </div>
                    <button
                      class="btn btn-sm btn-warning"
                      (click)="startGC()"
                      [disabled]="gcStatus()?.status === 'running'"
                    >
                      @if (gcStatus()?.status === "running") {
                        <span
                          class="spinner-border spinner-border-sm me-1"
                        ></span>
                        Running...
                      } @else {
                        <i class="bi bi-recycle me-1"></i>
                        Run GC
                      }
                    </button>
                  </div>

                  @if (gcStatus() && gcStatus()!.status !== "idle") {
                    <!-- Status badge -->
                    <div class="d-flex align-items-center gap-2 mb-2">
                      <span [class]="getGCBadgeClass(gcStatus()!.status)">
                        {{ gcStatus()!.status }}
                      </span>
                      @if (
                        gcStatus()!.status === "done" &&
                        gcStatus()!.freed_bytes > 0
                      ) {
                        <span class="text-success small fw-semibold">
                          <i class="bi bi-arrow-down-circle-fill me-1"></i>
                          {{ gcStatus()!.freed_human }} freed
                        </span>
                      }
                      @if (
                        gcStatus()!.status === "done" &&
                        gcStatus()!.freed_bytes === 0
                      ) {
                        <span class="text-muted small">Nothing to reclaim</span>
                      }
                    </div>

                    <!-- Output log -->
                    @if (gcStatus()!.output || gcStatus()!.error) {
                      <pre class="gc-output mb-0">{{
                        gcStatus()!.error || gcStatus()!.output
                      }}</pre>
                    }

                    <!-- Timestamps -->
                    @if (gcStatus()!.finished_at) {
                      <div class="text-muted mt-1" style="font-size:.68rem">
                        Finished: {{ gcStatus()!.finished_at | date: "medium" }}
                      </div>
                    }
                  }
                </div>

                <!-- ── Ghost repositories cleanup ── -->
                <div class="gc-panel rounded p-3">
                  <div
                    class="d-flex align-items-center justify-content-between"
                  >
                    <div>
                      <div
                        class="fw-semibold small d-flex align-items-center gap-2"
                      >
                        <i class="bi bi-ghost text-secondary"></i>
                        Ghost Repositories
                        @if (ghostCount() > 0) {
                          <span class="badge bg-warning-subtle text-warning">{{
                            ghostCount()
                          }}</span>
                        }
                      </div>
                      <div class="text-muted" style="font-size:.72rem">
                        Repositories with no tags left after deletion
                      </div>
                    </div>
                    <button
                      class="btn btn-sm btn-outline-secondary"
                      (click)="purgeGhostRepos()"
                      [disabled]="purgingGhosts() || ghostCount() === 0"
                    >
                      @if (purgingGhosts()) {
                        <span
                          class="spinner-border spinner-border-sm me-1"
                        ></span>
                      } @else {
                        <i class="bi bi-eraser me-1"></i>
                      }
                      Purge
                    </button>
                  </div>
                  @if (ghostRepos().length > 0) {
                    <div class="d-flex flex-wrap gap-1 mt-2">
                      @for (repo of ghostRepos(); track repo) {
                        <span
                          class="badge bg-secondary-subtle text-secondary font-monospace"
                          style="font-size:.68rem"
                        >
                          <i class="bi bi-ghost me-1"></i>{{ repo }}
                        </span>
                      }
                    </div>
                  } @else if (ghostsChecked()) {
                    <div class="text-success small mt-1">
                      <i class="bi bi-check-circle me-1"></i>No ghost
                      repositories
                    </div>
                  }
                </div>
              </div>
            </div>
          </div>
        </div>
      }
    </div>
  `,
  styles: [
    `
      .stat-card {
        background: var(--pc-card-bg);
        border-radius: 12px;
        transition:
          transform 0.15s,
          box-shadow 0.15s;
      }
      .stat-card:hover {
        transform: translateY(-2px);
        box-shadow: 0 4px 16px var(--pc-shadow);
      }
      .stat-icon {
        width: 36px;
        height: 36px;
        border-radius: 8px;
        display: flex;
        align-items: center;
        justify-content: center;
        font-size: 1rem;
      }
      .stat-value {
        font-size: 1.75rem;
        font-weight: 700;
        line-height: 1.2;
        color: var(--pc-text);
      }
      .fw-mono {
        font-family: "Courier New", monospace;
        font-size: 0.8rem;
      }
      .gc-panel {
        background: var(--pc-main-bg);
        border: 1px solid var(--pc-border);
      }
      .gc-output {
        background: #0d1117;
        color: #c9d1d9;
        font-size: 0.7rem;
        border-radius: 6px;
        padding: 0.5rem 0.75rem;
        max-height: 120px;
        overflow-y: auto;
        white-space: pre-wrap;
        word-break: break-all;
      }
      .spin {
        animation: spin 1s linear infinite;
      }
      @keyframes spin {
        from {
          transform: rotate(0deg);
        }
        to {
          transform: rotate(360deg);
        }
      }
      .card {
        background: var(--pc-card-bg);
        border-radius: 12px;
      }
    `,
  ],
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
