/**
 * Portalcrane - Network Config Panel Component
 * Settings tab for proxy and syslog overrides.
 */
import {
  Component,
  OnInit,
  computed,
  inject,
  signal,
} from '@angular/core';
import { FormsModule } from '@angular/forms';
import {
  NetworkService,
  ProxySettings,
  SyslogSettings,
} from '../../../core/services/network.service';

@Component({
  selector: 'app-network-config-panel',
  imports: [FormsModule],
  templateUrl: './network-config-panel.component.html',
  styleUrl: './network-config-panel.component.css',
})
export class NetworkConfigPanelComponent implements OnInit {
  private networkSvc = inject(NetworkService);

  // ── Expose service state to template ─────────────────────────────────────
  readonly config = this.networkSvc.config;
  readonly loading = this.networkSvc.loading;
  readonly saving = this.networkSvc.saving;
  readonly saved = this.networkSvc.saved;
  readonly error = this.networkSvc.error;
  readonly testResult = this.networkSvc.testResult;

  // ── Proxy form state ──────────────────────────────────────────────────────
  proxyForm = signal<ProxySettings>({
    http_proxy: '',
    https_proxy: '',
    no_proxy: 'localhost,127.0.0.1',
    proxy_username: '',
    proxy_password: '',
    proxy_override: false,
  });

  showProxyPassword = signal(false);

  // ── Syslog form state ─────────────────────────────────────────────────────
  syslogForm = signal<SyslogSettings>({
    enabled: false,
    host: '',
    port: 514,
    protocol: 'udp',
    rfc: 'rfc5424',
    forward_audit: true,
    forward_uvicorn: false,
    tls_verify: true,
    tls_ca_cert: '',
    auth_enabled: false,
    auth_username: '',
    auth_password: '',
  });

  showSyslogPassword = signal(false);

  /** True when the selected protocol supports TLS. */
  readonly tlsAvailable = computed(
    () => this.syslogForm().protocol === 'tcp+tls'
  );

  /**
   * RFC 6587 / RELP authentication is only meaningful over TCP connections.
   * UDP syslog has no session layer and cannot carry credentials.
   */
  readonly authAvailable = computed(
    () =>
      this.syslogForm().protocol === 'tcp' ||
      this.syslogForm().protocol === 'tcp+tls'
  );

  // ── Lifecycle ─────────────────────────────────────────────────────────────

  ngOnInit(): void {
    this.networkSvc.loadConfig().then(() => {
      const cfg = this.config();
      if (cfg) {
        this.proxyForm.set({ ...cfg.proxy });
        this.syslogForm.set({ ...cfg.syslog });
      }
    });
  }

  // ── Proxy actions ─────────────────────────────────────────────────────────

  updateProxy(patch: Partial<ProxySettings>): void {
    this.proxyForm.update((f) => ({ ...f, ...patch }));
  }

  async saveProxy(): Promise<void> {
    await this.networkSvc.saveProxy(this.proxyForm());
    const cfg = this.config();
    if (cfg) this.proxyForm.set({ ...cfg.proxy });
  }

  async resetProxy(): Promise<void> {
    await this.networkSvc.resetProxy();
    const cfg = this.config();
    if (cfg) this.proxyForm.set({ ...cfg.proxy });
  }

  /**
   * Toggle the proxy override on/off and apply immediately.
   *
   * - Enabling  → update the local signal (user still needs to fill fields
   *               and click "Save Proxy Settings" to persist).
   * - Disabling → call resetProxy() at once so os.environ is cleared and
   *               the persisted JSON override is deleted on the backend.
   *               No explicit "Save" click should be required to take effect.
   */
  async toggleProxyOverride(value: boolean): Promise<void> {
    if (!value) {
      // Disabling: reset immediately on the backend
      await this.resetProxy();
    } else {
      // Enabling: just update the local form state so the fields become editable
      this.updateProxy({ proxy_override: true });
    }
  }

  // ── Syslog actions ────────────────────────────────────────────────────────

  updateSyslog(patch: Partial<SyslogSettings>): void {
    this.syslogForm.update((f) => ({ ...f, ...patch }));

    // When switching away from TCP, disable auth (not available for UDP)
    const proto = this.syslogForm().protocol;
    if (proto === 'udp' && this.syslogForm().auth_enabled) {
      this.syslogForm.update((f) => ({ ...f, auth_enabled: false }));
    }
    // When switching away from TCP+TLS, reset TLS fields
    if (proto !== 'tcp+tls') {
      this.syslogForm.update((f) => ({ ...f, tls_ca_cert: '' }));
    }
  }

  /**
   * Toggle syslog enabled/disabled and save immediately.
   *
   * The switch is a global on/off — it should take effect at once without
   * requiring the user to click "Save".  This mirrors the OIDC enabled toggle
   * pattern used in oidc-config-panel.
   */
  async onSyslogEnabledToggle(enabled: boolean): Promise<void> {
    this.syslogForm.update((f) => ({ ...f, enabled }));
    await this.networkSvc.saveSyslog(this.syslogForm());
    const cfg = this.networkSvc.config();
    if (cfg) this.syslogForm.set({ ...cfg.syslog });
  }

  async saveSyslog(): Promise<void> {
    await this.networkSvc.saveSyslog(this.syslogForm());
    const cfg = this.config();
    if (cfg) this.syslogForm.set({ ...cfg.syslog });
  }

  async testSyslog(): Promise<void> {
    await this.networkSvc.testSyslog();
  }

  async disableSyslog(): Promise<void> {
    await this.networkSvc.disableSyslog();
    this.syslogForm.update((f) => ({ ...f, enabled: false }));
  }
}
