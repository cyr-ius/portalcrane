import { HttpClient } from "@angular/common/http";
import { computed, Injectable, signal } from "@angular/core";
import { tap } from "rxjs";

export interface PublicConfig {
  advanced_mode: boolean;
  clamav_enabled: boolean;
  clamav_host: string;
  clamav_port: number;
  vuln_scan_enabled: boolean;
  vuln_scan_severities: string;
  vuln_ignore_unfixed: boolean;
  vuln_scan_timeout: string;
}

export interface ClamAVStatus {
  enabled: boolean;
  reachable: boolean;
  host: string;
  port: number;
  message: string;
}

/** All severity levels supported by Trivy, in display order */
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

const KEYS = {
  ADVANCED_MODE: "pc_advanced_mode",
  CLAMAV_ENABLED: "pc_clamav_enabled",
  VULN_ENABLED: "pc_vuln_enabled",
  VULN_SEVERITIES: "pc_vuln_severities",
  VULN_IGNORE_UNFIXED: "pc_vuln_ignore_unfixed",
  VULN_TIMEOUT: "pc_vuln_timeout",
};

function readBool(key: string, fallback: boolean): boolean {
  const v = localStorage.getItem(key);
  return v === null ? fallback : v === "true";
}

function readStr(key: string, fallback: string): string {
  return localStorage.getItem(key) ?? fallback;
}

@Injectable({ providedIn: "root" })
export class AppConfigService {
  /** Server-side defaults loaded at startup (read-only reference). */
  private _serverConfig = signal<PublicConfig | null>(null);
  readonly serverConfig = this._serverConfig.asReadonly();

  // ── User preferences (persisted in localStorage) ──────────────────────────

  private _advancedMode = signal<boolean>(
    localStorage.getItem(KEYS.ADVANCED_MODE) !== null
      ? localStorage.getItem(KEYS.ADVANCED_MODE) === "true"
      : false,
  );
  readonly advancedMode = this._advancedMode.asReadonly();

  /** Whether the user has enabled ClamAV scanning (local override). */
  private _clamavEnabled = signal<boolean>(readBool(KEYS.CLAMAV_ENABLED, true));
  readonly clamavEnabled = this._clamavEnabled.asReadonly();

  /** Whether the user has enabled Trivy CVE scanning (local override). */
  private _vulnEnabled = signal<boolean>(readBool(KEYS.VULN_ENABLED, false));
  readonly vulnEnabled = this._vulnEnabled.asReadonly();

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

  /** Whether to ignore unfixed CVEs. */
  private _vulnIgnoreUnfixed = signal<boolean>(
    readBool(KEYS.VULN_IGNORE_UNFIXED, false),
  );
  readonly vulnIgnoreUnfixed = this._vulnIgnoreUnfixed.asReadonly();

  /** Timeout for Trivy scans (e.g. "5m"). */
  private _vulnTimeout = signal<string>(readStr(KEYS.VULN_TIMEOUT, "5m"));
  readonly vulnTimeout = this._vulnTimeout.asReadonly();

  /** Comma-separated severities string ready for the API. */
  readonly vulnSeveritiesString = computed(() =>
    this._vulnSeverities().join(","),
  );

  constructor(private http: HttpClient) {}

  // ── Bootstrap ─────────────────────────────────────────────────────────────

  loadConfig() {
    return this.http.get<PublicConfig>("/api/config/public").pipe(
      tap((cfg) => {
        this._serverConfig.set(cfg);
        // Apply server defaults only when no local override exists yet
        if (localStorage.getItem(KEYS.ADVANCED_MODE) === null)
          this._advancedMode.set(cfg.advanced_mode);
        if (localStorage.getItem(KEYS.CLAMAV_ENABLED) === null)
          this._clamavEnabled.set(cfg.clamav_enabled);
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

  // ── Setters ───────────────────────────────────────────────────────────────

  setAdvancedMode(value: boolean) {
    this._advancedMode.set(value);
    localStorage.setItem(KEYS.ADVANCED_MODE, String(value));
  }

  setClamavEnabled(value: boolean) {
    this._clamavEnabled.set(value);
    localStorage.setItem(KEYS.CLAMAV_ENABLED, String(value));
  }

  setVulnEnabled(value: boolean) {
    this._vulnEnabled.set(value);
    localStorage.setItem(KEYS.VULN_ENABLED, String(value));
  }

  /**
   * Toggle a Trivy severity level on/off.
   * At least one severity must always remain selected.
   */
  toggleVulnSeverity(sev: TrivySeverity) {
    const current = this._vulnSeverities();
    const next = current.includes(sev)
      ? current.filter((s) => s !== sev)
      : [...current, sev];
    if (next.length === 0) return; // never allow empty selection
    this._vulnSeverities.set(next);
    localStorage.setItem(KEYS.VULN_SEVERITIES, next.join(","));
  }

  setVulnIgnoreUnfixed(value: boolean) {
    this._vulnIgnoreUnfixed.set(value);
    localStorage.setItem(KEYS.VULN_IGNORE_UNFIXED, String(value));
  }

  setVulnTimeout(value: string) {
    this._vulnTimeout.set(value);
    localStorage.setItem(KEYS.VULN_TIMEOUT, String(value));
  }

  // ── ClamAV live probe ─────────────────────────────────────────────────────

  getClamAVStatus() {
    return this.http.get<ClamAVStatus>("/api/staging/clamav/status");
  }
}
