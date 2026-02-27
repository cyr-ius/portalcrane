/**
 * Portalcrane - Staging Service
 * HTTP client for /api/staging endpoints.
 * Updated to support folder prefix and external registry push.
 */
import { HttpClient } from "@angular/common/http";
import { inject, Injectable } from "@angular/core";
import { Observable } from "rxjs";

// ── Models ────────────────────────────────────────────────────────────────────

export type JobStatus =
  | "pending"
  | "pulling"
  | "scanning"
  | "scan_skipped"
  | "scan_clean"
  | "scan_infected"
  | "vuln_scanning"
  | "scan_vulnerable"
  | "pushing"
  | "done"
  | "failed";

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
  scanned_at?: string;
}

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
  error: string | null;
  vuln_scan_enabled_override: boolean | null;
  vuln_severities_override: string | null;
}

export interface DockerHubResult {
  name: string;
  description: string;
  star_count: number;
  pull_count: number;
  is_official: boolean;
  is_automated: boolean;
}

export interface PullOptions {
  image: string;
  tag: string;
  vuln_scan_enabled_override?: boolean | null;
  vuln_severities_override?: string | null;
}

/**
 * Push options sent to /api/staging/push.
 * When external_registry_id or external_registry_host is set
 * the backend routes the push to the external registry.
 */
export interface PushOptions {
  job_id: string;
  /** Optional rename (image name only, no host/folder). */
  target_image?: string | null;
  /** Optional retag. */
  target_tag?: string | null;
  /** Optional folder prefix, e.g. "infra" or "app/backend". */
  folder?: string | null;
  /** ID of a saved external registry — mutually exclusive with host. */
  external_registry_id?: string | null;
  /** Ad-hoc external registry host — used when not saved. */
  external_registry_host?: string | null;
  external_registry_username?: string | null;
  external_registry_password?: string | null;
}

export interface OrphanTarballsResult {
  files: string[];
  count: number;
  total_size_bytes: number;
  total_size_human: string;
}

/** Active job statuses — used to sort running jobs to the top. */
const ACTIVE_STATUSES: JobStatus[] = [
  "pending",
  "pulling",
  "scanning",
  "vuln_scanning",
  "pushing",
];

// ── Service ───────────────────────────────────────────────────────────────────

@Injectable({ providedIn: "root" })
export class StagingService {
  private readonly BASE = "/api/staging";
  private http = inject(HttpClient);

  pullImage(options: PullOptions): Observable<StagingJob> {
    return this.http.post<StagingJob>(`${this.BASE}/pull`, options);
  }

  getJob(jobId: string): Observable<StagingJob> {
    return this.http.get<StagingJob>(`${this.BASE}/jobs/${jobId}`);
  }

  listJobs(): Observable<StagingJob[]> {
    return this.http.get<StagingJob[]>(`${this.BASE}/jobs`);
  }

  /**
   * Push a staged image.
   * Supports folder prefix and external registry routing.
   */
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

  searchDockerHub(
    query: string,
    page = 1,
  ): Observable<{ results: DockerHubResult[]; count: number }> {
    return this.http.get<{ results: DockerHubResult[]; count: number }>(
      `${this.BASE}/search/dockerhub`,
      { params: { q: query, page } },
    );
  }

  getDockerHubTags(
    image: string,
  ): Observable<{ image: string; tags: string[] }> {
    return this.http.get<{ image: string; tags: string[] }>(
      `${this.BASE}/dockerhub/tags/${image}`,
    );
  }

  getOrphanTarballs(): Observable<OrphanTarballsResult> {
    return this.http.get<OrphanTarballsResult>(`${this.BASE}/orphan-tarballs`);
  }

  purgeOrphanTarballs(): Observable<{
    message: string;
    deleted: string[];
    freed_bytes: number;
    freed_human: string;
    errors: { file: string; error: string }[];
  }> {
    return this.http.post<{
      message: string;
      deleted: string[];
      freed_bytes: number;
      freed_human: string;
      errors: { file: string; error: string }[];
    }>(`${this.BASE}/orphan-tarballs/purge`, {});
  }

  /** Sort jobs so active ones appear at the top. */
  static sortJobs(jobs: StagingJob[]): StagingJob[] {
    return [...jobs].sort((a, b) => {
      const aActive = ACTIVE_STATUSES.includes(a.status) ? 0 : 1;
      const bActive = ACTIVE_STATUSES.includes(b.status) ? 0 : 1;
      return aActive - bActive;
    });
  }
}
