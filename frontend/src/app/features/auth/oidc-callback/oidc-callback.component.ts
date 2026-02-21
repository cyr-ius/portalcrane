import { Component, OnInit, inject } from '@angular/core';
import { ActivatedRoute, Router } from '@angular/router';
import { AuthService } from '../../../core/services/auth.service';
import { CommonModule } from '@angular/common';

@Component({
  selector: 'app-oidc-callback',
  imports: [CommonModule],
  template: `
    <div class="d-flex flex-column align-items-center justify-content-center min-vh-100 gap-3">
      @if (error()) {
        <div class="alert alert-danger">
          <i class="bi bi-exclamation-triangle-fill me-2"></i>
          Authentication failed: {{ error() }}
        </div>
      } @else {
        <div class="spinner-border text-primary" role="status"></div>
        <p class="text-muted">Completing authentication...</p>
      }
    </div>
  `,
})
export class OidcCallbackComponent implements OnInit {
  private route = inject(ActivatedRoute);
  private router = inject(Router);
  private auth = inject(AuthService);

  error = () => this._error;
  private _error = '';

  ngOnInit() {
    const code = this.route.snapshot.queryParamMap.get('code');
    const errorParam = this.route.snapshot.queryParamMap.get('error');

    if (errorParam) {
      this._error = errorParam;
      return;
    }

    if (!code) {
      this._error = 'No authorization code received';
      return;
    }

    this.auth.handleOidcCallback(code).subscribe({
      next: () => this.router.navigate(['/']),
      error: (err) => {
        this._error = err.error?.detail || 'OIDC callback failed';
      },
    });
  }
}
