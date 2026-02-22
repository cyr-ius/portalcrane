import { Component, signal, inject, OnInit, OnDestroy } from "@angular/core";
import { CommonModule } from "@angular/common";
import { FormsModule } from "@angular/forms";
import {
  StagingService,
  StagingJob,
  DockerHubResult,
} from "../../core/services/staging.service";
import { VulnConfigService } from "../../core/services/vuln-config.service";

@Component({
  selector: "app-staging",
  imports: [CommonModule, FormsModule],
  template: `
    <div class="p-4">
      <div class="d-flex align-items-center justify-content-between mb-4">
        <div>
          <h2 class="fw-bold mb-1">Staging Pipeline</h2>
          <p class="text-muted small mb-0">
            Pull from Docker Hub → ClamAV Scan → Trivy CVE Scan → Push to
            Registry
          </p>
        </div>
      </div>

      <div class="row g-3">
        <!-- Left: Pull panel -->
        <div class="col-lg-5">
          <!-- Active vuln config badge -->
          <div
            class="d-flex align-items-center gap-2 mb-3 p-2 rounded vuln-badge-bar"
          >
            <i class="bi bi-shield-check text-primary"></i>
            <span class="small">
              Trivy:
              @if (vulnConfig.config().enabled) {
                <span class="text-success fw-semibold">enabled</span>
                — blocking
                @for (
                  sev of vulnConfig.config().severities;
                  track sev;
                  let last = $last
                ) {
                  <span class="badge sev-mini" [class]="getSevColor(sev)">{{
                    sev
                  }}</span>
                }
              } @else {
                <span class="text-muted">disabled</span>
              }
            </span>
            @if (vulnConfig.hasLocalOverrides()) {
              <span
                class="badge bg-warning-subtle text-warning ms-auto"
                style="font-size:0.65rem"
              >
                <i class="bi bi-pencil-fill me-1"></i>Custom
              </span>
            }
            <a
              routerLink="/settings"
              class="text-muted ms-auto"
              style="font-size:0.75rem"
              title="Edit in Settings"
            >
              <i class="bi bi-gear"></i>
            </a>
          </div>

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
                      class="search-result-item p-2 rounded mb-1"
                      [class.selected]="pullImage() === result.name"
                      (click)="selectDockerHubImage(result)"
                    >
                      <div class="d-flex align-items-center gap-2">
                        <div class="flex-grow-1 min-w-0">
                          <div class="fw-semibold small text-truncate">
                            {{ result.name }}
                            @if (result.is_official) {
                              <i
                                class="bi bi-patch-check-fill text-primary ms-1"
                                title="Official"
                              ></i>
                            }
                          </div>
                          @if (result.description) {
                            <div
                              class="text-muted text-truncate"
                              style="font-size:0.7rem"
                            >
                              {{ result.description }}
                            </div>
                          }
                        </div>
                        <div
                          class="text-muted text-end"
                          style="font-size:0.7rem"
                        >
                          <div>
                            <i class="bi bi-star-fill text-warning me-1"></i
                            >{{ formatCount(result.star_count) }}
                          </div>
                          <div>
                            <i class="bi bi-download me-1"></i
                            >{{ formatCount(result.pull_count) }}
                          </div>
                        </div>
                      </div>
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
              <div class="mb-2">
                <label class="form-label small fw-semibold">Image</label>
                <input
                  type="text"
                  class="form-control"
                  placeholder="nginx, library/redis, myorg/myimage..."
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
                    @for (t of availableTags(); track t) {
                      <option [value]="t">{{ t }}</option>
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
              <button
                class="btn btn-primary w-100"
                (click)="startPull()"
                [disabled]="!pullImage() || pulling()"
              >
                @if (pulling()) {
                  <span class="spinner-border spinner-border-sm me-2"></span>
                  Pulling...
                } @else {
                  <i class="bi bi-cloud-download me-2"></i>Start Pipeline
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
                @if (jobs().length > 0) {
                  <span class="badge bg-secondary ms-2">{{
                    jobs().length
                  }}</span>
                }
              </h6>
              <button
                class="btn btn-sm btn-outline-secondary border-0"
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

                      <!-- Progress bar -->
                      <div class="mb-2">
                        <div class="progress mb-1" style="height:6px">
                          <div
                            class="progress-bar"
                            [class.bg-success]="
                              job.status === 'done' ||
                              job.status === 'scan_clean'
                            "
                            [class.bg-danger]="
                              job.status === 'failed' ||
                              job.status === 'scan_infected' ||
                              job.status === 'scan_vulnerable'
                            "
                            [class.bg-warning]="job.status === 'vuln_scanning'"
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

                      <!-- ClamAV scan result -->
                      @if (job.scan_result) {
                        <div
                          class="small mb-2 p-2 rounded bg-body-secondary font-monospace text-break"
                          style="font-size:0.7rem"
                        >
                          {{ job.scan_result }}
                        </div>
                      }

                      <!-- Trivy vuln result -->
                      @if (job.vuln_result) {
                        <div
                          class="small mb-2 p-2 rounded"
                          [class.bg-danger-subtle]="job.vuln_result.blocked"
                          [class.bg-success-subtle]="!job.vuln_result.blocked"
                        >
                          <div class="d-flex align-items-center gap-2 mb-1">
                            <i
                              class="bi"
                              [class.bi-shield-x]="job.vuln_result.blocked"
                              [class.bi-shield-check]="!job.vuln_result.blocked"
                              [class.text-danger]="job.vuln_result.blocked"
                              [class.text-success]="!job.vuln_result.blocked"
                            ></i>
                            <span class="fw-semibold" style="font-size:0.75rem">
                              Trivy CVE —
                              {{
                                job.vuln_result.blocked
                                  ? "Policy blocked"
                                  : "Clean"
                              }}
                            </span>
                          </div>
                          <div class="d-flex flex-wrap gap-2">
                            <span
                              class="badge"
                              [class.bg-danger]="
                                job.vuln_result.counts['CRITICAL'] > 0
                              "
                              [class.bg-secondary]="
                                job.vuln_result.counts['CRITICAL'] === 0
                              "
                            >
                              CRITICAL {{ job.vuln_result.counts["CRITICAL"] }}
                            </span>
                            <span
                              class="badge"
                              [class.bg-danger]="
                                job.vuln_result.counts['HIGH'] > 0
                              "
                              [class.bg-secondary]="
                                job.vuln_result.counts['HIGH'] === 0
                              "
                            >
                              HIGH {{ job.vuln_result.counts["HIGH"] }}
                            </span>
                            <span
                              class="badge"
                              [class.bg-warning]="
                                job.vuln_result.counts['MEDIUM'] > 0
                              "
                              [class.bg-secondary]="
                                job.vuln_result.counts['MEDIUM'] === 0
                              "
                            >
                              MEDIUM {{ job.vuln_result.counts["MEDIUM"] }}
                            </span>
                            <span
                              class="badge"
                              [class.bg-info]="
                                job.vuln_result.counts['LOW'] > 0
                              "
                              [class.bg-secondary]="
                                job.vuln_result.counts['LOW'] === 0
                              "
                            >
                              LOW {{ job.vuln_result.counts["LOW"] }}
                            </span>
                            @if (job.vuln_result.counts["UNKNOWN"] > 0) {
                              <span class="badge bg-secondary"
                                >UNKNOWN
                                {{ job.vuln_result.counts["UNKNOWN"] }}</span
                              >
                            }
                          </div>
                          @if (job.vuln_result.blocked) {
                            <div
                              class="mt-1 text-danger"
                              style="font-size:0.7rem"
                            >
                              <i class="bi bi-info-circle me-1"></i>
                              Blocking:
                              {{ job.vuln_result.severities.join(", ") }}
                            </div>
                          }
                        </div>
                      }

                      <!-- Push action -->
                      @if (job.status === "scan_clean") {
                        <div class="push-section border-top pt-2 mt-2">
                          <div class="small fw-semibold mb-2 text-success">
                            <i class="bi bi-shield-check me-1"></i>Ready to push
                          </div>
                          <div class="row g-2 mb-2">
                            <div class="col">
                              <input
                                type="text"
                                class="form-control form-control-sm"
                                placeholder="Target image (optional rename)"
                                [(ngModel)]="pushTargets[job.job_id + '_img']"
                              />
                            </div>
                            <div class="col-auto">
                              <input
                                type="text"
                                class="form-control form-control-sm"
                                placeholder="Tag"
                                [(ngModel)]="pushTargets[job.job_id + '_tag']"
                              />
                            </div>
                          </div>
                          <button
                            class="btn btn-sm btn-success w-100"
                            (click)="pushImage(job)"
                            [disabled]="pushing() === job.job_id"
                          >
                            @if (pushing() === job.job_id) {
                              <span
                                class="spinner-border spinner-border-sm me-1"
                              ></span>
                            }
                            <i class="bi bi-cloud-upload me-1"></i>Push to
                            Registry
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
      .vuln-badge-bar {
        background: var(--pc-card-bg);
        border: 1px solid var(--pc-border);
        border-radius: 8px;
      }
      .sev-mini {
        font-size: 0.6rem;
        padding: 2px 5px;
        border-radius: 10px;
      }
      .search-results-list {
        max-height: 280px;
        overflow-y: auto;
      }
      .search-result-item {
        cursor: pointer;
        transition: background 0.1s;
        border: 1px solid var(--pc-border);
      }
      .search-result-item:hover {
        background: var(--pc-nav-hover);
      }
      .search-result-item.selected {
        background: var(--pc-nav-active-bg);
        border-color: var(--pc-accent);
      }
      .job-card {
        background: var(--pc-main-bg);
        border: 1px solid var(--pc-border);
        border-radius: 8px;
      }
      .job-list {
        max-height: 680px;
        overflow-y: auto;
      }
    `,
  ],
})
export class StagingComponent implements OnInit, OnDestroy {
  private staging = inject(StagingService);
  vulnConfig = inject(VulnConfigService);

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

