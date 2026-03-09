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

  private _activeTab = signal<SettingsTab>("vulnerabilities");
  readonly activeTab = this._activeTab.asReadonly();

  setTab(tab: SettingsTab) {
    this._activeTab.set(tab);
  }

}
