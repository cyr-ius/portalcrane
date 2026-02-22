import { Component, inject, OnInit } from "@angular/core";
import { CommonModule } from "@angular/common";
import { ThemeService } from "../../core/services/theme.service";
import { AuthService } from "../../core/services/auth.service";
import { VulnConfigService } from "../../core/services/vuln-config.service";
import { VulnConfigPanelComponent } from "../../shared/components/vuln-config-panel/vuln-config-panel.component";

@Component({
  selector: "app-settings",
  imports: [CommonModule, VulnConfigPanelComponent],
  template: `
    <div class="p-4">
      <h2 class="fw-bold mb-1">Settings</h2>
      <p class="text-muted small mb-4">
        Application preferences and information
      </p>

      <!-- Theme -->
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

      <!-- Vulnerability Scanner -->
      <app-vuln-config-panel />

      <!-- About -->
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
              fill="none"
              xmlns="http://www.w3.org/2000/svg"
            >
              <rect width="200" height="200" rx="24" fill="#0D1B2A" />
              <rect
                x="20"
                y="130"
                width="160"
                height="20"
                rx="4"
                fill="#1B4D7E"
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
            Features: Browse images, manage tags, staging pipeline with ClamAV
            scanning, Trivy CVE scan, OIDC support.
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
    `,
  ],
})
export class SettingsComponent implements OnInit {
  themeService = inject(ThemeService);
  authService = inject(AuthService);
  private vulnConfigService = inject(VulnConfigService);

  ngOnInit() {
    this.vulnConfigService.loadConfig().subscribe();
  }
}
