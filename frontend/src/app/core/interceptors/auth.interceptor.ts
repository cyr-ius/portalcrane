/**
 * Portalcrane - Auth Interceptor
 *
 * The session JWT travels in an HttpOnly cookie set by the backend at login,
 * so this interceptor only ensures the cookie is sent with API calls
 * (withCredentials) — it never reads or attaches the token in JavaScript.
 * It also handles:
 *   - 401 responses: clears the session and shows the session-expired modal
 *   - 0, 502, 503, 504 errors: marks the backend as unavailable
 *
 * Change: the backend unavailability detection now applies to ALL /api/ requests,
 * including those made from the login page (/api/auth/login, /api/health).
 * Previously the health check request from the login page was excluded because
 * the BackendAvailabilityService was only initialised inside the layout.
 * Now that the service is injected at the root level (app.component.ts), all
 * API errors are correctly propagated.
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

  // The auth travels in an HttpOnly cookie; ensure it is sent with API calls.
  const authReq = req.url.includes("/api/")
    ? req.clone({ withCredentials: true })
    : req;

  return next(authReq).pipe(
    catchError((error: HttpErrorResponse) => {
      // Handle expired or invalid token — skip the auth endpoints themselves.
      // A 401 on /api/auth/* is expected when simply not logged in (e.g. the
      // startup /me probe or /login) and must not surface as "session expired".
      if (error.status === 401 && !req.url.includes("/api/auth/")) {
        auth.clearSession();
        sessionExpired.show();
      }

      // Detect backend down for all /api/ requests except the health check
      // (the health check is used by the recovery poller itself)
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
