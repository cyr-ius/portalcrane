/**
 * Portalcrane - Network Settings Service
 * Manages proxy and syslog configuration via the /api/network/* endpoints.
 */
import { HttpClient } from '@angular/common/http';
import { Injectable, inject, signal } from '@angular/core';
import { firstValueFrom } from 'rxjs';

// ── Interfaces ────────────────────────────────────────────────────────────────

export interface ProxySettings {
  http_proxy: string;
  https_proxy: string;
  no_proxy: string;
  proxy_username: string;
  proxy_password: string;
  proxy_override: boolean;
}

export interface SyslogSettings {
  enabled: boolean;
  host: string;
  port: number;
  /** 'udp' | 'tcp' | 'tcp+tls' */
  protocol: string;
  /** 'rfc3164' | 'rfc5424' */
  rfc: string;
  forward_audit: boolean;
  forward_uvicorn: boolean;
  tls_verify: boolean;
  tls_ca_cert: string;
  auth_enabled: boolean;
  auth_username: string;
  auth_password: string;
}

export interface NetworkConfig {
  proxy: ProxySettings;
  syslog: SyslogSettings;
}

// ── Service ───────────────────────────────────────────────────────────────────

@Injectable({ providedIn: 'root' })
export class NetworkService {
  private http = inject(HttpClient);
  private readonly BASE = '/api/network';

  // ── Reactive state ────────────────────────────────────────────────────────

  readonly config = signal<NetworkConfig | null>(null);
  readonly loading = signal(false);
  readonly saving = signal(false);
  readonly saved = signal(false);
  readonly error = signal<string | null>(null);
  readonly testResult = signal<{ success: boolean; message: string } | null>(null);

  // ── Load ──────────────────────────────────────────────────────────────────

  async loadConfig(): Promise<void> {
    this.loading.set(true);
    this.error.set(null);
    try {
      const cfg = await firstValueFrom(
        this.http.get<NetworkConfig>(`${this.BASE}/config`)
      );
      this.config.set(cfg);
    } catch {
      this.error.set('Unable to load network configuration.');
    } finally {
      this.loading.set(false);
    }
  }

  // ── Proxy ─────────────────────────────────────────────────────────────────

  async saveProxy(payload: ProxySettings): Promise<void> {
    this.saving.set(true);
    this.error.set(null);
    try {
      const cfg = await firstValueFrom(
        this.http.put<NetworkConfig>(`${this.BASE}/proxy`, payload)
      );
      this.config.set(cfg);
      this._flashSaved();
    } catch (err: any) {
      this.error.set(err?.error?.detail ?? 'Failed to save proxy settings.');
    } finally {
      this.saving.set(false);
    }
  }

  async resetProxy(): Promise<void> {
    this.saving.set(true);
    this.error.set(null);
    try {
      const cfg = await firstValueFrom(
        this.http.delete<NetworkConfig>(`${this.BASE}/proxy`)
      );
      this.config.set(cfg);
      this._flashSaved();
    } catch (err: any) {
      this.error.set(err?.error?.detail ?? 'Failed to reset proxy settings.');
    } finally {
      this.saving.set(false);
    }
  }

  // ── Syslog ────────────────────────────────────────────────────────────────

  async saveSyslog(payload: SyslogSettings): Promise<void> {
    this.saving.set(true);
    this.error.set(null);
    try {
      const cfg = await firstValueFrom(
        this.http.put<NetworkConfig>(`${this.BASE}/syslog`, payload)
      );
      this.config.set(cfg);
      this._flashSaved();
    } catch (err: any) {
      this.error.set(err?.error?.detail ?? 'Failed to save syslog settings.');
    } finally {
      this.saving.set(false);
    }
  }

  async disableSyslog(): Promise<void> {
    this.saving.set(true);
    this.error.set(null);
    try {
      const cfg = await firstValueFrom(
        this.http.delete<NetworkConfig>(`${this.BASE}/syslog`)
      );
      this.config.set(cfg);
      this._flashSaved();
    } catch (err: any) {
      this.error.set(err?.error?.detail ?? 'Failed to disable syslog.');
    } finally {
      this.saving.set(false);
    }
  }

  async testSyslog(): Promise<void> {
    this.testResult.set(null);
    try {
      const result = await firstValueFrom(
        this.http.post<{ success: boolean; message: string }>(
          `${this.BASE}/syslog/test`,
          {}
        )
      );
      this.testResult.set(result);
    } catch {
      this.testResult.set({ success: false, message: 'Test request failed.' });
    }
  }

  // ── Private helpers ───────────────────────────────────────────────────────

  private _flashSaved(): void {
    this.saved.set(true);
    setTimeout(() => this.saved.set(false), 3000);
  }
}
