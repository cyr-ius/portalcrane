/**
 * Portalcrane - Registry constants
 *
 * Single source of truth for the local registry coordinates used by the
 * Angular frontend. These values mirror the backend REGISTRY_URL / REGISTRY_HOST
 * constants defined in backend/app/config.py and must be kept in sync if the
 * internal registry address ever changes.
 *
 * The registry always runs on localhost inside the container (managed by
 * supervisord) so these values are fixed and do not need to be injected at
 * runtime via an API call.
 */

/** Internal HTTP URL of the local Docker registry (same as backend REGISTRY_URL). */
export const LOCAL_REGISTRY_URL = "http://localhost:5000";

/**
 * Bare host:port of the local Docker registry (same as backend REGISTRY_HOST).
 * Used to detect when a pull/push source points to the embedded registry
 * so that folder access rules can be enforced on the frontend.
 */
export const LOCAL_REGISTRY_HOST = "localhost:5000";
