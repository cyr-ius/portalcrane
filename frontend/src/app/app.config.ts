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
import { provideTranslateService } from "@ngx-translate/core";
import { provideTranslateHttpLoader } from "@ngx-translate/http-loader";

import { routes } from "./app.routes";
import { authInterceptor } from "./core/interceptors/auth.interceptor";
import { AuthService } from "./core/services/auth.service";

export const appConfig: ApplicationConfig = {
  providers: [
    provideZonelessChangeDetection(),
    provideRouter(routes, withComponentInputBinding()),
    provideHttpClient(withInterceptors([authInterceptor])),
    // Restore the session from the HttpOnly auth cookie (a /me probe) before the
    // router and auth guard run, so a page reload keeps the user authenticated.
    provideAppInitializer(() => inject(AuthService).bootstrap()),
    // Internationalisation : chargement des traductions à l'exécution depuis
    // /i18n/{lang}.json (dossier public/). La langue active est appliquée au
    // démarrage dans AppComponent selon localStorage / la langue du navigateur.
    provideTranslateService({ lang: "en" }),
    provideTranslateHttpLoader({ prefix: "/i18n/", suffix: ".json" }),
    // NOTE: TrivyService.loadConfig() is intentionally NOT called here at bootstrap.
    // The config is loaded on-demand in StagingComponent.ngOnInit() and
    // VulnConfigPanelComponent.ngOnInit(). localStorage acts as cache between
    // sessions. A global initializer would block the app startup unnecessarily.
  ],
};
