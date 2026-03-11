/**
 * Portalcrane - Account Modal
 * User profile panel: account info,
 * personal access tokens, and personal external registries.
 * Accessible to ALL authenticated users via the sidebar user zone.
 *
 * Change: tls_verify boolean field added to the registry form model so users
 * can declare self-signed registries from their personal account panel.
 */
import { Component, inject, OnInit, output, signal } from "@angular/core";
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
import { PersonalTokensPanelComponent } from "../personal-tokens-panel/personal-tokens-panel.component";

/** Internal shape of the registry form model. */
interface RegistryFormModel {
  name: string;
  host: string;
  username: string;
  password: string;
  /**
   * When false, plain HTTP is used for all connections.
   * Useful for private registries running without TLS.
   */
  use_tls: boolean;
  /**
   * Only relevant when use_tls is true.
   * When false, the TLS certificate is not validated (self-signed allowed).
   */
  tls_verify: boolean;
}

@Component({
  selector: "app-account-modal",
  // FormField directive is required for [formField] bindings in the template
  imports: [PersonalTokensPanelComponent, FormField],
  templateUrl: "./account-modal.component.html",
  styleUrl: "./account-modal.component.css",
})
export class AccountModalComponent implements OnInit {
  readonly close = output<void>();
  readonly authService = inject(AuthService);
  private readonly extRegSvc = inject(ExternalRegistryService);

  readonly currentUser = this.authService.currentUser;

  // ── Personal external registries list ──────────────────────────────────────
  readonly registries = signal<ExternalRegistry[]>([]);
  readonly showAddForm = signal(false);

  // Tracks which registry is being edited (null = create mode)
  readonly editingId = signal<string | null>(null);

  // Known-registry quick-select presets (can be extended with custom hosts)
  readonly registryPresets = signal<RegistryPreset[]>([...KNOWN_REGISTRY_PRESETS]);

  // Custom host input for the preset adder (not part of the main form)
  readonly customHost = signal("");

  // Test-connection state and result
  readonly testingNew = signal(false);
  readonly savingRegistry = signal(false);
  readonly testResult = signal<{
    reachable: boolean;
    auth_ok: boolean;
    message: string;
  } | null>(null);

  // ── Signal Form – registry create / edit ───────────────────────────────────

  /** Initial (blank) registry form values — used to reset the form. */
  private readonly registryInit: RegistryFormModel = {
    name: "",
    host: "",
    username: "",
    password: "",
    use_tls: true,
    tls_verify: true,
  };

  /**
   * Reactive model backing the Signal Form.
   * Spread to avoid mutating the init object on reset.
   */
  readonly registryModel = signal<RegistryFormModel>({ ...this.registryInit });

  /**
   * Signal Form definition.
   * Only name and host are required; username/password/tls_verify are optional.
   */
  readonly registryForm = form(this.registryModel, (p) => {
    required(p.name);
    required(p.host);
  });

  // ──────────────────────────────────────────────────────────────────────────

  ngOnInit(): void {
    this.loadRegistries();
  }

  // ── Registry list helpers ──────────────────────────────────────────────────

  /** Fetch personal (non-global) registries from the backend. */
  loadRegistries(): void {
    this.extRegSvc.listRegistries().subscribe({
      next: (list: ExternalRegistry[]) =>
        this.registries.set(list.filter((r) => r.owner !== "global")),
    });
  }

  // ── Form lifecycle ─────────────────────────────────────────────────────────

  /** Open the form in create mode (blank fields). */
  openAddForm(): void {
    this.editingId.set(null);
    this.registryModel.set({ ...this.registryInit });
    this.customHost.set("");
    this.testResult.set(null);
    this.showAddForm.set(true);
  }

  /** Open the form in edit mode (pre-filled with existing registry data). */
  openEditForm(reg: ExternalRegistry): void {
    this.editingId.set(reg.id);
    this.registryModel.set({
      name: reg.name,
      host: reg.host,
      username: reg.username ?? "",
      // Password is intentionally left blank — backend keeps current value if empty
      password: "",
      // Preserve saved TLS settings; default to true for old entries
      use_tls: reg.use_tls ?? true,
      tls_verify: reg.tls_verify ?? true,
    });
    this.customHost.set("");
    this.testResult.set(null);
    this.showAddForm.set(true);
  }

  /** Close and reset the form without saving. */
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

  /** Save (create or update) the registry via Signal Form submit. */
  saveRegistry(): void {
    submit(this.registryForm, async (f) => {
      const { name, host, username, password, use_tls, tls_verify } = f().value();
      this.savingRegistry.set(true);
      this.testResult.set(null);

      const useTls = use_tls ?? true;
      const payload = {
        name: name!,
        host: host!,
        username,
        password,
        use_tls: useTls,
        tls_verify: useTls ? (tls_verify ?? true) : true,
      };
      const id = this.editingId();
      const request$ = id
        ? this.extRegSvc.updateRegistry(id, payload)
        : this.extRegSvc.createRegistry(payload);

      try {
        await firstValueFrom(request$);
        this.showAddForm.set(false);
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

  /** Test the current host/credentials/TLS settings without saving. */
  testNewRegistry(): void {
    const { host, username, password, use_tls, tls_verify } = this.registryModel();
    this.testingNew.set(true);
    this.testResult.set(null);

    this.extRegSvc
      .testConnection(host, username, password, { use_tls: use_tls ?? true, tls_verify: tls_verify ?? true })
      .subscribe({
      next: (result) => {
        this.testResult.set(result);
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

  /** Delete a registry by id. */
  deleteRegistry(id: string): void {
    this.extRegSvc.deleteRegistry(id).subscribe({
      next: () => this.loadRegistries(),
    });
  }

  // ── Utility ────────────────────────────────────────────────────────────────

  /** Strip protocol prefix and trailing paths to keep only the host part. */
  private normalizeHost(host: string): string {
    return host
      .trim()
      .toLowerCase()
      .replace(/^https?:\/\//, "")
      .split("/")[0]
      .trim();
  }
}
