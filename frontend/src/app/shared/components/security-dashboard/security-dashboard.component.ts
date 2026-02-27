/**
 * Portalcrane - Security Dashboard Component
 * Displays Trivy DB status, manual image scan, and registry garbage collection.
 * RouterLink removed — no router navigation used in this component.
 */
import { Component, computed, inject, OnInit, signal } from "@angular/core";
import { FormsModule } from "@angular/forms";
import { RegistryService } from "../../../core/services/registry.service";
import {
  ScanResult,
  SystemService,
  TrivyDbInfo,
} from "../../../core/services/system.service";

@Component({
  selector: "app-security-dashboard",
  // RouterLink removed: it was imported but never used in the template
  imports: [FormsModule],
  templateUrl: "./security-dashboard.component.html",
})
export class SecurityDashboardComponent implements OnInit {
  private svc = inject(SystemService);
  private registry = inject(RegistryService);

  processes = signal<any[]>([]);
  trivyDb = signal<TrivyDbInfo | null>(null);
  updatingDb = signal(false);

  // Registry image list for the scan dropdown
  registryImages = signal<string[]>([]);
  loadingImages = signal(false);

  imageToScan = signal("");
  severityFilter = signal<string[]>(["HIGH", "CRITICAL"]);
  ignoreUnfixed = signal(false);
  scanning = signal(false);
  scanResult = signal<ScanResult | null>(null);

  gcRunning = signal(false);
  gcOutput = signal<string | null>(null);

  criticalCount = computed(() => this.scanResult()?.summary?.["CRITICAL"] ?? 0);

  readonly severities = ["CRITICAL", "HIGH", "MEDIUM", "LOW", "UNKNOWN"];

  ngOnInit(): void {
    this.loadAll();
    this.loadRegistryImages();
  }

  async loadAll(): Promise<void> {
    const [procs, db] = await Promise.all([
      this.svc.getProcessStatuses(),
      this.svc.getTrivyDbInfo(),
    ]);
    this.processes.set(procs);
    this.trivyDb.set(db);
  }

  /** Load all repo:tag combinations from the local registry for the dropdown. */
  loadRegistryImages(): void {
    this.loadingImages.set(true);
    this.registry.getImages(1, 100).subscribe({
      next: (data) => {
        const images: string[] = [];
        for (const item of data.items) {
          for (const tag of item.tags) {
            images.push(`localhost:5000/${item.name}:${tag}`);
          }
        }
        this.registryImages.set(images);
        if (images.length > 0 && !this.imageToScan()) {
          this.imageToScan.set(images[0]);
        }
        this.loadingImages.set(false);
      },
      error: () => this.loadingImages.set(false),
    });
  }

  toggleSeverity(sev: string): void {
    const current = this.severityFilter();
    this.severityFilter.set(
      current.includes(sev)
        ? current.filter((s) => s !== sev)
        : [...current, sev],
    );
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

  async runGc(dryRun: boolean): Promise<void> {
    this.gcRunning.set(true);
    this.gcOutput.set(null);
    try {
      const result = await this.svc.runGc(dryRun);
      this.gcOutput.set(result.output);
    } finally {
      this.gcRunning.set(false);
    }
  }

  /**
   * Return the count of a given severity from the current scan result.
   * The summary map is typed as Record<string, number> so the value is
   * always a number — no nullish coalescing needed here.
   */
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
