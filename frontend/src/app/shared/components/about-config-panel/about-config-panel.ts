import { ChangeDetectionStrategy, Component, inject } from "@angular/core";
import { AboutService } from "../../../core/services/about.service";

@Component({
  selector: "app-about-config-panel",
  imports: [],
  templateUrl: "./about-config-panel.html",
  styleUrl: "./about-config-panel.css",
  changeDetection: ChangeDetectionStrategy.OnPush,
})
export class AboutConfigPanel {
  aboutService = inject(AboutService);
}
