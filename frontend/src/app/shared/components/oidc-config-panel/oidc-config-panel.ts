import { JsonPipe } from "@angular/common";
import { ChangeDetectionStrategy, Component, inject } from "@angular/core";
import { AppConfigService } from "../../../core/services/app-config.service";

@Component({
  selector: "app-oidc-config-panel",
  imports: [JsonPipe],
  templateUrl: "./oidc-config-panel.html",
  styleUrl: "./oidc-config-panel.css",
  changeDetection: ChangeDetectionStrategy.OnPush,
})
export class OidcConfigPanel {
  configService = inject(AppConfigService);
}
