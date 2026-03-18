/**
 * Portalcrane - JobService
 *
 * Manages the staging pipeline job list as a reactive singleton.
 *
 * Session isolation fix:
 *   JobService is providedIn: 'root', meaning the _jobs signal persists
 *   across user sessions in the same browser tab. Without an explicit reset,
 *   a second user logging in would briefly see the previous user's jobs
 *   (the 200 ms before the first polling cycle).
 *
 *   clearState() resets all mutable state and is called by AuthService
 *   .clearSession() on every logout (local, OIDC, and session-expired 401).
 */
import { HttpClient } from "@angular/common/http";
import { inject, Injectable, signal } from "@angular/core";
import { Observable } from "rxjs";
import { VulnResult } from "./staging.service";

export type JobStatus =
  | "pending"
  | "pulling"
  | "scan_skipped"
  | "scan_clean"
  | "vuln_scanning"
  | "scan_vulnerable"
  | "pushing"
  | "done"
  | "failed";

export interface StagingJob {
  job_id: string;
  status: JobStatus;
  image: string;
  tag: string;
  progress: number;
  message: string;
  scan_result: string | null;
  vuln_result: VulnResult | null;
  target_image: string | null;
  target_tag: string | null;
  folder?: string | null;
  error: string | null;
  vuln_scan_enabled_override: boolean | null;
  vuln_severities_override: string | null;
  owner?: string;
  source_registry_host?: string | null;
  created_at?: string | null;
}

export interface PushOptions {
  job_id: string;
  target_image?: string | null;
  target_tag?: string | null;
  folder?: string | null;
  external_registry_id?: string | null;
  external_registry_host?: string | null;
  external_registry_username?: string | null;
  external_registry_password?: string | null;
}

// Use Set<JobStatus> for O(1) .has() lookups.
export const ACTIVE_STATUSES = new Set<JobStatus>([
  "pending",
  "pulling",
  "vuln_scanning",
  "pushing",
]);

export const TERMINATE_STATUSES = new Set<JobStatus>([
  "scan_clean",
  "scan_skipped",
  "done",
  "scan_vulnerable",
  "failed",
]);

@Injectable({ providedIn: "root" })
export class JobService {
  private readonly BASE = "/api/staging";
  private http = inject(HttpClient);

  private _jobs = signal<StagingJob[]>([]);
  readonly jobs = this._jobs.asReadonly();

  private readonly _pushingJobId = signal<string | null>(null);
  readonly pushingJobId = this._pushingJobId.asReadonly();

  private readonly _rePushOverrides = new Set<string>();

  clearState(): void {
    this._jobs.set([]);
    this._pushingJobId.set(null);
    this._rePushOverrides.clear();
  }

  startPushing(jobId: string): void {
    this._pushingJobId.set(jobId);
  }

  clearPushing(jobId: string): void {
    if (this._pushingJobId() === jobId) {
      this._pushingJobId.set(null);
    }
  }

  setJobs(jobs: StagingJob[]): void {
    const merged = jobs.map((job) => {
      if (this._rePushOverrides.has(job.job_id)) {
        if (job.status === "done") {
          // Backend still shows "done": keep the local scan_clean override.
          return { ...job, status: "scan_clean" as const };
        }
        // Backend moved on (pulling / vuln_scanning / pushing / failed):
        // the new pipeline has started — clear the override.
        this._rePushOverrides.delete(job.job_id);
      }

      if (
        this._pushingJobId() === job.job_id &&
        job.status !== "pending"
      ) {
        this._pushingJobId.set(null);
      }

      return job;
    });

    this._jobs.set(this.sortJobs(merged));
  }

  updateJob(job: StagingJob): void {
    this._jobs.update((jobs) => {
      const exists = jobs.some((j) => j.job_id === job.job_id);
      if (exists) {
        return this.sortJobs(
          jobs.map((j) => (j.job_id === job.job_id ? job : j)),
        );
      }
      return this.sortJobs([job, ...jobs]);
    });
  }

  reUpdateJob(job: StagingJob): void {
    this._rePushOverrides.add(job.job_id);

    this._jobs.update((jobs) =>
      jobs.map((j) =>
        j.job_id === job.job_id ? { ...j, status: "scan_clean" as const } : j,
      ),
    );
  }

  getJob(jobId: string): Observable<StagingJob> {
    return this.http.get<StagingJob>(`${this.BASE}/jobs/${jobId}`);
  }

  listJobs(): Observable<StagingJob[]> {
    return this.http.get<StagingJob[]>(`${this.BASE}/jobs`);
  }

  loadJobs(): void {
    this.listJobs().subscribe({
      next: (jobs) => this.setJobs(jobs),
    });
  }

  pushImage(
    options: PushOptions,
  ): Observable<{ message: string; job_id: string }> {
    return this.http.post<{ message: string; job_id: string }>(
      `${this.BASE}/push`,
      options,
    );
  }

  deleteJob(jobId: string): Observable<{ message: string }> {
    this._rePushOverrides.delete(jobId);
    // Also clear pushing state if this job was being pushed.
    this.clearPushing(jobId);
    return this.http.delete<{ message: string }>(`${this.BASE}/jobs/${jobId}`);
  }

  sortJobs(jobs: StagingJob[]): StagingJob[] {
    return [...jobs].sort((a, b) => {
      const aActive = ACTIVE_STATUSES.has(a.status) ? 0 : 1;
      const bActive = ACTIVE_STATUSES.has(b.status) ? 0 : 1;
      if (aActive !== bActive) return aActive - bActive;
      const aTime = a.created_at ? new Date(a.created_at).getTime() : 0;
      const bTime = b.created_at ? new Date(b.created_at).getTime() : 0;
      return bTime - aTime;
    });
  }

  clearRePushOverride(jobId: string): void {
    this._rePushOverrides.delete(jobId);
  }
}
