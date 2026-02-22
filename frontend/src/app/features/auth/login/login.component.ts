import { CommonModule } from "@angular/common";
import { Component, inject, signal } from "@angular/core";
import { FormBuilder, ReactiveFormsModule, Validators } from "@angular/forms";
import { Router } from "@angular/router";
import { AuthService, OidcConfig } from "../../../core/services/auth.service";
import { ThemeService } from "../../../core/services/theme.service";

@Component({
  selector: "app-login",
  imports: [CommonModule, ReactiveFormsModule],
  templateUrl: "./login.component.html",
  styleUrl: "./login.component.css",
})
export class LoginComponent {
  private auth = inject(AuthService);
  private router = inject(Router);
  private fb = inject(FormBuilder);
  themeService = inject(ThemeService);

  loginForm = this.fb.group({
    username: ["", Validators.required],
    password: ["", Validators.required],
  });

  loading = signal(false);
  error = signal("");
  showPassword = signal(false);
  oidcConfig = signal<OidcConfig | null>(null);

  constructor() {
    this.auth.getOidcConfig().subscribe({
      next: (config) => this.oidcConfig.set(config),
    });
  }

  onSubmit() {
    if (this.loginForm.invalid) return;
    this.loading.set(true);
    this.error.set("");

    const { username, password } = this.loginForm.value;
    this.auth.login(username!, password!).subscribe({
      next: () => this.router.navigate(["/"]),
      error: (err) => {
        this.error.set(err.error?.detail || "Authentication failed");
        this.loading.set(false);
      },
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
