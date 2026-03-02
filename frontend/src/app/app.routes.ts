import { Routes } from "@angular/router";
import { authGuard } from "./core/guards/auth.guard";

export const routes: Routes = [
  {
    path: "auth",
    loadComponent: () =>
      import("./features/auth/login/login.component").then(
        (m) => m.LoginComponent,
      ),
  },
  {
    path: "auth/callback",
    loadComponent: () =>
      import("./features/auth/oidc-callback/oidc-callback.component").then(
        (m) => m.OidcCallbackComponent,
      ),
  },
  {
    path: "",
    canActivate: [authGuard],
    loadComponent: () =>
      import("./shared/components/layout/layout.component").then(
        (m) => m.LayoutComponent,
      ),
    children: [
      {
        path: "",
        redirectTo: "dashboard",
        pathMatch: "full",
      },
      {
        path: "dashboard",
        loadComponent: () =>
          import("./features/dashboard/dashboard.component").then(
            (m) => m.DashboardComponent,
          ),
      },
      {
        path: "images",
        loadComponent: () =>
          import("./features/images/images-list/images-list.component").then(
            (m) => m.ImagesListComponent,
          ),
      },
      {
        // Repository name is passed as ?repository=... query param instead of
        // a path segment to avoid %2F encoding issues with reverse proxies.
        path: "images/detail",
        loadComponent: () =>
          import("./features/images/image-detail/image-detail.component").then(
            (m) => m.ImageDetailComponent,
          ),
      },
      {
        path: "staging",
        loadComponent: () =>
          import("./features/staging/staging.component").then(
            (m) => m.StagingComponent,
          ),
      },
      {
        path: "settings",
        loadComponent: () =>
          import("./features/settings/settings.component").then(
            (m) => m.SettingsComponent,
          ),
      },
    ],
  },
  {
    path: "**",
    redirectTo: "",
  },
];
