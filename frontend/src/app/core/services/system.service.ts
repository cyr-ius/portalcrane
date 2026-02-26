// frontend/src/app/shared/services/system.service.ts
import { HttpClient } from "@angular/common/http";
import { Injectable, inject } from "@angular/core";
import { firstValueFrom } from "rxjs";

export interface ProcessStatus {
  name: string;
  running: boolean;
  state: string;
  pid?: number;
  uptime_seconds?: number;
  error?: string;
}

export interface TrivyDbInfo {
  last_update: string | null;
  next_update: string | null;
  version: number | null;
  up_to_date: boolean;
  error?: string;
}

export interface VulnerabilityEntry {
  id: string;
  package: string;
  installed_version: string;
  fixed_version: string | null;
  severity: string;
  title: string | null;
  description: string | null;
  cvss_score: number | null;
  target: string;
  type: string;
}

export interface ScanResult {
  success: boolean;
  image: string;
  scanned_at: string;
  summary: Record<string, number>;
  total: number;
  vulnerabilities: VulnerabilityEntry[];
  error?: string;
}

export interface GcResult {
  success: boolean;
  output: string;
  dry_run: boolean;
  return_code: number | null;
}

@Injectable({ providedIn: "root" })
export class SystemService {
  private http = inject(HttpClient);
  private readonly BASE = "/api/system";

  /** Returns the runtime status of all supervised processes. */
  async getProcessStatuses(): Promise<ProcessStatus[]> {
    return firstValueFrom(
      this.http.get<ProcessStatus[]>(`${this.BASE}/processes`),
    );
  }

  /** Returns Trivy vulnerability database metadata. */
  async getTrivyDbInfo(): Promise<TrivyDbInfo> {
    return firstValueFrom(this.http.get<TrivyDbInfo>(`${this.BASE}/trivy/db`));
  }

  /** Forces an immediate Trivy DB update. */
  async updateTrivyDb(): Promise<{ success: boolean; output: string }> {
    return firstValueFrom(
      this.http.post<{ success: boolean; output: string }>(
        `${this.BASE}/trivy/db/update`,
        {},
      ),
    );
  }

  /**
   * Scans a specific image from the local registry with Trivy.
   * @param image Full image reference, e.g. localhost:5000/myimage:latest
   * @param severity List of severity levels to filter
   * @param ignoreUnfixed Skip vulnerabilities without a known fix
   */
  async scanImage(
    image: string,
    severity: string[] = ["HIGH", "CRITICAL"],
    ignoreUnfixed: boolean = false,
  ): Promise<ScanResult> {
    const params = new URLSearchParams();
    params.set("image", image);
    severity.forEach((s) => params.append("severity", s));
    if (ignoreUnfixed) params.set("ignore_unfixed", "true");

    return firstValueFrom(
      this.http.get<ScanResult>(`${this.BASE}/trivy/scan?${params.toString()}`),
    );
  }

  /**
   * Triggers registry garbage collection.
   * @param dryRun Preview what would be deleted without actually deleting
   */
  async runGc(dryRun: boolean = false): Promise<GcResult> {
    return firstValueFrom(
      this.http.post<GcResult>(`${this.BASE}/gc?dry_run=${dryRun}`, {}),
    );
  }
}
