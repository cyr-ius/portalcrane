/**
 * Portalcrane - TransferService
 * HTTP client for /api/transfer endpoints.
 *
 * Manages image transfer jobs between any combination of registries
 * (local <-> local, local <-> external, external <-> external)
 * with integrated Trivy CVE scanning.
 *
 * This service replaces the Sync import/export feature.
 */
import { HttpClient } from "@angular/common/http";
import { inject, Injectable, signal } from "@angular/core";
import { Observable, Subscription, switchMap, timer } from "rxjs";

// ── Transfer status type ──────────────────────────────────────────────────────

export type TransferStatus =
  | "pending"
  | "pulling"
  | "scanning"
  | "scan_clean"
  | "scan_vulnerable"
  | "scan_skipped"
  | "pushing"
  | "done"
  | "failed";

// ── Active statuses (job is still running) ────────────────────────────────────

export const TRANSFER_ACTIVE_STATUSES = new Set<TransferStatus>([
  "pending",
  "pulling",
  "scanning",
  "pushing",
]);

// ── Terminal statuses (job is complete) ───────────────────────────────────────

export const TRANSFER_TERMINAL_STATUSES = new Set<TransferStatus>([
  "scan_clean",
  "scan_skipped",
  "scan_vulnerable",
  "done",
  "failed",
]);

// ── Interfaces ────────────────────────────────────────────────────────────────

export interface TransferImageRef {
  repository: string;
  tag: string;
}

export interface VulnerabilityEntry {
  id: string;
  package: string;
  installed_version: string;
  fixed_version: string | null;
  severity: string;
  title: string | null;
  cvss_score: number | null;
  target: string;
}

export interface VulnResult {
  enabled: boolean;
  blocked: boolean;
  severities: string[];
  counts: Record<string, number>;
  vulnerabilities?: VulnerabilityEntry[];
  total?: number;
}

/** A single transfer job as returned by the API. */
export interface TransferJob {
  job_id: string;
  status: TransferStatus;
  source_registry_id: string | null;
  dest_registry_id: string | null;
  repository: string;
  tag: string;
  dest_repository: string;
  dest_tag: string;
  progress: number;
  message: string;
  vuln_result: VulnResult | null;
  error: string | null;
  created_at: string;
}

/** Request payload to start transfer jobs. */
export interface TransferRequest {
  images: TransferImageRef[];
  source_registry_id: string | null;
  dest_registry_id: string | null;
  dest_folder?: string | null;
  dest_name_override?: string | null;
  dest_tag_override?: string | null;
  vuln_scan_enabled_override?: boolean | null;
  vuln_severities_override?: string | null;
}

/** Polling interval in milliseconds. */
const POLL_INTERVAL_MS = 2500;

@Injectable({ providedIn: "root" })
export class TransferService {
  private readonly BASE = "/api/transfer";
  private readonly http = inject(HttpClient);

  // ── Reactive state ─────────────────────────────────────────────────────────

  private readonly _jobs = signal<TransferJob[]>([]);
  readonly jobs = this._jobs.asReadonly();

  private _pollSub: Subscription | null = null;

  // ── Polling lifecycle ──────────────────────────────────────────────────────

  /** Start background polling for transfer jobs (idempotent). */
  startPolling(): void {
    if (this._pollSub) return;
    this._pollSub = timer(0, POLL_INTERVAL_MS)
      .pipe(switchMap(() => this.listJobs()))
      .subscribe((jobs) => this._jobs.set(jobs));
  }

  /** Stop polling. */
  stopPolling(): void {
    this._pollSub?.unsubscribe();
    this._pollSub = null;
  }

  /** Reset all state (called on logout). */
  clearState(): void {
    this.stopPolling();
    this._jobs.set([]);
  }

  // ── HTTP methods ───────────────────────────────────────────────────────────

  /** Start one or more transfer jobs. */
  startTransfer(
    request: TransferRequest,
  ): Observable<{ job_ids: string[]; count: number }> {
    return this.http.post<{ job_ids: string[]; count: number }>(
      `${this.BASE}`,
      request,
    );
  }

  /** List all transfer jobs visible to the current user. */
  listJobs(): Observable<TransferJob[]> {
    return this.http.get<TransferJob[]>(`${this.BASE}/jobs`);
  }

  /** Delete / cancel a transfer job. */
  deleteJob(jobId: string): Observable<void> {
    return this.http.delete<void>(`${this.BASE}/jobs/${jobId}`);
  }

  /** Load jobs once without starting the polling loop. */
  loadJobs(): void {
    this.listJobs().subscribe({ next: (jobs) => this._jobs.set(jobs) });
  }
}
