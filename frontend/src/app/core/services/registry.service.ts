/**
 * Portalcrane - External Registry Service (Angular)
 *
 * Changes:
 *   - tls_verify added to ExternalRegistry, CreateRegistryPayload and
 *     UpdateRegistryPayload (previous patch).
 *   - [NEW] ImportRequest interface (Évolution 2).
 *   - [NEW] startImport() method — POST /api/external/import (Évolution 2).
 *   - SyncJob now exposes a direction field ("export" | "import") so the
 *     history list can display directional badges (Évolution 2).
 */
import { HttpClient } from "@angular/common/http";
import { inject, Injectable, signal } from "@angular/core";
import { Observable } from "rxjs";

/** A saved external registry entry (password is redacted by the backend). */
export interface ExternalRegistry {
  id: string;
  name: string;
  host: string;
  username: string;
  password: string;
  owner: string;
  /** When false, plain HTTP is used — no TLS at all. Defaults to true. */
  use_tls: boolean;
  /** Only relevant when use_tls is true. False = skip cert validation. Defaults to true. */
  tls_verify: boolean;
  created_at: string;
}

/** Payload for creating a new external registry. */
export interface CreateRegistryPayload {
  name: string;
  host: string;
  username?: string;
  password?: string;
  owner?: string;
  use_tls?: boolean;
  tls_verify?: boolean;
}

/** Payload for updating an existing external registry (all fields optional). */
export interface UpdateRegistryPayload {
  name?: string;
  host?: string;
  username?: string;
  password?: string;
  owner?: string;
  use_tls?: boolean;
  tls_verify?: boolean;
}

/** Payload to trigger an export sync job (local → external). */
export interface SyncRequest {
  source_image: string;
  dest_registry_id: string;
  dest_folder?: string | null;
}

/**
 * Payload to trigger an import job (external → local).
 * Évolution 2: mirrors SyncRequest with source/destination swapped.
 */
export interface ImportRequest {
  source_registry_id: string;
  source_image: string;
  dest_folder?: string | null;
}

/**
 * A sync or import job entry returned by GET /api/external/sync/jobs.
 *
 * direction "export" = local → external (sync)
 * direction "import" = external → local (import)
 */
export interface SyncJob {
  id: string;
  /** Transfer direction: "export" for sync, "import" for import jobs. */
  direction: "export" | "import";
  source: string;
  /** Source external registry ID (import jobs only). */
  source_registry_id: string | null;
  /** Destination external registry ID (export jobs only). */
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

  // ── Registry CRUD ──────────────────────────────────────────────────────────

  listRegistries(): Observable<ExternalRegistry[]> {
    return this.http.get<ExternalRegistry[]>(`${this.BASE}/registries`);
  }

  /**
   * Load registries from the API and update the in-memory cache signal.
   * Called at app init (staging component, images list, etc.).
   */
  loadRegistries(): void {
    this.listRegistries().subscribe({
      next: (regs) => this._externalRegistries.set(regs),
    });
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
   *                  tls_verify is only relevant when use_tls is true.
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

  /**
   * Alias kept for backward compatibility with
   * ExternalRegistriesConfigPanelComponent which calls testSaved(id).
   */
  testSaved(
    id: string,
  ): Observable<{ reachable: boolean; auth_ok: boolean; message: string }> {
    return this.testSavedConnection(id);
  }

  // ── Sync (local → external) ────────────────────────────────────────────────

  startSync(request: SyncRequest): Observable<{ job_id: string; status: string }> {
    return this.http.post<{ job_id: string; status: string }>(
      `${this.BASE}/sync`,
      request,
    );
  }

  listSyncJobs(): Observable<SyncJob[]> {
    return this.http.get<SyncJob[]>(`${this.BASE}/sync/jobs`);
  }

  // ── Import (external → local, Évolution 2) ────────────────────────────────

  /**
   * Start an asynchronous import job (external registry → local registry).
   *
   * POST /api/external/import — requires push access.
   *
   * @param request - { source_registry_id, source_image, dest_folder? }
   */
  startImport(
    request: ImportRequest,
  ): Observable<{ job_id: string; status: string }> {
    return this.http.post<{ job_id: string; status: string }>(
      `${this.BASE}/import`,
      request,
    );
  }
}
