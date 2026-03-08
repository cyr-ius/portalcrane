/**
 * Portalcrane - Settings Component
 * Updated with two new tabs:
 *  - "External Registries": CRUD for saved registries + connectivity test
 *  - "Sync": trigger and monitor sync jobs from local → external registry
 *
 * Changes:
 *  - Added sync job polling: auto-refreshes every 3 s while any job is "running",
 *    stops automatically when all jobs are terminal (done/partial/error).
 *    Polling starts when entering the Sync tab and stops on component destroy.
 *  - Added getSyncPreview(): computes the correct destination path by applying
 *    the same namespace-rewriting logic as the backend (_rewrite_image_name_for_sync):
 *    only the last segment of the source image name is kept, prefixed with
 *    dest_folder (if set) or the registry username.
 */
import { SlicePipe } from "@angular/common";
import {
  Component,
  computed,
  DestroyRef,
  inject,
  OnInit,
  signal,
} from "@angular/core";
import { takeUntilDestroyed } from "@angular/core/rxjs-interop";
import { FormsModule } from "@angular/forms";
import { Router } from "@angular/router";
import { Subject, switchMap, timer } from "rxjs";
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
import { AuditEvent, SystemService } from "../../core/services/system.service";
import { ThemeService } from "../../core/services/theme.service";
import { AboutConfigPanel } from "../../shared/components/about-config-panel/about-config-panel";
import { AccountsConfigPanel } from "../../shared/components/accounts-config-panel/accounts-config-panel";
import { FoldersConfigPanel } from "../../shared/components/folders-config-panel/folders-config-panel.component";
import { OidcConfigPanel } from "../../shared/components/oidc-config-panel/oidc-config-panel";
import { VulnConfigPanelComponent } from "../../shared/components/vuln-config-panel/vuln-config-panel.component";
import {
  KNOWN_REGISTRY_PRESETS,
  RegistryPreset,
} from "../../core/constants/registry-presets.constants";

