import { Injectable, signal } from "@angular/core";

/**
 * Tabs available in the Settings page.
 * Note: the 'sync' tab has been removed — image transfer is now handled
 * by the Transfer modal in the Images section.
 */
export type SettingsTab =
  | "vulnerabilities"
  | "accounts"
  | "folders"
  | "registries"
  | "audit"
  | "oidc"
  | "network"
  | "about";

@Injectable({
  providedIn: "root",
})
export class SettingsService {
  private _activeTab = signal<SettingsTab>("vulnerabilities");
  readonly activeTab = this._activeTab.asReadonly();

  setTab(tab: SettingsTab): void {
    this._activeTab.set(tab);
  }
}
