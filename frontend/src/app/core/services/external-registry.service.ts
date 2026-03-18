/**
 * Portalcrane - External Registry Service
 * HTTP client for /api/external endpoints.
 *
 * Change: browsable field added to ExternalRegistry interface.
 * The backend sets this automatically by probing /v2/_catalog when a registry
 * is created or updated. The frontend uses it to filter registries in:
 *   - Images source selector (images-list component)
 *   - Staging pull source selector (staging component)
 * Only registries with browsable === true appear in those selectors.
 *
 * Refactor (catalog-check removal from images-list):
 *   - loadRegistries() is now the single entry point for populating the shared
 *     cache. It fetches GET /api/external/registries and updates the
 *     _externalRegistries signal so all consumers react automatically.
 *   - browsableRegistries computed signal filters on browsable !== false;
 *     components must use this signal instead of calling catalog-check
 *     individually on each registry at display time.
 *   - refreshRegistries() is a public alias of loadRegistries() intended for
 *     use after create/update/delete operations in the config panel so the
 *     shared cache stays in sync without requiring a page reload.
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
  private readonly BASE = "/api/external";
  private http = inject(HttpClient);

  private _externalRegistries = signal<ExternalRegistry[]>([]);
  readonly externalRegistries = this._externalRegistries.asReadonly();
  readonly browsableRegistries = computed<ExternalRegistry[]>(() =>
    this._externalRegistries().filter((r) => r.browsable !== false),
  );

  // ── Registry CRUD ──────────────────────────────────────────────────────────

  listRegistries(): Observable<ExternalRegistry[]> {
    return this.http.get<ExternalRegistry[]>(`${this.BASE}/registries`);
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
    return this.http.post<ExternalRegistry>(`${this.BASE}/registries`, payload);
  }

  updateRegistry(
    id: string,
    payload: UpdateRegistryPayload,
  ): Observable<ExternalRegistry> {
    return this.http.patch<ExternalRegistry>(
      `${this.BASE}/registries/${id}`,
      payload,
    );
  }

  deleteRegistry(id: string): Observable<void> {
    return this.http.delete<void>(`${this.BASE}/registries/${id}`);
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
    }>(`${this.BASE}/registries/test`, {
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
      `${this.BASE}/registries/${id}/catalog-check`,
    );
  }

  // ── List Sync Jobs ────────────────────────────────────────────────

  listSyncJobs(): Observable<SyncJob[]> {
    return this.http.get<SyncJob[]>(`${this.BASE}/sync/jobs`);
  }

  // ── Export (local -> external) ────────────────────────────────────────────────

  startSync(request: SyncRequest): Observable<{ job_id: string; status: string }> {
    return this.http.post<{ job_id: string; status: string }>(
      `${this.BASE}/export`,
      request,
    );
  }

  // ── Import (external -> local) ────────────────────────────────────────────

  startImport(
    request: ImportRequest,
  ): Observable<{ job_id: string; status: string }> {
    return this.http.post<{ job_id: string; status: string }>(
      `${this.BASE}/import`,
      request,
    );
  }
}
