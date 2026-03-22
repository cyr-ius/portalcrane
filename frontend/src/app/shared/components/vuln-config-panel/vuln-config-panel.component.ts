// frontend/src/app/shared/components/vuln-config-panel/vuln-config-panel.component.ts
import { Component, computed, inject, OnInit, signal } from "@angular/core";
import { LOCAL_REGISTRY_SYSTEM_ID } from "../../../core/constants/registry.constants";
import { RegistryService } from "../../../core/services/registry.service";
import {
  ScanResult,
  TRIVY_SEVERITIES,
  TRIVY_TIMEOUT_OPTIONS,
  TrivyDbInfo,
  TrivyService,
  TrivySeverity,
} from "../../../core/services/trivy.service";

@Component({
  selector: "app-vuln-config-panel",
  imports: [],
  templateUrl: "./vuln-config-panel.component.html",
  styleUrl: "./vuln-config-panel.component.css",
})
export class VulnConfigPanelComponent implements OnInit {
  private registryService = inject(RegistryService);
  readonly trivySvc = inject(TrivyService);

  // ── Trivy DB ──────────────────────────────────────────────────────────────

  trivyDb = signal<TrivyDbInfo | null>(null);
  updatingDb = signal(false);

  // ── Image scan ────────────────────────────────────────────────────────────

  selectedImage = signal("");
  selectedTag = signal("");
  scanning = signal(false);
  scanResult = signal<ScanResult | null>(null);
  severityFilter = signal<string[]>(["HIGH", "CRITICAL"]);
  ignoreUnfixed = signal(false);

  // Registry image/tag lists for the scan dropdowns.
  // Uses the unified external registry path via __local__ system entry.
  registryImages = signal<string[]>([]);
  availableTags = signal<string[]>([]);
  loadingImages = signal(false);
  loadingTags = signal(false);

  readonly imageToScan = computed(() => {
    const image = this.selectedImage().trim();
    const tag = this.selectedTag().trim();
    return image && tag ? `localhost:5000/${image}:${tag}` : "";
  });

  readonly imagePreview = computed(() => {
    const image = this.selectedImage().trim();
    const tag = this.selectedTag().trim();
    return image && tag ? `${image}:${tag}` : "";
  });

  // ── Constants used in the template ───────────────────────────────────────

  readonly allSeverities = TRIVY_SEVERITIES;
  readonly timeoutOptions = TRIVY_TIMEOUT_OPTIONS;

  // ── Lifecycle ─────────────────────────────────────────────────────────────

  ngOnInit(): void {
    this.loadTrivyDb();
    this.loadRegistryImages();
  }

  // ── Trivy DB ──────────────────────────────────────────────────────────────

  async loadTrivyDb(): Promise<void> {
    try {
      this.trivyDb.set(await this.trivySvc.getTrivyDbInfo());
    } catch {
      // Silently ignore — DB info is non-critical
    }
  }

  async updateDb(): Promise<void> {
    if (this.updatingDb()) return;
    this.updatingDb.set(true);
    try {
      await this.trivySvc.updateTrivyDb();
      await this.loadTrivyDb();
    } finally {
      this.updatingDb.set(false);
    }
  }

  // ── Registry image/tag loading ────────────────────────────────────────────

  /**
   * Load the image list from the local registry via the unified __local__ path.
   * Uses getExternalImages(LOCAL_REGISTRY_SYSTEM_ID) which replaces getImages().
   */
  loadRegistryImages(): void {
    this.loadingImages.set(true);
    this.registryService.getExternalImages(LOCAL_REGISTRY_SYSTEM_ID, 1, 100).subscribe({
      next: (res) => {
        this.registryImages.set(res.items.map((i) => i.name));
        if (res.items.length > 0 && !this.selectedImage()) {
          this.onImageSelected(res.items[0].name);
        }
        this.loadingImages.set(false);
      },
      error: () => this.loadingImages.set(false),
    });
  }

  onImageSelected(image: string): void {
    this.selectedImage.set(image);
    this.selectedTag.set("");
    this.scanResult.set(null);
    if (!image) return;

    this.loadingTags.set(true);
    // Uses getExternalImageTags via __local__ which replaces getImageTags()
    this.registryService
      .getExternalImageTags(LOCAL_REGISTRY_SYSTEM_ID, image)
      .subscribe({
        next: (res) => {
          const tags = res.tags ?? [];
          this.availableTags.set(tags);
          if (tags.length > 0) this.selectedTag.set(tags[0]);
          this.loadingTags.set(false);
        },
        error: () => this.loadingTags.set(false),
      });
  }

  // ── Manual image scan ─────────────────────────────────────────────────────

  async runScan(): Promise<void> {
    const ref = this.imageToScan();
    if (!ref || this.scanning()) return;

    this.scanning.set(true);
    this.scanResult.set(null);
    try {
      const result = await this.trivySvc.scanImage(
        ref,
        this.severityFilter(),
        this.ignoreUnfixed(),
      );
      this.scanResult.set(result);
    } finally {
      this.scanning.set(false);
    }
  }

  // ── Severity badge helpers ────────────────────────────────────────────────

  getSevBtnClass(sev: TrivySeverity): string {
    const active = this.trivySvc.vulnSeverities().includes(sev);
    const map: Record<TrivySeverity, string> = {
      CRITICAL: active ? "btn-danger" : "btn-outline-danger",
      HIGH: active ? "btn-warning" : "btn-outline-warning",
      MEDIUM: active ? "btn-primary" : "btn-outline-primary",
      LOW: active ? "btn-info" : "btn-outline-info",
      UNKNOWN: active ? "btn-secondary" : "btn-outline-secondary",
    };
    return map[sev] ?? "btn-outline-secondary";
  }

  severityBadgeClass(sev: string): string {
    const map: Record<string, string> = {
      CRITICAL: "badge bg-danger",
      HIGH: "badge bg-warning text-dark",
      MEDIUM: "badge bg-primary",
      LOW: "badge bg-info text-dark",
      UNKNOWN: "badge bg-secondary",
    };
    return map[sev.toUpperCase()] ?? "badge bg-secondary";
  }

  getSeverityCount(sev: string): number {
    return this.scanResult()?.summary?.[sev.toUpperCase()] ?? 0;
  }

  formatUtcDate(value: string | null): string {
    if (!value) return "Unknown";
    const date = new Date(value);
    if (Number.isNaN(date.getTime())) return value;
    return date.toLocaleString();
  }
}
