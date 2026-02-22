import {
  Component,
  signal,
  inject,
  OnInit,
  OnDestroy,
  computed,
} from "@angular/core";
import { CommonModule } from "@angular/common";
import { FormsModule } from "@angular/forms";
import {
  StagingService,
  StagingJob,
  DockerHubResult,
} from "../../core/services/staging.service";
import {
  AppConfigService,
  ClamAVStatus,
} from "../../core/services/app-config.service";

@Component({
  selector: "app-staging",
  imports: [CommonModule, FormsModule],
  template: `
    <div class="p-4">
      <!-- Page header + ClamAV live indicator -->
      <div
        class="d-flex align-items-start justify-content-between mb-4 flex-wrap gap-2"
      >
        <div>
          <h2 class="fw-bold mb-1">Staging Pipeline</h2>
          <p class="text-muted small mb-0">
            Pull from Docker Hub → Scan → Push to Registry
          </p>
        </div>

        <!-- ClamAV status badge -->
        <div class="d-flex align-items-center gap-2">
          <span
            [class]="clamavBadgeClass()"
            [title]="clamavStatus()?.message ?? ''"
            style="font-size:0.78rem; padding:0.35em 0.75em; cursor:default"
          >
            <i [class]="'bi ' + clamavBadgeIcon() + ' me-1'"></i>
            {{ clamavBadgeLabel() }}
          </span>
          <button
            class="btn btn-sm btn-outline-secondary border-0 p-1"
            (click)="refreshClamAVStatus()"
            title="Refresh ClamAV status"
          >
            <i class="bi bi-arrow-clockwise" [class.spin]="clamavLoading()"></i>
          </button>
        </div>
      </div>

      <div class="row g-3">
        <!-- Left: Pull panel -->
        <div class="col-lg-5">
          <!-- Search Docker Hub -->
          <div class="card border-0 mb-3">
            <div class="card-header border-0">
              <h6 class="fw-semibold mb-0">
                <i class="bi bi-search me-2"></i>Search Docker Hub
              </h6>
            </div>
            <div class="card-body">
              <div class="input-group mb-2">
                <input
                  type="text"
                  class="form-control"
                  placeholder="nginx, redis, node..."
                  [(ngModel)]="searchQuery"
                  (keyup.enter)="searchDockerHub()"
                />
                <button
                  class="btn btn-primary"
                  (click)="searchDockerHub()"
                  [disabled]="searching()"
                >
                  @if (searching()) {
                    <span class="spinner-border spinner-border-sm"></span>
                  } @else {
                    <i class="bi bi-search"></i>
                  }
                </button>
              </div>

              @if (searchResults().length > 0) {
                <div class="search-results-list">
                  @for (result of searchResults(); track result.name) {
                    <div
                      class="search-result-item p-2 rounded d-flex align-items-center justify-content-between gap-2"
                      (click)="selectDockerHubImage(result)"
                    >
                      <div class="overflow-hidden">
                        <div class="fw-semibold small text-truncate">
                          {{ result.name }}
                          @if (result.is_official) {
                            <span
                              class="badge bg-primary-subtle text-primary ms-1"
                              style="font-size:0.65rem"
                              >Official</span
                            >
                          }
                        </div>
                        <div class="text-muted small text-truncate">
                          {{ result.description || "—" }}
                        </div>
                        <div class="text-muted" style="font-size:0.7rem">
                          <i class="bi bi-star me-1"></i
                          >{{ formatCount(result.star_count) }}
                          <span class="ms-2"
                            ><i class="bi bi-download me-1"></i
                            >{{ formatCount(result.pull_count) }}</span
                          >
                        </div>
                      </div>
                      <button
                        class="btn btn-sm btn-outline-primary flex-shrink-0"
                      >
                        <i class="bi bi-plus"></i>
                      </button>
                    </div>
                  }
                </div>
              }
            </div>
          </div>

          <!-- Pull form -->
          <div class="card border-0">
            <div class="card-header border-0">
              <h6 class="fw-semibold mb-0">
                <i class="bi bi-cloud-download me-2"></i>Pull Image
              </h6>
            </div>
            <div class="card-body">
              <div class="mb-3">
                <label class="form-label small fw-semibold">Image</label>
                <input
                  type="text"
                  class="form-control"
                  placeholder="nginx, library/redis, myorg/myapp"
                  [value]="pullImage()"
                  (input)="onImageChange($any($event.target).value)"
                />
              </div>
              <div class="mb-3">
                <label class="form-label small fw-semibold">Tag</label>
                @if (availableTags().length > 0) {
                  <select
                    class="form-select"
                    [value]="pullTag()"
                    (change)="pullTag.set($any($event.target).value)"
                  >
                    @for (tag of availableTags(); track tag) {
                      <option [value]="tag">{{ tag }}</option>
                    }
                  </select>
                } @else {
                  <input
                    type="text"
                    class="form-control"
                    placeholder="latest"
                    [value]="pullTag()"
                    (input)="pullTag.set($any($event.target).value)"
                  />
                }
              </div>

              <!-- Active scan config summary (read-only, driven by Settings) -->
              <div class="scan-summary d-flex gap-2 flex-wrap mb-3">
                <span
                  class="badge"
                  [class.bg-success-subtle]="configService.clamavEnabled()"
                  [class.text-success]="configService.clamavEnabled()"
                  [class.bg-secondary-subtle]="!configService.clamavEnabled()"
                  [class.text-secondary]="!configService.clamavEnabled()"
                >
                  <i class="bi bi-shield-virus me-1"></i>
                  ClamAV {{ configService.clamavEnabled() ? "ON" : "OFF" }}
                </span>
                <span
                  class="badge"
                  [class.bg-success-subtle]="configService.vulnEnabled()"
                  [class.text-success]="configService.vulnEnabled()"
                  [class.bg-secondary-subtle]="!configService.vulnEnabled()"
                  [class.text-secondary]="!configService.vulnEnabled()"
                >
                  <i class="bi bi-bug me-1"></i>
                  Trivy {{ configService.vulnEnabled() ? "ON" : "OFF" }}
                  @if (configService.vulnEnabled()) {
                    <span class="ms-1 opacity-75"
                      >({{ configService.vulnSeveritiesString() }})</span
                    >
                  }
                </span>
                <a
                  routerLink="/settings"
                  class="badge bg-body-secondary text-muted text-decoration-none"
                  title="Configure scans in Settings"
                >
                  <i class="bi bi-gear me-1"></i>Configure
                </a>
              </div>

              <button
                class="btn btn-primary w-100"
                (click)="startPull()"
                [disabled]="!pullImage() || pulling()"
              >
                @if (pulling()) {
                  <span class="spinner-border spinner-border-sm me-2"></span>
                  Starting pipeline...
                } @else {
                  <i class="bi bi-play-circle me-2"></i>
                  Start Pipeline
                }
              </button>
            </div>
          </div>
        </div>

        <!-- Right: Jobs panel -->
        <div class="col-lg-7">
          <div class="card border-0">
            <div
              class="card-header border-0 d-flex align-items-center justify-content-between"
            >
              <h6 class="fw-semibold mb-0">
                <i class="bi bi-list-task me-2"></i>Pipeline Jobs
              </h6>
              <button
                class="btn btn-sm btn-outline-secondary"
                (click)="loadJobs()"
              >
                <i class="bi bi-arrow-clockwise"></i>
              </button>
            </div>

            <div class="card-body p-0">
              @if (jobs().length === 0) {
                <div class="text-center text-muted py-5">
                  <i class="bi bi-inbox display-5 d-block mb-3"></i>
                  No staging jobs yet
                </div>
              } @else {
                <div class="job-list p-3 d-flex flex-column gap-3">
                  @for (job of jobs(); track job.job_id) {
                    <div class="job-card p-3 rounded">
                      <!-- Header -->
                      <div
                        class="d-flex align-items-start justify-content-between mb-2"
                      >
                        <div>
                          <div class="fw-semibold small">
                            {{ job.image }}:{{ job.tag }}
                          </div>
                          <div class="text-muted" style="font-size:0.7rem">
                            {{ job.job_id }}
                          </div>
                        </div>
                        <div class="d-flex align-items-center gap-2">
                          <span [class]="getStatusBadgeClass(job.status)">{{
                            job.status
                          }}</span>
                          <button
                            class="btn btn-sm btn-outline-danger border-0 p-0 px-1"
                            (click)="deleteJob(job.job_id)"
                          >
                            <i class="bi bi-x-lg"></i>
                          </button>
                        </div>
                      </div>

                      <!-- Scan badges applied on this job (advanced mode only) -->
                      @if (configService.advancedMode()) {
                        <div class="d-flex gap-1 mb-2 flex-wrap">
                          @if (job.clamav_enabled_override !== null) {
                            <span
                              class="badge"
                              [class.bg-success-subtle]="
                                job.clamav_enabled_override
                              "
                              [class.text-success]="job.clamav_enabled_override"
                              [class.bg-secondary-subtle]="
                                !job.clamav_enabled_override
                              "
                              [class.text-secondary]="
                                !job.clamav_enabled_override
                              "
                              style="font-size:0.65rem"
                            >
                              <i class="bi bi-shield-virus me-1"></i>ClamAV
                              {{ job.clamav_enabled_override ? "ON" : "OFF" }}
                            </span>
                          }
                          @if (job.vuln_scan_enabled_override !== null) {
                            <span
                              class="badge"
                              [class.bg-success-subtle]="
                                job.vuln_scan_enabled_override
                              "
                              [class.text-success]="
                                job.vuln_scan_enabled_override
                              "
                              [class.bg-secondary-subtle]="
                                !job.vuln_scan_enabled_override
                              "
                              [class.text-secondary]="
                                !job.vuln_scan_enabled_override
                              "
                              style="font-size:0.65rem"
                            >
                              <i class="bi bi-bug me-1"></i>Trivy
                              {{
                                job.vuln_scan_enabled_override ? "ON" : "OFF"
                              }}
                              @if (
                                job.vuln_scan_enabled_override &&
                                job.vuln_severities_override
                              ) {
                                ({{ job.vuln_severities_override }})
                              }
                            </span>
                          }
                        </div>
                      }

                      <!-- Progress -->
                      <div class="mb-2">
                        <div class="progress mb-1" style="height:6px">
                          <div
                            class="progress-bar"
                            [class.bg-success]="
                              job.status === 'done' ||
                              job.status === 'scan_clean' ||
                              job.status === 'scan_skipped'
                            "
                            [class.bg-danger]="
                              job.status === 'failed' ||
                              job.status === 'scan_infected' ||
                              job.status === 'scan_vulnerable'
                            "
                            [class.progress-bar-striped]="
                              [
                                'pulling',
                                'scanning',
                                'vuln_scanning',
                                'pushing',
                              ].includes(job.status)
                            "
                            [class.progress-bar-animated]="
                              [
                                'pulling',
                                'scanning',
                                'vuln_scanning',
                                'pushing',
                              ].includes(job.status)
                            "
                            [style.width.%]="job.progress"
                          ></div>
                        </div>
                        <div class="small text-muted">{{ job.message }}</div>
                      </div>

                      <!-- Scan result -->
                      @if (job.scan_result) {
                        <div
                          class="small mb-2 p-2 rounded bg-body-secondary font-monospace text-break"
                          style="font-size:0.7rem"
                        >
                          {{ job.scan_result }}
                        </div>
                      }

                      <!-- Push action -->
                      @if (
                        job.status === "scan_clean" ||
                        job.status === "scan_skipped"
                      ) {
                        <div class="push-section border-top pt-2 mt-2">
                          <div
                            class="small fw-semibold mb-2"
                            [class.text-success]="job.status === 'scan_clean'"
                            [class.text-secondary]="
                              job.status === 'scan_skipped'
                            "
                          >
                            @if (job.status === "scan_clean") {
                              <i class="bi bi-shield-check me-1"></i>Ready to
                              push
                            } @else {
                              <i class="bi bi-skip-forward me-1"></i>Ready to
                              push (scan skipped)
                            }
                          </div>
                          <div class="row g-2 mb-2">
                            <div class="col">
                              <input
                                type="text"
                                class="form-control form-control-sm"
                                placeholder="Target image (optional)"
                                [(ngModel)]="pushTargets[job.job_id + '_img']"
                              />
                            </div>
                            <div class="col-auto">
                              <input
                                type="text"
                                class="form-control form-control-sm"
                                placeholder="tag"
                                style="width:90px"
                                [(ngModel)]="pushTargets[job.job_id + '_tag']"
                              />
                            </div>
                          </div>
                          <button
                            class="btn btn-success btn-sm w-100"
                            (click)="pushImage(job)"
                            [disabled]="pushing() === job.job_id"
                          >
                            @if (pushing() === job.job_id) {
                              <span
                                class="spinner-border spinner-border-sm me-2"
                              ></span>
                              Pushing...
                            } @else {
                              <i class="bi bi-cloud-upload me-2"></i>
                              Push to Registry
                            }
                          </button>
                        </div>
                      }
                    </div>
                  }
                </div>
              }
            </div>
          </div>
        </div>
      </div>
    </div>
  `,
  styles: [
    `
      .card {
        background: var(--pc-card-bg);
        border-radius: 12px;
      }
      .search-results-list {
        max-height: 220px;
        overflow-y: auto;
        display: flex;
        flex-direction: column;
        gap: 4px;
      }
      .search-result-item {
        cursor: pointer;
        background: var(--pc-bg-secondary, rgba(0, 0, 0, 0.03));
        transition: background 0.15s;
      }
      .search-result-item:hover {
        background: var(--bs-primary-bg-subtle);
      }
      .job-card {
        background: var(--pc-bg-secondary, rgba(0, 0, 0, 0.03));
        border: 1px solid var(--pc-border);
      }
      .job-list {
        max-height: 600px;
        overflow-y: auto;
      }
      @keyframes spin {
        from {
          transform: rotate(0deg);
        }
        to {
          transform: rotate(360deg);
        }
      }
      .spin {
        display: inline-block;
        animation: spin 0.8s linear infinite;
      }
    `,
  ],
})
export class StagingComponent implements OnInit, OnDestroy {
  private staging = inject(StagingService);
  readonly configService = inject(AppConfigService);

