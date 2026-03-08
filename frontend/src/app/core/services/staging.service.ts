/**
 * Portalcrane - Staging Service
 * HTTP client for /api/staging endpoints.
 * Supports: pull pipeline (Docker Hub or any external registry), push
 * (local + external registry), Docker Hub search and tags.
 *
 * Changes:
 *  - PullOptions now includes optional source registry fields:
 *      source_registry_id, source_registry_host, source_registry_username,
 *      source_registry_password.
 *  - StagingJob interface now includes source_registry_host for display.
 *  - Added optional folder property to StagingJob and PushOptions; backend
 *    now respects the folder when pushing to the local registry.
 */
import { HttpClient } from "@angular/common/http";
import { inject, Injectable } from "@angular/core";
import { Observable } from "rxjs";

// ── Models ────────────────────────────────────────────────────────────────────

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
  folder?: string | null;
  error: string | null;
  vuln_scan_enabled_override: boolean | null;
  vuln_severities_override: string | null;
  owner?: string;
  source_registry_host?: string | null;
  created_at?: string | null;
}

export interface DockerHubResult {
  name: string;
  description: string;
  star_count: number;
  pull_count: number;
  is_official: boolean;
  is_automated: boolean;
}

/**
 * Pull options sent to POST /api/staging/pull.
 *
 * Source resolution order (backend):
 *   1. source_registry_id  → saved external registry (host + creds looked up server-side)
 *   2. source_registry_host → ad-hoc registry with optional credentials
 *   3. (default)            → Docker Hub using the user's saved Hub credentials
 */
export interface PullOptions {
  image: string;
  tag: string;

  // ── Source registry (optional) ───────────────────────────────────────────
  source_registry_id?: string | null;
  source_registry_host?: string | null;
  source_registry_username?: string | null;
  source_registry_password?: string | null;

  // ── Vulnerability scan overrides ─────────────────────────────────────────
  vuln_scan_enabled_override?: boolean | null;
  vuln_severities_override?: string | null;
}

/**
 * Push options sent to /api/staging/push.
 * When external_registry_id or external_registry_host is provided,
 * the backend routes the push to the external registry.
 */
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

export interface OrphanOCIResult {
  dirs: string[];
  count: number;
  total_size_bytes: number;
  total_size_human: string;
}

/** Active job statuses — used to sort running jobs to the top. */
export const ACTIVE_STATUSES: JobStatus[] = [
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

  // ── Pull ──────────────────────────────────────────────────────────────────

  pullImage(options: PullOptions): Observable<StagingJob> {
    return this.http.post<StagingJob>(`${this.BASE}/pull`, options);
  }

  // ── Jobs ──────────────────────────────────────────────────────────────────

  getJob(jobId: string): Observable<StagingJob> {
    return this.http.get<StagingJob>(`${this.BASE}/jobs/${jobId}`);
  }

  listJobs(): Observable<StagingJob[]> {
    return this.http.get<StagingJob[]>(`${this.BASE}/jobs`);
  }

  // ── Push ──────────────────────────────────────────────────────────────────

  /**
   * Push a staged image to the local or an external registry.
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

  // ── Docker Hub search ─────────────────────────────────────────────────────

  /**
   * Search Docker Hub for images matching the given query.
   * The backend uses the authenticated user's Hub credentials when configured.
   * Calls GET /api/staging/search/dockerhub?q=<query>&page=<page>
   */
  searchDockerHub(
    query: string,
    page = 1,
  ): Observable<{ results: DockerHubResult[]; count: number }> {
    return this.http.get<{ results: DockerHubResult[]; count: number }>(
      `${this.BASE}/search/dockerhub`,
      { params: { q: query, page } },
    );
  }

  /**
   * Fetch available tags for a Docker Hub image.
   * The backend uses the authenticated user's Hub credentials when configured.
   * Calls GET /api/staging/dockerhub/tags/<image>
   */
  getDockerHubTags(
    image: string,
  ): Observable<{ image: string; tags: string[] }> {
    return this.http.get<{ image: string; tags: string[] }>(
      `${this.BASE}/dockerhub/tags/${image}`,
    );
  }

  // ── Orphan cleanup ────────────────────────────────────────────────────────

  getOrphanOci(): Observable<OrphanOCIResult> {
    return this.http.get<OrphanOCIResult>(`${this.BASE}/orphan-oci`);
  }

  purgeOrphanOci(): Observable<{ message: string; purged: string[] }> {
    return this.http.delete<{ message: string; purged: string[] }>(
      `${this.BASE}/orphan-oci`,
    );
  }

  getDanglingImages(): Observable<{
    images: {
      id: string;
      repository: string;
      tag: string;
      size: string;
      created: string;
    }[];
    count: number;
  }> {
    return this.http.get<{
      images: {
        id: string;
        repository: string;
        tag: string;
        size: string;
        created: string;
      }[];
      count: number;
    }>(`${this.BASE}/dangling-images`);
  }

  purgeDanglingImages(): Observable<{ message: string; output: string }> {
    return this.http.post<{ message: string; output: string }>(
      `${this.BASE}/dangling-images/purge`,
      {},
    );
  }

  // ── Utilities ─────────────────────────────────────────────────────────────

  /** Sort jobs so active ones appear at the top, then preserve backend order. */
   static sortJobs(jobs: StagingJob[]): StagingJob[] {
     return [...jobs].sort((a, b) => {
       // Active jobs bubble to the top
       const aActive = ACTIVE_STATUSES.includes(a.status) ? 0 : 1;
       const bActive = ACTIVE_STATUSES.includes(b.status) ? 0 : 1;
       if (aActive !== bActive) return aActive - bActive;

       // Within the same group: newest first
       const aTime = a.created_at ? new Date(a.created_at).getTime() : 0;
       const bTime = b.created_at ? new Date(b.created_at).getTime() : 0;
       return bTime - aTime;
     });
   }
}
