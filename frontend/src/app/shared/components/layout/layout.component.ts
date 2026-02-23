import { CommonModule } from "@angular/common";
import {
  Component,
  effect,
  inject,
  OnDestroy,
  OnInit,
  signal,
} from "@angular/core";
import { RouterLink, RouterLinkActive, RouterOutlet } from "@angular/router";
import { AuthService } from "../../../core/services/auth.service";
import { ThemeService } from "../../../core/services/theme.service";

// Breakpoint below which the sidebar auto-collapses (matches Bootstrap 'lg')
const COLLAPSE_BREAKPOINT = 992;

@Component({
  selector: "app-layout",
  imports: [RouterOutlet, RouterLink, RouterLinkActive, CommonModule],
  templateUrl: "./layout.component.html",
  styleUrl: "./layout.component.css",
})
export class LayoutComponent implements OnInit, OnDestroy {
  auth = inject(AuthService);
  themeService = inject(ThemeService);

  sidebarCollapsed = signal(false);
  themePickerOpen = signal(false);

  // Tracks whether the sidebar was manually toggled by the user.
  // When true, automatic breakpoint logic will not override the user's choice.
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
      this.sidebarCollapsed.set(false);
    }
    // Wide screen + user has toggled: leave sidebarCollapsed as-is
  }

  toggleSidebar(): void {
    this.userHasToggled.set(true);
    this.sidebarCollapsed.set(!this.sidebarCollapsed());
    if (!this.sidebarCollapsed()) {
      this.themePickerOpen.set(false);
    }
  }

  setTheme(theme: "light" | "dark" | "auto"): void {
    this.themeService.setTheme(theme);
    this.themePickerOpen.set(false);
  }
}
