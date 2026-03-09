/**
 * Portalcrane - ExternalRegistriesConfigPanelComponent
 * Admin settings panel to manage global and personal external registries.
 *
 * MIGRATION: Registry add/edit form now uses Angular Signal Forms (form / FormField)
 * instead of bare signal-per-field bindings with manual payload assembly.
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
  };

  /** Reactive model backing the Signal Form. */
  readonly registryModel = signal<RegistryFormModel>({ ...this.registryInit });

  /**
   * Signal Form definition.
   * name and host are required; credentials are optional.
   * owner is not validated (always has a default value).
   */
  readonly registryForm = form(this.registryModel, (p) => {
    required(p.name);
    required(p.host);
  });

  // ──────────────────────────────────────────────────────────────────────────

  ngOnInit(): void {
    this.loadRegistries();
  }

  /** Fetch the full registry list from the backend. */
  loadRegistries(): void {
    this.extRegSvc.listRegistries().subscribe({
      next: (regs) => this.registries.set(regs),
    });
  }

  // ── Form lifecycle ─────────────────────────────────────────────────────────

  /** Open the form in create mode. */
  openAddForm(): void {
    this.editingId.set(null);
    this.registryModel.set({ ...this.registryInit });
    this.customHost.set("");
    this.testResult.set(null);
    this.showAddForm.set(true);
  }

  /** Open the form in edit mode, pre-filled with the existing registry values. */
  openEditForm(reg: ExternalRegistry): void {
    this.editingId.set(reg.id);
    this.registryModel.set({
      name: reg.name,
      host: reg.host,
      username: reg.username ?? "",
      // Password is intentionally left blank — backend keeps current value when empty
      password: "",
      owner: reg.owner === "global" ? "global" : "personal",
    });
    this.customHost.set("");
    this.testResult.set(null);
    this.showAddForm.set(true);
  }

  /** Close and reset the form without saving. */
  cancelForm(): void {
    this.showAddForm.set(false);
    this.editingId.set(null);
    this.customHost.set("");
    this.testResult.set(null);
  }

  // ── Preset helpers ─────────────────────────────────────────────────────────

  /** Apply a known-registry preset to the host (and name if still empty). */
  selectPreset(preset: RegistryPreset): void {
    const host = this.normalizeHost(preset.host);
    this.registryModel.update((m) => ({
      ...m,
      host,
      name: m.name.trim() ? m.name : preset.name,
    }));
  }

  /** Add a custom host to the preset list and apply it to the host field. */
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

  /** Save (create or update) the registry via Signal Form submit. */
  saveRegistry(): void {
    submit(this.registryForm, async (f) => {
      const { name, host, username, password, owner } = f().value();
      this.savingRegistry.set(true);
      this.testResult.set(null);

      const payload = {
        name: name!,
        host: host!,
        username,
        password,
        owner: owner === "global" ? "global" : undefined,
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
        this.loadRegistries();
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

  /** Test the form's current host/username/password without saving. */
  testNewConnection(): void {
    const { host, username, password } = this.registryModel();
    this.testingNew.set(true);
    this.testResult.set(null);

    this.extRegSvc.testConnection(host, username, password).subscribe({
      next: (res) => {
        this.testResult.set(res);
        this.testingNew.set(false);
      },
      error: () => {
        this.testResult.set({ reachable: false, auth_ok: false, message: "Test failed." });
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

  /** Delete a registry by id. */
  deleteRegistry(id: string): void {
    this.extRegSvc.deleteRegistry(id).subscribe({
      next: () => this.loadRegistries(),
    });
  }

  // ── Utility ────────────────────────────────────────────────────────────────

  /** Strip protocol and paths, keep only the bare hostname. */
  private normalizeHost(host: string): string {
    return host
      .trim()
      .toLowerCase()
      .replace(/^https?:\/\//, "")
      .split("/")[0]
      .trim();
  }
}
