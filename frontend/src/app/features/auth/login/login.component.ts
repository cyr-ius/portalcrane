import { SlicePipe } from "@angular/common";
import { Component, inject, OnInit, signal } from "@angular/core";
import { form, FormField, required, submit } from "@angular/forms/signals";
import { Router } from "@angular/router";
import { firstValueFrom } from "rxjs";
import { AuthService, OidcConfig } from "../../../core/services/auth.service";
import { ThemeService } from "../../../core/services/theme.service";

@Component({
  selector: "app-login",
  imports: [SlicePipe, FormField],
  templateUrl: "./login.component.html",
  styleUrl: "./login.component.css",
})
export class LoginComponent implements OnInit {
  private readonly OIDC_STATE_KEY = "pc_oidc_state";
  private auth = inject(AuthService);
  private router = inject(Router);
  themeService = inject(ThemeService);

  loginModel = signal({ username: "", password: "" });
  loginForm = form(this.loginModel, (p) => ({
    username: [required(p.username)],
    password: [required(p.password)],
  }));

  loading = signal(false);
  error = signal("");
  showPassword = signal(false);
  oidcConfig = signal<OidcConfig | null>(null);

  ngOnInit() {
    this.auth.getOidcConfig().subscribe({
      next: (config) => this.oidcConfig.set(config),
    });
  }

  onSubmit(event: Event) {
    event.preventDefault();

    this.loading.set(true);
    this.error.set("");

    submit(this.loginForm, async (form) => {
      const { username, password } = form().value();
      try {
        await firstValueFrom(this.auth.login(username!, password!));
        this.router.navigate(["/"]);
      } catch (err: unknown) {
        const httpErr = err as { error?: { detail?: string } };
        this.error.set(httpErr.error?.detail ?? "Authentication failed");
      } finally {
        this.loading.set(false);
      }
    });
  }

  loginWithOidc() {
    const config = this.oidcConfig();
    if (!config?.authorization_endpoint) return;

    const state = crypto.randomUUID();
    sessionStorage.setItem(this.OIDC_STATE_KEY, state);

    const params = new URLSearchParams({
      response_type: config.response_type,
      client_id: config.client_id,
      redirect_uri: config.redirect_uri,
      scope: config.scope,
      state,
    });

    window.location.href = `${config.authorization_endpoint}?${params}`;
  }
}