  // ── Core state ─────────────────────────────────────────────────────────────
  jobs = signal<StagingJob[]>([]);
  searchQuery = "";
  searchResults = signal<DockerHubResult[]>([]);
  searching = signal(false);
  pullImage = signal("");
  pullTag = signal("latest");
  pulling = signal(false);
  pushing = signal<string | null>(null);
  availableTags = signal<string[]>([]);
  pushTargets: Record<string, string> = {};

  // ── ClamAV live indicator ──────────────────────────────────────────────────
  clamavStatus = signal<ClamAVStatus | null>(null);
  clamavLoading = signal(false);

  private refreshInterval: ReturnType<typeof setInterval> | null = null;
  private clamavInterval: ReturnType<typeof setInterval> | null = null;

  ngOnInit() {
    this.loadJobs();
    this.refreshClamAVStatus();

    // Auto-refresh active jobs every 3 s
    this.refreshInterval = setInterval(() => {
      const active = this.jobs().filter((j) =>
        ["pending", "pulling", "scanning", "vuln_scanning", "pushing"].includes(
          j.status,
        ),
      );
      if (active.length > 0) this.loadJobs();
    }, 3000);

    // Refresh ClamAV status every 30 s
    this.clamavInterval = setInterval(() => this.refreshClamAVStatus(), 30_000);
  }

