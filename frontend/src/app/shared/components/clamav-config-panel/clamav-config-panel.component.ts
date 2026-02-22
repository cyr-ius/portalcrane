import { CommonModule } from "@angular/common";
import { Component, inject } from "@angular/core";
import { AppConfigService } from "../../../core/services/app-config.service";

@Component({
  selector: "app-clamav-config-panel",
  imports: [CommonModule],
  templateUrl: "./clamav-config-panel.component.html",
})
export class ClamAvConfigPanelComponent {
  configService = inject(AppConfigService);
}
