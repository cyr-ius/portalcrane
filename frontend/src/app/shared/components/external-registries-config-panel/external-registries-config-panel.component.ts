/**
 * Portalcrane - ExternalRegistriesConfigPanelComponent
 * Admin settings panel to manage global and personal external registries.
 *
 * Change: use_tls + tls_verify fields added to the registry form model.
 * tls_verify is only shown when use_tls is enabled.
 * Both are forwarded in create/update payloads and to testConnection().
 *
 * Refactor (catalog-check): after saveRegistry() or deleteRegistry() succeed,
 * the component now calls extRegSvc.refreshRegistries() instead of a local
 * loadRegistries(). This propagates the updated list (including the new
 * `browsable` value set by the backend) to the shared service signal so that
 * all consumers (images-list, staging) react without extra HTTP calls.
 */
import { Component, effect, inject, OnInit, signal } from "@angular/core";
import { form, FormField, required, submit } from "@angular/forms/signals";
import { firstValueFrom } from "rxjs";
import {
  KNOWN_REGISTRY_PRESETS,
  RegistryPreset,
} from "../../../core/constants/registry-presets.constants";
import { AuthService } from "../../../core/services/auth.service";
import {
  ExternalRegistry,
  ExternalRegistryService,
} from "../../../core/services/external-registry.service";

interface RegistryFormModel {
  name: string;
  host: string;
  username: string;
  password: string;
  owner: "global" | "personal";
  use_tls: boolean;
  tls_verify: boolean;
}

@Component({
  selector: "app-external-registries-config-panel",
  imports: [FormField],
  templateUrl: "./external-registries-config-panel.component.html",
  styleUrl: "./external-registries-config-panel.component.css",
})
export class ExternalRegistriesConfigPanelComponent implements OnInit {
  readonly authService = inject(AuthService);
  private readonly extRegSvc = inject(ExternalRegistryService);

  readonly loading = signal(false);

  // ── Registry list ──────────────────────────────────────────────────────────

  readonly registries = signal<ExternalRegistry[]>([]);
  readonly showAddForm = signal(false);
  readonly editingId = signal<string | null>(null);
  readonly registryPresets = signal<RegistryPreset[]>([...KNOWN_REGISTRY_PRESETS]);
  readonly savingRegistry = signal(false);
  readonly testingNew = signal(false);
  readonly testingRegistryId = signal<string | null>(null);
  readonly testResult = signal<{
    reachable: boolean;
    auth_ok: boolean;
    message: string;
  } | null>(null);

  // ── Signal Form – registry add / edit ──────────────────────────────────────

  /** Blank defaults; spread on every form open to avoid shared-reference bugs. */
  private readonly registryInit: RegistryFormModel = {
    name: "",
    host: "",
    username: "",
    password: "",
    owner: "personal",
    use_tls: true,
    tls_verify: true,
  };

  /** Reactive model backing the Signal Form. */
  readonly registryModel = signal<RegistryFormModel>({ ...this.registryInit });

  /**
   * Signal Form definition.
   * name and host are required; credentials and flags are optional.
   */
  readonly registryForm = form(this.registryModel, (p) => {
    required(p.name);
    required(p.host);
    required(p.username);
    required(p.password);
  });

  // ──────────────────────────────────────────────────────────────────────────

  ngOnInit(): void {
    this.loading.set(true);
    this.extRegSvc.listRegistries().subscribe({
      next: (regs) => {
        this.extRegSvc.setRegistriesCache(regs);
        this.loading.set(false); // ← maintenant APRÈS la réponse
      },
      error: () => this.loading.set(false),
    });
  }

  constructor() {
    effect(() => {
      this.registries.set(this.extRegSvc.externalRegistries());
    });
  }

  // ── Registry list helpers ──────────────────────────────────────────────────

  private syncLocalList(): void {
    this.registries.set(this.extRegSvc.externalRegistries());
  }

