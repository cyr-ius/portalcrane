import { HttpErrorResponse, HttpInterceptorFn } from "@angular/common/http";
import { inject } from "@angular/core";
import { catchError, throwError } from "rxjs";
import { AuthService } from "../services/auth.service";
import { SessionExpiredService } from "../services/session-expired.service";

export const authInterceptor: HttpInterceptorFn = (req, next) => {
  const auth = inject(AuthService);
  const sessionExpired = inject(SessionExpiredService);
  const token = auth.getToken();

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
      return throwError(() => error);
    }),
  );
};
