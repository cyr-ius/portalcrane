import { Component, computed, inject, OnInit, signal } from "@angular/core";
import { RegistryService } from "../../../core/services/registry.service";
import { ScanResult, TRIVY_SEVERITIES, TRIVY_TIMEOUT_OPTIONS, TrivyDbInfo, TrivyService, TrivySeverity } from "../../../core/services/trivy.service";

@Component({
  selector: "app-vuln-config-panel",
  imports: [],
  templateUrl: "./vuln-config-panel.component.html",
  styleUrl: "./vuln-config-panel.component.css",
})
export class VulnConfigPanelComponent implements OnInit {
  private registryService = inject(RegistryService);
  trivySvc = inject(TrivyService)

  trivyDb = signal<TrivyDbInfo | null>(null);
  updatingDb = signal(false);

  selectedImage = signal("");
  selectedTag = signal("");
  scanning = signal(false);
  scanResult = signal<ScanResult | null>(null);
  severityFilter = signal<string[]>(["HIGH", "CRITICAL"]);
  ignoreUnfixed = signal(false);

  // Registry image/tag lists for the scan dropdowns
  registryImages = signal<string[]>([]);
  availableTags = signal<string[]>([]);
  loadingImages = signal(false);
  loadingTags = signal(false);

  readonly imageToScan = computed(() => {
    const image = this.selectedImage().trim();
    const tag = this.selectedTag().trim();
    return image && tag ? `${image}:${tag}` : "";
  });

  readonly allSeverities = TRIVY_SEVERITIES;
  readonly timeoutOptions = TRIVY_TIMEOUT_OPTIONS;
  readonly severities = ["CRITICAL", "HIGH", "MEDIUM", "LOW", "UNKNOWN"];

  ngOnInit(): void {
    this.trivySvc.loadConfig().subscribe();
    this.refreshTrivyDb();
    this.loadRegistryImages();
  }

  async refreshTrivyDb(): Promise<void> {
    try {
      this.trivyDb.set(await this.trivySvc.getTrivyDbInfo());
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

        if (images.length === 0) {
          this.selectedImage.set("");
          this.selectedTag.set("");
          this.availableTags.set([]);
          this.loadingImages.set(false);
          return;
        }

        const nextImage = this.selectedImage() || images[0];
        this.selectedImage.set(nextImage);
        this.loadTagsForImage(nextImage);
        this.loadingImages.set(false);
      },
      error: () => {
        this.loadingImages.set(false);
        this.availableTags.set([]);
      },
    });
  }

  onImageChange(image: string): void {
    this.selectedImage.set(image);
    this.selectedTag.set("");
    this.availableTags.set([]);
    this.loadTagsForImage(image);
  }

  private loadTagsForImage(image: string): void {
    if (!image) {
      this.availableTags.set([]);
      this.selectedTag.set("");
      return;
    }

    this.loadingTags.set(true);
    this.registryService.getImageTags(image).subscribe({
      next: (res) => {
        const tags = (res.tags || []).filter(Boolean);
        this.availableTags.set(tags);
        this.selectedTag.set(tags[0] ?? "");
        this.loadingTags.set(false);
      },
      error: () => {
        this.availableTags.set([]);
        this.selectedTag.set("");
        this.loadingTags.set(false);
      },
    });
  }

  getSevBtnClass(sev: TrivySeverity): string {
    const active = this.trivySvc.vulnSeverities().includes(sev);
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
      await this.trivySvc.updateTrivyDb();
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
      const result = await this.trivySvc.scanImage(
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
