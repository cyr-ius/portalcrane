/**
 * Portalcrane - RegistryFormPanelComponent
 *
 * Shared add/edit form + list for external registries. Used both by the admin
 * settings page (global + personal scope) and by the personal Account drawer
 * (personal scope only). Behaviour is tuned through inputs:
 *
 *   - allowGlobalScope : show the global/personal visibility selector (admin
 *                        only) and include global registries in the list.
 *   - allowCustomHost  : show the "add custom host to presets" input.
 *   - compact          : denser layout for the narrow Account drawer.
 *
 * The displayed list is derived from the shared ExternalRegistryService cache
 * so create/update/delete propagate to every other consumer (images-list,
 * staging, sync panel) without extra HTTP calls.
 */
import { Component, computed, inject, input, OnInit, signal } from "@angular/core";
import { form, FormField, required, submit } from "@angular/forms/signals";
import { TranslatePipe, TranslateService } from "@ngx-translate/core";
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

/** Internal shape of the registry form model. */
interface RegistryFormModel {
  name: string;
  host: string;
  username: string;
  password: string;
  owner: "global" | "personal";
  /** When false, plain HTTP is used (registries running without TLS). */
  use_tls: boolean;
  /** Only relevant when use_tls is true; false allows self-signed certs. */
  tls_verify: boolean;
}

@Component({
  selector: "app-registry-form-panel",
  imports: [FormField, TranslatePipe],
  templateUrl: "./registry-form-panel.component.html",
  styleUrl: "./registry-form-panel.component.css",
})
export class RegistryFormPanelComponent implements OnInit {
  // ── Presentation / behaviour inputs ────────────────────────────────────────
  readonly titleKey = input("EXTREG.TITLE");
  readonly descriptionKey = input("EXTREG.DESC");
  readonly allowGlobalScope = input(false);
  readonly allowCustomHost = input(false);
  readonly compact = input(false);

  readonly authService = inject(AuthService);
  private readonly extRegSvc = inject(ExternalRegistryService);
  private readonly translate = inject(TranslateService);

  readonly loading = signal(false);

  /** Unique suffix so switch/radio ids stay distinct across instances. */
  readonly uid = Math.random().toString(36).slice(2, 9);

  /** Owner selector is only offered to admins when the parent allows it. */
  readonly canChooseScope = computed(
    () => this.allowGlobalScope() && !!this.authService.currentUser()?.is_admin,
  );

  // ── Registry list (derived from the shared service cache) ──────────────────
  // userRegistries already excludes the hidden system registry (__local__).
  // Personal-only panels additionally hide global registries.
  readonly registries = computed<ExternalRegistry[]>(() => {
    const list = this.extRegSvc.userRegistries();
    return this.allowGlobalScope() ? list : list.filter((r) => r.owner !== "global");
  });

  readonly showAddForm = signal(false);
  readonly editingId = signal<string | null>(null);
  readonly registryPresets = signal<RegistryPreset[]>([...KNOWN_REGISTRY_PRESETS]);

  // Custom host input for the preset adder (not part of the main form).
  readonly customHost = signal("");

  readonly savingRegistry = signal(false);
  readonly testingNew = signal(false);
  readonly testResult = signal<{
    reachable: boolean;
    auth_ok: boolean;
    message: string;
  } | null>(null);

  // ── Signal Form – registry create / edit ───────────────────────────────────

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

  readonly registryModel = signal<RegistryFormModel>({ ...this.registryInit });

  /** name and host are required; credentials and flags are optional. */
  readonly registryForm = form(this.registryModel, (p) => {
    required(p.name);
    required(p.host);
  });

  // ──────────────────────────────────────────────────────────────────────────

  ngOnInit(): void {
    this.loading.set(true);
    this.extRegSvc.listRegistries().subscribe({
      next: (regs) => {
        this.extRegSvc.setRegistriesCache(regs);
        this.loading.set(false);
      },
      error: () => this.loading.set(false),
    });
  }

  /** Re-fetch the list and propagate it to the shared service cache. */
  private refreshAndSync(): void {
    this.extRegSvc.listRegistries().subscribe({
      next: (regs) => this.extRegSvc.setRegistriesCache(regs),
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
      // Password left blank — backend keeps the current value when empty.
      password: "",
      owner: reg.owner === "global" ? "global" : "personal",
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

  saveRegistry(): void {
    submit(this.registryForm, async (f) => {
      const { name, host, username, password, owner, use_tls, tls_verify } =
        f().value();
      this.savingRegistry.set(true);
      this.testResult.set(null);

      const useTls = use_tls ?? true;
      const payload = {
        name: name!,
        host: host!,
        username,
        password,
        // Force personal scope when global registries are not allowed here.
        owner: this.allowGlobalScope() && owner === "global" ? "global" : "personal",
        use_tls: useTls,
        // tls_verify is only meaningful when use_tls is true.
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

  /** Test the current host/credentials/TLS settings without saving. */
  testNewConnection(): void {
    const { host, username, password, use_tls, tls_verify } = this.registryModel();
    this.testingNew.set(true);
    this.testResult.set(null);

    // When use_tls is false, force tls_verify to false so the client uses HTTP.
    const effectiveTlsVerify = (use_tls ?? true) ? (tls_verify ?? true) : false;
    this.extRegSvc
      .testConnection(host, username, password, {
        use_tls: use_tls ?? true,
        tls_verify: effectiveTlsVerify,
      })
      .subscribe({
        next: (res) => {
          this.testResult.set(res);
          this.testingNew.set(false);
        },
        error: () => {
          this.testResult.set({
            reachable: false,
            auth_ok: false,
            message: this.translate.instant("ACCOUNT.TEST_FAILED"),
          });
          this.testingNew.set(false);
        },
      });
  }

  deleteRegistry(id: string): void {
    this.extRegSvc.deleteRegistry(id).subscribe({
      next: () => this.refreshAndSync(),
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