  private refreshAndSync(): void {
    this.extRegSvc.listRegistries().subscribe({
      next: (regs) => {
        this.extRegSvc.setRegistriesCache(regs);
        this.syncLocalList();
      },
    });
  }

  // ── Form lifecycle ─────────────────────────────────────────────────────────

  openAddForm(): void {
    this.editingId.set(null);
    this.registryModel.set({ ...this.registryInit });
    this.testResult.set(null);
    this.showAddForm.set(true);
  }

  openEditForm(reg: ExternalRegistry): void {
    this.editingId.set(reg.id);
    this.registryModel.set({
      name: reg.name,
      host: reg.host,
      username: reg.username ?? "",
      password: "",
      owner: reg.owner === "global" ? "global" : "personal",
      use_tls: reg.use_tls ?? true,
      tls_verify: reg.tls_verify ?? true,
    });
    this.testResult.set(null);
    this.showAddForm.set(true);
  }

  cancelForm(): void {
    this.showAddForm.set(false);
    this.testResult.set(null);
  }

  // ── Preset helpers ─────────────────────────────────────────────────────────

  selectPreset(preset: RegistryPreset): void {
    const host = this.normalizeHost(preset.host);
    this.registryModel.update((m) => ({
      ...m,
      host,
      name: m.name.trim() ? m.name : preset.name,
    }));
  }

  // ── Form actions ───────────────────────────────────────────────────────────


  saveRegistry(): void {
    submit(this.registryForm, async (f) => {
      const { name, host, username, password, owner, use_tls, tls_verify } = f().value();
      this.savingRegistry.set(true);
      this.testResult.set(null);

      const useTls = use_tls ?? true;
      const payload = {
        name: name!,
        host: host!,
        username,
        password,
        owner: owner === "global" ? "global" : "personal",
        use_tls: useTls,
        // tls_verify is only meaningful when use_tls is true
        tls_verify: useTls ? (tls_verify ?? true) : true,
      };
      const id = this.editingId();
      const request$ = id
        ? this.extRegSvc.updateRegistry(id, payload)
        : this.extRegSvc.createRegistry(payload);

      try {
        await firstValueFrom(request$);
        this.showAddForm.set(false);
        this.editingId.set(null);
        f().reset({ ...this.registryInit });
        // Refresh shared cache + local list — browsable field is now up to date
        this.refreshAndSync();
      } catch (err: unknown) {
        const httpErr = err as { error?: { detail?: string } };
        this.testResult.set({
          reachable: false,
          auth_ok: false,
          message: httpErr?.error?.detail ?? "Failed to save.",
        });
      } finally {
        this.savingRegistry.set(false);
      }
    });
  }

  testNewConnection(): void {
    const { host, username, password, use_tls, tls_verify } = this.registryModel();
    this.testingNew.set(true);
    this.testResult.set(null);

    const effectiveTlsVerify = (use_tls ?? true) ? (tls_verify ?? true) : false;
    this.extRegSvc
      .testConnection(host, username, password, { use_tls: use_tls ?? true, tls_verify: effectiveTlsVerify })
      .subscribe({
        next: (res) => {
          this.testResult.set(res);
          this.testingNew.set(false);
        },
        error: () => {
          this.testResult.set({
            reachable: false,
            auth_ok: false,
            message: "Test failed.",
          });
          this.testingNew.set(false);
        },
      });
  }

  testSavedRegistry(id: string): void {
    this.testingRegistryId.set(id);
    this.extRegSvc.testSaved(id).subscribe({
      next: () => this.testingRegistryId.set(null),
      error: () => this.testingRegistryId.set(null),
    });
  }

  deleteRegistry(id: string): void {
    this.extRegSvc.deleteRegistry(id).subscribe({
      next: () => this.refreshAndSync(),
    });
  }

  // ── Helpers ────────────────────────────────────────────────────────────────

  normalizeHost(raw: string): string {
    return raw.trim().replace(/^https?:\/\//, "").replace(/\/$/, "");
  }
}