  private refreshInterval: ReturnType<typeof setInterval> | null = null;

  private readonly ACTIVE_STATUSES: string[] = [
    "pending",
    "pulling",
    "scanning",
    "vuln_scanning",
    "pushing",
  ];

  ngOnInit() {
    this.vulnConfig.loadConfig().subscribe();
    this.loadJobs();
    this.refreshInterval = setInterval(() => {
      const hasActive = this.jobs().some((j) =>
        this.ACTIVE_STATUSES.includes(j.status),
      );
      if (hasActive) this.loadJobs();
    }, 3000);
  }

  ngOnDestroy() {
    if (this.refreshInterval) clearInterval(this.refreshInterval);
  }

  loadJobs() {
    this.staging.listJobs().subscribe({ next: (jobs) => this.jobs.set(jobs) });
  }

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
          if (!data.tags.includes("latest")) this.pullTag.set(data.tags[0]);
        }
      },
      error: () => this.availableTags.set([]),
    });
  }

  startPull() {
    if (!this.pullImage()) return;
    this.pulling.set(true);
    const cfg = this.vulnConfig.config();
    this.staging
      .pullImage(this.pullImage(), this.pullTag() || "latest", cfg)
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
    this.staging.deleteJob(jobId).subscribe({ next: () => this.loadJobs() });
  }

  getStatusBadgeClass(status: string): string {
    const map: Record<string, string> = {
      pending: "badge bg-secondary-subtle text-secondary",
      pulling: "badge bg-info-subtle text-info",
      scanning: "badge bg-warning-subtle text-warning",
      vuln_scanning: "badge bg-warning-subtle text-warning",
      scan_clean: "badge bg-success-subtle text-success",
      scan_infected: "badge bg-danger text-white",
      scan_vulnerable: "badge bg-danger text-white",
      pushing: "badge bg-primary-subtle text-primary",
      done: "badge bg-success text-white",
      failed: "badge bg-danger text-white",
    };
    return map[status] ?? "badge bg-secondary";
  }

  getSevColor(sev: string): string {
    const map: Record<string, string> = {
      CRITICAL: "bg-danger",
      HIGH: "bg-danger",
      MEDIUM: "bg-warning text-dark",
      LOW: "bg-info text-dark",
      UNKNOWN: "bg-secondary",
    };
    return map[sev] ?? "bg-secondary";
  }

  formatCount(n: number): string {
    if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(1)}M`;
    if (n >= 1_000) return `${(n / 1_000).toFixed(1)}K`;
    return `${n}`;
  }
}
