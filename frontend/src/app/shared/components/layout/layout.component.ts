import { Component, inject, signal } from "@angular/core";
import { RouterOutlet, RouterLink, RouterLinkActive } from "@angular/router";
import { CommonModule } from "@angular/common";
import { AuthService } from "../../../core/services/auth.service";
import { ThemeService } from "../../../core/services/theme.service";

@Component({
  selector: "app-layout",
  imports: [RouterOutlet, RouterLink, RouterLinkActive, CommonModule],
  template: `
    <div class="app-shell d-flex">
      <!-- Sidebar -->
      <nav
        class="sidebar d-flex flex-column"
        [class.collapsed]="sidebarCollapsed()"
      >
        <!-- Logo -->
        <div class="sidebar-brand d-flex align-items-center gap-2 px-3 py-3">
          <svg
            width="32"
            height="32"
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
            <rect x="62" y="60" width="12" height="97" rx="2" fill="#2E7FCF" />
            <rect x="62" y="60" width="96" height="10" rx="2" fill="#2E7FCF" />
            <rect x="30" y="60" width="34" height="8" rx="2" fill="#1B4D7E" />
            <rect x="22" y="55" width="16" height="18" rx="3" fill="#1B4D7E" />
            <line
              x1="158"
              y1="65"
              x2="68"
              y2="40"
              stroke="#E8A020"
              stroke-width="1.5"
              opacity="0.8"
            />
            <rect x="56" y="35" width="24" height="28" rx="3" fill="#1B4D7E" />
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
            <rect x="128" y="128" width="44" height="5" rx="2" fill="#F0B030" />
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
            <rect x="56" y="100" width="24" height="20" rx="3" fill="#1B4D7E" />
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
          </svg>
          @if (!sidebarCollapsed()) {
            <span class="brand-name fw-bold">Portalcrane</span>
          }
        </div>

        <!-- Navigation -->
        <div class="sidebar-nav flex-grow-1 px-2">
          <ul class="nav flex-column gap-1">
            <li class="nav-item">
              <a
                routerLink="/dashboard"
                routerLinkActive="active"
                class="nav-link d-flex align-items-center gap-2"
                [title]="sidebarCollapsed() ? 'Dashboard' : ''"
              >
                <i class="bi bi-speedometer2 nav-icon"></i>
                @if (!sidebarCollapsed()) {
                  <span>Dashboard</span>
                }
              </a>
            </li>
            <li class="nav-item">
              <a
                routerLink="/images"
                routerLinkActive="active"
                class="nav-link d-flex align-items-center gap-2"
                [title]="sidebarCollapsed() ? 'Images' : ''"
              >
                <i class="bi bi-layers nav-icon"></i>
                @if (!sidebarCollapsed()) {
                  <span>Images</span>
                }
              </a>
            </li>
            <li class="nav-item">
              <a
                routerLink="/staging"
                routerLinkActive="active"
                class="nav-link d-flex align-items-center gap-2"
                [title]="sidebarCollapsed() ? 'Staging' : ''"
              >
                <i class="bi bi-cloud-arrow-down nav-icon"></i>
                @if (!sidebarCollapsed()) {
                  <span>Staging</span>
                }
              </a>
            </li>

            <li class="nav-item mt-3">
              <span class="nav-section-label text-muted small px-2">
                @if (!sidebarCollapsed()) {
                  ADMIN
                }
              </span>
            </li>
            <li class="nav-item">
              <a
                routerLink="/settings"
                routerLinkActive="active"
                class="nav-link d-flex align-items-center gap-2"
                [title]="sidebarCollapsed() ? 'Settings' : ''"
              >
                <i class="bi bi-gear nav-icon"></i>
                @if (!sidebarCollapsed()) {
                  <span>Settings</span>
                }
              </a>
            </li>
          </ul>
        </div>

        <!-- Theme + User -->
        <div class="sidebar-footer p-3 border-top">
          <!-- Theme switcher -->
          <div
            class="d-flex gap-1 mb-2"
            [class.flex-column]="sidebarCollapsed()"
          >
            <button
              class="btn btn-sm px-2 flex-fill"
              [class.btn-primary]="themeService.theme() === 'light'"
              [class.btn-outline-secondary]="themeService.theme() !== 'light'"
              (click)="themeService.setTheme('light')"
              title="Light"
            >
              <i class="bi bi-sun-fill"></i>
            </button>
            <button
              class="btn btn-sm px-2 flex-fill"
              [class.btn-primary]="themeService.theme() === 'dark'"
              [class.btn-outline-secondary]="themeService.theme() !== 'dark'"
              (click)="themeService.setTheme('dark')"
              title="Dark"
            >
              <i class="bi bi-moon-fill"></i>
            </button>
            <button
              class="btn btn-sm px-2 flex-fill"
              [class.btn-primary]="themeService.theme() === 'auto'"
              [class.btn-outline-secondary]="themeService.theme() !== 'auto'"
              (click)="themeService.setTheme('auto')"
              title="Auto"
            >
              <i class="bi bi-circle-half"></i>
            </button>
          </div>

          <!-- User info -->
          <div class="d-flex align-items-center gap-2">
            <div class="avatar-circle">
              <i class="bi bi-person-fill"></i>
            </div>
            @if (!sidebarCollapsed()) {
              <div class="flex-grow-1 overflow-hidden">
                <div class="small fw-semibold text-truncate">
                  {{ auth.currentUser()?.username }}
                </div>
                <div class="x-small text-muted">Administrator</div>
              </div>
              <button
                class="btn btn-sm btn-link text-muted p-0"
                (click)="auth.logout()"
                title="Logout"
              >
                <i class="bi bi-box-arrow-right"></i>
              </button>
            }
          </div>
        </div>

        <!-- Collapse toggle -->
        <button
          class="sidebar-toggle btn btn-sm"
          (click)="sidebarCollapsed.set(!sidebarCollapsed())"
          [title]="sidebarCollapsed() ? 'Expand sidebar' : 'Collapse sidebar'"
        >
          <i
            [class]="
              sidebarCollapsed()
                ? 'bi bi-chevron-double-right'
                : 'bi bi-chevron-double-left'
            "
          ></i>
        </button>
      </nav>

      <!-- Main content -->
      <main class="main-content flex-grow-1 overflow-auto">
        <router-outlet />
      </main>
    </div>
  `,
  styles: [
    `
      .app-shell {
        min-height: 100vh;
      }
      .sidebar {
        width: 240px;
        min-width: 240px;
        transition:
          width 0.25s ease,
          min-width 0.25s ease;
        background: var(--pc-sidebar-bg);
        border-right: 1px solid var(--pc-border);
        position: relative;
      }
      .sidebar.collapsed {
        width: 64px;
        min-width: 64px;
      }
      .brand-name {
        font-size: 1.1rem;
        color: var(--pc-accent);
        white-space: nowrap;
      }
      .nav-link {
        border-radius: 8px;
        color: var(--pc-text-muted);
        padding: 0.5rem 0.75rem;
        font-size: 0.875rem;
        font-weight: 500;
        transition: all 0.15s;
        white-space: nowrap;
      }
      .nav-link:hover {
        background: var(--pc-nav-hover);
        color: var(--pc-accent);
      }
      .nav-link.active {
        background: var(--pc-nav-active-bg);
        color: var(--pc-accent);
        font-weight: 600;
      }
      .nav-icon {
        font-size: 1.1rem;
        min-width: 20px;
        text-align: center;
      }
      .nav-section-label {
        font-size: 0.65rem;
        font-weight: 700;
        letter-spacing: 0.08em;
      }
      .avatar-circle {
        width: 32px;
        height: 32px;
        border-radius: 50%;
        background: var(--pc-accent-soft);
        color: var(--pc-accent);
        display: flex;
        align-items: center;
        justify-content: center;
        font-size: 0.875rem;
        flex-shrink: 0;
      }
      .x-small {
        font-size: 0.7rem;
      }
      .sidebar-toggle {
        position: absolute;
        top: 50%;
        right: -12px;
        transform: translateY(-50%);
        width: 24px;
        height: 24px;
        border-radius: 50%;
        background: var(--pc-sidebar-bg);
        border: 1px solid var(--pc-border);
        display: flex;
        align-items: center;
        justify-content: center;
        padding: 0;
        font-size: 0.6rem;
        color: var(--pc-text-muted);
        z-index: 10;
      }
      .main-content {
        background: var(--pc-main-bg);
      }
    `,
  ],
})
export class LayoutComponent {
  auth = inject(AuthService);
  themeService = inject(ThemeService);
  sidebarCollapsed = signal(false);
}
