/**
 * Portalcrane - Staging Component
 * Pull pipeline: Docker Hub → Trivy CVE scan → Push (local or external registry).
 * New features:
 *   - Optional folder prefix when pushing
 *   - Push to external registry (saved or ad-hoc)
 *   - Per-job push mode toggle (local / external)
 */
import { SlicePipe } from "@angular/common";
import { Component, DestroyRef, inject, OnInit, signal } from "@angular/core";
import { takeUntilDestroyed } from "@angular/core/rxjs-interop";
import { FormsModule } from "@angular/forms";
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
  imports: [FormsModule, SlicePipe],
  templateUrl: "./staging.component.html",
  styleUrl: "./staging.component.css",
})
export class StagingComponent implements OnInit {
  private staging = inject(StagingService);
  private externalRegistryService = inject(ExternalRegistryService);
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

  /**
   * Per-job push state keyed by `{job_id}_{field}`.
   * Fields: _img (target image name), _tag (target tag), _folder (folder prefix).
   */
  pushTargets = signal<Record<string, string>>({});

  /**
   * Per-job push mode: "local" or "external".
   * Defaults to "local" when not set.
   */
  pushModes = signal<Record<string, PushMode>>({});

  /**
   * Per-job external registry selection.
   * Value is either a registry ID (saved) or "" for ad-hoc entry.
   */
  pushExtRegistryId = signal<Record<string, string>>({});

  // ── External registries list ───────────────────────────────────────────────
  externalRegistries = signal<ExternalRegistry[]>([]);

  // ── Derived: preview path ──────────────────────────────────────────────────
  /**
   * Compute the push preview string for a given job.
   * Used by the template for real-time feedback.
   */
  pushPreview(job: StagingJob): string {
    const mode = this.pushModes()[job.job_id] ?? "local";
    const folder = (this.pushTargets()[job.job_id + "_folder"] ?? "").trim();
    const img = (
      this.pushTargets()[job.job_id + "_img"] ??
      job.image.split("/").pop() ??
      job.image
    ).trim();
    const tag = (this.pushTargets()[job.job_id + "_tag"] ?? job.tag).trim();
    const path = folder ? `${folder}/${img}` : img;

    if (mode === "local") {
      return `localhost:5000/${path}:${tag}`;
    }
    const regId = this.pushExtRegistryId()[job.job_id] ?? "";
    const reg = this.externalRegistries().find((r) => r.id === regId);
    const host = reg
      ? reg.host
      : (this.pushTargets()[job.job_id + "_ext_host"] ?? "<registry>");
    return `${host}/${path}:${tag}`;
  }

  ngOnInit() {
    this.loadJobs();
    this.startJobsAutoRefresh();
    this.loadExternalRegistries();
  }

  // ── Auto-refresh ───────────────────────────────────────────────────────────

  /** Poll job list every 3 s while at least one job is active. */
  private startJobsAutoRefresh() {
    timer(0, 3000)
      .pipe(
        filter(() => this.jobs().some((j) => ACTIVE_STATUSES.has(j.status))),
        switchMap(() => this.staging.listJobs()),
        takeUntilDestroyed(this.destroyRef),
      )
      .subscribe((jobs) => this.jobs.set(StagingService.sortJobs(jobs)));
  }

  // ── Data loading ───────────────────────────────────────────────────────────

  loadJobs() {
    this.staging.listJobs().subscribe({
      next: (jobs) => this.jobs.set(StagingService.sortJobs(jobs)),
    });
  }

  loadExternalRegistries() {
    this.externalRegistryService.listRegistries().subscribe({
      next: (regs) => this.externalRegistries.set(regs),
    });
  }

  // ── Search ─────────────────────────────────────────────────────────────────

  onSearch() {
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

  selectImage(name: string) {
    this.pullImage.set(name);
    this.searchResults.set([]);
    this.searchQuery.set("");
    this.staging.getDockerHubTags(name).subscribe({
      next: ({ tags }) => this.availableTags.set(tags),
    });
  }

  // ── Pull ───────────────────────────────────────────────────────────────────

  startPull() {
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
          this.pullImage.set("");
          this.pullTag.set("latest");
          this.availableTags.set([]);
        },
        error: () => this.pulling.set(false),
      });
  }

  // ── Push mode helpers ──────────────────────────────────────────────────────

  getPushMode(job: StagingJob): PushMode {
    return this.pushModes()[job.job_id] ?? "local";
  }

  setPushMode(job: StagingJob, mode: PushMode) {
    this.pushModes.update((m) => ({ ...m, [job.job_id]: mode }));
  }

  getExtRegistryId(job: StagingJob): string {
    return this.pushExtRegistryId()[job.job_id] ?? "";
  }

  setExtRegistryId(job: StagingJob, id: string) {
    this.pushExtRegistryId.update((m) => ({ ...m, [job.job_id]: id }));
  }

  updatePushTarget(job: StagingJob, field: string, value: string) {
    this.pushTargets.update((t) => ({
      ...t,
      [`${job.job_id}_${field}`]: value,
    }));
  }

  getPushTarget(job: StagingJob, field: string): string {
    return this.pushTargets()[`${job.job_id}_${field}`] ?? "";
  }

  // ── Push ───────────────────────────────────────────────────────────────────

  pushImage(job: StagingJob) {
    this.pushing.set(job.job_id);

    const mode = this.getPushMode(job);
    const folder = this.getPushTarget(job, "folder") || null;
    const targetImage = this.getPushTarget(job, "img") || null;
    const targetTag = this.getPushTarget(job, "tag") || null;

    const isExternal = mode === "external";
    const regId = this.getExtRegistryId(job) || null;
    const adHocHost = this.getPushTarget(job, "ext_host") || null;

    this.staging
      .pushImage({
        job_id: job.job_id,
        target_image: targetImage,
        target_tag: targetTag,
        folder,
        external_registry_id: isExternal ? regId : null,
        external_registry_host: isExternal && !regId ? adHocHost : null,
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

  deleteJob(jobId: string) {
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

  formatCount(n: number): string {
    if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(1)}M`;
    if (n >= 1_000) return `${(n / 1_000).toFixed(1)}K`;
    return `${n}`;
  }

  getVulnCount(job: StagingJob, severity: string): number {
    return job.vuln_result?.counts[severity] ?? 0;
  }

  /** Severity badge CSS class for the CVE table rows. */
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

  /** CSS row class to highlight blocking CVE rows in the table. */
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
