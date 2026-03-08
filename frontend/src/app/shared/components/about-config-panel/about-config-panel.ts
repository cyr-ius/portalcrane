import { Component, inject, OnInit } from "@angular/core";
import { AboutService } from "../../../core/services/about.service";

@Component({
  selector: "app-about-config-panel",
  imports: [],
  templateUrl: "./about-config-panel.html",
  styleUrl: "./about-config-panel.css",
})
export class AboutConfigPanel implements OnInit {
  aboutService = inject(AboutService);

  ngOnInit(): void {
    this.aboutService.load();
  }
}
