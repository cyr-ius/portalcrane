import { CommonModule } from "@angular/common";
import { Component, inject, signal } from "@angular/core";
import { RouterLink, RouterLinkActive, RouterOutlet } from "@angular/router";
import { AuthService } from "../../../core/services/auth.service";
import { ThemeService } from "../../../core/services/theme.service";

@Component({
  selector: "app-layout",
  imports: [RouterOutlet, RouterLink, RouterLinkActive, CommonModule],
  templateUrl: "./layout.component.html",
  styleUrl: "./layout.component.css",
})
export class LayoutComponent {
  auth = inject(AuthService);
  themeService = inject(ThemeService);
  sidebarCollapsed = signal(false);
  themePickerOpen = signal(false);

  toggleSidebar() {
    this.sidebarCollapsed.set(!this.sidebarCollapsed());
    if (!this.sidebarCollapsed()) {
      this.themePickerOpen.set(false);
    }
  }

  setTheme(theme: "light" | "dark" | "auto") {
    this.themeService.setTheme(theme);
    this.themePickerOpen.set(false);
  }
}