  ngOnDestroy() {
    if (this.refreshInterval) clearInterval(this.refreshInterval);
    if (this.clamavInterval) clearInterval(this.clamavInterval);
  }

  // ── ClamAV indicator ───────────────────────────────────────────────────────

  refreshClamAVStatus() {
    this.clamavLoading.set(true);
    this.configService.getClamAVStatus().subscribe({
      next: (s) => {
        this.clamavStatus.set(s);
        this.clamavLoading.set(false);
      },
      error: () => this.clamavLoading.set(false),
    });
  }

  clamavBadgeClass = computed(() => {
    const s = this.clamavStatus();
    if (!s) return "badge bg-secondary-subtle text-secondary";
    if (!s.enabled) return "badge bg-secondary-subtle text-secondary";
    if (s.reachable) return "badge bg-success-subtle text-success";
    return "badge bg-danger-subtle text-danger";
  });

  clamavBadgeIcon = computed(() => {
    if (this.clamavLoading()) return "bi-hourglass-split";
    const s = this.clamavStatus();
    if (!s) return "bi-hourglass-split";
    if (!s.enabled) return "bi-slash-circle";
    if (s.reachable) return "bi-shield-check";
    return "bi-shield-exclamation";
  });

  clamavBadgeLabel = computed(() => {
    if (this.clamavLoading()) return "Checking…";
    const s = this.clamavStatus();
    if (!s) return "ClamAV: unknown";
    if (!s.enabled) return "ClamAV disabled";
    if (s.reachable) return `ClamAV online (${s.host}:${s.port})`;
    return `ClamAV offline (${s.host}:${s.port})`;
  });

