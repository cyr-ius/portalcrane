import { Component, signal, inject, OnInit, OnDestroy } from "@angular/core";
import { CommonModule } from "@angular/common";
import { FormsModule } from "@angular/forms";
import {
  StagingService,
  StagingJob,
  DockerHubResult,
} from "../../core/services/staging.service";

@Component({
  selector: "app-staging",
  imports: [CommonModule, FormsModule],
  template: `
    <div class="p-4">
      <div class="d-flex align-items-center justify-content-between mb-4">
        <div>
          <h2 class="fw-bold mb-1">Staging Pipeline</h2>
          <p class="text-muted small mb-0">
            Pull from Docker Hub → Scan → Push to Registry
          </p>
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
                      class="search-result-item p-2 rounded mb-1"
                      (click)="selectDockerHubImage(result)"
                      [class.selected]="pullImage() === result.name"
                    >
                      <div class="d-flex align-items-start gap-2">
                        <div class="flex-grow-1">
                          <div class="d-flex align-items-center gap-1 mb-1">
                            <strong class="small">{{ result.name }}</strong>
                            @if (result.is_official) {
                              <span
                                class="badge bg-primary-subtle text-primary"
                                style="font-size:0.6rem"
                                >OFFICIAL</span
                              >
                            }
                          </div>
                          <div class="text-muted" style="font-size:0.72rem">
                            {{ result.description | slice: 0 : 80
                            }}{{ result.description.length > 80 ? "..." : "" }}
                          </div>
                          <div class="d-flex gap-2 mt-1">
                            <span class="text-muted" style="font-size:0.65rem">
                              <i class="bi bi-star-fill text-warning"></i>
                              {{ formatCount(result.star_count) }}
                            </span>
                            <span class="text-muted" style="font-size:0.65rem">
                              <i class="bi bi-download"></i>
                              {{ formatCount(result.pull_count) }}
                            </span>
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
                <i class="bi bi-cloud-arrow-down me-2"></i>Pull Image
              </h6>
            </div>
            <div class="card-body">
              <div class="mb-3">
                <label class="form-label small fw-semibold">Image Name</label>
                <input
                  type="text"
                  class="form-control"
                  placeholder="e.g. nginx, library/redis, myorg/myapp"
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

                      <!-- Progress -->
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
                              job.status === 'scan_infected'
                            "
                            [class.progress-bar-striped]="
                              ['pulling', 'scanning', 'pushing'].includes(
                                job.status
                              )
                            "
                            [class.progress-bar-animated]="
                              ['pulling', 'scanning', 'pushing'].includes(
                                job.status
                              )
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
                            <i class="bi bi-cloud-upload me-1"></i>
                            Push to Registry
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
      }
      .job-list {
        max-height: 600px;
        overflow-y: auto;
      }
    `,
  ],
})
export class StagingComponent implements OnInit, OnDestroy {
  private staging = inject(StagingService);

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

  ngOnInit() {
    this.loadJobs();
    // Auto-refresh active jobs
    this.refreshInterval = setInterval(() => {
      const activeJobs = this.jobs().filter((j) =>
        ["pending", "pulling", "scanning", "pushing"].includes(j.status),
      );
      if (activeJobs.length > 0) this.loadJobs();
    }, 3000);
  }

  ngOnDestroy() {
    if (this.refreshInterval) clearInterval(this.refreshInterval);
  }

  loadJobs() {
    this.staging.listJobs().subscribe({
      next: (jobs) => this.jobs.set(jobs),
    });
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
    if (value.length > 2) {
      this.loadAvailableTags(value);
    } else {
      this.availableTags.set([]);
    }
  }

  loadAvailableTags(image: string) {
    this.staging.getDockerHubTags(image).subscribe({
      next: (data) => {
        this.availableTags.set(data.tags);
        // Auto-select first tag if available
        if (data.tags.length > 0 && this.pullTag() === "latest") {
          const hasLatest = data.tags.includes("latest");
          if (!hasLatest) this.pullTag.set(data.tags[0]);
        }
      },
      error: () => this.availableTags.set([]),
    });
  }

  startPull() {
    if (!this.pullImage()) return;
    this.pulling.set(true);
    this.staging
      .pullImage(this.pullImage(), this.pullTag() || "latest")
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

  getStatusBadgeClass(status: string): string {
    const map: Record<string, string> = {
      pending: "badge bg-secondary-subtle text-secondary",
      pulling: "badge bg-info-subtle text-info",
      scanning: "badge bg-warning-subtle text-warning",
      scan_clean: "badge bg-success-subtle text-success",
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
