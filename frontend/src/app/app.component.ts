/**
 * Portalcrane - Root Application Component
 *
 * Serves as the application shell. Hosts the router outlet and integrates
 * the BackendAvailabilityService so backend detection works on ALL routes,
 * including the login page (/auth) and the OIDC callback (/auth/callback).
 *
 * The BackendUnavailableComponent is rendered here (outside the router outlet)
 * when the backend is detected as unreachable, regardless of the current route.
 * This avoids the previous limitation where the detection only worked inside
 * the authenticated layout (after login).
 */
import { Component, inject } from "@angular/core";
import { RouterOutlet } from "@angular/router";
import { BackendAvailabilityService } from "./core/services/backend-availability.service";
import { BackendUnavailableComponent } from "./features/backend-unavailable/backend-unavailable.component";

@Component({
  selector: "app-root",
  imports: [RouterOutlet, BackendUnavailableComponent],
  template: `
    @if (backendAvailability.backendUnavailable()) {
      <app-backend-unavailable />
    } @else {
      <router-outlet />
    }
  `,
})
export class AppComponent {
  /**
   * Inject BackendAvailabilityService at the root level so it is instantiated
   * immediately when the app starts, regardless of which route is active.
   * The constructor probe inside the service will fire at bootstrap time,
   * covering the login page scenario.
   */
  readonly backendAvailability = inject(BackendAvailabilityService);
}
