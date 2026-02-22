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
  template: `
    <div class="p-4">
      <h2 class="fw-bold mb-1">Settings</h2>
      <p class="text-muted small mb-4">
        Application preferences and information
      </p>

      <!-- ── Appearance ─────────────────────────────────────────────────── -->
      <div class="card border-0 mb-3">
        <div class="card-header border-0">
          <h6 class="fw-semibold mb-0">
            <i class="bi bi-palette me-2"></i>Appearance
          </h6>
        </div>
        <div class="card-body">
          <label class="form-label small fw-semibold">Color Theme</label>
          <div class="d-flex gap-2">
            <button
              class="btn d-flex flex-column align-items-center gap-1 p-3"
              [class.btn-primary]="themeService.theme() === 'light'"
              [class.btn-outline-secondary]="themeService.theme() !== 'light'"
              (click)="themeService.setTheme('light')"
            >
              <i class="bi bi-sun-fill fs-4"></i>
              <span class="small">Light</span>
            </button>
            <button
              class="btn d-flex flex-column align-items-center gap-1 p-3"
              [class.btn-primary]="themeService.theme() === 'dark'"
              [class.btn-outline-secondary]="themeService.theme() !== 'dark'"
              (click)="themeService.setTheme('dark')"
            >
              <i class="bi bi-moon-fill fs-4"></i>
              <span class="small">Dark</span>
            </button>
            <button
              class="btn d-flex flex-column align-items-center gap-1 p-3"
              [class.btn-primary]="themeService.theme() === 'auto'"
              [class.btn-outline-secondary]="themeService.theme() !== 'auto'"
              (click)="themeService.setTheme('auto')"
            >
              <i class="bi bi-circle-half fs-4"></i>
              <span class="small">Auto</span>
            </button>
          </div>
        </div>
      </div>

      <!-- ── Advanced mode ──────────────────────────────────────────────── -->
      <div class="card border-0 mb-3">
        <div class="card-header border-0">
          <h6 class="fw-semibold mb-0">
            <i class="bi bi-sliders me-2"></i>Advanced Mode
          </h6>
        </div>
        <div class="card-body">
          <div class="d-flex align-items-center justify-content-between">
            <div>
              <div class="small fw-semibold">Enable Advanced Mode</div>
              <div class="text-muted" style="font-size:0.8rem">
                Shows scan detail badges on pipeline jobs and other power-user
                features.
              </div>
              @if (configService.serverConfig()?.advanced_mode) {
                <div class="text-primary mt-1" style="font-size:0.75rem">
                  <i class="bi bi-info-circle me-1"></i>Server default is
                  <strong>ON</strong> (ADVANCED_MODE=true). Your local choice
                  overrides this.
                </div>
              }
            </div>
            <div class="form-check form-switch ms-4 flex-shrink-0">
              <input
                class="form-check-input"
                type="checkbox"
                id="advancedModeToggle"
                style="width:2.5em; height:1.3em"
                [checked]="configService.advancedMode()"
                (change)="
                  configService.setAdvancedMode($any($event.target).checked)
                "
              />
            </div>
          </div>
        </div>
      </div>

      <!-- ── ClamAV ─────────────────────────────────────────────────────── -->
      @if (configService.advancedMode()) {
        <div class="card border-0 mb-3">
          <div
            class="card-header border-0 d-flex align-items-center justify-content-between"
          >
            <h6 class="fw-semibold mb-0">
              <i class="bi bi-shield-virus me-2"></i>ClamAV Antivirus
            </h6>
            <span
              class="badge"
              [class.bg-success-subtle]="configService.clamavEnabled()"
              [class.text-success]="configService.clamavEnabled()"
              [class.bg-secondary-subtle]="!configService.clamavEnabled()"
              [class.text-secondary]="!configService.clamavEnabled()"
            >
              {{ configService.clamavEnabled() ? "Enabled" : "Disabled" }}
            </span>
          </div>
          <div class="card-body">
            <p class="text-muted small mb-3">
              Scans every pulled image against virus definitions before allowing
              a push to the registry.
            </p>

            <!-- ClamAV toggle -->
            <div
              class="d-flex align-items-center justify-content-between p-3 rounded scan-row"
            >
              <div>
                <div class="small fw-semibold">Enable ClamAV scan</div>
                <div class="text-muted" style="font-size:0.8rem">
                  When disabled the pipeline skips the antivirus step entirely.
                </div>
              </div>
              <div class="form-check form-switch ms-4 flex-shrink-0">
                <input
                  class="form-check-input"
                  type="checkbox"
                  id="clamavToggle"
                  style="width:2.5em; height:1.3em"
                  [checked]="configService.clamavEnabled()"
                  (change)="
                    configService.setClamavEnabled($any($event.target).checked)
                  "
                />
              </div>
            </div>

            <!-- Server origin note -->
            @if (configService.serverConfig(); as cfg) {
              <div class="text-muted mt-2" style="font-size:0.75rem">
                <i class="bi bi-server me-1"></i>Server default:
                {{ cfg.clamav_enabled ? "enabled" : "disabled" }} —
                {{ cfg.clamav_host }}:{{ cfg.clamav_port }}
              </div>
            }
          </div>
        </div>

        <!-- ── Trivy CVE Scan ─────────────────────────────────────────── -->
        <div class="card border-0 mb-3">
          <div
            class="card-header border-0 d-flex align-items-center justify-content-between"
          >
            <h6 class="fw-semibold mb-0">
              <i class="bi bi-bug me-2"></i>Trivy CVE Scan
            </h6>
            <span
              class="badge"
              [class.bg-success-subtle]="configService.vulnEnabled()"
              [class.text-success]="configService.vulnEnabled()"
              [class.bg-secondary-subtle]="!configService.vulnEnabled()"
              [class.text-secondary]="!configService.vulnEnabled()"
            >
              {{ configService.vulnEnabled() ? "Enabled" : "Disabled" }}
            </span>
          </div>
          <div class="card-body">
            <p class="text-muted small mb-3">
              Runs a Trivy vulnerability scan after ClamAV. Jobs are blocked
              from pushing if any selected severity is found.
            </p>

            <!-- Trivy toggle -->
            <div
              class="d-flex align-items-center justify-content-between p-3 rounded scan-row mb-3"
            >
              <div>
                <div class="small fw-semibold">Enable Trivy CVE scan</div>
                <div class="text-muted" style="font-size:0.8rem">
                  Scans for known CVEs using the Trivy binary inside the
                  container.
                </div>
              </div>
              <div class="form-check form-switch ms-4 flex-shrink-0">
                <input
                  class="form-check-input"
                  type="checkbox"
                  id="vulnToggle"
                  style="width:2.5em; height:1.3em"
                  [checked]="configService.vulnEnabled()"
                  (change)="
                    configService.setVulnEnabled($any($event.target).checked)
                  "
                />
              </div>
            </div>

            <!-- Severity selector — only shown when Trivy is enabled -->
            @if (configService.vulnEnabled()) {
              <div class="mb-3">
                <label class="form-label small fw-semibold d-block mb-2">
                  Blocking severity levels
                  <span class="text-muted fw-normal">
                    — click to toggle, at least one required
                  </span>
                </label>
                <div class="d-flex gap-2 flex-wrap">
                  @for (sev of severities; track sev) {
                    <button
                      [class]="getSeverityClass(sev)"
                      (click)="configService.toggleVulnSeverity(sev)"
                    >
                      <i [class]="'bi ' + getSeverityIcon(sev) + ' me-1'"></i>
                      {{ sev }}
                    </button>
                  }
                </div>
                <div class="form-text mt-2">
                  Active:
                  <strong>{{ configService.vulnSeveritiesString() }}</strong>
                </div>
              </div>

              <!-- Ignore unfixed toggle -->
              <div
                class="d-flex align-items-center justify-content-between p-3 rounded scan-row"
              >
                <div>
                  <div class="small fw-semibold">Ignore unfixed CVEs</div>
                  <div class="text-muted" style="font-size:0.8rem">
                    Skip vulnerabilities that have no available fix yet.
                  </div>
                </div>
                <div class="form-check form-switch ms-4 flex-shrink-0">
                  <input
                    class="form-check-input"
                    type="checkbox"
                    id="ignoreUnfixedToggle"
                    style="width:2.5em; height:1.3em"
                    [checked]="configService.vulnIgnoreUnfixed()"
                    (change)="
                      configService.setVulnIgnoreUnfixed(
                        $any($event.target).checked
                      )
                    "
                  />
                </div>
              </div>
            }

            <!-- Server origin note -->
            @if (configService.serverConfig(); as cfg) {
              <div class="text-muted mt-2" style="font-size:0.75rem">
                <i class="bi bi-server me-1"></i>Server default:
                {{ cfg.vuln_scan_enabled ? "enabled" : "disabled" }} —
                severities: {{ cfg.vuln_scan_severities }} — timeout:
                {{ cfg.vuln_scan_timeout }}
              </div>
            }
          </div>
        </div>
      }

      <!-- ── Account ────────────────────────────────────────────────────── -->
      <div class="card border-0 mb-3">
        <div class="card-header border-0">
          <h6 class="fw-semibold mb-0">
            <i class="bi bi-person me-2"></i>Account
          </h6>
        </div>
        <div class="card-body">
          <div class="d-flex align-items-center justify-content-between">
            <div>
              <div class="fw-semibold">
                {{ authService.currentUser()?.username }}
              </div>
              <div class="text-muted small">Administrator</div>
            </div>
            <button
              class="btn btn-outline-danger btn-sm"
              (click)="authService.logout()"
            >
              <i class="bi bi-box-arrow-right me-1"></i>
              Logout
            </button>
          </div>
        </div>
      </div>

      <!-- ── About ──────────────────────────────────────────────────────── -->
      <div class="card border-0">
        <div class="card-header border-0">
          <h6 class="fw-semibold mb-0">
            <i class="bi bi-info-circle me-2"></i>About
          </h6>
        </div>
        <div class="card-body">
          <div class="d-flex align-items-center gap-3 mb-3">
            <svg
              width="48"
              height="48"
              viewBox="0 0 200 200"
              xmlns="http://www.w3.org/2000/svg"
            >
              <circle
                cx="100"
                cy="100"
                r="96"
                fill="#0D1B2A"
                stroke="#2E7FCF"
                stroke-width="2"
              />
              <rect
                x="30"
                y="155"
                width="140"
                height="12"
                rx="3"
                fill="#1B4D7E"
              />
              <rect
                x="62"
                y="60"
                width="12"
                height="97"
                rx="2"
                fill="#2E7FCF"
              />
              <rect
                x="62"
                y="60"
                width="96"
                height="10"
                rx="2"
                fill="#2E7FCF"
              />
              <rect x="30" y="60" width="34" height="8" rx="2" fill="#1B4D7E" />
              <rect
                x="22"
                y="55"
                width="16"
                height="18"
                rx="3"
                fill="#1B4D7E"
              />
              <line
                x1="158"
                y1="65"
                x2="68"
                y2="40"
                stroke="#E8A020"
                stroke-width="1.5"
                opacity="0.8"
              />
              <rect
                x="56"
                y="35"
                width="24"
                height="28"
                rx="3"
                fill="#1B4D7E"
              />
              <circle
                cx="68"
                cy="49"
                r="7"
                fill="#0D1B2A"
                stroke="#E8A020"
                stroke-width="2"
              />
              <circle cx="68" cy="49" r="3" fill="#E8A020" opacity="0.8" />
              <line
                x1="150"
                y1="70"
                x2="150"
                y2="128"
                stroke="#E8A020"
                stroke-width="2"
                stroke-dasharray="4,3"
              />
              <rect
                x="128"
                y="128"
                width="44"
                height="26"
                rx="3"
                fill="#E8A020"
              />
              <rect
                x="128"
                y="128"
                width="44"
                height="5"
                rx="2"
                fill="#F0B030"
              />
              <circle
                cx="50"
                cy="157"
                r="5"
                fill="#2E7FCF"
                stroke="#0D1B2A"
                stroke-width="1.5"
              />
              <circle
                cx="86"
                cy="157"
                r="5"
                fill="#2E7FCF"
                stroke="#0D1B2A"
                stroke-width="1.5"
              />
              <rect
                x="56"
                y="100"
                width="24"
                height="20"
                rx="3"
                fill="#1B4D7E"
              />
              <rect
                x="59"
                y="103"
                width="18"
                height="11"
                rx="2"
                fill="#B0D4F1"
                opacity="0.85"
              />
              <path
                d="M28 165 Q64 155 100 165 Q136 175 172 165"
                stroke="#2E7FCF"
                stroke-width="2.5"
                fill="none"
                stroke-linecap="round"
              />
              <circle cx="168" cy="30" r="2.5" fill="#E8A020" />
            </svg>
            <div>
              <div class="fw-bold fs-5">Portalcrane</div>
              <div class="text-muted small">Docker Registry Manager v1.0.0</div>
            </div>
          </div>
          <p class="text-muted small mb-2">
            Built with Angular 21 (Zoneless + Signals) + FastAPI. Manages CNCF
            Distribution (Docker Registry).
          </p>
          <p class="text-muted small mb-0">
            Features: Browse images, manage tags, staging pipeline with optional
            ClamAV scanning, optional Trivy CVE scanning, OIDC support.
          </p>
        </div>
      </div>
    </div>
  `,
  styles: [
    `
      .card {
        background: var(--pc-card-bg);
        border-radius: 12px;
      }
      .scan-row {
        background: var(--pc-bg-secondary, rgba(0, 0, 0, 0.03));
      }
    `,
  ],
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
