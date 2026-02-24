import { Component, OnInit, inject, signal } from "@angular/core";
import { ActivatedRoute, Router } from "@angular/router";
import { AuthService } from "../../../core/services/auth.service";

@Component({
  selector: "app-oidc-callback",
  imports: [],
  templateUrl: "./oidc-callback.component.html",
})
export class OidcCallbackComponent implements OnInit {
  private route = inject(ActivatedRoute);
  private router = inject(Router);
  private auth = inject(AuthService);

  readonly error = signal("");

  ngOnInit() {
    const code = this.route.snapshot.queryParamMap.get("code");
    const errorParam = this.route.snapshot.queryParamMap.get("error");

    if (errorParam) {
      this.error.set(errorParam);
      return;
    }

    if (!code) {
      this.error.set("No authorization code received");
      return;
    }

    this.auth.handleOidcCallback(code).subscribe({
      next: () => this.router.navigate(["/"]),
      error: (err) => {
        this.error.set(err.error?.detail || "OIDC callback failed");
      },
    });
  }
}
