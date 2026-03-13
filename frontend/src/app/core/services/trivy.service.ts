// frontend/src/app/core/services/trivy.service.ts
import { HttpClient } from "@angular/common/http";
import { computed, inject, Injectable, signal } from "@angular/core";
import { firstValueFrom, Observable, tap } from "rxjs";

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

/**
 * Vuln configuration returned by GET /api/trivy/state.
 *
 * vuln_scan_override = true  → a persisted admin override is active server-side.
 * vuln_scan_override = false → values come from env vars.
 */
export interface VulnConfig {
  vuln_scan_override: boolean;
  vuln_scan_enabled: boolean;
  vuln_scan_severities: string;
  vuln_ignore_unfixed: boolean;
  vuln_scan_timeout: string;
}

/** Body sent to PUT /api/trivy/override. */
interface VulnOverridePayload {
  vuln_scan_enabled: boolean;
  vuln_scan_severities: string;
  vuln_ignore_unfixed: boolean;
  vuln_scan_timeout: string;
}

// ── Legacy localStorage cleanup ───────────────────────────────────────────────
// These keys were previously used to store overrides in the browser.
// They are cleaned up on first load so they no longer shadow the server config.
const LEGACY_LS_KEYS = [
  "pc_vuln_override",
  "pc_vuln_enabled",
  "pc_vuln_severities",
  "pc_vuln_ignore_unfixed",
  "pc_vuln_timeout",
];

@Injectable({ providedIn: "root" })
export class TrivyService {
  private http = inject(HttpClient);
  private readonly BASE = "/api/trivy";

  // ── Reactive state (driven entirely by the server response) ───────────────

  private _vulnConfig = signal<VulnConfig | null>(null);
  readonly vulnConfig = this._vulnConfig.asReadonly();

  /** True when a persisted admin override is active on the server. */
  private _vulnOverride = signal<boolean>(false);
  readonly vulnOverride = this._vulnOverride.asReadonly();

  private _vulnEnabled = signal<boolean>(false);
  readonly vulnEnabled = this._vulnEnabled.asReadonly();

  private _vulnIgnoreUnfixed = signal<boolean>(false);
  readonly vulnIgnoreUnfixed = this._vulnIgnoreUnfixed.asReadonly();

  private _vulnTimeout = signal<string>("5m");
  readonly vulnTimeout = this._vulnTimeout.asReadonly();

  private _vulnSeverities = signal<TrivySeverity[]>(["CRITICAL", "HIGH"]);
  readonly vulnSeverities = this._vulnSeverities.asReadonly();

  readonly vulnSeveritiesString = computed(() =>
    this._vulnSeverities().join(",")
  );

  /** True while a PUT/DELETE /override request is in-flight. */
  readonly saving = signal<boolean>(false);
  /** Set to true for 3 s after a successful save. */
  readonly saved = signal<boolean>(false);
  /** Holds the last save error message, or null. */
  readonly saveError = signal<string | null>(null);

  // ── Config loading ────────────────────────────────────────────────────────

  /**
   * Load the effective vuln configuration from the server.
   *
   * Called by the app initialiser (layout / app init) so that ALL users
   * — admin and non-admin alike — get the correct config on startup.
   *
   * Purges legacy localStorage keys on first call so they no longer
   * interfere with the server-side config.
   */
  loadConfig(): Observable<VulnConfig> {
    // Clean up legacy localStorage keys from the old browser-only override
    LEGACY_LS_KEYS.forEach((k) => localStorage.removeItem(k));

    return this.http.get<VulnConfig>(`${this.BASE}/state`).pipe(
      tap((cfg) => this._applyConfig(cfg))
    );
  }

  // ── Admin override setters (write-through to server) ─────────────────────

  /**
   * Toggle the global override on or off.
   *
   * - Enabling  → PUT  /api/trivy/override  (persists current values)
   * - Disabling → DELETE /api/trivy/override (reverts to env vars)
   *
   * Admin only — the backend enforces this with require_admin (403 for others).
   */
  setVulnOverride(value: boolean): void {
    if (value) {
      // Enable override: persist the current in-memory values
      this._saveOverride();
    } else {
      // Disable override: delete the persisted file
      this._deleteOverride();
    }
  }

