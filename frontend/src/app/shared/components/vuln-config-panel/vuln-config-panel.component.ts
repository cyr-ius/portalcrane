import { Component, inject } from "@angular/core";
import {
  AppConfigService,
  TRIVY_SEVERITIES,
  TRIVY_TIMEOUT_OPTIONS,
  TrivySeverity,
} from "../../../core/services/app-config.service";

@Component({
  selector: "app-vuln-config-panel",
  imports: [],
  templateUrl: "./vuln-config-panel.component.html",
  styleUrl: "./vuln-config-panel.component.css",
})
export class VulnConfigPanelComponent {
  configService = inject(AppConfigService);

  readonly allSeverities = TRIVY_SEVERITIES;
  readonly timeoutOptions = TRIVY_TIMEOUT_OPTIONS;

  getSevBtnClass(sev: TrivySeverity): string {
    const active = this.configService.vulnSeverities().includes(sev);
    const colorMap: Record<TrivySeverity, string> = {
      CRITICAL: active ? "btn-danger" : "btn-outline-danger",
      HIGH: active ? "btn-danger" : "btn-outline-danger",
      MEDIUM: active ? "btn-warning" : "btn-outline-warning",
      LOW: active ? "btn-info" : "btn-outline-info",
      UNKNOWN: active ? "btn-secondary" : "btn-outline-secondary",
    };
    return colorMap[sev];
  }
}
