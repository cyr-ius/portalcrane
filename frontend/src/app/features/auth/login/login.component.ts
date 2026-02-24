import { SlicePipe } from "@angular/common";
import { Component, inject, signal } from "@angular/core";
import { ReactiveFormsModule } from "@angular/forms";
import { form, FormField, required, submit } from "@angular/forms/signals";
import { Router } from "@angular/router";
import { firstValueFrom } from "rxjs";
import { AuthService, OidcConfig } from "../../../core/services/auth.service";
import { ThemeService } from "../../../core/services/theme.service";

@Component({
  selector: "app-login",
  imports: [SlicePipe, ReactiveFormsModule, FormField],
  templateUrl: "./login.component.html",
  styleUrl: "./login.component.css",
})
export class LoginComponent {
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

  constructor() {
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
      } catch (err: any) {
        this.error.set(err.error?.detail || "Authentication failed");
        this.loading.set(false);
      }
    });
  }

  loginWithOidc() {
    const config = this.oidcConfig();
    if (!config?.authorization_endpoint) return;

    const params = new URLSearchParams({
      response_type: "code",
      client_id: config.client_id,
      redirect_uri: config.redirect_uri,
      scope: "openid profile email",
    });

    window.location.href = `${config.authorization_endpoint}?${params}`;
  }
}
