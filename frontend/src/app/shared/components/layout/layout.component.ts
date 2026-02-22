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
          </ul>
        </div>

        <!-- Footer : Settings + séparateur + User -->
        <div class="sidebar-footer d-flex flex-column">
          <!-- Settings — toujours visible, juste au-dessus du séparateur -->
          <div class="px-2 pb-2">
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
          </div>

          <!-- Séparateur -->
          <div class="border-top mx-3"></div>

          <!-- Zone utilisateur -->
          <div class="p-3">
            <!-- Menu replié : avatar cliquable qui ouvre le picker de thème -->
            @if (sidebarCollapsed()) {
              <div class="position-relative">
                <button
                  class="avatar-circle border-0 w-100"
                  (click)="themePickerOpen.set(!themePickerOpen())"
                  [title]="'Theme: ' + themeService.theme()"
                >
                  <i class="bi bi-person-fill"></i>
                  <!-- Indicateur thème actif -->
                  <span class="theme-dot">
                    @if (themeService.theme() === "light") {
                      <i class="bi bi-sun-fill"></i>
                    } @else if (themeService.theme() === "dark") {
                      <i class="bi bi-moon-fill"></i>
                    } @else {
                      <i class="bi bi-circle-half"></i>
                    }
                  </span>
                </button>

                <!-- Dropdown thème (flottant à droite) -->
                @if (themePickerOpen()) {
                  <div class="theme-dropdown-collapsed">
                    <button
                      class="theme-option"
                      [class.active]="themeService.theme() === 'light'"
                      (click)="setTheme('light')"
                      title="Light"
                    >
                      <i class="bi bi-sun-fill"></i>
                    </button>
                    <button
                      class="theme-option"
                      [class.active]="themeService.theme() === 'dark'"
                      (click)="setTheme('dark')"
                      title="Dark"
                    >
                      <i class="bi bi-moon-fill"></i>
                    </button>
                    <button
                      class="theme-option"
                      [class.active]="themeService.theme() === 'auto'"
                      (click)="setTheme('auto')"
                      title="Auto"
                    >
                      <i class="bi bi-circle-half"></i>
                    </button>
                  </div>
                }
              </div>
            }

            <!-- Menu déplié : sélecteur thème inline + info utilisateur -->
            @if (!sidebarCollapsed()) {
              <!-- Theme switcher inline -->
              <div class="d-flex gap-1 mb-2">
                <button
                  class="btn btn-sm px-2 flex-fill"
                  [class.btn-primary]="themeService.theme() === 'light'"
                  [class.btn-outline-secondary]="
                    themeService.theme() !== 'light'
                  "
                  (click)="themeService.setTheme('light')"
                  title="Light"
                >
                  <i class="bi bi-sun-fill"></i>
                </button>
                <button
                  class="btn btn-sm px-2 flex-fill"
                  [class.btn-primary]="themeService.theme() === 'dark'"
                  [class.btn-outline-secondary]="
                    themeService.theme() !== 'dark'
                  "
                  (click)="themeService.setTheme('dark')"
                  title="Dark"
                >
                  <i class="bi bi-moon-fill"></i>
                </button>
                <button
                  class="btn btn-sm px-2 flex-fill"
                  [class.btn-primary]="themeService.theme() === 'auto'"
                  [class.btn-outline-secondary]="
                    themeService.theme() !== 'auto'
                  "
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
              </div>
            }
          </div>
        </div>

        <!-- Collapse toggle -->
        <button
          class="sidebar-toggle btn btn-sm"
          (click)="toggleSidebar()"
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

      /* Navigation links */
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

      /* Avatar */
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
        cursor: default;
        position: relative;
      }

      /* Avatar cliquable (mode replié) */
      button.avatar-circle {
        cursor: pointer;
        transition:
          background 0.15s,
          transform 0.15s;
        padding: 0;
        margin: 0 auto;
      }
      button.avatar-circle:hover {
        background: var(--pc-accent);
        color: #fff;
        transform: scale(1.08);
      }

      /* Indicateur thème actif sur l'avatar */
      .theme-dot {
        position: absolute;
        bottom: -2px;
        right: -2px;
        width: 14px;
        height: 14px;
        border-radius: 50%;
        background: var(--pc-sidebar-bg);
        border: 1px solid var(--pc-border);
        display: flex;
        align-items: center;
        justify-content: center;
        font-size: 0.5rem;
        color: var(--pc-accent-2);
        pointer-events: none;
      }

      /* Dropdown thème en mode replié */
      .theme-dropdown-collapsed {
        position: absolute;
        left: 44px;
        bottom: 0;
        background: var(--pc-card-bg);
        border: 1px solid var(--pc-border);
        border-radius: 10px;
        padding: 6px;
        display: flex;
        flex-direction: column;
        gap: 4px;
        box-shadow: 4px 4px 16px var(--pc-shadow);
        z-index: 100;
        min-width: 44px;
      }
      .theme-option {
        width: 32px;
        height: 32px;
        border-radius: 8px;
        border: 1px solid transparent;
        background: transparent;
        color: var(--pc-text-muted);
        display: flex;
        align-items: center;
        justify-content: center;
        font-size: 0.85rem;
        cursor: pointer;
        transition: all 0.15s;
      }
      .theme-option:hover {
        background: var(--pc-nav-hover);
        color: var(--pc-accent);
      }
      .theme-option.active {
        background: var(--pc-nav-active-bg);
        border-color: var(--pc-accent);
        color: var(--pc-accent);
      }

      .x-small {
        font-size: 0.7rem;
      }

      /* Collapse toggle button */
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
  themePickerOpen = signal(false);

  toggleSidebar() {
    this.sidebarCollapsed.set(!this.sidebarCollapsed());
    // Ferme le picker quand on déplie le menu
    if (!this.sidebarCollapsed()) {
      this.themePickerOpen.set(false);
    }
  }

  setTheme(theme: "light" | "dark" | "auto") {
    this.themeService.setTheme(theme);
    this.themePickerOpen.set(false);
  }
}
