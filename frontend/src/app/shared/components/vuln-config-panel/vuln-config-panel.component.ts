import { Component, inject, signal } from "@angular/core";
import {
  AppConfigService,
  TRIVY_SEVERITIES,
  TRIVY_TIMEOUT_OPTIONS,
  TrivySeverity,
} from "../../../core/services/app-config.service";
import {
  ScanResult,
  SystemService,
  TrivyDbInfo,
} from "../../../core/services/system.service";

@Component({
  selector: "app-vuln-config-panel",
  imports: [],
  templateUrl: "./vuln-config-panel.component.html",
  styleUrl: "./vuln-config-panel.component.css",
})
export class VulnConfigPanelComponent {
  configService = inject(AppConfigService);
  private svc = inject(SystemService);

  trivyDb = signal<TrivyDbInfo | null>(null);
  updatingDb = signal(false);

  imageToScan = signal("");
  scanning = signal(false);
  scanResult = signal<ScanResult | null>(null);
  severityFilter = signal<string[]>(["HIGH", "CRITICAL"]);
  ignoreUnfixed = signal(false);

  // Registry image list for the scan dropdown
  registryImages = signal<string[]>([]);
  loadingImages = signal(false);

  readonly allSeverities = TRIVY_SEVERITIES;
  readonly timeoutOptions = TRIVY_TIMEOUT_OPTIONS;
  readonly severities = ["CRITICAL", "HIGH", "MEDIUM", "LOW", "UNKNOWN"];

  getSevBtnClass(sev: TrivySeverity): string {
    const active = this.configService.vulnSeverities().includes(sev);
    const colorMap: Record<TrivySeverity, string> = {
      CRITICAL: active ? "btn-danger" : "btn-outline-danger",
      HIGH: active ? "btn-danger" : "btn-outline-danger",
      MEDIUM: active ? "btn-warning" : "btn-outline-warning",
      LOW: active ? "btn-info" : "btn-outline-info",
      UNKNOWN: active ? "btn-secondary" : "btn-outline-secondary",
    };
    return colorMap[sev];
  }

  async updateTrivyDb(): Promise<void> {
    this.updatingDb.set(true);
    try {
      await this.svc.updateTrivyDb();
      this.trivyDb.set(await this.svc.getTrivyDbInfo());
    } finally {
      this.updatingDb.set(false);
    }
  }

  toggleSeverity(sev: string): void {
    const current = this.severityFilter();
    this.severityFilter.set(
      current.includes(sev)
        ? current.filter((s) => s !== sev)
        : [...current, sev],
    );
  }

  async runScan(): Promise<void> {
    if (!this.imageToScan()) return;
    this.scanning.set(true);
    this.scanResult.set(null);
    try {
      const result = await this.svc.scanImage(
        this.imageToScan(),
        this.severityFilter(),
        this.ignoreUnfixed(),
      );
      this.scanResult.set(result);
    } finally {
      this.scanning.set(false);
    }
  }

  getSeverityCount(sev: string): number {
    return this.scanResult()?.summary?.[sev] ?? 0;
  }

  severityBadgeClass(sev: string): string {
    const map: Record<string, string> = {
      CRITICAL: "badge bg-danger",
      HIGH: "badge bg-warning text-dark",
      MEDIUM: "badge bg-primary",
      LOW: "badge bg-secondary",
      UNKNOWN: "badge bg-light text-dark",
    };
    return map[sev] ?? "badge bg-light";
  }
}
