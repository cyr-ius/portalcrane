import { HttpClient } from "@angular/common/http";
import { computed, Injectable, signal } from "@angular/core";
import { Router } from "@angular/router";
import { tap } from "rxjs";

export interface LoginResponse {
  access_token: string;
  token_type: string;
  expires_in: number;
}

export interface UserInfo {
  username: string;
  is_admin: boolean;
}

export interface OidcConfig {
  enabled: boolean;
  client_id: string;
  issuer: string;
  redirect_uri: string;
  authorization_endpoint: string;
}

@Injectable({ providedIn: "root" })
export class AuthService {
  private readonly TOKEN_KEY = "pc_token";
  private readonly USER_KEY = "pc_user";

  // Signals
  private _token = signal<string | null>(localStorage.getItem(this.TOKEN_KEY));
  private _user = signal<UserInfo | null>(
    JSON.parse(localStorage.getItem(this.USER_KEY) || "null"),
  );

  readonly isAuthenticated = computed(() => !!this._token());
  readonly currentUser = this._user.asReadonly();

  constructor(
    private http: HttpClient,
    private router: Router,
  ) {}

  login(username: string, password: string) {
    return this.http
      .post<LoginResponse>("/api/auth/login", { username, password })
      .pipe(
        tap((response) => {
          this.setToken(response.access_token);
          this.loadUserInfo();
        }),
      );
  }

  loadUserInfo() {
    this.http.get<UserInfo>("/api/auth/me").subscribe({
      next: (user) => {
        this._user.set(user);
        localStorage.setItem(this.USER_KEY, JSON.stringify(user));
      },
    });
  }

  getOidcConfig() {
    return this.http.get<OidcConfig>("/api/auth/oidc-config");
  }

  handleOidcCallback(code: string) {
    return this.http
      .post<LoginResponse>(`/api/auth/oidc/callback?code=${code}`, {})
      .pipe(
        tap((response) => {
          this.setToken(response.access_token);
          this.loadUserInfo();
        }),
      );
  }

  clearSession() {
    this._token.set(null);
    this._user.set(null);
    localStorage.removeItem(this.TOKEN_KEY);
    localStorage.removeItem(this.USER_KEY);
  }

  logout() {
    this.clearSession();
    this.router.navigate(["/auth"]);
  }

  getToken(): string | null {
    return this._token();
  }

  private setToken(token: string) {
    this._token.set(token);
    localStorage.setItem(this.TOKEN_KEY, token);
  }
}
