/**
 * Portalcrane - Settings Component
 * Updated with two new tabs:
 *  - "External Registries": CRUD for saved registries + connectivity test
 *  - "Sync": trigger and monitor sync jobs from local → external registry
 */
import { SlicePipe } from "@angular/common";
import { Component, inject, OnInit, signal } from "@angular/core";
import { FormsModule } from "@angular/forms";
import { AboutService } from "../../core/services/about.service";
import {
  AppConfigService,
  TRIVY_SEVERITIES,
  TrivySeverity,
} from "../../core/services/app-config.service";
import { AuthService } from "../../core/services/auth.service";
import {
  ExternalRegistry,
  ExternalRegistryService,
  SyncJob,
} from "../../core/services/external-registry.service";
import { RegistryService } from "../../core/services/registry.service";
import { ThemeService } from "../../core/services/theme.service";
import { VulnConfigPanelComponent } from "../../shared/components/vuln-config-panel/vuln-config-panel.component";

/** Tabs available in the Settings page. */
type SettingsTab =
  | "appearance"
  | "registries"
  | "sync"
  | "advanced"
  | "account";

const SEVERITY_STYLE: Record<
  TrivySeverity,
  { active: string; inactive: string; icon: string }
> = {
  CRITICAL: {
    active: "btn btn-sm btn-danger",
    inactive: "btn btn-sm btn-outline-danger",
    icon: "bi-radioactive",
  },
  HIGH: {
    active: "btn btn-sm btn-warning text-dark",
    inactive: "btn btn-sm btn-outline-warning",
    icon: "bi-exclamation-triangle-fill",
  },
  MEDIUM: {
    active: "btn btn-sm btn-info text-dark",
    inactive: "btn btn-sm btn-outline-info",
    icon: "bi-exclamation-circle",
  },
  LOW: {
    active: "btn btn-sm btn-secondary",
    inactive: "btn btn-sm btn-outline-secondary",
    icon: "bi-info-circle",
  },
  UNKNOWN: {
    active: "btn btn-sm btn-dark",
    inactive: "btn btn-sm btn-outline-dark",
    icon: "bi-question-circle",
  },
};

@Component({
  selector: "app-settings",
  imports: [FormsModule, VulnConfigPanelComponent, SlicePipe],
  templateUrl: "./settings.component.html",
  styleUrl: "./settings.component.css",
})
export class SettingsComponent implements OnInit {
  themeService = inject(ThemeService);
  authService = inject(AuthService);
  configService = inject(AppConfigService);
  aboutService = inject(AboutService);
  private extRegSvc = inject(ExternalRegistryService);
  private registrySvc = inject(RegistryService);

  readonly severities = TRIVY_SEVERITIES;

  // ── Tab state ──────────────────────────────────────────────────────────────
  activeTab = signal<SettingsTab>("appearance");

  // ── External registries ────────────────────────────────────────────────────
  registries = signal<ExternalRegistry[]>([]);
  showAddForm = signal(false);
  editingId = signal<string | null>(null);

  // Add / edit form fields
  formName = signal("");
  formHost = signal("");
  formUser = signal("");
  formPass = signal("");

  savingRegistry = signal(false);
  testingRegistryId = signal<string | null>(null);
  testResult = signal<{
    reachable: boolean;
    auth_ok: boolean;
    message: string;
  } | null>(null);
  testingNew = signal(false);

  // ── Sync ───────────────────────────────────────────────────────────────────
  syncJobs = signal<SyncJob[]>([]);
  localImages = signal<string[]>([]);
  loadingLocalImages = signal(false);

  syncSource = signal("(all)");
  syncDestId = signal("");
  syncFolder = signal("");
  startingSync = signal(false);
  loadingSyncJobs = signal(false);

  ngOnInit(): void {
    if (!this.configService.serverConfig()) {
      this.configService.loadConfig().subscribe();
    }
    this.aboutService.load();
    this.loadRegistries();
  }

  setTab(tab: SettingsTab) {
    this.activeTab.set(tab);
    if (tab === "sync") {
      this.loadSyncData();
    }
  }

