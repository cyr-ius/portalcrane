import { CommonModule } from "@angular/common";
import { Component, computed, inject, OnInit, signal } from "@angular/core";
import {
  SEVERITIES,
  Severity,
  VulnConfig,
  VulnConfigService,
} from "../../../core/services/vuln-config.service";

@Component({
  selector: "app-vuln-config-panel",
  imports: [CommonModule],
  templateUrl: "./vuln-config-panel.component.html",
  styleUrl: "./vuln-config-panel.component.css",
})
export class VulnConfigPanelComponent implements OnInit {
  vulnConfig = inject(VulnConfigService);

  readonly allSeverities = SEVERITIES;
  readonly timeoutOptions = ["1m", "3m", "5m", "10m", "15m", "30m"];

  draft = signal<VulnConfig>({ ...this.vulnConfig.config() });
  saved = signal(false);

  canSave = computed(
    () => !this.draft().enabled || this.draft().severities.length > 0,
  );

  ngOnInit() {
    // Sync le draft avec la config chargée
    this.draft.set({ ...this.vulnConfig.config() });
  }

  setEnabled(value: boolean) {
    this.draft.update((d) => ({ ...d, enabled: value }));
    this.saved.set(false);
  }

  toggleSeverity(sev: Severity) {
    this.draft.update((d) => {
      const has = d.severities.includes(sev);
      const updated = has
        ? d.severities.filter((s) => s !== sev)
        : [...d.severities, sev];
      return { ...d, severities: updated };
    });
    this.saved.set(false);
  }

  setIgnoreUnfixed(value: boolean) {
    this.draft.update((d) => ({ ...d, ignore_unfixed: value }));
    this.saved.set(false);
  }

  setTimeout(value: string) {
    this.draft.update((d) => ({ ...d, timeout: value }));
    this.saved.set(false);
  }

  save() {
    if (!this.canSave()) return;
    this.vulnConfig.saveConfig({ ...this.draft() });
    this.saved.set(true);
    setTimeout(() => this.saved.set(false), 3000);
  }

  reset() {
    this.vulnConfig.resetToDefaults();
    this.draft.set({ ...this.vulnConfig.config() });
    this.saved.set(false);
  }

  getSevBtnClass(sev: Severity): string {
    const active = this.draft().severities.includes(sev);
    const colorMap: Record<Severity, string> = {
      CRITICAL: active ? "btn-danger" : "btn-outline-danger",
      HIGH: active ? "btn-danger" : "btn-outline-danger",
      MEDIUM: active ? "btn-warning" : "btn-outline-warning",
      LOW: active ? "btn-info" : "btn-outline-info",
      UNKNOWN: active ? "btn-secondary" : "btn-outline-secondary",
    };
    return colorMap[sev];
  }
}
