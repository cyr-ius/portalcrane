import { Component, inject } from "@angular/core";
import { CommonModule } from "@angular/common";
import { ThemeService } from "../../core/services/theme.service";
import { AuthService } from "../../core/services/auth.service";

@Component({
  selector: "app-settings",
  imports: [CommonModule],
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

      <!-- User info -->
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
            Features: Browse images, manage tags, staging pipeline with ClamAV
            scanning, OIDC support.
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
export class SettingsComponent {
  themeService = inject(ThemeService);
  authService = inject(AuthService);
}
