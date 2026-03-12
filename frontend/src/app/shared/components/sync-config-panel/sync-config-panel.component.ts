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
 * NOTE: All <select> values are strings; [formField] works correctly here
 * without manual coercion.
 */
import { Component, DestroyRef, inject, OnInit, signal } from "@angular/core";
import { takeUntilDestroyed } from "@angular/core/rxjs-interop";
import { form, FormField, required, submit } from "@angular/forms/signals";
import { Subject, switchMap, timer } from "rxjs";
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
  readonly loadingSyncJobs = signal(false);

  private readonly syncPollTrigger$ = new Subject<void>();

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
    this.loadSyncData();
  }

  /** Set up auto-polling for job status (every 3 s while any job is running). */
  private setupSyncPolling(): void {
    this.syncPollTrigger$
      .pipe(
        switchMap(() =>
          timer(0, 3000).pipe(
            switchMap(() => this.extRegSvc.listSyncJobs()),
          ),
        ),
        takeUntilDestroyed(this.destroyRef),
      )
      .subscribe((jobs) => {
        this.syncJobs.set(jobs);
        this.loadingSyncJobs.set(false);
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

  /** Refresh job history and reload local images for the export source dropdown. */
  loadSyncData(): void {
    this.loadingSyncJobs.set(true);
    this.syncPollTrigger$.next();

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
            this.syncPollTrigger$.next();
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
            this.syncPollTrigger$.next();
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
    const folder = this.syncModel().folder.trim();
    const source = this.syncModel().source;

    if (source === "(all)") {
      const ns = folder || username;
      return `${host}/${ns ? ns + "/" : ""}*`;
    }

    // Split tag from the full "repo/path:tag" source string
    const colonIdx = source.lastIndexOf(":");
    const repoPath = colonIdx >= 0 ? source.slice(0, colonIdx) : source;
    const tag = colonIdx >= 0 ? source.slice(colonIdx + 1) : "";
    const tagSuffix = tag ? `:${tag}` : "";
    const leaf = repoPath.split("/").at(-1)!;

    let destPath: string;
    if (folder) {
      // Rule 1: explicit folder replaces namespace, keeps leaf only
      destPath = `${folder}/${leaf}`;
    } else if (username) {
      // Rule 2: Docker Hub compat — username + leaf
      destPath = `${username}/${leaf}`;
    } else {
      // Rule 3 (FIX): no override → keep the full source path
      destPath = repoPath;
    }

    return `${host}/${destPath}${tagSuffix}`;
  }

  /**
   * Build a preview of the destination image in the local registry for the
   * import form. Leaf image name is kept; dest_folder is prepended when set.
   */
  getImportPreview(): string {
    const srcHost = this.getImportSrcHost();
    if (!srcHost) return "";

    const image = this.importModel().image.trim();
    const destFolder = this.importModel().destFolder.trim();

    if (image === "(all)") {
      return `${destFolder ? destFolder + "/" : ""}*`;
    }

    // "repo:tag" → leaf = "repo", tag = "tag"
    const [repoWithPath, tagPart] = image.split(":");
    const leaf = repoWithPath.split("/").at(-1) ?? repoWithPath;
    const tagSuffix = tagPart ? `:${tagPart}` : "";
    const destPath = destFolder ? `${destFolder}/${leaf}` : leaf;

    return destPath + tagSuffix;
  }

  /** Bootstrap badge class for a sync/import job status. */
  syncStatusBadge(status: string): string {
    const map: Record<string, string> = {
      running: "badge bg-info-subtle text-info",
      done: "badge bg-success-subtle text-success",
      partial: "badge bg-warning-subtle text-warning",
      error: "badge bg-danger-subtle text-danger",
    };
    return map[status] ?? "badge bg-secondary";
  }

  /** Bootstrap icon class for a sync/import job status. */
  syncStatusIcon(status: string): string {
    const map: Record<string, string> = {
      running: "bi-arrow-repeat text-info",
      done: "bi-check-circle text-success",
      partial: "bi-exclamation-circle text-warning",
      error: "bi-x-circle text-danger",
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
