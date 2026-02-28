import { Component, inject, OnInit, signal } from "@angular/core";
import {
  AppConfigService,
  TRIVY_SEVERITIES,
  TRIVY_TIMEOUT_OPTIONS,
  TrivySeverity,
} from "../../../core/services/app-config.service";
import { RegistryService } from "../../../core/services/registry.service";
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
export class VulnConfigPanelComponent implements OnInit {
  configService = inject(AppConfigService);
  private svc = inject(SystemService);
  private registryService = inject(RegistryService);

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

  ngOnInit(): void {
    this.refreshTrivyDb();
    this.loadRegistryImages();
  }

  async refreshTrivyDb(): Promise<void> {
    try {
      this.trivyDb.set(await this.svc.getTrivyDbInfo());
    } catch {
      this.trivyDb.set(null);
    }
  }

  loadRegistryImages(): void {
    this.loadingImages.set(true);
    this.registryService.getImages(1, 100).subscribe({
      next: (res) => {
        const images = (res.items || []).map((r) => r.name);
        this.registryImages.set(images);
        if (!this.imageToScan() && images.length > 0) {
          this.imageToScan.set(images[0]);
        }
        this.loadingImages.set(false);
      },
      error: () => this.loadingImages.set(false),
    });
  }

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
      await this.refreshTrivyDb();
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

  formatUtcDate(value: string | null): string {
    if (!value) return "Unknown";
    const date = new Date(value);
    if (Number.isNaN(date.getTime())) return value;
    return date.toLocaleString();
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
