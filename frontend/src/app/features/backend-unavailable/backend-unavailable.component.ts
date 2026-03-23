/**
 * Portalcrane - BackendUnavailableComponent
 *
 * Displayed when the backend is unreachable. Shown as a full-screen overlay
 * by AppComponent, replacing the router outlet entirely. This approach avoids
 * the "stuck on backend-unavailable after browser refresh" problem that
 * occurred when this page was a regular route — a browser refresh would
 * navigate to /backend-unavailable and stay there even after recovery.
 *
 * Now the component is rendered conditionally via a signal in AppComponent:
 * a browser refresh restarts the health-check probe from scratch and the
 * normal application renders immediately when the backend is healthy.
 */
import { Component } from "@angular/core";

@Component({
  selector: "app-backend-unavailable",
  templateUrl: "./backend-unavailable.component.html",
  styleUrl: "./backend-unavailable.component.css",
})
export class BackendUnavailableComponent {}
