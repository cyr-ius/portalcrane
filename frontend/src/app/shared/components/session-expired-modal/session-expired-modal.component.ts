import { Component, effect, inject, OnDestroy, OnInit } from "@angular/core";
import { Router } from "@angular/router";
import { Modal } from "bootstrap";
import { SessionExpiredService } from "../../../core/services/session-expired.service";

@Component({
  selector: "app-session-expired-modal",
  imports: [],
  templateUrl: "./session-expired-modal.component.html",
})
export class SessionExpiredModalComponent implements OnInit, OnDestroy {
  private sessionExpired = inject(SessionExpiredService);
  private router = inject(Router);

  private modal: Modal | null = null;

  ngOnInit() {
    const el = document.getElementById("sessionExpiredModal");
    if (el) {
      this.modal = new Modal(el, { backdrop: "static", keyboard: false });

      // Close event: navigate to login
      el.addEventListener("hidden.bs.modal", () => {
        this.sessionExpired.hide();
        this.router.navigate(["/auth"]);
      });
    }

    // React to signal changes
    effect(() => {
      if (this.sessionExpired.isVisible()) {
        this.modal?.show();
      }
    });
  }

  ngOnDestroy() {
    this.modal?.dispose();
  }

  onConfirm() {
    this.modal?.hide();
  }
}
