import { Injectable } from "@angular/core";
import { HttpClient } from "@angular/common/http";
import { Observable } from "rxjs";
import { VulnConfig } from "./vuln-config.service";

export type JobStatus =
  | "pending"
  | "pulling"
  | "scanning"
  | "vuln_scanning"
  | "scan_clean"
  | "scan_infected"
  | "scan_vulnerable"
  | "pushing"
  | "done"
  | "failed";

export interface VulnResult {
  enabled: boolean;
  blocked: boolean;
  severities: string[];
  counts: {
    CRITICAL: number;
    HIGH: number;
    MEDIUM: number;
    LOW: number;
    UNKNOWN: number;
  };
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
}

export interface DockerHubResult {
  name: string;
  description: string;
  star_count: number;
  pull_count: number;
  is_official: boolean;
  is_automated: boolean;
}

@Injectable({ providedIn: "root" })
export class StagingService {
  private readonly BASE = "/api/staging";

  constructor(private http: HttpClient) {}

  pullImage(
    image: string,
    tag: string,
    vulnConfig?: VulnConfig | null,
  ): Observable<StagingJob> {
    return this.http.post<StagingJob>(`${this.BASE}/pull`, {
      image,
      tag,
      ...(vulnConfig !== undefined && vulnConfig !== null
        ? {
            vuln_scan_enabled: vulnConfig.enabled,
            vuln_scan_severities: vulnConfig.severities,
            vuln_ignore_unfixed: vulnConfig.ignore_unfixed,
            vuln_scan_timeout: vulnConfig.timeout,
          }
        : {}),
    });
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
