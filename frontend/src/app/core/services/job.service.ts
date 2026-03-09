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

  /**
   * FIX #1 (flicker) + FIX #2 (rePush broken):
   *
   * `pushingJobId` is a service-level signal that tracks which job is
   * currently being submitted for push. Moving it out of JobDetailComponent
   * (where it was a local signal) solves both bugs:
   *
   * - FLICKER: The component was calling `pushing.set(null)` in the HTTP
   *   `next:` callback, which briefly re-enabled the Push button before
   *   the next polling cycle brought back `status: "pushing"` from the
   *   backend. Now the state is cleared only when the backend status
   *   transitions, not on HTTP response.
   *
   * - REPUSH BROKEN: Angular's @for re-creates JobDetailComponent when the
   *   job object reference changes (every 3 s polling cycle). A local signal
   *   is reset to its initial value on each re-creation, losing the pushing
   *   state and orphaning the HTTP subscribe. A service-level signal survives
   *   component re-creation.
   */
  private readonly _pushingJobId = signal<string | null>(null);
  readonly pushingJobId = this._pushingJobId.asReadonly();

  /**
   * Tracks job IDs whose status has been locally overridden to "scan_clean"
   * by the "Push again" action.
   */
  private readonly _rePushOverrides = new Set<string>();

  /**
   * Register a job as currently being pushed.
   * Called synchronously before the HTTP request so the UI transitions
   * atomically (no intermediate flash of the idle Push button).
   */
  startPushing(jobId: string): void {
    this._pushingJobId.set(jobId);
  }

  /**
   * Clear the pushing state for a given job.
   * Called by setJobs() when the backend status moves away from the
   * initial "pending" state (i.e. the backend has accepted the push),
   * or immediately on HTTP error.
   */
  clearPushing(jobId: string): void {
    if (this._pushingJobId() === jobId) {
      this._pushingJobId.set(null);
    }
  }

  /**
   * Replace the full job list with data from the backend.
   * - Reapplies re-push overrides so the push panel stays visible across polling cycles.
   * - Clears pushingJobId once the backend acknowledges the push (status !== "pending").
   */
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

      /**
       * FIX #1 — Clear pushingJobId when the backend confirms the push
       * pipeline has started (status moves from pending to any other state).
       * This is the single authoritative place to clear the pushing flag,
       * replacing the previous approach of clearing it in the HTTP next:
       * callback (which caused a brief idle-button flash).
       */
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

  /**
   * Insert or update a single job without duplicating it.
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

  /**
   * Sort jobs: active jobs first, then by creation date descending.
   */
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
}
