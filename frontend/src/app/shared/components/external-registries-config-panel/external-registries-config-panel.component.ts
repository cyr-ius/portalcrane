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
import { Component, inject, OnInit, signal } from "@angular/core";
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

/** Shape of the registry add/edit form model. */
interface RegistryFormModel {
  name: string;
  host: string;
  username: string;
  password: string;
  /** "global" (admin-visible to all) or "personal" (current user only). */
  owner: "global" | "personal";
  /**
   * When false, all connections use plain HTTP — no TLS handshake at all.
   * Useful for registries running on http:// behind a private network.
   */
  use_tls: boolean;
  /**
   * Only relevant when use_tls is true.
   * When false, the TLS certificate is accepted without verification
   * (e.g. for self-signed certificates on HTTPS registries).
   */
  tls_verify: boolean;
}

@Component({
  selector: "app-external-registries-config-panel",
  // FormField directive required for [formField] bindings in template
  imports: [FormField],
  templateUrl: "./external-registries-config-panel.component.html",
  styleUrl: "./external-registries-config-panel.component.css",
})
export class ExternalRegistriesConfigPanelComponent implements OnInit {
  readonly authService = inject(AuthService);
  private readonly extRegSvc = inject(ExternalRegistryService);

  // ── Registry list ──────────────────────────────────────────────────────────

  /**
   * Local display list, kept in sync with the shared service cache.
   * Populated by loadLocalRegistries() which reads from the service signal.
   */
  readonly registries = signal<ExternalRegistry[]>([]);
  readonly showAddForm = signal(false);
  readonly editingId = signal<string | null>(null);

  // Known-registry quick-select presets (extensible with custom hosts)
  readonly registryPresets = signal<RegistryPreset[]>([...KNOWN_REGISTRY_PRESETS]);

  // Custom host input — outside the main form (preset adder only)
  readonly customHost = signal("");

  // Test and save state
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
  });

  // ──────────────────────────────────────────────────────────────────────────

  ngOnInit(): void {
    // Ensure the shared service cache is populated so this panel and all
    // other consumers (images-list, staging) start with current data.
    this.extRegSvc.loadRegistries();

    // Mirror the service signal into the local display list.
    // Using effect() would require ChangeDetectionStrategy.OnPush; instead
    // we sync after every mutating operation via refreshAndSync().
    this.syncLocalList();
  }

  // ── Registry list helpers ──────────────────────────────────────────────────

  /**
   * Copy the current service cache into the local display signal.
   * Called at init and after every create/update/delete operation.
   */
  private syncLocalList(): void {
    this.registries.set(this.extRegSvc.externalRegistries());
  }

  /**
   * Reload from the API (via the service), then sync the local display list.
   * This is the single method to call after any mutation so both the shared
   * cache and the panel list stay consistent.
   */
  private refreshAndSync(): void {
    // refreshRegistries() updates the service signal; once the HTTP call
    // completes, all computed signals (browsableRegistries in images-list,
    // externalRegistries in staging) react automatically.
    this.extRegSvc.listRegistries().subscribe({
      next: (regs) => {
        // Update the shared service cache manually since refreshRegistries()
        // does not expose a completion callback. Access via the service method.
        this.extRegSvc.setRegistriesCache(regs);
        this.syncLocalList();
      },
    });
  }

  // ── Form lifecycle ─────────────────────────────────────────────────────────

  openAddForm(): void {
    this.editingId.set(null);
    this.registryModel.set({ ...this.registryInit });
    this.customHost.set("");
    this.testResult.set(null);
    this.showAddForm.set(true);
  }

  openEditForm(reg: ExternalRegistry): void {
    this.editingId.set(reg.id);
    this.registryModel.set({
      name: reg.name,
      host: reg.host,
      username: reg.username ?? "",
      // Password intentionally left blank — backend keeps current value if empty
      password: "",
      owner: reg.owner === "global" ? "global" : "personal",
      // Preserve saved TLS settings; default to true for old entries
      use_tls: reg.use_tls ?? true,
      tls_verify: reg.tls_verify ?? true,
    });
    this.customHost.set("");
    this.testResult.set(null);
    this.showAddForm.set(true);
  }

  cancelForm(): void {
    this.showAddForm.set(false);
    this.customHost.set("");
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

  addCustomHostToPresets(): void {
    const host = this.normalizeHost(this.customHost());
    if (!host) return;

    const exists = this.registryPresets().some(
      (p) => this.normalizeHost(p.host) === host,
    );
    if (!exists) {
      this.registryPresets.set([
        { id: `custom-${host}`, name: host, host, logo: "🏢" },
        ...this.registryPresets(),
      ]);
    }

    this.registryModel.update((m) => ({
      ...m,
      host,
      name: m.name.trim() ? m.name : host,
    }));
    this.customHost.set("");
  }

  // ── Form actions ───────────────────────────────────────────────────────────

  /**
   * Save (create or update) the registry via Signal Form submit.
   *
   * On success, refreshAndSync() is called so:
   *   1. The shared ExternalRegistryService cache gets the updated list,
   *      including the `browsable` value freshly computed by the backend.
   *   2. The local panel list mirrors the cache.
   *   3. browsableRegistries in images-list and staging react automatically.
   */
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
        owner: owner === "global" ? "global" : undefined,
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

  /** Test the form's current host/credentials/TLS settings without saving. */
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

  /** Test an already-saved registry by id. */
  testSavedRegistry(id: string): void {
    this.testingRegistryId.set(id);
    this.extRegSvc.testSaved(id).subscribe({
      next: () => this.testingRegistryId.set(null),
      error: () => this.testingRegistryId.set(null),
    });
  }

  /**
   * Delete a registry by id.
   * On success, refreshAndSync() updates the shared cache and local list.
   */
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
