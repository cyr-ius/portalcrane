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

// FIX #4: Use Set<JobStatus> instead of Array for O(1) .has() lookups.
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

  setJobs(jobs: StagingJob[]) {
    this._jobs.set(this.sortJobs(jobs));
  }

  updateJob(job: StagingJob) {
    this._jobs.update((jobs) => this.sortJobs([job, ...jobs]));
  }

  reUpdateJob(job: StagingJob) {
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

  loadJobs() {
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
    return this.http.delete<{ message: string }>(`${this.BASE}/jobs/${jobId}`);
  }

  sortJobs(jobs: StagingJob[]): StagingJob[] {
    return [...jobs].sort((a, b) => {
      // Active jobs bubble to the top — Set.has() is O(1)
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
