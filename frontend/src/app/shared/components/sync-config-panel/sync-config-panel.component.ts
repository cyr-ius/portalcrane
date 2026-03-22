/**
 * Portalcrane - SyncConfigPanelComponent
 *
 * Migration note: loadLocalImages() now uses getExternalImages(LOCAL_REGISTRY_SYSTEM_ID)
 * instead of the removed getImages() method. The behaviour is identical — the
 * __local__ system registry entry transparently maps to the embedded registry
 * via V2Provider.
 *
 * All other logic is unchanged.
 */
import { Component, DestroyRef, inject, OnInit, signal } from "@angular/core";
import { takeUntilDestroyed } from "@angular/core/rxjs-interop";
import { form, FormField, required, submit } from "@angular/forms/signals";
import { switchMap, timer } from "rxjs";
import { LOCAL_REGISTRY_SYSTEM_ID } from "../../../core/constants/registry.constants";
import {
  ExternalRegistry,
  ExternalRegistryService,
  SyncJob,
} from "../../../core/services/external-registry.service";
import { RegistryService } from "../../../core/services/registry.service";
import { SettingsService } from "../../../core/services/settings.service";

/** Shape of the export sync trigger form model. */
interface SyncFormModel {
  source: string;
  destId: string;
  folder: string;
}

/** Shape of the import form model. */
interface ImportFormModel {
  sourceId: string;
  image: string;
  destFolder: string;
}

/** Direction of the unified sync form. */
type SyncDirection = "export" | "import";

@Component({
  selector: "app-sync-config-panel",
  imports: [FormField],
  templateUrl: "./sync-config-panel.component.html",
  styleUrl: "./sync-config-panel.component.css",
})
export class SyncConfigPanelComponent implements OnInit {
  private readonly extRegSvc = inject(ExternalRegistryService);
  private readonly destroyRef = inject(DestroyRef);
  private readonly registrySvc = inject(RegistryService);
  readonly settingsSvc = inject(SettingsService);

  // ── Data ───────────────────────────────────────────────────────────────────

  readonly registries = signal<ExternalRegistry[]>([]);
  readonly syncJobs = signal<SyncJob[]>([]);
  readonly localImages = signal<string[]>([]);
  readonly loadingLocalImages = signal(false);
  readonly startingSync = signal(false);
  readonly startingImport = signal(false);
  readonly loadingSyncJobs = signal(false);

  // ── Direction toggle ───────────────────────────────────────────────────────

  readonly syncDirection = signal<SyncDirection>("export");

  setSyncDirection(direction: SyncDirection): void {
    this.syncDirection.set(direction);
  }

  // ── Signal Form – export sync trigger ─────────────────────────────────────

  private readonly syncInit: SyncFormModel = {
    source: "(all)",
    destId: "",
    folder: "",
  };

  readonly syncModel = signal<SyncFormModel>({ ...this.syncInit });

  readonly syncForm = form(this.syncModel, (p) => {
    required(p.destId);
  });

  // ── Signal Form – import form ──────────────────────────────────────────────

  private readonly importInit: ImportFormModel = {
    sourceId: "",
    image: "(all)",
    destFolder: "",
  };

  readonly importModel = signal<ImportFormModel>({ ...this.importInit });

  readonly importForm = form(this.importModel, (p) => {
    required(p.sourceId);
  });

  // ── Lifecycle ──────────────────────────────────────────────────────────────

  ngOnInit(): void {
    this.loadRegistries();
    this.setupSyncPolling();
    this.loadLocalImages();
  }

  private setupSyncPolling(): void {
    if (this.syncJobs().length === 0) {
      this.loadingSyncJobs.set(true);
    }

    timer(0, 3000)
      .pipe(
        switchMap(() => this.extRegSvc.listSyncJobs()),
        takeUntilDestroyed(this.destroyRef),
      )
      .subscribe((jobs) => {
        this.syncJobs.set(jobs);
        if (this.loadingSyncJobs()) {
          this.loadingSyncJobs.set(false);
        }
      });
  }

  loadRegistries(): void {
    this.extRegSvc.listRegistries().subscribe({
      next: (regs) => {
        this.extRegSvc.setRegistriesCache(regs);
        const userRegs = this.extRegSvc.browsableUserRegistries();
        this.registries.set(userRegs);
        if (!this.syncModel().destId && userRegs.length > 0) {
          this.syncModel.update((m) => ({ ...m, destId: userRegs[0].id }));
        }
        if (!this.importModel().sourceId && userRegs.length > 0) {
          this.importModel.update((m) => ({ ...m, sourceId: userRegs[0].id }));
        }
      },
    });
  }

  loadSyncData(): void {
    this.extRegSvc.listSyncJobs().subscribe({
      next: (jobs) => this.syncJobs.set(jobs),
    });
    this.loadLocalImages();
  }

