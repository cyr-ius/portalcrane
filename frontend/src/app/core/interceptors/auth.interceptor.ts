/**
 * Portalcrane - Auth Interceptor
 * Attaches the Bearer token to every /api/ request and handles 401 responses
 * by clearing the session and showing the session-expired modal.
 */

import { HttpErrorResponse, HttpInterceptorFn } from "@angular/common/http";
import { inject } from "@angular/core";
import { catchError, throwError } from "rxjs";

import { AuthService } from "../services/auth.service";
import { BackendAvailabilityService } from "../services/backend-availability.service";
import { SessionExpiredService } from "../services/session-expired.service";

export const authInterceptor: HttpInterceptorFn = (req, next) => {
  const auth = inject(AuthService);
  const sessionExpired = inject(SessionExpiredService);
  const backendAvailability = inject(BackendAvailabilityService);
  const token = auth.getToken();

  // Attach Authorization header to all API requests when a token is available
  const authReq =
    token && req.url.includes("/api/")
      ? req.clone({ setHeaders: { Authorization: `Bearer ${token}` } })
      : req;

  return next(authReq).pipe(
    catchError((error: HttpErrorResponse) => {
      // Handle expired or invalid token — skip the login endpoint itself
      if (error.status === 401 && !req.url.includes("/auth/login")) {
        auth.clearSession();
        sessionExpired.show();
      }

      const isBackendDownError = [0, 502, 503, 504].includes(error.status);
      const isApiRequest = req.url.includes("/api/");
      const isHealthCheck = req.url.includes("/api/health");

      if (isBackendDownError && isApiRequest && !isHealthCheck) {
        backendAvailability.markBackendUnavailable();
      }

      return throwError(() => error);
    }),
  );
};
