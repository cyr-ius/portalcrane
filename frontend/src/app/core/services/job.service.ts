/**
 * Portalcrane - JobService
 *
 * Manages the staging pipeline job list as a reactive singleton.
 *
 * Design goals:
 *   1. The job list persists across route changes (no disappearing on re-entry).
 *   2. Returning to the Staging page always shows up-to-date statuses immediately.
 *   3. A single HTTP subscription is active at any time — no race conditions
 *      between concurrent requests that could cause the list to flash empty.
 *
 * Implementation:
 *   A BehaviorSubject (_trigger$) drives a single switchMap polling chain.
 *   - startPolling()  : creates the chain if not yet active (idempotent).
 *   - triggerRefresh(): emits on _trigger$ to restart the timer from 0,
 *                       giving an immediate fetch without a second subscription.
 *   - stopPolling()   : unsubscribes and resets for the next session.
 *   - clearState()    : called by AuthService on logout.
 */
import { HttpClient } from "@angular/common/http";
import { inject, Injectable, signal } from "@angular/core";
import {
  BehaviorSubject,
  Observable,
  Subscription,
  switchMap,
  timer,
} from "rxjs";
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

/** Use Set<JobStatus> for O(1) .has() lookups. */
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

/** Background polling interval in milliseconds. */
const POLL_INTERVAL_MS = 3000;

@Injectable({ providedIn: "root" })
export class JobService {
  private readonly BASE = "/api/staging";
  private http = inject(HttpClient);

  private _jobs = signal<StagingJob[]>([]);
  readonly jobs = this._jobs.asReadonly();

  private readonly _pushingJobId = signal<string | null>(null);
  readonly pushingJobId = this._pushingJobId.asReadonly();

  private readonly _rePushOverrides = new Set<string>();

  /**
   * Emitting on this subject restarts the timer(0, POLL_INTERVAL_MS) chain,
   * which triggers an immediate HTTP fetch followed by periodic polling.
   * Using switchMap ensures the previous timer is cancelled before starting
   * a new one — no concurrent requests, no race conditions.
   */
  private readonly _trigger$ = new BehaviorSubject<void>(undefined);

  /** Single active polling subscription. */
  private _pollingSub: Subscription | null = null;

  // ── Polling lifecycle ──────────────────────────────────────────────────────

  /**
   * Start the background polling loop.
   * Idempotent: does nothing when polling is already active.
   *
   * The chain is: _trigger$ → switchMap(timer(0, 3000)) → listJobs()
   * An immediate fetch happens as soon as startPolling() is first called
   * because BehaviorSubject replays its current value on subscription.
   */
  startPolling(): void {
    if (this._pollingSub) return;

    this._pollingSub = this._trigger$
      .pipe(switchMap(() => timer(0, POLL_INTERVAL_MS)))
      .pipe(switchMap(() => this.listJobs()))
      .subscribe((jobs) => this.setJobs(jobs));
  }

  /**
   * Restart the polling timer from zero, causing an immediate fetch.
   *
   * Unlike having two concurrent subscriptions (which caused race conditions),
   * emitting on _trigger$ uses switchMap to cancel the previous timer and
   * restart it — a single fetch happens immediately, then every 3 seconds.
   *
   * Call this when the Staging page becomes visible so statuses are always
   * fresh on entry, regardless of where we were in the polling cycle.
   */
  triggerRefresh(): void {
    this._trigger$.next();
  }

  /**
   * Stop the background polling loop and reset the trigger subject.
   * Called by clearState() on logout.
   */
  stopPolling(): void {
    this._pollingSub?.unsubscribe();
    this._pollingSub = null;
  }

  // ── Session isolation ──────────────────────────────────────────────────────

  /**
   * Reset all mutable state and stop polling.
   * Called by AuthService.clearSession() on every logout so that a subsequent
   * login (same browser tab, different user) starts with a clean slate.
   */
  clearState(): void {
    this.stopPolling();
    this._jobs.set([]);
    this._pushingJobId.set(null);
    this._rePushOverrides.clear();
  }

  // ── Push state helpers ─────────────────────────────────────────────────────

  startPushing(jobId: string): void {
    this._pushingJobId.set(jobId);
  }

  clearPushing(jobId: string): void {
    if (this._pushingJobId() === jobId) {
      this._pushingJobId.set(null);
    }
  }

  // ── Job list management ────────────────────────────────────────────────────

  /**
   * Merge a fresh job list from the backend into the local signal.
   *
   * Per-job rules applied during merge:
   *   1. Re-push override: keep showing scan_clean while the backend still
   *      reports done. Clear the override once the backend advances.
   *   2. Pushing state: clear _pushingJobId once the backend moves the job
   *      away from pending so the spinner on the Push button stops.
   */
  setJobs(jobs: StagingJob[]): void {
    const merged = jobs.map((job) => {
      // Rule 1: re-push override
      if (this._rePushOverrides.has(job.job_id)) {
        if (job.status === "done") {
          return { ...job, status: "scan_clean" as const };
        }
        this._rePushOverrides.delete(job.job_id);
      }

      // Rule 2: pushing state cleanup
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
   * Insert or update a single job in the local signal.
   * Used when a new pull is triggered — adds the job immediately without
   * waiting for the next polling tick.
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
   * Mark a completed job as scan_clean so the push panel reappears.
   * The _rePushOverrides set ensures setJobs() preserves this local status
   * until the backend confirms a new pipeline has started.
   */
  reUpdateJob(job: StagingJob): void {
    this._rePushOverrides.add(job.job_id);

    this._jobs.update((jobs) =>
      jobs.map((j) =>
        j.job_id === job.job_id ? { ...j, status: "scan_clean" as const } : j,
      ),
    );
  }

  // ── HTTP methods ───────────────────────────────────────────────────────────

  getJob(jobId: string): Observable<StagingJob> {
    return this.http.get<StagingJob>(`${this.BASE}/jobs/${jobId}`);
  }

  listJobs(): Observable<StagingJob[]> {
    return this.http.get<StagingJob[]>(`${this.BASE}/jobs`);
  }

  /** One-shot load without starting the polling loop. */
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
    this.clearPushing(jobId);
    return this.http.delete<{ message: string }>(`${this.BASE}/jobs/${jobId}`);
  }

  // ── Utility ────────────────────────────────────────────────────────────────

  /** Sort jobs: active statuses first, then newest first by creation timestamp. */
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