  setVulnEnabled(value: boolean): void {
    this._vulnEnabled.set(value);
    if (this._vulnOverride()) {
      this._saveOverride();
    }
  }

  setVulnIgnoreUnfixed(value: boolean): void {
    this._vulnIgnoreUnfixed.set(value);
    if (this._vulnOverride()) {
      this._saveOverride();
    }
  }

  setVulnTimeout(value: string): void {
    this._vulnTimeout.set(value);
    if (this._vulnOverride()) {
      this._saveOverride();
    }
  }

  toggleVulnSeverity(sev: TrivySeverity): void {
    const current = this._vulnSeverities();
    const next = current.includes(sev)
      ? current.filter((s) => s !== sev)
      : [...current, sev];
    if (next.length === 0) return; // never allow empty selection
    this._vulnSeverities.set(next);
    if (this._vulnOverride()) {
      this._saveOverride();
    }
  }

  // ── Private helpers ───────────────────────────────────────────────────────

  /** Apply a VulnConfig response to all reactive signals. */
  private _applyConfig(cfg: VulnConfig): void {
    this._vulnConfig.set(cfg);
    this._vulnOverride.set(cfg.vuln_scan_override);
    this._vulnEnabled.set(cfg.vuln_scan_enabled);
    this._vulnIgnoreUnfixed.set(cfg.vuln_ignore_unfixed);
    this._vulnTimeout.set(cfg.vuln_scan_timeout);
    this._vulnSeverities.set(
      cfg.vuln_scan_severities
        .split(",")
        .map((s) => s.trim().toUpperCase())
        .filter((s): s is TrivySeverity =>
          (TRIVY_SEVERITIES as readonly string[]).includes(s)
        )
    );
  }

  /** Build the override payload from the current in-memory signal values. */
  private _buildPayload(): VulnOverridePayload {
    return {
      vuln_scan_enabled: this._vulnEnabled(),
      vuln_scan_severities: this._vulnSeverities().join(","),
      vuln_ignore_unfixed: this._vulnIgnoreUnfixed(),
      vuln_scan_timeout: this._vulnTimeout(),
    };
  }

  /** PUT /api/trivy/override — persist current values as global override. */
  private _saveOverride(): void {
    this.saving.set(true);
    this.saveError.set(null);
    this.http
      .put<VulnConfig>(`${this.BASE}/override`, this._buildPayload())
      .subscribe({
        next: (cfg) => {
          this._applyConfig(cfg);
          this.saving.set(false);
          this.saved.set(true);
          setTimeout(() => this.saved.set(false), 3000);
        },
        error: (err) => {
          this.saving.set(false);
          this.saveError.set(
            err?.error?.detail ?? "Failed to save override"
          );
        },
      });
  }

  /** DELETE /api/trivy/override — remove global override, revert to env vars. */
  private _deleteOverride(): void {
    this.saving.set(true);
    this.saveError.set(null);
    this.http.delete<VulnConfig>(`${this.BASE}/override`).subscribe({
      next: (cfg) => {
        this._applyConfig(cfg);
        this.saving.set(false);
        this.saved.set(true);
        setTimeout(() => this.saved.set(false), 3000);
      },
      error: (err) => {
        this.saving.set(false);
        this.saveError.set(
          err?.error?.detail ?? "Failed to remove override"
        );
      },
    });
  }

  // ── Trivy DB helpers ──────────────────────────────────────────────────────

  /** Returns Trivy vulnerability database metadata. */
  async getTrivyDbInfo(): Promise<TrivyDbInfo> {
    return firstValueFrom(this.http.get<TrivyDbInfo>(`${this.BASE}/db`));
  }

  /** Forces an immediate Trivy DB update. */
  async updateTrivyDb(): Promise<{ success: boolean; output: string }> {
    return firstValueFrom(
      this.http.post<{ success: boolean; output: string }>(
        `${this.BASE}/db/update`,
        {}
      )
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
    ignoreUnfixed: boolean = false
  ): Promise<ScanResult> {
    const params = new URLSearchParams();
    params.set("image", image);
    severity.forEach((s) => params.append("severity", s));
    if (ignoreUnfixed) params.set("ignore_unfixed", "true");

    return firstValueFrom(
      this.http.get<ScanResult>(`${this.BASE}/scan?${params.toString()}`)
    );
  }
}
