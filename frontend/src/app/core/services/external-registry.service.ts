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
  /** Password is always redacted in API responses. */
  password?: string;
  owner: string;
  use_tls: boolean;
  tls_verify: boolean;
  /**
   * True when the registry's /v2/_catalog endpoint responds with HTTP 200 or
   * 401, meaning repository listing is available.
   * Set automatically by the backend on create/update by calling
   * check_catalog_browsable().
   *
   * Defaults to true for legacy entries (created before this field existed)
   * so they keep appearing in selectors until they are saved again.
   *
   * Components that display a source selector (Images list, Staging pull)
   * should filter on this field using the browsableRegistries computed signal.
   */
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

/**
 * A sync or import job entry returned by GET /api/external/sync/jobs.
 *
 * direction "export" = local -> external (sync)
 * direction "import" = external -> local (import)
 */
export interface SyncJob {
  id: string;
  direction: "export" | "import";
  source: string;
  source_registry_id: string | null;
  dest_registry_id: string | null;
  dest_folder: string | null;
  status: "running" | "done" | "partial" | "error";
  started_at: string;
  finished_at: string | null;
  message: string;
  error: string | null;
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

  /**
   * In-memory cache used by Staging, Images and other components.
   * Write via _externalRegistries; read via the public readonly signal.
   */
  private _externalRegistries = signal<ExternalRegistry[]>([]);
  readonly externalRegistries = this._externalRegistries.asReadonly();

  /**
   * Computed signal: only registries that support /v2/_catalog browsing
   * (browsable === true, or undefined for legacy entries).
   *
   * Use this signal in:
   *   - images-list source selector buttons
   *   - staging "saved registry" dropdown
   *
   * This replaces the previous pattern of calling catalog-check on each
   * registry individually at display time. The backend persists the `browsable`
   * field when registries are created or updated, so no extra HTTP calls are
   * required here.
   */
  readonly browsableRegistries = computed<ExternalRegistry[]>(() =>
    this._externalRegistries().filter((r) => r.browsable !== false),
  );

  // ── Registry CRUD ──────────────────────────────────────────────────────────

  listRegistries(): Observable<ExternalRegistry[]> {
    return this.http.get<ExternalRegistry[]>(`${this.BASE}/registries`);
  }

  /**
   * Load registries from the API and update the in-memory cache signal.
   *
   * Called at app init by consuming components (staging, images-list, etc.).
   * After this call, browsableRegistries automatically reflects registries
   * whose `browsable` field is true — no catalog-check calls are needed.
   */
  loadRegistries(): void {
    this.listRegistries().subscribe({
      next: (regs) => this._externalRegistries.set(regs),
    });
  }


  /**
   * Directly update the in-memory registry cache signal.
   * Called by components that already have the registry list (e.g. after
   * listRegistries() returns) so they can push the data into the shared
   * cache without a second HTTP call.
   *
   * @param regs  Full registry list returned by listRegistries().
   */
  setRegistriesCache(regs: ExternalRegistry[]): void {
    this._externalRegistries.set(regs);
  }

  /**
   * Refresh the shared registry cache after a create, update or delete
   * operation. All components consuming externalRegistries or
   * browsableRegistries will reactively update.
   *
   * Intended for use in ExternalRegistriesConfigPanelComponent after
   * saveRegistry() or deleteRegistry() succeed.
   */
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

  /**
   * Test connectivity to an unsaved registry.
   *
   * @param host      Registry host (bare hostname or with http:// / https://)
   * @param username  Optional username
   * @param password  Optional password / token
   * @param options   TLS options: use_tls (default true) and tls_verify (default true).
   */
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

  testSavedConnection(
    id: string,
  ): Observable<{ reachable: boolean; auth_ok: boolean; message: string }> {
    return this.http.post<{
      reachable: boolean;
      auth_ok: boolean;
      message: string;
    }>(`${this.BASE}/registries/${id}/test`, {});
  }

  /** Alias kept for backward compatibility. */
  testSaved(
    id: string,
  ): Observable<{ reachable: boolean; auth_ok: boolean; message: string }> {
    return this.testSavedConnection(id);
  }

  // ── Catalog availability check ─────────────────────────────────────────────

  /**
   * Probe /v2/_catalog on a saved registry to determine whether it supports
   * catalog browsing.
   *
   * This endpoint is kept for manual refresh use cases (e.g. a "refresh"
   * button in the registry panel). It is no longer called automatically when
   * the Images page loads — the `browsable` field persisted by the backend
   * is used instead via the browsableRegistries computed signal.
   *
   * @param id  Saved external registry ID.
   */
  checkCatalog(id: string): Observable<{ available: boolean; reason: string }> {
    return this.http.get<{ available: boolean; reason: string }>(
      `${this.BASE}/registries/${id}/catalog-check`,
    );
  }

  // ── Sync (local -> external) ────────────────────────────────────────────────

  startSync(request: SyncRequest): Observable<{ job_id: string; status: string }> {
    return this.http.post<{ job_id: string; status: string }>(
      `${this.BASE}/sync`,
      request,
    );
  }

  listSyncJobs(): Observable<SyncJob[]> {
    return this.http.get<SyncJob[]>(`${this.BASE}/sync/jobs`);
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
