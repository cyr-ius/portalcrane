/**
 * Portalcrane - SyncConfigPanelComponent
 * Allows admins to trigger image synchronisations to external registries
 * and view the sync job history.
 *
 * MIGRATION: The sync "form" (source, destination, folder prefix) now uses
 * Angular Signal Forms (form / FormField) instead of bare signal-per-field
 * bindings with manual validation guards.
 *
 * NOTE: syncDestId and syncSource are string selects (IDs / image names).
 * Since <select> values are always strings, [formField] works correctly here —
 * unlike numeric selects, no manual coercion is needed.
 */
import { SlicePipe } from "@angular/common";
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

/** Shape of the sync trigger form model. */
interface SyncFormModel {
  /** Local image reference, or "(all)" to sync everything. */
  source: string;
  /** ID of the destination external registry. */
  destId: string;
  /** Optional folder/namespace prefix on the destination. */
  folder: string;
}

@Component({
  selector: "app-sync-config-panel",
  // FormField required for [formField] bindings; SlicePipe for job log display
  imports: [SlicePipe, FormField],
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
  readonly loadingSyncJobs = signal(false);

  private readonly syncPollTrigger$ = new Subject<void>();

  // ── Signal Form – sync trigger ─────────────────────────────────────────────

  /** Blank defaults; spread on reset to avoid shared-reference bugs. */
  private readonly syncInit: SyncFormModel = {
    source: "(all)",
    destId: "",
    folder: "",
  };

  /** Reactive model backing the Signal Form. */
  readonly syncModel = signal<SyncFormModel>({ ...this.syncInit });

  /**
   * Signal Form definition.
   * destId is required (must choose a destination before syncing).
   * source defaults to "(all)"; folder is optional.
   */
  readonly syncForm = form(this.syncModel, (p) => {
    required(p.destId);
  });

  // ──────────────────────────────────────────────────────────────────────────

  ngOnInit(): void {
    this.loadRegistries();
    this.setupSyncPolling();
    this.loadSyncData();
  }

  /** Set up auto-polling for sync job status (every 3 s while running). */
  private setupSyncPolling(): void {
    this.syncPollTrigger$
      .pipe(
        // Each new trigger cancels the previous poll cycle (switchMap)
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
      next: (regs) => this.registries.set(regs),
    });
  }

  /** Refresh sync job history and reload local images for the source dropdown. */
  loadSyncData(): void {
    this.loadingSyncJobs.set(true);
    this.syncPollTrigger$.next();

    // Load local images for the source dropdown
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

        // Pre-select first registry when none is selected yet
        if (!this.syncModel().destId && this.registries().length > 0) {
          this.syncModel.update((m) => ({ ...m, destId: this.registries()[0].id }));
        }
      },
      error: () => this.loadingLocalImages.set(false),
    });
  }

  /** Submit the sync form via Signal Forms. */
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
            this.syncPollTrigger$.next();
          },
          error: () => this.startingSync.set(false),
        });
    });
  }

  // ── Utility helpers ────────────────────────────────────────────────────────

  /** Resolve a registry ID to its display host. */
  getRegistryHost(registryId: string): string {
    return this.registries().find((r) => r.id === registryId)?.host ?? registryId;
  }

  /** Resolve the currently selected destination registry host. */
  getSyncDestHost(): string {
    return this.registries().find((r) => r.id === this.syncModel().destId)?.host ?? "";
  }

  /**
   * Build a preview of the destination image reference, mirroring the backend's
   * _rewrite_image_name_for_sync() logic: only the LAST path segment of the
   * source is kept, prefixed with dest_folder or registry username.
   * Example: "cyrius44/alpine/ansible:2.20.0" → "docker.io/cyrius44/ansible:2.20.0"
   */
  getSyncPreview(): string {
    const host = this.getSyncDestHost();
    if (!host) return "";

    const reg = this.registries().find((r) => r.id === this.syncModel().destId);
    const username = reg?.username ?? "";
    const folder = this.syncModel().folder.trim();
    const source = this.syncModel().source;

    // Folder prefix takes priority over username
    const ns = folder || username;

    if (source === "(all)") {
      return `${host}/${ns ? ns + "/" : ""}*`;
    }

    // Extract the bare image name (last path segment before the ":" tag)
    const nameWithTag = source.includes("/") ? source.split("/").at(-1)! : source;
    const [imageName, tag] = nameWithTag.split(":");
    const tagSuffix = tag ? `:${tag}` : "";
    const destPath = ns ? `${ns}/${imageName}` : imageName;

    return `${host}/${destPath}${tagSuffix}`;
  }

  /** Return the Bootstrap badge class for a given sync job status. */
  syncStatusBadge(status: string): string {
    const map: Record<string, string> = {
      running: "badge bg-info-subtle text-info",
      done: "badge bg-success-subtle text-success",
      partial: "badge bg-warning-subtle text-warning",
      error: "badge bg-danger-subtle text-danger",
    };
    return map[status] ?? "badge bg-secondary";
  }

  /** Return the Bootstrap icon class for a given sync job status. */
  syncStatusIcon(status: string): string {
    const map: Record<string, string> = {
      running: "bi-arrow-repeat text-info",
      done: "bi-check-circle text-success",
      partial: "bi-exclamation-circle text-warning",
      error: "bi-x-circle text-danger",
    };
    return map[status] ?? "bi-circle";
  }
}
