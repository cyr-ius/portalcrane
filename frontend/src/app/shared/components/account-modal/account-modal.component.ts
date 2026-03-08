/**
 * Portalcrane - Account Modal
 * User profile panel: account info,
 * personal access tokens, and personal external registries.
 * Accessible to ALL authenticated users via the sidebar user zone.
 */
import { Component, inject, OnInit, output, signal } from "@angular/core";
import { AuthService } from "../../../core/services/auth.service";
import {
  ExternalRegistry,
  ExternalRegistryService,
} from "../../../core/services/external-registry.service";
import { PersonalTokensPanelComponent } from "../personal-tokens-panel/personal-tokens-panel.component";
import {
  KNOWN_REGISTRY_PRESETS,
  RegistryPreset,
} from "../../../core/constants/registry-presets.constants";

@Component({
  selector: "app-account-modal",
  imports: [PersonalTokensPanelComponent],
  templateUrl: "./account-modal.component.html",
  styleUrl: "./account-modal.component.css",
})
export class AccountModalComponent implements OnInit {
  readonly close = output<void>();
  readonly authService = inject(AuthService);
  private readonly extRegSvc = inject(ExternalRegistryService);

  readonly currentUser = this.authService.currentUser;


  // ── Personal external registries ───────────────────────────────────────────
  registries = signal<ExternalRegistry[]>([]);
  showAddForm = signal(false);
  editingId = signal<string | null>(null);
  formName = signal("");
  formHost = signal("");
  customHost = signal("");
  registryPresets = signal<RegistryPreset[]>([...KNOWN_REGISTRY_PRESETS]);
  formUser = signal("");
  formPass = signal("");
  savingRegistry = signal(false);
  testResult = signal<{
    reachable: boolean;
    auth_ok: boolean;
    message: string;
  } | null>(null);
  testingNew = signal(false);

  ngOnInit(): void {
    this.loadRegistries();
  }


  // ── Personal external registries ───────────────────────────────────────────

  loadRegistries(): void {
    this.extRegSvc.listRegistries().subscribe({
      next: (list: ExternalRegistry[]) =>
        this.registries.set(list.filter((r) => r.owner !== "global")),
    });
  }

  openAddForm(): void {
    this.editingId.set(null);
    this.formName.set("");
    this.formHost.set("");
    this.formUser.set("");
    this.formPass.set("");
    this.customHost.set("");
    this.testResult.set(null);
    this.showAddForm.set(true);
  }

  openEditForm(reg: ExternalRegistry): void {
    this.editingId.set(reg.id);
    this.formName.set(reg.name);
    this.formHost.set(reg.host);
    this.formUser.set(reg.username || "");
    this.formPass.set("");
    this.customHost.set("");
    this.testResult.set(null);
    this.showAddForm.set(true);
  }

  cancelForm(): void {
    this.showAddForm.set(false);
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

  selectPreset(preset: RegistryPreset): void {
    const host = this.normalizeHost(preset.host);
    this.formHost.set(host);
    if (!this.formName().trim()) {
      this.formName.set(preset.name);
    }
  }

  addCustomHostToPresets(): void {
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

  saveRegistry(): void {
    this.savingRegistry.set(true);
    const payload = {
      name: this.formName(),
      host: this.formHost(),
      username: this.formUser(),
      password: this.formPass(),
    };
    const id = this.editingId();
    const request = id
      ? this.extRegSvc.updateRegistry(id, payload)
      : this.extRegSvc.createRegistry(payload);

    request.subscribe({
      next: () => {
        this.savingRegistry.set(false);
        this.showAddForm.set(false);
        this.loadRegistries();
      },
      error: (err: any) => {
        this.savingRegistry.set(false);
        this.testResult.set({
          reachable: false,
          auth_ok: false,
          message: err?.error?.detail || "Failed to save.",
        });
      },
    });
  }

  testNewRegistry(): void {
    this.testingNew.set(true);
    this.testResult.set(null);
    this.extRegSvc
      .testConnection(this.formHost(), this.formUser(), this.formPass())
      .subscribe({
        next: (result) => {
          this.testResult.set(result);
          this.testingNew.set(false);
        },
        error: () => {
          this.testResult.set({ reachable: false, auth_ok: false, message: "Test failed." });
          this.testingNew.set(false);
        },
      });
  }

  deleteRegistry(id: string): void {
    this.extRegSvc.deleteRegistry(id).subscribe({
      next: () => this.loadRegistries(),
    });
  }
}