  // ── Jobs ───────────────────────────────────────────────────────────────────

  loadJobs() {
    this.staging.listJobs().subscribe({
      next: (jobs) => this.jobs.set(jobs),
    });
  }

  // ── Docker Hub ─────────────────────────────────────────────────────────────

  searchDockerHub() {
    if (!this.searchQuery.trim()) return;
    this.searching.set(true);
    this.staging.searchDockerHub(this.searchQuery).subscribe({
      next: (data) => {
        this.searchResults.set(data.results);
        this.searching.set(false);
      },
      error: () => this.searching.set(false),
    });
  }

  selectDockerHubImage(result: DockerHubResult) {
    this.pullImage.set(result.name);
    this.pullTag.set("latest");
    this.loadAvailableTags(result.name);
  }

  onImageChange(value: string) {
    this.pullImage.set(value);
    if (value.length > 2) this.loadAvailableTags(value);
    else this.availableTags.set([]);
  }

  loadAvailableTags(image: string) {
    this.staging.getDockerHubTags(image).subscribe({
      next: (data) => {
        this.availableTags.set(data.tags);
        if (data.tags.length > 0 && this.pullTag() === "latest") {
          const hasLatest = data.tags.includes("latest");
          if (!hasLatest) this.pullTag.set(data.tags[0]);
        }
      },
      error: () => this.availableTags.set([]),
    });
  }

