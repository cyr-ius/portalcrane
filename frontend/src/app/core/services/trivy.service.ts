// frontend/src/app/shared/services/system.service.ts
import { HttpClient } from "@angular/common/http";
import { Injectable, computed, inject, signal } from "@angular/core";
import { firstValueFrom, tap } from "rxjs";
import { readBool, readStr } from "../helpers/storage";

export const TRIVY_SEVERITIES = [
  "CRITICAL",
  "HIGH",
  "MEDIUM",
  "LOW",
  "UNKNOWN",
] as const;
export type TrivySeverity = (typeof TRIVY_SEVERITIES)[number];

export const TRIVY_TIMEOUT_OPTIONS = [
  "1m",
  "3m",
  "5m",
  "10m",
  "15m",
  "30m",
] as const;
export type TrivyTimeoutOption = (typeof TRIVY_TIMEOUT_OPTIONS)[number];

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

export interface VulnConfig {
  vuln_scan_override: boolean;
  vuln_scan_enabled: boolean;
  vuln_scan_severities: string;
  vuln_ignore_unfixed: boolean;
  vuln_scan_timeout: string;
}

const KEYS = {
  VULN_OVERRIDE: "pc_vuln_override",
  VULN_ENABLED: "pc_vuln_enabled",
  VULN_SEVERITIES: "pc_vuln_severities",
  VULN_IGNORE_UNFIXED: "pc_vuln_ignore_unfixed",
  VULN_TIMEOUT: "pc_vuln_timeout",
};

@Injectable({ providedIn: "root" })
export class TrivyService {
  private http = inject(HttpClient);
  private readonly BASE = "/api/trivy";

  private _vulnConfig = signal<VulnConfig | null>(null);
  readonly vulnConfig = this._vulnConfig.asReadonly();

  private _vulnOverride = signal<boolean>(readBool(KEYS.VULN_OVERRIDE, false));
  readonly vulnOverride = this._vulnOverride.asReadonly();

  private _vulnEnabled = signal<boolean>(readBool(KEYS.VULN_ENABLED, false));
  readonly vulnEnabled = this._vulnEnabled.asReadonly();

  private _vulnIgnoreUnfixed = signal<boolean>(readBool(KEYS.VULN_IGNORE_UNFIXED, false));
  readonly vulnIgnoreUnfixed = this._vulnIgnoreUnfixed.asReadonly();

  private _vulnTimeout = signal<string>(readStr(KEYS.VULN_TIMEOUT, "5m"));
  readonly vulnTimeout = this._vulnTimeout.asReadonly();

  /** Selected Trivy severity levels. */
  private _vulnSeverities = signal<TrivySeverity[]>(
    readStr(KEYS.VULN_SEVERITIES, "CRITICAL,HIGH")
      .split(",")
      .map((s) => s.trim().toUpperCase())
      .filter((s): s is TrivySeverity =>
        (TRIVY_SEVERITIES as readonly string[]).includes(s),
      ),
  );
  readonly vulnSeverities = this._vulnSeverities.asReadonly();

  readonly vulnSeveritiesString = computed(() => this._vulnSeverities().join(","));


  loadConfig () {
    return this.http.get<VulnConfig>("/api/trivy/state").pipe(
      tap((cfg) => {
        this._vulnConfig.set(cfg)
        if (localStorage.getItem(KEYS.VULN_OVERRIDE) === null)
          this._vulnOverride.set(cfg.vuln_scan_override);
        if (localStorage.getItem(KEYS.VULN_ENABLED) === null)
          this._vulnEnabled.set(cfg.vuln_scan_enabled);
        if (localStorage.getItem(KEYS.VULN_SEVERITIES) === null) {
          const sev = cfg.vuln_scan_severities
            .split(",")
            .map((s) => s.trim().toUpperCase())
            .filter((s): s is TrivySeverity =>
              (TRIVY_SEVERITIES as readonly string[]).includes(s),
            );
          this._vulnSeverities.set(sev);
        }
        if (localStorage.getItem(KEYS.VULN_IGNORE_UNFIXED) === null)
          this._vulnIgnoreUnfixed.set(cfg.vuln_ignore_unfixed);
        if (localStorage.getItem(KEYS.VULN_TIMEOUT) === null)
          this._vulnTimeout.set(cfg.vuln_scan_timeout);
      }),
    );
  }

  setVulnIgnoreUnfixed(value: boolean) {
    this._vulnIgnoreUnfixed.set(value);
    localStorage.setItem(KEYS.VULN_IGNORE_UNFIXED, String(value));
  }

  setVulnEnabled(value: boolean) {
    this._vulnEnabled.set(value);
    localStorage.setItem(KEYS.VULN_ENABLED, String(value));
  }

  setVulnTimeout(value: string) {
    this._vulnTimeout.set(value);
    localStorage.setItem(KEYS.VULN_TIMEOUT, String(value));
  }

  setVulnOverride(value: boolean) {
    this._vulnOverride.set(value);
    localStorage.setItem(KEYS.VULN_OVERRIDE, String(value));
    if (!value) {
      // If disabling override, also reset related settings to server defaults
      const server = this._vulnConfig();
      if (server) {
        this.setVulnEnabled(server.vuln_scan_enabled);
        this._vulnSeverities.set(
          server.vuln_scan_severities
            .split(",")
            .map((s) => s.trim().toUpperCase())
            .filter((s): s is TrivySeverity =>
              (TRIVY_SEVERITIES as readonly string[]).includes(s),
            ),
        );
        this.setVulnIgnoreUnfixed(server.vuln_ignore_unfixed);
        this.setVulnTimeout(server.vuln_scan_timeout);
      }
    }
  }

  toggleVulnSeverity(sev: TrivySeverity) {
    const current = this._vulnSeverities();
    const next = current.includes(sev)
      ? current.filter((s) => s !== sev)
      : [...current, sev];
    if (next.length === 0) return; // never allow empty selection
    this._vulnSeverities.set(next);
    localStorage.setItem(KEYS.VULN_SEVERITIES, next.join(","));
  }

  /** Returns Trivy vulnerability database metadata. */
  async getTrivyDbInfo(): Promise<TrivyDbInfo> {
    return firstValueFrom(this.http.get<TrivyDbInfo>(`${this.BASE}/db`));
  }

  /** Forces an immediate Trivy DB update. */
  async updateTrivyDb(): Promise<{ success: boolean; output: string }> {
    return firstValueFrom(
      this.http.post<{ success: boolean; output: string }>(
        `${this.BASE}/db/update`,
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
      this.http.get<ScanResult>(`${this.BASE}/scan?${params.toString()}`),
    );
  }
}
