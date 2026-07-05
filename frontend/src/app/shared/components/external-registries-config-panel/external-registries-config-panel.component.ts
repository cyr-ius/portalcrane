/**
 * Portalcrane - ExternalRegistriesConfigPanelComponent
 * Admin settings panel to manage global and personal external registries.
 *
 * The whole add/edit form + list is provided by the shared
 * RegistryFormPanelComponent; this component only configures it for the admin
 * (global-capable) context.
 */
import { Component } from "@angular/core";
import { RegistryFormPanelComponent } from "../registry-form-panel/registry-form-panel.component";

@Component({
  selector: "app-external-registries-config-panel",
  imports: [RegistryFormPanelComponent],
  templateUrl: "./external-registries-config-panel.component.html",
  styleUrl: "./external-registries-config-panel.component.css",
})
export class ExternalRegistriesConfigPanelComponent {}
