/**
 * Portalcrane - SystemService
 *
 * HTTP client for /api/system/* endpoints.
 *
 * Migration note: process statuses, audit logs, GC, orphan OCI, ghost repo
 * management, registry ping, and image copy are now all served by the
 * consolidated /api/system router on the backend. No more /api/registry/*
 * calls are made from this service.
 */
import { HttpClient } from "@angular/common/http";
import { inject, Injectable } from "@angular/core";
import { firstValueFrom } from "rxjs";

// ── Interfaces ────────────────────────────────────────────────────────────────

export interface ProcessStatus {
  name: string;
  running: boolean;
  state: string;
  pid?: number;
  uptime_seconds?: number;
  error?: string;
}

export interface AuditEvent {
  event: string;
  timestamp: string;
  path: string;
  method: string;
  http_status: number;
  bytes: number;
  elapsed_s: number;
  client_ip: string;
  username: string | null;
}

// ── Service ───────────────────────────────────────────────────────────────────

@Injectable({ providedIn: "root" })
export class SystemService {
  private http = inject(HttpClient);
  private readonly BASE = "/api/system";

  /** Returns the runtime status of all supervised processes. */
  async getProcessStatuses(): Promise<ProcessStatus[]> {
    return firstValueFrom(
      this.http.get<ProcessStatus[]>(`${this.BASE}/processes`),
    );
  }

  /** Returns the latest audit events emitted by the Audit Service. */
  async getAuditLogs(limit: number = 200): Promise<AuditEvent[]> {
    const params = new URLSearchParams();
    params.set("limit", String(limit));

    const response = await firstValueFrom(
      this.http.get<{ events: AuditEvent[] }>(
        `${this.BASE}/audit/logs?${params.toString()}`,
      ),
    );
    return response.events;
  }

  /**
   * Check local registry connectivity.
   *
   * Replaces: GET /api/registry/ping
   * Now uses: GET /api/system/ping
   */
  async pingRegistry(): Promise<{ status: string; url: string }> {
    return firstValueFrom(
      this.http.get<{ status: string; url: string }>(`${this.BASE}/ping`),
    );
  }
}
