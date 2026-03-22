/**
 * Portalcrane - External Registry Service
 * HTTP client for /api/registries endpoints.
 *
 * Change: system field added to ExternalRegistry interface.
 * The backend injects the local embedded registry as a hidden system entry
 * with id="__local__" and system=true. The frontend uses two derived signals:
 *
 *   - externalRegistries      : ALL registries including system entries.
 *                                Used by images-list and staging to list sources.
 *   - userRegistries           : Only non-system registries (system=false/undefined).
 *                                Used by External Registries settings panel.
 *   - browsableRegistries      : Registries with browsable !== false (includes system).
 *                                Used by images-list source selector.
 *   - browsableUserRegistries  : browsable + non-system registries only.
 *                                Used by sync panel (export/import destination).
 *
 * The local system registry (__local__) is shown in the Images source selector
 * and Staging pull source, but hidden from the External Registries settings panel
 * and sync destinations.
 */
import { HttpClient } from "@angular/common/http";
import { computed, inject, Injectable, signal } from "@angular/core";
import { Observable } from "rxjs";

// ── Models ────────────────────────────────────────────────────────────────────

export interface ExternalRegistry {
  id: string;
  name: string;
  host: string;
  username?: string;
  password?: string;
  owner: string;
  use_tls: boolean;
  tls_verify: boolean;
  browsable: boolean;
  /** When true, this is a hidden system registry (e.g. the local embedded registry). */
  system?: boolean;
  created_at?: string;
}

export interface CreateRegistryPayload {
  name: string;
  host: string;
  username?: string;
  password?: string;
  owner?: string;
  use_tls?: boolean;
  tls_verify?: boolean;
}

export interface UpdateRegistryPayload {
  name?: string;
  host?: string;
  username?: string;
  password?: string;
  owner?: string;
  use_tls?: boolean;
  tls_verify?: boolean;
}

export interface SyncJob {
  id: string;
  direction: "export" | "import";
  source: string;
  source_registry_id: string | null;
  dest_registry_id: string | null;
  dest_folder: string | null;
  status: "running" | "done" | "done_with_errors" | "failed" | "partial" | "error";
  started_at: string;
  finished_at: string | null;
  message: string;
  error: string | null;
  errors: string[];
  progress: number;
  images_total: number;
  images_done: number;
}

export interface SyncRequest {
  source_image: string;
  dest_registry_id: string;
  dest_folder?: string | null;
}

export interface ImportRequest {
  source_registry_id: string;
  source_image: string;
  dest_folder?: string | null;
}

@Injectable({ providedIn: "root" })
export class ExternalRegistryService {
  private readonly REGISTRIES = "/api/registries";
  private readonly IMAGES = "/api/images";
  private http = inject(HttpClient);

  private _externalRegistries = signal<ExternalRegistry[]>([]);

  readonly externalRegistries = this._externalRegistries.asReadonly();
  readonly userRegistries = computed<ExternalRegistry[]>(() =>
    this._externalRegistries().filter((r) => !r.system),
  );
  readonly browsableRegistries = computed<ExternalRegistry[]>(() =>
    this._externalRegistries().filter((r) => r.browsable !== false),
  );
  readonly browsableUserRegistries = computed<ExternalRegistry[]>(() =>
    this._externalRegistries().filter((r) => r.browsable !== false && !r.system),
  );

  // ── Registry CRUD ──────────────────────────────────────────────────────────

  listRegistries(): Observable<ExternalRegistry[]> {
    return this.http.get<ExternalRegistry[]>(`${this.REGISTRIES}`);
  }

  loadRegistries(): void {
    this.listRegistries().subscribe({
      next: (regs) => this._externalRegistries.set(regs),
    });
  }

  setRegistriesCache(regs: ExternalRegistry[]): void {
    this._externalRegistries.set(regs);
  }

  refreshRegistries(): void {
    this.loadRegistries();
  }

  createRegistry(payload: CreateRegistryPayload): Observable<ExternalRegistry> {
    return this.http.post<ExternalRegistry>(`${this.REGISTRIES}`, payload);
  }

  updateRegistry(
    id: string,
    payload: UpdateRegistryPayload,
  ): Observable<ExternalRegistry> {
    return this.http.patch<ExternalRegistry>(`${this.REGISTRIES}/${id}`, payload);
  }

  deleteRegistry(id: string): Observable<void> {
    return this.http.delete<void>(`${this.REGISTRIES}/${id}`);
  }

  // ── Connectivity test ──────────────────────────────────────────────────────

  testConnection(
    host: string,
    username: string,
    password: string,
    options: { use_tls?: boolean; tls_verify?: boolean } = {},
  ): Observable<{ reachable: boolean; auth_ok: boolean; message: string }> {
    const { use_tls = true, tls_verify = true } = options;
    return this.http.post<{
      reachable: boolean;
      auth_ok: boolean;
      message: string;
    }>(`${this.REGISTRIES}/test`, {
      host,
      username,
      password,
      use_tls,
      tls_verify,
    });
  }

  // ── Catalog availability check ─────────────────────────────────────────────

  checkCatalog(id: string): Observable<{ available: boolean; reason: string }> {
    return this.http.get<{ available: boolean; reason: string }>(
      `${this.REGISTRIES}/${id}/catalog-check`,
    );
  }

  // ── List Sync Jobs ────────────────────────────────────────────────

  listSyncJobs(): Observable<SyncJob[]> {
    return this.http.get<SyncJob[]>(`${this.IMAGES}/sync/jobs`);
  }

  // ── Export (local -> external) ────────────────────────────────────────────────

  startSync(request: SyncRequest): Observable<{ job_id: string; status: string }> {
    return this.http.post<{ job_id: string; status: string }>(
      `${this.IMAGES}/export`,
      request,
    );
  }

  // ── Import (external -> local) ────────────────────────────────────────────

  startImport(
    request: ImportRequest,
  ): Observable<{ job_id: string; status: string }> {
    return this.http.post<{ job_id: string; status: string }>(
      `${this.IMAGES}/import`,
      request,
    );
  }
}
