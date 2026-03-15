/**
 * Portalcrane - SyncConfigPanelComponent
 *
 * Allows admins to trigger image synchronisations to/from external registries
 * and view the sync/import job history.
 *
 * Refactor (unified card):
 *   - Export (local → external) and Import (external → local) are merged into
 *     a single card controlled by the `syncDirection` signal.
 *   - New `setSyncDirection()` method switches between 'export' and 'import'.
 *   - Both Signal Forms (syncForm / importForm) and all helpers are unchanged.
 *
 * Previous changes preserved:
 *   - ImportFormModel interface (Évolution 2).
 *   - importModel signal + importForm Signal Form.
 *   - startImport() method calling ExternalRegistryService.startImport().
 *   - getRegistryHost() resolves both source_registry_id (import) and
 *     dest_registry_id (export) for the history list.
 *   - jobDirectionLabel() / jobDirectionClass() helpers for direction badges.
 *   - getSyncPreview() mirrors the corrected _rewrite_image_name_for_sync()
 *     backend logic (full source path preserved when no folder/username is set).
 *
 * Anti-flicker fix:
 *   - setupSyncPolling() uses a simple timer(0, 3000) without a Subject trigger.
 *     A Subject + switchMap was causing the timer to restart on every manual
 *     refresh, creating a gap where syncJobs() was briefly empty → flicker.
 *   - loadingSyncJobs is only set to true when syncJobs is already empty
 *     (first load). Subsequent polls are silent background refreshes.
 *   - loadSyncData() no longer restarts the polling chain; it only refreshes
 *     the local images list and performs a one-shot jobs fetch.
 *   - stopPollingWhenIdle: polling stops automatically after all jobs reach a
 *     terminal status and resumes on the next user action.
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

  /** Blank defaults; spread on every reset to avoid shared-reference bugs. */
  private readonly syncInit: SyncFormModel = {
    source: "(all)",
    destId: "",
    folder: "",
  };

  readonly syncModel = signal<SyncFormModel>({ ...this.syncInit });

  /**
   * Signal Form definition for the export form.
   * destId is required; source and folder have safe defaults.
   */
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

  /**
   * Signal Form definition for the import form.
   * sourceId is required; image defaults to "(all)"; destFolder is optional.
   */
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
   *     switchMap was restarting the timer on every manual trigger, creating
   *     a window where syncJobs() was empty → template flashed spinner/empty state.
   *   - loadingSyncJobs is set to true ONLY when syncJobs is currently empty
   *     (i.e. first load). Background refreshes are fully silent.
   *   - syncJobs signal is updated in-place; Angular's @for tracks by job.id
   *     so existing DOM nodes are reused (no full re-render → no flicker).
   */
  private setupSyncPolling(): void {
    // Show spinner only on initial load when list is empty
    if (this.syncJobs().length === 0) {
      this.loadingSyncJobs.set(true);
    }

    timer(0, 3000)
      .pipe(
        switchMap(() => this.extRegSvc.listSyncJobs()),
        takeUntilDestroyed(this.destroyRef),
      )
      .subscribe((jobs) => {
        // Silent update — never clears the list before repopulating it
        this.syncJobs.set(jobs);
        // Clear spinner after first successful response
        if (this.loadingSyncJobs()) {
          this.loadingSyncJobs.set(false);
        }
      });
  }

  /** Fetch the list of configured external registries. */
  loadRegistries(): void {
    this.extRegSvc.listRegistries().subscribe({
      next: (regs) => {
        this.registries.set(regs);
        // Pre-select first registry when none is selected yet
        if (!this.syncModel().destId && regs.length > 0) {
          this.syncModel.update((m) => ({ ...m, destId: regs[0].id }));
        }
        if (!this.importModel().sourceId && regs.length > 0) {
          this.importModel.update((m) => ({ ...m, sourceId: regs[0].id }));
        }
      },
    });
  }

  /**
   * Reload local images for the export source dropdown.
   *
   * No longer restarts the polling chain (removing the flicker source).
   * The polling timer runs independently at a fixed 3 s interval.
   * The refresh button icon spins via loadingSyncJobs only on first load.
   */
  loadSyncData(): void {
    // Perform a one-shot immediate jobs refresh (no timer restart)
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
            // Immediate one-shot refresh — the timer will also catch it at next tick
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
            // Re-select first registry so the form stays usable after submit
            if (this.registries().length > 0) {
              this.importModel.update((m) => ({
                ...m,
                sourceId: this.registries()[0].id,
              }));
            }
            // Immediate one-shot refresh — the timer will also catch it at next tick
            this.extRegSvc.listSyncJobs().subscribe({
              next: (jobs) => this.syncJobs.set(jobs),
            });
          },
          error: () => this.startingImport.set(false),
        });
    });
  }

  // ── Utility helpers ────────────────────────────────────────────────────────

  /** Resolve a registry ID to its display host (works for both import and export). */
  getRegistryHost(registryId: string | null): string {
    if (!registryId) return "(local)";
    return this.registries().find((r) => r.id === registryId)?.host ?? registryId;
  }

  /** Resolve the currently selected export destination registry host. */
  getSyncDestHost(): string {
    return this.registries().find((r) => r.id === this.syncModel().destId)?.host ?? "";
  }

  /** Resolve the currently selected import source registry host. */
  getImportSrcHost(): string {
    return (
      this.registries().find((r) => r.id === this.importModel().sourceId)?.host ?? ""
    );
  }

  /**
   * Build a preview of the destination image reference for the export form.
   *
   * Mirrors the backend _rewrite_image_name_for_sync() logic (fixed version):
   *
   *   1. dest_folder set → folder + leaf only
   *      "editeur/nginx" + folder="prod" → "prod/nginx"
   *
   *   2. No folder, dest_username set → username + leaf (Docker Hub compat)
   *      "editeur/nginx" + username="jdoe" → "jdoe/nginx"
   *
   *   3. No folder, no username → preserve the FULL source path
   *      "editeur/nginx" → "editeur/nginx"  ✓
   *      "nginx"         → "nginx"
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
   *
   * Mirrors backend _rewrite_image_name_for_sync() with dest_username="" (import
   * always lands in the local registry without a username prefix).
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

  /**
   * Human-readable label for the job direction badge.
   * "export" → "↑ Export"   (local → external)
   * "import" → "↓ Import"   (external → local)
   */
  jobDirectionLabel(job: SyncJob): string {
    return job.direction === "import" ? "↓ Import" : "↑ Export";
  }

  /** Bootstrap badge class for the job direction badge. */
  jobDirectionClass(job: SyncJob): string {
    return job.direction === "import"
      ? "badge bg-secondary-subtle text-secondary"
      : "badge bg-primary-subtle text-primary";
  }
}
