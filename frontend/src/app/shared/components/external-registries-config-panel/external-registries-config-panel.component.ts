import { Component, inject, signal } from "@angular/core";
import { KNOWN_REGISTRY_PRESETS, RegistryPreset } from "../../../core/constants/registry-presets.constants";
import { AuthService } from "../../../core/services/auth.service";
import { ExternalRegistry, ExternalRegistryService } from "../../../core/services/external-registry.service";

@Component({
  selector: "app-external-registries-config-panel",
  imports: [],
  templateUrl: "./external-registries-config-panel.component.html",
  styleUrl: "./external-registries-config-panel.component.css",
})
export class ExternalRegistriesConfigPanelComponent {
  authService = inject(AuthService);

  private extRegSvc = inject(ExternalRegistryService);

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

  ngOnInit(): void {
    this.loadRegistries();
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

}
