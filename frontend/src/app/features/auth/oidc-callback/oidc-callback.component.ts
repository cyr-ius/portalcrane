/**
 * Portalcrane - OidcCallbackComponent
 * Handles the browser redirect from the OIDC provider.
 * Validates the state parameter, exchanges the code via OidcService,
 * stores the token via AuthService, then navigates to the home page.
 */

import { Component, inject, OnInit, signal } from "@angular/core";
import { ActivatedRoute, Router } from "@angular/router";

import { OIDC_STATE_KEY } from "../../../core/constants/oidc.constants";
import { AuthService } from "../../../core/services/auth.service";
import { OidcService } from "../../../core/services/oidc.service";

@Component({
  selector: "app-oidc-callback",
  imports: [],
  templateUrl: "./oidc-callback.component.html",
})
export class OidcCallbackComponent implements OnInit {
  private readonly route = inject(ActivatedRoute);
  private readonly router = inject(Router);
  private readonly auth = inject(AuthService);
  private readonly oidc = inject(OidcService);

  readonly error = signal("");

  ngOnInit(): void {
    const params = this.route.snapshot.queryParamMap;
    const code = params.get("code");
    const errorParam = params.get("error");
    const errorDesc = params.get("error_description");
    const state = params.get("state");
    const expectedState = sessionStorage.getItem(OIDC_STATE_KEY);

    // Provider returned an error
    if (errorParam) {
      this.error.set(errorDesc ?? errorParam);
      return;
    }

    // Missing authorization code
    if (!code) {
      this.error.set("No authorization code received");
      return;
    }

    // CSRF state mismatch
    if (!state || !expectedState || state !== expectedState) {
      this.error.set("Invalid OIDC state — possible CSRF attempt");
      sessionStorage.removeItem(OIDC_STATE_KEY);
      return;
    }

    sessionStorage.removeItem(OIDC_STATE_KEY);

    // Exchange the code for a local JWT
    this.oidc.exchangeCode(code, state).subscribe({
      next: (response) => {
        // Store the token and load the user profile
        this.auth.storeToken(response.access_token);
        this.auth.loadUserInfo();
        this.router.navigate(["/"]);
      },
      error: (err) => {
        this.error.set(err.error?.detail ?? "OIDC callback failed");
      },
    });
  }
}