  // ── Registry CRUD helpers ──────────────────────────────────────────────────

  loadRegistries() {
    this.extRegSvc.listRegistries().subscribe({
      next: (regs) => this.registries.set(regs),
    });
  }

  openAddForm() {
    this.editingId.set(null);
    this.formName.set("");
    this.formHost.set("");
    this.formUser.set("");
    this.formPass.set("");
    this.testResult.set(null);
    this.showAddForm.set(true);
  }

  openEditForm(reg: ExternalRegistry) {
    this.editingId.set(reg.id);
    this.formName.set(reg.name);
    this.formHost.set(reg.host);
    this.formUser.set(reg.username);
    this.formPass.set(""); // Do not pre-fill password
    this.testResult.set(null);
    this.showAddForm.set(true);
  }

  cancelForm() {
    this.showAddForm.set(false);
    this.editingId.set(null);
    this.testResult.set(null);
  }

  saveRegistry() {
    this.savingRegistry.set(true);
    const id = this.editingId();
    const payload = {
      name: this.formName(),
      host: this.formHost(),
      username: this.formUser(),
      password: this.formPass(),
    };
    const obs = id
      ? this.extRegSvc.updateRegistry(id, payload)
      : this.extRegSvc.createRegistry(payload);

    obs.subscribe({
      next: () => {
        this.savingRegistry.set(false);
        this.showAddForm.set(false);
        this.editingId.set(null);
        this.loadRegistries();
      },
      error: () => this.savingRegistry.set(false),
    });
  }

  deleteRegistry(id: string) {
    this.extRegSvc.deleteRegistry(id).subscribe({
      next: () => this.loadRegistries(),
    });
  }

  testNewConnection() {
    this.testingNew.set(true);
    this.testResult.set(null);
    this.extRegSvc
      .testConnection(this.formHost(), this.formUser(), this.formPass())
      .subscribe({
        next: (res) => {
          this.testResult.set(res);
          this.testingNew.set(false);
        },
        error: () => this.testingNew.set(false),
      });
  }

  testSavedRegistry(id: string) {
    this.testingRegistryId.set(id);
    this.extRegSvc.testSavedRegistry(id).subscribe({
      next: () => this.testingRegistryId.set(null),
      error: () => this.testingRegistryId.set(null),
    });
  }

  // ── Sync helpers ───────────────────────────────────────────────────────────

  loadSyncData() {
    this.loadingSyncJobs.set(true);
    this.extRegSvc.listSyncJobs().subscribe({
      next: (jobs) => {
        this.syncJobs.set(jobs);
        this.loadingSyncJobs.set(false);
      },
      error: () => this.loadingSyncJobs.set(false),
    });

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
          setTimeout(() => this.loadSyncData(), 500);
        },
        error: () => this.startingSync.set(false),
      });
  }

  // ── Registry lookup helpers (called from template to avoid lambdas) ────────

  /**
   * Return the host label of a registry by its ID.
   * Falls back to the raw ID if not found.
   * Used in sync history rows to display the destination host.
   */
  getRegistryHost(registryId: string): string {
    const reg = this.registries().find((r) => r.id === registryId);
    return reg ? reg.host : registryId;
  }

  /**
   * Return the host of the currently selected sync destination registry.
   * Used in the preview line of the sync form.
   */
  getSyncDestHost(): string {
    const reg = this.registries().find((r) => r.id === this.syncDestId());
    return reg ? reg.host : "";
  }

  // ── Status badge / icon helpers ────────────────────────────────────────────

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
      running: "bi-arrow-repeat",
      done: "bi-check-circle",
      partial: "bi-exclamation-circle",
      error: "bi-x-circle",
    };
    return map[status] ?? "bi-circle";
  }

  // ── Theme / severity helpers ───────────────────────────────────────────────

  getSeverityClass(sev: TrivySeverity): string {
    const selected = this.configService.vulnSeverities().includes(sev);
    return selected ? SEVERITY_STYLE[sev].active : SEVERITY_STYLE[sev].inactive;
  }

  getSeverityIcon(sev: TrivySeverity): string {
    return SEVERITY_STYLE[sev].icon;
  }
}
