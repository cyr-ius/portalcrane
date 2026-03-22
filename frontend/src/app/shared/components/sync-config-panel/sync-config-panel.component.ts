/**
 * Portalcrane - SyncConfigPanelComponent
 *
 * Allows admins to trigger image synchronisations to/from external registries
 * and view the sync/import job history.
 *
 * Change (local system registry):
 *   - Uses extRegSvc.browsableUserRegistries for the source/destination selectors.
 *     This excludes the hidden local system registry (__local__) because:
 *     - Export: local IS the source, not a valid destination.
 *     - Import: importing FROM local TO local makes no sense.
 *   The local registry still appears in Images and Staging via browsableRegistries.
 *
 * Refactor (unified card):
 *   - Export (local → external) and Import (external → local) are merged into
 *     a single card controlled by the `syncDirection` signal.
 *   - New `setSyncDirection()` method switches between 'export' and 'import'.
 *   - Both Signal Forms (syncForm / importForm) and all helpers are unchanged.
 *
 * Anti-flicker fix:
 *   - setupSyncPolling() uses a simple timer(0, 3000) without a Subject trigger.
 *
 * NOTE: All <select> values are strings; [formField] works correctly here
 * without manual coercion.
 */
import { Component, DestroyRef, inject, OnInit, signal } from "@angular/core";
import { takeUntilDestroyed } from "@angular/core/rxjs-interop";
import { form, FormField, required, submit } from "@angular/forms/signals";
import { switchMap, timer } from "rxjs";
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

/** Terminal statuses — polling stops when all jobs reach one of these. */
const TERMINAL_STATUSES = new Set([
  "done",
  "done_with_errors",
  "failed",
  "error",
  "partial",
]);

@Component({
  selector: "app-sync-config-panel",
  // FormField required for [formField] bindings
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

  /**
   * User-managed external registries excluding the hidden local system entry.
   * The local registry is never a valid export destination or import source
   * in the sync panel — it is always the implicit local registry.
   */
  readonly registries = signal<ExternalRegistry[]>([]);
  readonly syncJobs = signal<SyncJob[]>([]);
  readonly localImages = signal<string[]>([]);
  readonly loadingLocalImages = signal(false);
  readonly startingSync = signal(false);
  readonly startingImport = signal(false);
  /**
   * True only during the very first load when syncJobs is empty.
   * Background polling cycles never set this to true, preventing flicker.
   */
  readonly loadingSyncJobs = signal(false);

  // ── Direction toggle ───────────────────────────────────────────────────────

  /** Controls which sub-form is visible: 'export' (local→ext) or 'import' (ext→local). */
  readonly syncDirection = signal<SyncDirection>("export");

  /** Switch the active direction and reset both forms to their defaults. */
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

  // ─────────────────────────────────────────────────────────────────────────

  ngOnInit(): void {
    this.loadRegistries();
    this.setupSyncPolling();
    this.loadLocalImages();
  }

  /**
   * Set up automatic polling for job status every 3 seconds.
   *
   * Anti-flicker design:
   *   - A simple timer(0, 3000) is used instead of Subject + switchMap.
   *   - loadingSyncJobs is set to true ONLY when syncJobs is currently empty.
   *   - syncJobs signal is updated in-place; no full re-render.
   */
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

  /**
   * Fetch user-managed external registries excluding the local system entry.
   * Uses browsableUserRegistries which filters out system=true entries.
   */
  loadRegistries(): void {
    this.extRegSvc.listRegistries().subscribe({
      next: (regs) => {
        this.extRegSvc.setRegistriesCache(regs);
        // Use only non-system (user-managed) registries for sync destinations
        const userRegs = this.extRegSvc.browsableUserRegistries();
        this.registries.set(userRegs);
        // Pre-select first registry when none is selected yet
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

  /** Load local registry images for the sync source dropdown. */
  private loadLocalImages(): void {
    this.loadingLocalImages.set(true);
    this.registrySvc.getImages(1, 200).subscribe({
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

  /** Submit the export sync form via Signal Forms. */
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

  /** Submit the import form via Signal Forms. */
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

  /** Resolve a registry ID to its display host. */
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

  /**
   * Build a preview of the destination image reference for the export form.
   *
   * Mirrors the backend _rewrite_image_name_for_sync() logic:
   *   1. dest_folder set → folder + leaf only
   *   2. No folder, dest_username set → username + leaf (Docker Hub compat)
   *   3. No folder, no username → preserve the FULL source path
   */
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
    const destPath = folder ? `${folder}/${leaf}` : username ? `${username}/${leaf}` : imgPart;

    return `${host}/${destPath}${tagSuffix}`;
  }

  /**
   * Build a preview of the destination image reference for the import form.
   */
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

  /** Bootstrap badge class for a sync/import job status. */
  syncStatusBadge(status: string): string {
    const map: Record<string, string> = {
      running:          "badge bg-info-subtle text-info",
      done:             "badge bg-success-subtle text-success",
      done_with_errors: "badge bg-warning-subtle text-warning",
      partial:          "badge bg-warning-subtle text-warning",
      failed:           "badge bg-danger-subtle text-danger",
      error:            "badge bg-danger-subtle text-danger",
    };
    return map[status] ?? "badge bg-secondary";
  }

  /** Bootstrap icon class for a sync/import job status. */
  syncStatusIcon(status: string): string {
    const map: Record<string, string> = {
      running:          "bi-arrow-repeat text-info",
      done:             "bi-check-circle text-success",
      done_with_errors: "bi-exclamation-circle text-warning",
      partial:          "bi-exclamation-circle text-warning",
      failed:           "bi-x-circle text-danger",
      error:            "bi-x-circle text-danger",
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
