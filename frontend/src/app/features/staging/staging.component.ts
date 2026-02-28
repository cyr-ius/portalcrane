/**
 * Portalcrane - Staging Component
 * Pull pipeline: Docker Hub search → tag selection → pull → CVE scan → push
 * (local or external registry with optional folder prefix).
 *
 * Angular 21 zoneless — uses Signals exclusively, no Zone.js change detection.
 * Deprecated directives (*ngIf, *ngFor) are replaced by @if / @for control flow.
 */
import { Component, DestroyRef, inject, OnInit, signal } from "@angular/core";
import { takeUntilDestroyed } from "@angular/core/rxjs-interop";
import { filter, switchMap, timer } from "rxjs";
import { AppConfigService } from "../../core/services/app-config.service";
import {
  ExternalRegistry,
  ExternalRegistryService,
} from "../../core/services/external-registry.service";
import {
  DockerHubResult,
  StagingJob,
  StagingService,
} from "../../core/services/staging.service";

/** Job statuses that indicate an active pipeline step. */
const ACTIVE_STATUSES = new Set([
  "pending",
  "pulling",
  "scanning",
  "vuln_scanning",
  "pushing",
]);

/** Push destination modes. */
export type PushMode = "local" | "external";

@Component({
  selector: "app-staging",
  // No FormsModule import: all inputs use [value]/(input) signal bindings.
  imports: [],
  templateUrl: "./staging.component.html",
  styleUrl: "./staging.component.css",
})
export class StagingComponent implements OnInit {
  private staging = inject(StagingService);
  private externalRegistryService = inject(ExternalRegistryService);
  private destroyRef = inject(DestroyRef);
  readonly configService = inject(AppConfigService);

  // ── Job list ───────────────────────────────────────────────────────────────
  jobs = signal<StagingJob[]>([]);

  // ── Docker Hub search ──────────────────────────────────────────────────────
  searchQuery = signal("");
  searchResults = signal<DockerHubResult[]>([]);
  searching = signal(false);

  // ── Pull form ──────────────────────────────────────────────────────────────
  pullImage = signal("");
  pullTag = signal("latest");
  availableTags = signal<string[]>([]);
  pulling = signal(false);

  // ── Push state ─────────────────────────────────────────────────────────────
  pushing = signal<string | null>(null);

  /**
   * Per-job push field values keyed by `{job_id}_{field}`.
   * Fields: img, tag, folder, ext_host, ext_user, ext_pass.
   */
  pushTargets = signal<Record<string, string>>({});

  /**
   * Per-job push mode: "local" | "external".
   * Defaults to "local" when unset.
   */
  pushModes = signal<Record<string, PushMode>>({});

  /**
   * Per-job external registry selection.
   * Value is a saved registry ID or "" for ad-hoc entry.
   */
  pushExtRegistryId = signal<Record<string, string>>({});

  // ── External registries ────────────────────────────────────────────────────
  externalRegistries = signal<ExternalRegistry[]>([]);

  // ── Lifecycle ──────────────────────────────────────────────────────────────

  ngOnInit(): void {
    this.loadJobs();
    this.loadExternalRegistries();
    this.startJobsAutoRefresh();
  }

  // ── Auto-refresh ───────────────────────────────────────────────────────────

  /**
   * Poll the job list every 3 s while at least one job is active.
   * takeUntilDestroyed removes the subscription when the component is destroyed.
   */
  private startJobsAutoRefresh(): void {
    timer(0, 3000)
      .pipe(
        filter(() => this.jobs().some((j) => ACTIVE_STATUSES.has(j.status))),
        switchMap(() => this.staging.listJobs()),
        takeUntilDestroyed(this.destroyRef),
      )
      .subscribe((jobs) => this.jobs.set(StagingService.sortJobs(jobs)));
  }

  // ── Data loading ───────────────────────────────────────────────────────────

  loadJobs(): void {
    this.staging.listJobs().subscribe({
      next: (jobs) => this.jobs.set(StagingService.sortJobs(jobs)),
    });
  }

  loadExternalRegistries(): void {
    this.externalRegistryService.listRegistries().subscribe({
      next: (regs) => this.externalRegistries.set(regs),
    });
  }

  // ── Docker Hub search ──────────────────────────────────────────────────────

  /**
   * Triggered on every keystroke in the search input.
   * Clears results when the query is empty; otherwise calls the API.
   */
  onSearch(): void {
    const q = this.searchQuery().trim();
    if (!q) {
      this.searchResults.set([]);
      return;
    }
    this.searching.set(true);
    this.staging.searchDockerHub(q).subscribe({
      next: ({ results }) => {
        this.searchResults.set(results);
        this.searching.set(false);
      },
      error: () => this.searching.set(false),
    });
  }

  /**
   * Called when the user clicks the explicit Search button.
   * Identical behaviour to onSearch but kept separate for semantic clarity.
   */
  searchDockerHub(): void {
    this.onSearch();
  }

  /**
   * Select an image from the search results dropdown.
   * Clears the results list and immediately loads the available tags.
   */
  selectImage(name: string): void {
    this.pullImage.set(name);
    // Search results stay visible so the user can compare or pick another image.
    this.staging.getDockerHubTags(name).subscribe({
      next: ({ tags }) => {
        this.availableTags.set(tags);
        // Keep "latest" if it exists, otherwise pre-select the first available tag.
        if (tags.length > 0 && !tags.includes(this.pullTag())) {
          this.pullTag.set(tags[0]);
        }
      },
      error: () => this.availableTags.set([]),
    });
  }

