import { Injectable, signal } from '@angular/core';

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


@Injectable({
  providedIn: 'root',
})
export class SettingsService {

  activeTab = signal<SettingsTab>("vulnerabilities");

  setTab(tab: SettingsTab) {
    this.activeTab.set(tab);
  }

}
