/**
 * Portalcrane - Settings Component
 */
import {
  Component,
  computed,
  inject,
  OnInit
} from "@angular/core";
import { Router } from "@angular/router";
import { AuthService } from "../../core/services/auth.service";
import { SettingsService } from "../../core/services/settings.service";
import { ThemeService } from "../../core/services/theme.service";
import { AboutConfigPanel } from "../../shared/components/about-config-panel/about-config-panel";
import { AccountsConfigPanel } from "../../shared/components/accounts-config-panel/accounts-config-panel";
import { AuditConfigPanelComponent } from "../../shared/components/audit-config-panel/audit-config-panel.component";
import { ExternalRegistriesConfigPanelComponent } from "../../shared/components/external-registries-config-panel/external-registries-config-panel.component";
import { FoldersConfigPanel } from "../../shared/components/folders-config-panel/folders-config-panel.component";
import { OidcConfigPanel } from "../../shared/components/oidc-config-panel/oidc-config-panel";
import { SyncConfigPanelComponent } from "../../shared/components/sync-config-panel/sync-config-panel.component";
import { VulnConfigPanelComponent } from "../../shared/components/vuln-config-panel/vuln-config-panel.component";

/** Tabs available in the Settings page. */
type SettingsTab =
  | "vulnerabilities"
  | "accounts"
  | "folders"
  | "registries"
  | "sync"
  | "audit"
  | "oidc"
  | "about";

@Component({
  selector: "app-settings",
  imports: [
    VulnConfigPanelComponent,
    OidcConfigPanel,
    AccountsConfigPanel,
    FoldersConfigPanel,
    AboutConfigPanel,
    SyncConfigPanelComponent,
    AuditConfigPanelComponent,
    ExternalRegistriesConfigPanelComponent
  ],
  templateUrl: "./settings.component.html",
  styleUrl: "./settings.component.css",
})
export class SettingsComponent implements OnInit {
  themeService = inject(ThemeService);
  authService = inject(AuthService);
  private router = inject(Router);
  private settingsSvc = inject(SettingsService)

  readonly activeTab = computed(() => this.settingsSvc.activeTab())

  ngOnInit(): void {
    if (!this.authService.currentUser()?.is_admin) {
      this.router.navigate(["/dashboard"]);
      return;
    }
  }

  setTab(tab: SettingsTab) {
    this.settingsSvc.setTab(tab);
  }

}
