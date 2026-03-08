import { SlicePipe } from "@angular/common";
import { Component, DestroyRef, inject, OnInit, signal } from "@angular/core";
import { takeUntilDestroyed } from "@angular/core/rxjs-interop";
import { Subject, switchMap, timer } from "rxjs";
import { ExternalRegistry, ExternalRegistryService, SyncJob } from "../../../core/services/external-registry.service";
import { RegistryService } from "../../../core/services/registry.service";
import { SettingsService } from "../../../core/services/settings.service";

@Component({
  selector: "app-sync-config-panel",
  imports: [SlicePipe],
  templateUrl: "./sync-config-panel.component.html",
  styleUrl: "./sync-config-panel.component.css",
})
export class SyncConfigPanelComponent implements OnInit {
  private extRegSvc = inject(ExternalRegistryService);
  private destroyRef = inject(DestroyRef);
  private registrySvc = inject(RegistryService);
  settingsSvc = inject(SettingsService)

  registries = signal<ExternalRegistry[]>([]);

  syncJobs = signal<SyncJob[]>([]);
  localImages = signal<string[]>([]);
  loadingLocalImages = signal(false);

  syncSource = signal("(all)");
  syncDestId = signal("");
  syncFolder = signal("");
  startingSync = signal(false);
  loadingSyncJobs = signal(false);

  private readonly syncPollTrigger$ = new Subject<void>();

  ngOnInit(): void {
    this.loadRegistries();
    this.setupSyncPolling();
    this.loadSyncData();
  }

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

  loadRegistries() {
    this.extRegSvc.listRegistries().subscribe({
      next: (regs) => this.registries.set(regs),
    });
  }

  loadSyncData() {
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
        // Pre-select first registry if none selected
        if (!this.syncDestId() && this.registries().length > 0) {
          this.syncDestId.set(this.registries()[0].id);
        }
      },
      error: () => this.loadingLocalImages.set(false),
    });
  }

  startSync() {
    if (!this.syncDestId()) return;
    this.startingSync.set(true);
    this.extRegSvc
      .startSync({
        source_image: this.syncSource(),
        dest_registry_id: this.syncDestId(),
        dest_folder: this.syncFolder() || null,
      })
      .subscribe({
        next: () => {
          this.startingSync.set(false);
          this.syncPollTrigger$.next();
        },
        error: () => this.startingSync.set(false),
      });
  }

  getRegistryHost(registryId: string): string {
    const reg = this.registries().find((r) => r.id === registryId);
    return reg ? reg.host : registryId;
  }

  getSyncDestHost(): string {
    const reg = this.registries().find((r) => r.id === this.syncDestId());
    return reg ? reg.host : "";
  }

  getSyncPreview(): string {
    const host = this.getSyncDestHost();
    if (!host) return "";

    const reg = this.registries().find((r) => r.id === this.syncDestId());
    const username = reg?.username ?? "";
    const folder = this.syncFolder().trim();
    const source = this.syncSource();

    // Resolve the namespace prefix: folder takes priority over username
    const ns = folder || username;

    if (source === "(all)") {
      return `${host}/${ns ? ns + "/" : ""}*`;
    }

    // Extract the bare image name (last path segment, before any ":" tag)
    const nameWithTag = source.includes("/")
      ? source.split("/").at(-1)!
      : source;
    const [imageName, tag] = nameWithTag.split(":");
    const tagSuffix = tag ? `:${tag}` : "";

    const destPath = ns ? `${ns}/${imageName}` : imageName;
    return `${host}/${destPath}${tagSuffix}`;
  }

  syncStatusBadge(status: string): string {
    const map: Record<string, string> = {
      running: "badge bg-info-subtle text-info",
      done: "badge bg-success-subtle text-success",
      partial: "badge bg-warning-subtle text-warning",
      error: "badge bg-danger-subtle text-danger",
    };
    return map[status] ?? "badge bg-secondary";
  }

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
