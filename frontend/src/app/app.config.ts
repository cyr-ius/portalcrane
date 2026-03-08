/**
 * Portalcrane - Application Configuration
 */
import { provideHttpClient, withInterceptors } from "@angular/common/http";
import {
  ApplicationConfig,
  inject,
  provideAppInitializer,
  provideZonelessChangeDetection,
} from "@angular/core";
import { provideRouter, withComponentInputBinding } from "@angular/router";
import { catchError, firstValueFrom, of } from "rxjs";

import { routes } from "./app.routes";
import { authInterceptor } from "./core/interceptors/auth.interceptor";
import { AppConfigService } from "./core/services/app-config.service";

export const appConfig: ApplicationConfig = {
  providers: [
    provideZonelessChangeDetection(),
    provideRouter(routes, withComponentInputBinding()),
    provideHttpClient(withInterceptors([authInterceptor])),
    provideAppInitializer(() => {
      const configService = inject(AppConfigService);
      return firstValueFrom(
        configService.loadConfig().pipe(catchError(() => {
          configService.markConfigLoadFailed();
          return of(null);
        })),
      );
    }),
  ],
};
