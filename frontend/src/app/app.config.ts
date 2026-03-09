/**
 * Portalcrane - Application Configuration
 */
import { provideHttpClient, withInterceptors } from "@angular/common/http";
import {
  ApplicationConfig,
  provideZonelessChangeDetection
} from "@angular/core";
import { provideRouter, withComponentInputBinding } from "@angular/router";

import { routes } from "./app.routes";
import { authInterceptor } from "./core/interceptors/auth.interceptor";


export const appConfig: ApplicationConfig = {
  providers: [
    provideZonelessChangeDetection(),
    provideRouter(routes, withComponentInputBinding()),
    provideHttpClient(withInterceptors([authInterceptor])),
    // NOTE: TrivyService.loadConfig() is intentionally NOT called here at bootstrap.
    // The config is loaded on-demand in StagingComponent.ngOnInit() and
    // VulnConfigPanelComponent.ngOnInit(). localStorage acts as cache between
    // sessions. A global initializer would block the app startup unnecessarily.
  ],
};
