/**
 * Portalcrane - Auth Interceptor
 *
 * The session JWT travels in an HttpOnly cookie set by the backend at login,
 * so this interceptor only ensures the cookie is sent with API calls
 * (withCredentials) — it never reads or attaches the token in JavaScript.
 * It also handles:
 *   - 401 responses: clears the session and shows the session-expired modal
 *   - genuine backend-down errors: marks the backend as unavailable
 *
 * Change: the backend unavailability detection now applies to ALL /api/ requests,
 * including those made from the login page (/api/auth/login, /api/health).
 * Previously the health check request from the login page was excluded because
 * the BackendAvailabilityService was only initialised inside the layout.
 * Now that the service is injected at the root level (app.component.ts), all
 * API errors are correctly propagated.
 *
 * Upstream vs. backend distinction: several endpoints proxy external services
 * (Docker Hub search, external registries) and deliberately answer 502/503/504
 * when *that upstream* fails — the Portalcrane backend itself is perfectly alive
 * and replies with a JSON `{ detail }` body. Those must NOT trigger the
 * full-screen offline page; they belong to the feature that made the request.
 * We therefore only mark the backend unavailable when the app got no real
 * response from it: a network error (status 0) or a gateway error that did not
 * carry our backend's JSON error body (i.e. produced by a reverse proxy sitting
 * in front of a dead backend).
 */

import { HttpErrorResponse, HttpInterceptorFn } from "@angular/common/http";
import { inject } from "@angular/core";
import { catchError, throwError } from "rxjs";

import { AuthService } from "../services/auth.service";
import { BackendAvailabilityService } from "../services/backend-availability.service";
import { SessionExpiredService } from "../services/session-expired.service";

/**
 * True when the error carries our FastAPI backend's JSON error payload
 * (`{ detail: ... }`). Its presence proves the Portalcrane backend answered the
 * request itself — the failure comes from a proxied upstream, not a dead backend.
 */
function isBackendErrorBody(error: HttpErrorResponse): boolean {
  const body = error.error;
  return typeof body === "object" && body !== null && "detail" in body;
}

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
      // (the health check is used by the recovery poller itself).
      //
      // A 502/503/504 coming FROM our backend (upstream registry / Docker Hub
      // failure) carries a JSON `{ detail }` body and means the backend is up —
      // it must not take over the whole UI. Only a network error (status 0) or a
      // gateway error without that body signals a genuinely unreachable backend.
      const isNetworkError = error.status === 0;
      const isGatewayError = [502, 503, 504].includes(error.status);
      const fromBackend = isBackendErrorBody(error);
      const isBackendDownError =
        isNetworkError || (isGatewayError && !fromBackend);

      const isApiRequest = req.url.includes("/api/");
      const isHealthCheck = req.url.includes("/api/health");

      if (isBackendDownError && isApiRequest && !isHealthCheck) {
        backendAvailability.markBackendUnavailable();
      }

      return throwError(() => error);
    }),
  );
};
