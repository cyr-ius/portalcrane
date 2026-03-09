/**
 * Portalcrane - External Registry Service
 * HTTP client for the /api/external endpoints.
 */
import { HttpClient } from "@angular/common/http";
import { inject, Injectable, signal } from "@angular/core";
import { Observable } from "rxjs";

// ── Models ────────────────────────────────────────────────────────────────────

export interface ExternalRegistry {
  id: string;
  name: string;
  host: string;
  username: string;
  /** Always "••••••••" when returned from the API. */
  password: string;
  /**
   * "global" → visible to all users (admin-created).
   * Any other value → personal registry, visible only to that user and admins.
   */
  owner: string;
  created_at: string;
}

export interface CreateRegistryPayload {
  name: string;
  host: string;
  username?: string;
  password?: string;
  /** "global" for shared registries (admin only). Omit for personal. */
  owner?: string;
}

export interface UpdateRegistryPayload {
  name?: string;
  host?: string;
  username?: string;
  password?: string;
  owner?: string;
}

export interface ConnectionTestResult {
  reachable: boolean;
  auth_ok: boolean;
  message: string;
}

export interface SyncJob {
  id: string;
  source: string;
  dest_registry_id: string;
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

export interface ExternalPushPayload {
  job_id: string;
  registry_id?: string | null;
  registry_host?: string | null;
  registry_username?: string | null;
  registry_password?: string | null;
  folder?: string | null;
  image_name?: string | null;
  tag?: string | null;
}

export interface SyncPayload {
  source_image: string;
  dest_registry_id: string;
  dest_folder?: string | null;
}

@Injectable({ providedIn: "root" })
export class ExternalRegistryService {
  private readonly BASE = "/api/external";
  private http = inject(HttpClient);

  externalRegistries = signal<ExternalRegistry[]>([]);

  listRegistries(): Observable<ExternalRegistry[]> {
    return this.http.get<ExternalRegistry[]>(`${this.BASE}/registries`);
  }

  loadRegistries() {
    this.listRegistries().subscribe({
      next: (regs) => this.externalRegistries.set(regs)
    })
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

  testConnection(
    host: string,
    username: string,
    password: string,
  ): Observable<ConnectionTestResult> {
    return this.http.post<ConnectionTestResult>(
      `${this.BASE}/registries/test`,
      {
        host,
        username,
        password,
      },
    );
  }

  testSaved(id: string): Observable<ConnectionTestResult> {
    return this.http.post<ConnectionTestResult>(
      `${this.BASE}/registries/${id}/test`,
      {},
    );
  }

  // ── Sync ───────────────────────────────────────────────────────────────────

  listSyncJobs(): Observable<SyncJob[]> {
    return this.http.get<SyncJob[]>(`${this.BASE}/sync/jobs`);
  }

  startSync(
    payload: SyncPayload,
  ): Observable<{ job_id: string; message: string }> {
    return this.http.post<{ job_id: string; message: string }>(
      `${this.BASE}/sync`,
      payload,
    );
  }
}