  // ── Pipeline ───────────────────────────────────────────────────────────────

  startPull() {
    if (!this.pullImage()) return;
    this.pulling.set(true);

    // Read scan preferences from the Settings service
    this.staging
      .pullImage({
        image: this.pullImage(),
        tag: this.pullTag() || "latest",
        clamav_enabled_override: this.configService.clamavEnabled(),
        vuln_scan_enabled_override: this.configService.vulnEnabled(),
        vuln_severities_override: this.configService.vulnSeveritiesString(),
      })
      .subscribe({
        next: (job) => {
          this.jobs.update((jobs) => [job, ...jobs]);
          this.pulling.set(false);
          this.pullImage.set("");
          this.pullTag.set("latest");
          this.availableTags.set([]);
        },
        error: () => this.pulling.set(false),
      });
  }

  pushImage(job: StagingJob) {
    this.pushing.set(job.job_id);
    const targetImage = this.pushTargets[job.job_id + "_img"] || undefined;
    const targetTag = this.pushTargets[job.job_id + "_tag"] || undefined;
    this.staging.pushImage(job.job_id, targetImage, targetTag).subscribe({
      next: () => {
        this.pushing.set(null);
        this.loadJobs();
      },
      error: () => this.pushing.set(null),
    });
  }

  deleteJob(jobId: string) {
    this.staging.deleteJob(jobId).subscribe({
      next: () => this.loadJobs(),
    });
  }

  // ── Helpers ────────────────────────────────────────────────────────────────

  getStatusBadgeClass(status: string): string {
    const map: Record<string, string> = {
      pending: "badge bg-secondary-subtle text-secondary",
      pulling: "badge bg-info-subtle text-info",
      scanning: "badge bg-warning-subtle text-warning",
      scan_skipped: "badge bg-secondary-subtle text-secondary",
      vuln_scanning: "badge bg-warning-subtle text-warning",
      scan_clean: "badge bg-success-subtle text-success",
      scan_vulnerable: "badge bg-danger text-white",
      scan_infected: "badge bg-danger text-white",
      pushing: "badge bg-primary-subtle text-primary",
      done: "badge bg-success text-white",
      failed: "badge bg-danger text-white",
    };
    return map[status] || "badge bg-secondary";
  }

  formatCount(n: number): string {
    if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(1)}M`;
    if (n >= 1_000) return `${(n / 1_000).toFixed(1)}K`;
    return `${n}`;
  }
}
