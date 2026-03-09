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
  job = input<StagingJob>();

  private authService = inject(AuthService);
  private extRegistrySvc = inject(ExternalRegistryService);
  private folderSvc = inject(FolderService);
  jobSvc = inject(JobService);

  readonly ACTIVE_STATUSES = ACTIVE_STATUSES;
  readonly TERMINATE_STATUSES = TERMINATE_STATUSES;

  // These signals are keyed by job_id so they survive if Angular reuses
  // this component instance for a different job (unlikely with track, but safe).
  pushTargets = signal<Record<string, string>>({});
  pushModes = signal<Record<string, PushMode>>({});
  pushExtRegistryId = signal<Record<string, string>>({});

  readonly externalRegistries = computed<ExternalRegistry[]>(() => this.extRegistrySvc.externalRegistries());
  readonly isAdmin = computed(() => this.authService.currentUser()?.is_admin ?? false);
  readonly pushFolderOptions = computed(() => this.folderSvc.allowedPushFolders());

  /**
   * Delegate pushing state to the service so it survives @for re-renders.
   * The template uses isPushing(job.job_id) instead of pushing() === job.job_id.
   */
  isPushing(jobId: string): boolean {
    return this.jobSvc.pushingJobId() === jobId;
  }

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
   * User override always takes precedence over the computed name.
   */
  private computeExternalImageName(
    job: StagingJob,
    rawImg: string,
    username: string,
  ): string {
    const userOverriddenImg = this.getPushTarget(job, "img").trim();
    if (userOverriddenImg) return userOverriddenImg;
    if (!username) return rawImg;
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
    /**
     * FIX #1 — No flicker:
     * startPushing() is called synchronously before the HTTP call.
     * The UI moves to spinner state in the same change-detection cycle
     * as the click event — no intermediate idle-button frame is rendered.
     *
     * We do NOT call clearPushing() in the next: callback.
     * setJobs() in the polling loop (jobs-list) clears it automatically
     * once the backend status moves away from "pending".
     *
     * FIX #2 — rePush works:
     * Because pushingJobId lives in the service, it is not reset when
     * Angular re-creates this component during a @for re-render triggered
     * by the polling cycle. The spinner state is preserved across re-renders.
     */
    this.jobSvc.startPushing(job.job_id);

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
          // Intentionally empty: clearPushing() is handled by setJobs()
          // in the polling cycle, not here, to avoid the flicker.
        },
        error: () => {
          // On HTTP error: clear immediately so the button is re-enabled.
          this.jobSvc.clearPushing(job.job_id);
        },
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
    this.jobSvc.reUpdateJob(job);
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
    if (!host) return "🐳";
    if (host.includes("ghcr.io")) return "🐙";
    if (host.includes("quay.io")) return "🔴";
    if (host.includes("gcr.io") || host.includes("pkg.dev")) return "☁️";
    if (host.includes("amazonaws.com")) return "🟠";
    return "🏢";
  }
}
