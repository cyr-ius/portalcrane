import { Injectable, signal, inject } from "@angular/core";
import { HttpClient } from "@angular/common/http";
import { tap } from "rxjs/operators";

export const SEVERITIES = [
  "CRITICAL",
  "HIGH",
  "MEDIUM",
  "LOW",
  "UNKNOWN",
] as const;
export type Severity = (typeof SEVERITIES)[number];

export interface VulnConfig {
  enabled: boolean;
  severities: Severity[];
  ignore_unfixed: boolean;
  timeout: string;
}

const STORAGE_KEY = "portalcrane:vuln_config";

@Injectable({ providedIn: "root" })
export class VulnConfigService {
  private http = inject(HttpClient);

  /** Config effective (env defaults merged with localStorage overrides) */
  config = signal<VulnConfig>({
    enabled: false,
    severities: ["CRITICAL", "HIGH"],
    ignore_unfixed: false,
    timeout: "5m",
  });

  /** Config from server env vars (read-only reference) */
  serverDefaults = signal<VulnConfig | null>(null);

  /** True si l'admin a des overrides locaux actifs */
  hasLocalOverrides = signal(false);

  /**
   * Charge les valeurs par défaut depuis le serveur (variables d'env),
   * puis les fusionne avec les overrides localStorage s'ils existent.
   */
  loadConfig() {
    return this.http.get<VulnConfig>("/api/staging/vuln-config").pipe(
      tap((serverConfig) => {
        this.serverDefaults.set(serverConfig);
        const stored = this._readLocalStorage();
        if (stored) {
          this.config.set(stored);
          this.hasLocalOverrides.set(true);
        } else {
          this.config.set(serverConfig);
          this.hasLocalOverrides.set(false);
        }
      }),
    );
  }

  /**
   * Sauvegarde les overrides admin dans le localStorage.
   * Si la config est identique aux defaults serveur, on efface l'override.
   */
  saveConfig(cfg: VulnConfig): void {
    const defaults = this.serverDefaults();
    const isDefault =
      defaults &&
      defaults.enabled === cfg.enabled &&
      defaults.ignore_unfixed === cfg.ignore_unfixed &&
      defaults.timeout === cfg.timeout &&
      JSON.stringify([...defaults.severities].sort()) ===
        JSON.stringify([...cfg.severities].sort());

    if (isDefault) {
      localStorage.removeItem(STORAGE_KEY);
      this.hasLocalOverrides.set(false);
    } else {
      localStorage.setItem(STORAGE_KEY, JSON.stringify(cfg));
      this.hasLocalOverrides.set(true);
    }
    this.config.set(cfg);
  }

  /** Remet les valeurs d'environnement et efface le localStorage */
  resetToDefaults(): void {
    localStorage.removeItem(STORAGE_KEY);
    const defaults = this.serverDefaults();
    if (defaults) {
      this.config.set(defaults);
    }
    this.hasLocalOverrides.set(false);
  }

  private _readLocalStorage(): VulnConfig | null {
    try {
      const raw = localStorage.getItem(STORAGE_KEY);
      if (!raw) return null;
      const parsed = JSON.parse(raw) as VulnConfig;
      // Validation basique
      if (
        typeof parsed.enabled !== "boolean" ||
        !Array.isArray(parsed.severities) ||
        typeof parsed.ignore_unfixed !== "boolean" ||
        typeof parsed.timeout !== "string"
      ) {
        return null;
      }
      return parsed;
    } catch {
      return null;
    }
  }
}
