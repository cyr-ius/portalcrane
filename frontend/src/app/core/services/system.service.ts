// frontend/src/app/shared/services/system.service.ts
import { HttpClient } from "@angular/common/http";
import { Injectable, inject } from "@angular/core";
import { firstValueFrom } from "rxjs";

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

}
