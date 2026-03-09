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
// sortJobs() and displayProgress() call these on every polling cycle (every 3s).
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

  /**
   * Tracks job IDs whose status has been locally overridden to "scan_clean"
   * by the "Push again" action. This set persists across polling cycles so
   * that the push panel remains visible until the user actually triggers a
   * new push (which transitions the backend status to "pushing" → "done").
   *
   * The override is cleared automatically in setJobs() once the backend
   * reports a status other than "done" for that job (i.e. a new push
   * pipeline has started).
   */
  private readonly _rePushOverrides = new Set<string>();

  /**
   * Replace the full job list with data from the backend.
   * Locally overridden re-push statuses are reapplied so that the push
   * panel stays visible across polling cycles.
   *
   * FIX #1 (audit): this is the only write path for the full list —
   * no risk of duplicates here.
   */
  setJobs(jobs: StagingJob[]): void {
    const merged = jobs.map((job) => {
      if (this._rePushOverrides.has(job.job_id)) {
        // Backend still shows "done": keep the local scan_clean override
        // so the push panel stays visible.
        if (job.status === "done") {
          return { ...job, status: "scan_clean" as const };
        }
        // User triggered a new push: backend moved on (pushing / done / failed).
        // Clear the override so we track the real status again.
        this._rePushOverrides.delete(job.job_id);
      }
      return job;
    });

    this._jobs.set(this.sortJobs(merged));
  }

  /**
   * Insert or update a single job without duplicating it.
   *
   * FIX #1 (audit): previous implementation used [job, ...jobs] which
   * prepended unconditionally, creating a duplicate when the job already
   * existed in the list.
   *
   * New logic:
   *   - job_id exists → replace in-place then re-sort.
   *   - job_id is new → prepend then re-sort.
   */
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

  /**
   * Locally override a job's status to "scan_clean" so the push panel
   * becomes visible again after a completed push ("Push again" button).
   *
   * The job ID is registered in _rePushOverrides so that setJobs()
   * reapplies the override on every polling cycle until the backend
   * reports a status transition away from "done" (new push started).
   */
  reUpdateJob(job: StagingJob): void {
    // Register override so polling does not revert it.
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
    // Also clear any pending re-push override for the deleted job.
    this._rePushOverrides.delete(jobId);
    return this.http.delete<{ message: string }>(`${this.BASE}/jobs/${jobId}`);
  }

  /**
   * Sort jobs: active jobs first, then by creation date descending (newest first).
   * Uses Set.has() for O(1) status checks on every polling cycle.
   */
  sortJobs(jobs: StagingJob[]): StagingJob[] {
    return [...jobs].sort((a, b) => {
      // Active jobs bubble to the top
      const aActive = ACTIVE_STATUSES.has(a.status) ? 0 : 1;
      const bActive = ACTIVE_STATUSES.has(b.status) ? 0 : 1;
      if (aActive !== bActive) return aActive - bActive;

      // Within the same group: newest first
      const aTime = a.created_at ? new Date(a.created_at).getTime() : 0;
      const bTime = b.created_at ? new Date(b.created_at).getTime() : 0;
      return bTime - aTime;
    });
  }
}
