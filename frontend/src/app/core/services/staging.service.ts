import { HttpClient } from "@angular/common/http";
import { inject, Injectable } from "@angular/core";
import { Observable } from "rxjs";

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
  // Overrides that were applied for this job (null = server default was used)
  vuln_scan_enabled_override: boolean | null;
  vuln_severities_override: string | null;
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
  // Full CVE list (present in registry_inside branch)
  vulnerabilities?: VulnerabilityEntry[];
  total?: number;
  scanned_at?: string;
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
  /** User-level overrides coming from Settings (null = use server default) */
  vuln_scan_enabled_override?: boolean | null;
  vuln_severities_override?: string | null;
}

export interface DanglingImage {
  id: string;
  repository: string;
  tag: string;
  size: string;
  created: string;
}

export interface DanglingImagesResult {
  images: DanglingImage[];
  count: number;
}

export interface OrphanTarballsResult {
  files: string[];
  count: number;
  total_size_bytes: number;
  total_size_human: string;
}

/** Active job statuses — used to sort running jobs to the top */
const ACTIVE_STATUSES: JobStatus[] = [
  "pending",
  "pulling",
  "scanning",
  "vuln_scanning",
  "pushing",
];

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

  pushImage(
    jobId: string,
    targetImage?: string,
    targetTag?: string,
  ): Observable<{ message: string; job_id: string }> {
    return this.http.post<{ message: string; job_id: string }>(
      `${this.BASE}/push`,
      {
        job_id: jobId,
        target_image: targetImage || null,
        target_tag: targetTag || null,
      },
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

  // ── Quick Actions: Dangling Images ────────────────────────────────────────

  getDanglingImages(): Observable<DanglingImagesResult> {
    return this.http.get<DanglingImagesResult>(`${this.BASE}/dangling-images`);
  }

  purgeDanglingImages(): Observable<{ message: string; output: string }> {
    return this.http.post<{ message: string; output: string }>(
      `${this.BASE}/dangling-images/purge`,
      {},
    );
  }

  // ── Quick Actions: Orphan Tarballs ────────────────────────────────────────

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

  /**
   * Sort jobs so that active ones (pending/pulling/scanning/pushing) always
   * appear at the top of the list, then by most recent (reverse insertion order).
   */
  static sortJobs(jobs: StagingJob[]): StagingJob[] {
    return [...jobs].sort((a, b) => {
      const aActive = ACTIVE_STATUSES.includes(a.status) ? 0 : 1;
      const bActive = ACTIVE_STATUSES.includes(b.status) ? 0 : 1;
      return aActive - bActive;
    });
  }
}
