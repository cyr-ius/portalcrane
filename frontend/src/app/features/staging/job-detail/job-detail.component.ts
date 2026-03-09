import { Component, computed, inject, input, signal } from "@angular/core";
import { AuthService } from "../../../core/services/auth.service";
import { ExternalRegistry, ExternalRegistryService } from "../../../core/services/external-registry.service";
import { FolderService } from "../../../core/services/folder.service";
import { ACTIVE_STATUSES, JobService, StagingJob, TERMINATE_STATUSES } from "../../../core/services/job.service";

export type PushMode = "local" | "external";

@Component({
  selector: "app-job-detail",
  imports: [],
  templateUrl: "./job-detail.component.html",
  styleUrl: "./job-detail.component.css",
})
export class JobDetailComponent {
  job = input<StagingJob>()

  private authService = inject(AuthService);
  private extRegistrySvc = inject(ExternalRegistryService)
  private folderSvc = inject(FolderService)
  jobSvc = inject(JobService)

  readonly ACTIVE_STATUSES = ACTIVE_STATUSES
  readonly TERMINATE_STATUSES = TERMINATE_STATUSES

  pushing = signal<string | null>(null);
  pushTargets = signal<Record<string, string>>({});
  pushModes = signal<Record<string, PushMode>>({});
  pushExtRegistryId = signal<Record<string, string>>({});

  readonly externalRegistries = computed<ExternalRegistry[]>(() => this.extRegistrySvc.externalRegistries())
  readonly isAdmin = computed(() => this.authService.currentUser()?.is_admin ?? false);
  readonly pushFolderOptions = computed(() => this.folderSvc.allowedPushFolders());

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
   * Compute the effective image name for a push to an external registry.
   *
   * When pushing to a saved external registry that has a declared username,
   * the source namespace/username prefix is replaced by that registry username.
   * The user's manual override (pushTargets img field) always takes precedence.
   *
   * Examples (no manual override, registry username = "myorg"):
   *   "library/nginx"  → "myorg/nginx"
   *   "someuser/myapp" → "myorg/myapp"
   *   "nginx"          → "myorg/nginx"
   *
   * @param job       The staging job.
   * @param rawImg    The raw source image name (job.image or user override).
   * @param username  The target registry declared username (may be empty).
   */
  private computeExternalImageName(
    job: StagingJob,
    rawImg: string,
    username: string,
  ): string {
    // If the user has explicitly typed an image name, never touch it
    const userOverriddenImg = this.getPushTarget(job, "img").trim();
    if (userOverriddenImg) return userOverriddenImg;

    if (!username) return rawImg;

    // Strip the leading namespace segment, keep only the bare image name
    const bareName = rawImg.includes("/")
      ? rawImg.split("/").slice(1).join("/")
      : rawImg;
    return `${username}/${bareName}`;
  }

  getImageNamePlaceholder(job: StagingJob): string {
    const mode = this.getPushMode(job);
    const rawImg = job.image.trim();

    if (mode === "local") return rawImg;

    const regId = this.getExtRegistryId(job);
    const reg = this.externalRegistries().find((r) => r.id === regId);
    const username = reg?.username ?? "";

    return this.computeExternalImageName(job, rawImg, username);
  }

  pushPreview(job: StagingJob): string {
    const mode = this.getPushMode(job);
    // Prefer explicit user input for folder, otherwise fall back to job.folder
    let folder = this.getPushTarget(job, "folder").trim();
    if (!folder && job.folder) {
      folder = job.folder;
    }

    const rawImg = (this.getPushTarget(job, "img") || job.image).trim();
    const tag = (this.getPushTarget(job, "tag") || job.tag).trim();

    if (mode === "local") {
      const path = folder ? `${folder}/${rawImg}` : rawImg;
      return `localhost:5000/${path}:${tag}`;
    }

    // ── External mode ──────────────────────────────────────────────────────────
    const regId = this.getExtRegistryId(job);
    const reg = this.externalRegistries().find((r) => r.id === regId);
    const host = reg
      ? reg.host
      : this.getPushTarget(job, "ext_host") || "<registry>";
    const username = reg?.username ?? "";

    const effectiveImg = this.computeExternalImageName(job, rawImg, username);
    const path = folder ? `${folder}/${effectiveImg}` : effectiveImg;
    return `${host}/${path}:${tag}`;
  }

  pushImage(job: StagingJob): void {
    this.pushing.set(job.job_id);

    const mode = this.getPushMode(job);
    const isExternal = mode === "external";
    const regId = this.getExtRegistryId(job) || null;

    this.jobSvc
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
          // Trigger an immediate refresh so the PUSHING status appears without delay
          this.jobSvc.loadJobs();
        },
        error: () => this.pushing.set(null),
      });
  }

  deleteJob(jobId: string): void {
    this.jobSvc.deleteJob(jobId).subscribe({
      next: () => this.jobSvc.loadJobs(),
    });
  }

  displayProgress(job: StagingJob): number {
    if (this.TERMINATE_STATUSES.has(job.status)) {
      return 100;
    }
    return job.progress;
  }

  allowRePush(job: StagingJob): void {
    this.jobSvc.reUpdateJob(job)
  }

  getStatusBadgeClass(status: string): string {
    const map: Record<string, string> = {
      pending: "badge bg-secondary-subtle text-secondary",
      pulling: "badge bg-info-subtle text-info",
      scan_skipped: "badge bg-secondary-subtle text-secondary",
      vuln_scanning: "badge bg-warning-subtle text-warning",
      scan_clean: "badge bg-success-subtle text-success",
      scan_vulnerable: "badge bg-danger text-white",
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

  getCveCount(job: StagingJob, severity: string): number {
    return job.vuln_result?.counts?.[severity] ?? 0;
  }

  sourceRegistryIcon(host: string | null | undefined): string {
    if (!host) return "🐳"; // Docker Hub
    if (host.includes("ghcr.io")) return "🐙";
    if (host.includes("quay.io")) return "🔴";
    if (host.includes("gcr.io") || host.includes("pkg.dev")) return "☁️";
    if (host.includes("amazonaws.com")) return "🟠";
    return "🏢";
  }

}