/** Tabs available in the Settings page. */
type SettingsTab =
  | "vulnerabilities"
  | "accounts"
  | "folders"
  | "registries"
  | "sync"
  | "audit"
  | "oidc"
  | "about";

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
  imports: [
    FormsModule,
    VulnConfigPanelComponent,
    SlicePipe,
    OidcConfigPanel,
    AccountsConfigPanel,
    FoldersConfigPanel,
    AboutConfigPanel,
  ],
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
  private systemService = inject(SystemService);
  private router = inject(Router);
  private destroyRef = inject(DestroyRef);

  readonly severities = TRIVY_SEVERITIES;

  // ── Tab state ──────────────────────────────────────────────────────────────
  activeTab = signal<SettingsTab>("vulnerabilities");

  // ── External registries ────────────────────────────────────────────────────
  registries = signal<ExternalRegistry[]>([]);
  showAddForm = signal(false);
  editingId = signal<string | null>(null);

  // Add / edit form fields
  formName = signal("");
  formHost = signal("");
  customHost = signal("");
  registryPresets = signal<RegistryPreset[]>([...KNOWN_REGISTRY_PRESETS]);
  formUser = signal("");
  formPass = signal("");
  formOwner = signal<string>("personal");

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

  /**
   * True when at least one sync job is still running.
   * Used to drive the polling loop: polling is active only when this is true.
   */
  private readonly hasSyncRunning = computed(() =>
    this.syncJobs().some((j) => j.status === "running"),
  );

  /**
   * Subject that triggers a new polling cycle each time a sync job is started.
   * Emitting on this subject (re)starts the 3-second poll loop.
   */
  private readonly syncPollTrigger$ = new Subject<void>();

  // ── Audit logs ─────────────────────────────────────────────────────────────
  auditLogs = signal<AuditEvent[]>([]);
  loadingAuditLogs = signal(false);
  auditLogError = signal<string | null>(null);

  ngOnInit(): void {
    if (!this.authService.currentUser()?.is_admin) {
      this.router.navigate(["/dashboard"]);
      return;
    }
    if (!this.configService.serverConfig()) {
      this.configService.loadConfig().subscribe();
    }
    this.aboutService.load();
    this.loadRegistries();
    this.setupSyncPolling();
  }

  // ── Sync polling ───────────────────────────────────────────────────────────

  /**
   * Set up automatic sync-job refresh.
   *
   * Each time syncPollTrigger$ emits (i.e. when a sync job is started or the
   * Sync tab is opened), a timer fires every 3 seconds and fetches the job
   * list from the backend.  The inner observable is kept alive as long as at
   * least one job has status "running"; it stops automatically once all jobs
   * reach a terminal state, avoiding unnecessary network traffic.
   *
   * The outer pipe uses takeUntilDestroyed so the subscription is cleaned up
   * when the component is destroyed.
   */
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

  setTab(tab: SettingsTab) {
    this.activeTab.set(tab);
    if (tab === "sync") {
      this.loadSyncData();
    }
    if (tab === "audit") {
      this.loadAuditLogs();
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
    this.customHost.set("");
    this.testResult.set(null);
    this.showAddForm.set(true);
    this.formOwner.set("personal");
  }

  openEditForm(reg: ExternalRegistry) {
    this.editingId.set(reg.id);
    this.formName.set(reg.name);
    this.formHost.set(reg.host);
    this.formUser.set(reg.username);
    this.formPass.set(""); // Do not pre-fill password
    this.customHost.set("");
    this.testResult.set(null);
    this.showAddForm.set(true);
    this.formOwner.set(reg.owner === "global" ? "global" : "personal");
  }

  cancelForm() {
    this.showAddForm.set(false);
    this.editingId.set(null);
    this.customHost.set("");
    this.testResult.set(null);
  }

  private normalizeHost(host: string): string {
    return host
      .trim()
      .toLowerCase()
      .replace(/^https?:\/\//, "")
      .split("/")[0]
      .trim();
  }

  selectPreset(preset: RegistryPreset) {
    const host = this.normalizeHost(preset.host);
    this.formHost.set(host);
    if (!this.formName().trim()) {
      this.formName.set(preset.name);
    }
  }

  addCustomHostToPresets() {
    const host = this.normalizeHost(this.customHost());
    if (!host) return;

    const exists = this.registryPresets().some(
      (p) => this.normalizeHost(p.host) === host,
    );
    if (!exists) {
      this.registryPresets.set([
        {
          id: `custom-${host}`,
          name: host,
          host,
          logo: "🏢",
        },
        ...this.registryPresets(),
      ]);
    }

    this.formHost.set(host);
    if (!this.formName().trim()) {
      this.formName.set(host);
    }
    this.customHost.set("");
  }

  saveRegistry() {
    this.savingRegistry.set(true);
    const id = this.editingId();
    const payload = {
      name: this.formName(),
      host: this.formHost(),
      username: this.formUser(),
      password: this.formPass(),
      owner: this.formOwner() === "global" ? "global" : undefined,
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
    this.extRegSvc.testSaved(id).subscribe({
      next: () => this.testingRegistryId.set(null),
      error: () => this.testingRegistryId.set(null),
    });
  }

  // ── Sync helpers ───────────────────────────────────────────────────────────

  /**
   * Load sync jobs and local images, then kick off the polling loop.
   * Called when switching to the Sync tab or clicking the refresh button.
   */
  loadSyncData() {
    this.loadingSyncJobs.set(true);
    // Trigger the polling loop — it will immediately fire one request
    // and keep polling every 3 s while any job is running.
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
          // Kick off (or restart) the polling loop now that a job is running
          this.syncPollTrigger$.next();
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

  /**
   * Compute the destination image path preview shown below the folder field.
   *
   * Mirrors the backend _rewrite_image_name_for_sync() logic:
   *   - Only the LAST segment of the source image is kept (bare image name).
   *   - If dest_folder is set, it is used as the namespace prefix.
   *   - Otherwise, the registry username is used as the namespace prefix.
   *   - "(all)" source renders as a "*" wildcard.
   *
   * Examples (registry username = "cyrius44"):
   *   source="cyrius44/alpine/ansible:2.20.0", folder=""     → "cyrius44/ansible:2.20.0"
   *   source="cyrius44/alpine/ansible:2.20.0", folder="prod" → "prod/ansible:2.20.0"
   *   source="(all)",                          folder=""     → "cyrius44/*"
   */
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
      running: "bi-arrow-repeat text-info",
      done: "bi-check-circle text-success",
      partial: "bi-exclamation-circle text-warning",
      error: "bi-x-circle text-danger",
    };
    return map[status] ?? "bi-circle";
  }

  async loadAuditLogs() {
    this.loadingAuditLogs.set(true);
    this.auditLogError.set(null);
    try {
      const logs = await this.systemService.getAuditLogs(200);
      this.auditLogs.set(logs);
    } catch {
      this.auditLogError.set("Unable to load audit logs.");
    } finally {
      this.loadingAuditLogs.set(false);
    }
  }

  formatAuditTimestamp(timestamp: string): string {
    const date = new Date(timestamp);
    if (Number.isNaN(date.getTime())) return timestamp;
    return date.toLocaleString();
  }

  prettyAuditPayload(entry: AuditEvent): string {
    return JSON.stringify(entry, null, 2);
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
