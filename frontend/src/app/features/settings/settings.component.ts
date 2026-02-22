import { CommonModule } from "@angular/common";
import { Component, inject, OnInit } from "@angular/core";
import {
  AppConfigService,
  TRIVY_SEVERITIES,
  TrivySeverity,
} from "../../core/services/app-config.service";
import { AuthService } from "../../core/services/auth.service";
import { ThemeService } from "../../core/services/theme.service";

/** Badge colour for each Trivy severity level */
const SEVERITY_STYLE: Record<
  TrivySeverity,
  { active: string; inactive: string; icon: string }
> = {
  CRITICAL: {
    active: "btn btn-sm btn-danger",
    inactive: "btn btn-sm btn-outline-danger",
    icon: "bi-radioactive",
  },
  HIGH: {
    active: "btn btn-sm btn-warning text-dark",
    inactive: "btn btn-sm btn-outline-warning",
    icon: "bi-exclamation-triangle-fill",
  },
  MEDIUM: {
    active: "btn btn-sm btn-info text-dark",
    inactive: "btn btn-sm btn-outline-info",
    icon: "bi-exclamation-circle",
  },
  LOW: {
    active: "btn btn-sm btn-secondary",
    inactive: "btn btn-sm btn-outline-secondary",
    icon: "bi-info-circle",
  },
  UNKNOWN: {
    active: "btn btn-sm btn-dark",
    inactive: "btn btn-sm btn-outline-dark",
    icon: "bi-question-circle",
  },
};

@Component({
  selector: "app-settings",
  imports: [CommonModule],
  templateUrl: "./settings.component.html",
  styleUrl: "./settings.component.css",
})
export class SettingsComponent implements OnInit {
  themeService = inject(ThemeService);
  authService = inject(AuthService);
  configService = inject(AppConfigService);

  /** Expose the ordered severity list to the template */
  readonly severities = TRIVY_SEVERITIES;

  ngOnInit() {
    // Ensure config is loaded (may already be cached from app startup)
    if (!this.configService.serverConfig()) {
      this.configService.loadConfig().subscribe();
    }
  }

  getSeverityClass(sev: TrivySeverity): string {
    const selected = this.configService.vulnSeverities().includes(sev);
    return selected ? SEVERITY_STYLE[sev].active : SEVERITY_STYLE[sev].inactive;
  }

  getSeverityIcon(sev: TrivySeverity): string {
    return SEVERITY_STYLE[sev].icon;
  }
}
