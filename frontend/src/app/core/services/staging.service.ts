import { HttpClient } from "@angular/common/http";
import { Injectable } from "@angular/core";
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
  clamav_enabled_override: boolean | null;
  vuln_scan_enabled_override: boolean | null;
  vuln_severities_override: string | null;
}

export interface VulnResult {
  enabled: boolean;
  blocked: boolean;
  severities: string[];
  counts: Record<string, number>;
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
  clamav_enabled_override?: boolean | null;
  vuln_scan_enabled_override?: boolean | null;
  vuln_severities_override?: string | null;
}

@Injectable({ providedIn: "root" })
export class StagingService {
  private readonly BASE = "/api/staging";

  constructor(private http: HttpClient) {}

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
      `${this.BASE}/search/dockerhub/tags`,
      { params: { image } },
    );
  }
}