  /**
   * Load local registry images for the sync source dropdown.
   *
   * Replaces: this.registrySvc.getImages(1, 200)
   * Now uses: getExternalImages(LOCAL_REGISTRY_SYSTEM_ID, 1, 200)
   *
   * The __local__ system entry transparently routes through V2Provider
   * to the embedded registry — behaviour is identical.
   */
  private loadLocalImages(): void {
    this.loadingLocalImages.set(true);
    this.registrySvc
      .getExternalImages(LOCAL_REGISTRY_SYSTEM_ID, 1, 200)
      .subscribe({
        next: (data) => {
          const imgs: string[] = [];
          for (const item of data.items) {
            for (const tag of item.tags) {
              imgs.push(`${item.name}:${tag}`);
            }
          }
          this.localImages.set(imgs);
          this.loadingLocalImages.set(false);
        },
        error: () => this.loadingLocalImages.set(false),
      });
  }

  // ── Export sync ────────────────────────────────────────────────────────────

  startSync(): void {
    submit(this.syncForm, async (f) => {
      const { source, destId, folder } = f().value();
      this.startingSync.set(true);

      this.extRegSvc
        .startSync({
          source_image: source ?? "(all)",
          dest_registry_id: destId!,
          dest_folder: folder?.trim() || null,
        })
        .subscribe({
          next: () => {
            this.startingSync.set(false);
            this.syncModel.set({ ...this.syncInit });
            this.extRegSvc.listSyncJobs().subscribe({
              next: (jobs) => this.syncJobs.set(jobs),
            });
          },
          error: () => this.startingSync.set(false),
        });
    });
  }

  // ── Import ─────────────────────────────────────────────────────────────────

  startImport(): void {
    submit(this.importForm, async (f) => {
      const { sourceId, image, destFolder } = f().value();
      this.startingImport.set(true);

      this.extRegSvc
        .startImport({
          source_registry_id: sourceId!,
          source_image: image ?? "(all)",
          dest_folder: destFolder?.trim() || null,
        })
        .subscribe({
          next: () => {
            this.startingImport.set(false);
            this.importModel.set({ ...this.importInit });
            if (this.registries().length > 0) {
              this.importModel.update((m) => ({
                ...m,
                sourceId: this.registries()[0].id,
              }));
            }
            this.extRegSvc.listSyncJobs().subscribe({
              next: (jobs) => this.syncJobs.set(jobs),
            });
          },
          error: () => this.startingImport.set(false),
        });
    });
  }

  // ── Utility helpers ────────────────────────────────────────────────────────

  getRegistryHost(registryId: string | null): string {
    if (!registryId) return "(local)";
    return this.registries().find((r) => r.id === registryId)?.host ?? registryId;
  }

  getSyncDestHost(): string {
    return this.registries().find((r) => r.id === this.syncModel().destId)?.host ?? "";
  }

  getImportSrcHost(): string {
    return (
      this.registries().find((r) => r.id === this.importModel().sourceId)?.host ?? ""
    );
  }

  getSyncPreview(): string {
    const host = this.getSyncDestHost();
    if (!host) return "";

    const reg = this.registries().find((r) => r.id === this.syncModel().destId);
    const username = reg?.username ?? "";
    const folder = this.syncModel().folder?.trim() ?? "";
    const source = this.syncModel().source ?? "";
    if (!source || source === "(all)") return `${host}/*`;

    const [imgPart, tagPart] = source.split(":");
    const leaf = imgPart.split("/").pop() ?? imgPart;
    const tagSuffix = tagPart ? `:${tagPart}` : "";
    const destPath = folder
      ? `${folder}/${leaf}`
      : username
        ? `${username}/${leaf}`
        : imgPart;

    return `${host}/${destPath}${tagSuffix}`;
  }

  getImportPreview(): string {
    const destFolder = this.importModel().destFolder?.trim() ?? "";
    const image = this.importModel().image ?? "";
    if (!image || image === "(all)") return "local/*";

    const [imgPart, tagPart] = image.split(":");
    const leaf = imgPart.split("/").pop() ?? imgPart;
    const tagSuffix = tagPart ? `:${tagPart}` : "";
    const destPath = destFolder ? `${destFolder}/${leaf}` : imgPart;

    return destPath + tagSuffix;
  }

  syncStatusBadge(status: string): string {
    const map: Record<string, string> = {
      running: "badge bg-info-subtle text-info",
      done: "badge bg-success-subtle text-success",
      done_with_errors: "badge bg-warning-subtle text-warning",
      partial: "badge bg-warning-subtle text-warning",
      failed: "badge bg-danger-subtle text-danger",
      error: "badge bg-danger-subtle text-danger",
    };
    return map[status] ?? "badge bg-secondary";
  }

  syncStatusIcon(status: string): string {
    const map: Record<string, string> = {
      running: "bi-arrow-repeat text-info",
      done: "bi-check-circle text-success",
      done_with_errors: "bi-exclamation-circle text-warning",
      partial: "bi-exclamation-circle text-warning",
      failed: "bi-x-circle text-danger",
      error: "bi-x-circle text-danger",
    };
    return map[status] ?? "bi-circle";
  }

  jobDirectionLabel(job: SyncJob): string {
    return job.direction === "import" ? "↓ Import" : "↑ Export";
  }

  jobDirectionClass(job: SyncJob): string {
    return job.direction === "import"
      ? "badge bg-secondary-subtle text-secondary"
      : "badge bg-primary-subtle text-primary";
  }
}
