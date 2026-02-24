import {
  Component,
  computed,
  DestroyRef,
  inject,
  OnInit,
  signal,
} from "@angular/core";
import { takeUntilDestroyed } from "@angular/core/rxjs-interop";
import { FormsModule } from "@angular/forms";
import { filter, switchMap, timer } from "rxjs";
import {
  AppConfigService,
  ClamAVStatus,
} from "../../core/services/app-config.service";
import {
  DockerHubResult,
  StagingJob,
  StagingService,
} from "../../core/services/staging.service";

/** Job statuses that indicate an active pipeline step */
const ACTIVE_STATUSES = new Set([
  "pending",
  "pulling",
  "scanning",
  "vuln_scanning",
  "pushing",
]);

@Component({
  selector: "app-staging",
  imports: [FormsModule],
  templateUrl: "./staging.component.html",
  styleUrl: "./staging.component.css",
})
export class StagingComponent implements OnInit {
  private staging = inject(StagingService);
  private destroyRef = inject(DestroyRef);
  readonly configService = inject(AppConfigService);

  // ── Core state ─────────────────────────────────────────────────────────────
  jobs = signal<StagingJob[]>([]);
  searchQuery = signal("");
  searchResults = signal<DockerHubResult[]>([]);
  searching = signal(false);
  pullImage = signal("");
  pullTag = signal("latest");
  pulling = signal(false);
  pushing = signal<string | null>(null);
  availableTags = signal<string[]>([]);
  pushTargets = signal<Record<string, string>>({});

  // ── ClamAV live indicator ──────────────────────────────────────────────────
  clamavStatus = signal<ClamAVStatus | null>(null);
  clamavLoading = signal(false);

  ngOnInit() {
    this.loadJobs();
    this.refreshClamAVStatus();
    this.startJobsAutoRefresh();
    this.startClamAVAutoRefresh();
  }

  // ── Auto-refresh: active jobs every 3 s ───────────────────────────────────

  /**
   * Polls the job list every 3 s, but only when at least one job is active.
   * Uses takeUntilDestroyed so no manual cleanup is needed.
   */
  private startJobsAutoRefresh(): void {
    timer(3000, 3000)
      .pipe(
        filter(() => this.jobs().some((j) => ACTIVE_STATUSES.has(j.status))),
        switchMap(() => this.staging.listJobs()),
        takeUntilDestroyed(this.destroyRef),
      )
      .subscribe((jobs) => this.jobs.set(StagingService.sortJobs(jobs)));
  }

  // ── Auto-refresh: ClamAV status every 30 s ────────────────────────────────

  /**
   * Polls ClamAV reachability every 30 s automatically.
   * Uses takeUntilDestroyed so no manual cleanup is needed.
   */
  private startClamAVAutoRefresh(): void {
    timer(30_000, 30_000)
      .pipe(
        switchMap(() => this.configService.getClamAVStatus()),
        takeUntilDestroyed(this.destroyRef),
      )
      .subscribe({
        next: (s) => this.clamavStatus.set(s),
      });
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
      next: (jobs) => this.jobs.set(StagingService.sortJobs(jobs)),
    });
  }

  // ── Docker Hub ─────────────────────────────────────────────────────────────

  searchDockerHub() {
    if (!this.searchQuery().trim()) return;
    this.searching.set(true);
    this.staging.searchDockerHub(this.searchQuery()).subscribe({
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
    const targetImage = this.pushTargets()[job.job_id + "_img"] || undefined;
    const targetTag = this.pushTargets()[job.job_id + "_tag"] || undefined;
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

  getVulnCount(job: StagingJob, severity: string): number {
    return job.vuln_result?.counts[severity] ?? 0;
  }
}
