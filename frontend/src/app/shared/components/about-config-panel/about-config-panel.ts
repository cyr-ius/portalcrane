import { Component, inject, OnInit } from "@angular/core";
import { TranslatePipe } from "@ngx-translate/core";
import { AboutService } from "../../../core/services/about.service";
import { AppLogo } from "../app-logo/app-logo";

@Component({
  selector: "app-about-config-panel",
  imports: [AppLogo, TranslatePipe],
  templateUrl: "./about-config-panel.html",
  styleUrl: "./about-config-panel.css",
})
export class AboutConfigPanel implements OnInit {
  aboutService = inject(AboutService);

  ngOnInit(): void {
    this.aboutService.load();
  }
}
