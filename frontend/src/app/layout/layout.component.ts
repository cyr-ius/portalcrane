/**
 * Portalcrane - Layout Component
 *
 * Authenticated application shell with collapsible sidebar, navigation,
 * theme switcher, user zone, and account modal.
 *
 * Change: BackendAvailabilityService is no longer injected here for
 * backend-down detection. The detection is now handled at the root level
 * (AppComponent) so it also covers the login page. The layout no longer
 * needs to redirect to /backend-unavailable; AppComponent renders
 * BackendUnavailableComponent directly when the backend is unreachable.
 *
 * SessionExpiredModalComponent is kept here because it must only appear
 * when the user is in an authenticated session (inside the layout).
 */
import { Component, inject, OnDestroy, OnInit, signal } from "@angular/core";
import { RouterLink, RouterLinkActive, RouterOutlet } from "@angular/router";
import { readBool } from "../core/helpers/storage";
import { AuthService } from "../core/services/auth.service";
import { ThemeService } from "../core/services/theme.service";
import { AccountModalComponent } from "../shared/components/account-modal/account-modal.component";
import { AppLogo } from "../shared/components/app-logo/app-logo";
import { SessionExpiredModalComponent } from "../shared/components/session-expired-modal/session-expired-modal.component";


// Breakpoint below which the sidebar auto-collapses (matches Bootstrap 'lg')
const COLLAPSE_BREAKPOINT = 992;

@Component({
  selector: "app-layout",
  imports: [
    RouterOutlet,
    RouterLink,
    RouterLinkActive,
    SessionExpiredModalComponent,
    AccountModalComponent,
    AppLogo,
  ],
  templateUrl: "./layout.component.html",
  styleUrl: "./layout.component.css",
})
export class LayoutComponent implements OnInit, OnDestroy {
  auth = inject(AuthService);
  themeService = inject(ThemeService);

  private readonly SIDEBAR_KEY = "pc_sidebar_collapsed";

  sidebarCollapsed = signal<boolean>(readBool(this.SIDEBAR_KEY, false));
  themePickerOpen = signal(false);
  accountModalOpen = signal(false);

  private userHasToggled = signal(false);

  private resizeObserver: ResizeObserver | null = null;

  ngOnInit(): void {
    // Collapse immediately if the window is already narrow on load
    this.applyBreakpoint(window.innerWidth);

    // Watch for window width changes using ResizeObserver on <body>
    this.resizeObserver = new ResizeObserver((entries) => {
      const width = entries[0]?.contentRect.width ?? window.innerWidth;
      this.applyBreakpoint(width);
    });
    this.resizeObserver.observe(document.body);
  }

  ngOnDestroy(): void {
    this.resizeObserver?.disconnect();
  }

  /**
   * Collapse or expand the sidebar based on the current viewport width.
   * If the user has manually toggled the sidebar AND the screen is wide,
   * we respect their choice. On narrow screens we always force-collapse.
   */
  private applyBreakpoint(width: number): void {
    if (width < COLLAPSE_BREAKPOINT) {
      // Always collapse on small screens and reset the manual-toggle flag
      this.sidebarCollapsed.set(true);
      this.userHasToggled.set(false);
    } else if (!this.userHasToggled()) {
      // Wide screen and user hasn't toggled: expand automatically
      const saved = localStorage.getItem(this.SIDEBAR_KEY);
      this.sidebarCollapsed.set(saved === "true");
    }
  }

  toggleSidebar(): void {
    const next = !this.sidebarCollapsed();
    this.sidebarCollapsed.set(next);
    this.userHasToggled.set(true);
    localStorage.setItem(this.SIDEBAR_KEY, String(next));
  }

  setTheme(theme: "light" | "dark" | "auto"): void {
    this.themeService.setTheme(theme);
    this.themePickerOpen.set(false);
  }
}
