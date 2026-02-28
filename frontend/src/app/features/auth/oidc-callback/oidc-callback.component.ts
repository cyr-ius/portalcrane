import { Component, OnInit, inject, signal } from "@angular/core";
import { ActivatedRoute, Router } from "@angular/router";
import { AuthService } from "../../../core/services/auth.service";

@Component({
  selector: "app-oidc-callback",
  imports: [],
  templateUrl: "./oidc-callback.component.html",
})
export class OidcCallbackComponent implements OnInit {
  private readonly OIDC_STATE_KEY = "pc_oidc_state";
  private route = inject(ActivatedRoute);
  private router = inject(Router);
  private auth = inject(AuthService);

  readonly error = signal("");

  ngOnInit() {
    const code = this.route.snapshot.queryParamMap.get("code");
    const errorParam = this.route.snapshot.queryParamMap.get("error");
    const errorDesc =
      this.route.snapshot.queryParamMap.get("error_description");
    const state = this.route.snapshot.queryParamMap.get("state");
    const expectedState = sessionStorage.getItem(this.OIDC_STATE_KEY);

    if (errorParam) {
      this.error.set(errorDesc || errorParam);
      return;
    }

    if (!code) {
      this.error.set("No authorization code received");
      return;
    }

    if (!state || !expectedState || state !== expectedState) {
      this.error.set("Invalid OIDC state");
      sessionStorage.removeItem(this.OIDC_STATE_KEY);
      return;
    }

    sessionStorage.removeItem(this.OIDC_STATE_KEY);

    this.auth.handleOidcCallback(code, state).subscribe({
      next: () => this.router.navigate(["/"]),
      error: (err) => {
        this.error.set(err.error?.detail || "OIDC callback failed");
      },
    });
  }
}
