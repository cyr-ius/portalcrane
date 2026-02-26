import { Component, computed, inject, OnInit, signal } from "@angular/core";
import { FormsModule } from "@angular/forms";
import { RouterLink } from "@angular/router";
import {
  ScanResult,
  SystemService,
  TrivyDbInfo,
} from "../../../core/services/system.service";

@Component({
  selector: "app-security-dashboard",
  imports: [FormsModule, RouterLink],
  templateUrl: "./security-dashboard.component.html",
})
export class SecurityDashboardComponent implements OnInit {
  private svc = inject(SystemService);

  // Process monitoring
  processes = signal<any[]>([]);

  // Trivy DB status
  trivyDb = signal<TrivyDbInfo | null>(null);
  updatingDb = signal(false);

  // Scan state
  imageToScan = signal("");
  severityFilter = signal<string[]>(["HIGH", "CRITICAL"]);
  ignoreUnfixed = signal(false);
  scanning = signal(false);
  scanResult = signal<ScanResult | null>(null);

  // GC state
  gcRunning = signal(false);
  gcOutput = signal<string | null>(null);

  // Computed — critical count badge
  criticalCount = computed(() => this.scanResult()?.summary?.["CRITICAL"] ?? 0);

  readonly severities = ["CRITICAL", "HIGH", "MEDIUM", "LOW", "UNKNOWN"];

  ngOnInit(): void {
    this.loadAll();
  }

  async loadAll(): Promise<void> {
    const [procs, db] = await Promise.all([
      this.svc.getProcessStatuses(),
      this.svc.getTrivyDbInfo(),
    ]);
    this.processes.set(procs);
    this.trivyDb.set(db);
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
