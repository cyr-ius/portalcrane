import { HttpClient } from "@angular/common/http";
import { Injectable, signal } from "@angular/core";

/** Shape of the /api/about response from the backend. */
export interface AboutInfo {
  current_version: string;
  latest_version: string | null;
  update_available: boolean;
  author: string;
  ai_generator: string;
  github_url: string;
  github_error: string | null;
}

/**
 * Service that fetches application metadata (version, author, AI credits)
 * and caches it as a signal so Settings can be zoneless / reactive.
 */
@Injectable({ providedIn: "root" })
export class AboutService {
  /** Cached about information; null until first load. */
  readonly info = signal<AboutInfo | null>(null);

  /** True while the HTTP request is in flight. */
  readonly loading = signal(false);

  /** Non-null when the HTTP call itself fails (network/auth). */
  readonly error = signal<string | null>(null);

  constructor(private http: HttpClient) {}

  /** Fetch /api/about and store the result. Idempotent: skips if already loaded. */
  load(): void {
    if (this.info() !== null || this.loading()) return;

    this.loading.set(true);
    this.error.set(null);

    this.http.get<AboutInfo>("/api/about").subscribe({
      next: (data) => {
        this.info.set(data);
        this.loading.set(false);
      },
      error: (err) => {
        this.error.set(err?.message ?? "Failed to load about info");
        this.loading.set(false);
      },
    });
  }
}
