/**
 * Portalcrane - Staging Service
 * HTTP client for /api/staging endpoints.
 * Supports: pull pipeline (Docker Hub or any external registry), push
 * (local + external registry), Docker Hub search and tags.
 */
import { HttpClient } from "@angular/common/http";
import { inject, Injectable } from "@angular/core";
import { Observable } from "rxjs";
import { StagingJob } from "./job.service";

// ── Models ────────────────────────────────────────────────────────────────────

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

export interface OrphanOCIResult {
  dirs: string[];
  count: number;
  total_size_bytes: number;
  total_size_human: string;
}


// ── Service ───────────────────────────────────────────────────────────────────

@Injectable({ providedIn: "root" })
export class StagingService {
  private readonly BASE = "/api/staging";
  private readonly SYS_BASE = "/api/system";

  private http = inject(HttpClient);

  // ── Pull ──────────────────────────────────────────────────────────────────

  pullImage(options: PullOptions): Observable<StagingJob> {
    return this.http.post<StagingJob>(`${this.BASE}/pull`, options);
  }

  // ── Docker Hub search ─────────────────────────────────────────────────────

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

  // ── Orphan cleanup ────────────────────────────────────────────────────────

  getOrphanOci(): Observable<OrphanOCIResult> {
    return this.http.get<OrphanOCIResult>(`${this.SYS_BASE}/orphan-oci`);
  }

  purgeOrphanOci(): Observable<{ message: string; purged: string[] }> {
    return this.http.delete<{ message: string; purged: string[] }>(
      `${this.SYS_BASE}/orphan-oci`,
    );
  }

}