  // ── Pull ───────────────────────────────────────────────────────────────────

  startPull(): void {
    if (!this.pullImage()) return;
    this.pulling.set(true);

    const advanced = this.configService.advancedMode();
    this.staging
      .pullImage({
        image: this.pullImage(),
        tag: this.pullTag() || "latest",
        vuln_scan_enabled_override: advanced
          ? this.configService.vulnEnabled()
          : null,
        vuln_severities_override: advanced
          ? this.configService.vulnSeveritiesString()
          : null,
      })
      .subscribe({
        next: (job) => {
          this.jobs.update((jobs) => [job, ...jobs]);
          this.pulling.set(false);
          // Reset pull form
          this.pullImage.set("");
          this.pullTag.set("latest");
          this.availableTags.set([]);
        },
        error: () => this.pulling.set(false),
      });
  }

  // ── Push helpers ───────────────────────────────────────────────────────────

  getPushMode(job: StagingJob): PushMode {
    return this.pushModes()[job.job_id] ?? "local";
  }

  setPushMode(job: StagingJob, mode: PushMode): void {
    this.pushModes.update((m) => ({ ...m, [job.job_id]: mode }));
  }

  getExtRegistryId(job: StagingJob): string {
    return this.pushExtRegistryId()[job.job_id] ?? "";
  }

  setExtRegistryId(job: StagingJob, id: string): void {
    this.pushExtRegistryId.update((m) => ({ ...m, [job.job_id]: id }));
  }

  getPushTarget(job: StagingJob, field: string): string {
    return this.pushTargets()[`${job.job_id}_${field}`] ?? "";
  }

  updatePushTarget(job: StagingJob, field: string, value: string): void {
    this.pushTargets.update((t) => ({
      ...t,
      [`${job.job_id}_${field}`]: value,
    }));
  }

  /**
   * Compute the full target image reference for preview in the template.
   * Reflects current push mode, folder, image name, tag and registry.
   */
  pushPreview(job: StagingJob): string {
    const mode = this.getPushMode(job);
    const folder = this.getPushTarget(job, "folder").trim();
    const img = (
      this.getPushTarget(job, "img") ||
      job.image.split("/").pop() ||
      job.image
    ).trim();
    const tag = (this.getPushTarget(job, "tag") || job.tag).trim();
    const path = folder ? `${folder}/${img}` : img;

    if (mode === "local") {
      return `localhost:5000/${path}:${tag}`;
    }
    const regId = this.getExtRegistryId(job);
    const reg = this.externalRegistries().find((r) => r.id === regId);
    const host = reg
      ? reg.host
      : this.getPushTarget(job, "ext_host") || "<registry>";
    return `${host}/${path}:${tag}`;
  }

  // ── Push ───────────────────────────────────────────────────────────────────

  pushImage(job: StagingJob): void {
    this.pushing.set(job.job_id);

    const mode = this.getPushMode(job);
    const isExternal = mode === "external";
    const regId = this.getExtRegistryId(job) || null;

    this.staging
      .pushImage({
        job_id: job.job_id,
        target_image: this.getPushTarget(job, "img") || null,
        target_tag: this.getPushTarget(job, "tag") || null,
        folder: this.getPushTarget(job, "folder") || null,
        external_registry_id: isExternal ? regId : null,
        external_registry_host:
          isExternal && !regId
            ? this.getPushTarget(job, "ext_host") || null
            : null,
        external_registry_username:
          isExternal && !regId
            ? this.getPushTarget(job, "ext_user") || null
            : null,
        external_registry_password:
          isExternal && !regId
            ? this.getPushTarget(job, "ext_pass") || null
            : null,
      })
      .subscribe({
        next: () => {
          this.pushing.set(null);
          this.loadJobs();
        },
        error: () => this.pushing.set(null),
      });
  }

  // ── Delete ─────────────────────────────────────────────────────────────────

  deleteJob(jobId: string): void {
    this.staging.deleteJob(jobId).subscribe({
      next: () => this.loadJobs(),
    });
  }

  // ── Template helpers ───────────────────────────────────────────────────────

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
    return map[status] ?? "badge bg-secondary";
  }

  /** Format large numbers as K / M for star and pull counts. */
  formatCount(n: number): string {
    if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(1)}M`;
    if (n >= 1_000) return `${(n / 1_000).toFixed(1)}K`;
    return `${n}`;
  }

  getVulnCount(job: StagingJob, severity: string): number {
    return job.vuln_result?.counts[severity] ?? 0;
  }

  getSeverityBadge(sev: string): string {
    const map: Record<string, string> = {
      CRITICAL: "badge bg-danger text-white",
      HIGH: "badge bg-warning text-dark",
      MEDIUM: "badge bg-info text-dark",
      LOW: "badge bg-success text-white",
      UNKNOWN: "badge bg-secondary text-white",
    };
    return map[sev] ?? "badge bg-secondary";
  }

  getCveRowClass(job: StagingJob, sev: string): string {
    const blocking = job.vuln_result?.severities ?? [];
    if (blocking.includes(sev)) {
      return sev === "CRITICAL"
        ? "table-danger"
        : sev === "HIGH"
          ? "table-warning"
          : "";
    }
    return "";
  }
}
