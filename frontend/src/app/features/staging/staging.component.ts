/**
 * Portalcrane - Staging Component
 * Pull pipeline: source registry selection → image pull → CVE scan → push
 * (local or external registry with optional folder prefix).
 *
 * Changes:
 *  - Added pull source mode: "dockerhub" | "saved" | "adhoc"
 *  - New signals: pullSourceMode, pullSourceRegistryId, pullSourceHost,
 *    pullSourceUser, pullSourcePass
 *  - selectImage() now only fetches Docker Hub tags when source is Docker Hub
 *  - startPull() forwards the resolved source registry fields to the service
 *  - Job list now shows the source registry host badge
 */
import {
  Component,
  computed,
  DestroyRef,
  inject,
  OnInit,
  signal,
} from "@angular/core";
import { takeUntilDestroyed } from "@angular/core/rxjs-interop";
import { RouterLink } from "@angular/router";
import { switchMap, timer } from "rxjs";
import { AppConfigService } from "../../core/services/app-config.service";
import { AuthService } from "../../core/services/auth.service";
import {
  ExternalRegistry,
  ExternalRegistryService,
} from "../../core/services/external-registry.service";
import { RegistryService } from "../../core/services/registry.service";
import {
  DockerHubResult,
  StagingJob,
  StagingService,
} from "../../core/services/staging.service";

/** Job statuses that indicate an active pipeline step. */
const ACTIVE_STATUSES = new Set([
  "pending",
  "pulling",
  "scanning",
  "vuln_scanning",
  "pushing",
]);

/** Push destination modes. */
export type PushMode = "local" | "external";

/**
 * Pull source modes:
 *  - "dockerhub" → Docker Hub (default, uses saved Hub credentials)
 *  - "saved"     → a saved external registry selected by ID
 *  - "adhoc"     → ad-hoc host + optional credentials entered manually
 */
export type PullSourceMode = "dockerhub" | "saved" | "adhoc";

@Component({
  selector: "app-staging",
  imports: [RouterLink],
  templateUrl: "./staging.component.html",
  styleUrl: "./staging.component.css",
})
export class StagingComponent implements OnInit {
  private staging = inject(StagingService);
  private externalRegistryService = inject(ExternalRegistryService);
  private registry = inject(RegistryService);
  private destroyRef = inject(DestroyRef);
  private authService = inject(AuthService);
  readonly configService = inject(AppConfigService);

  // Exposed for template access (module-level constant cannot be used directly in templates)
  readonly ACTIVE_STATUSES = ACTIVE_STATUSES;

  // ── Job list ───────────────────────────────────────────────────────────────
  jobs = signal<StagingJob[]>([]);

  // ── Docker Hub search ──────────────────────────────────────────────────────
  searchQuery = signal("");
  searchResults = signal<DockerHubResult[]>([]);
  searching = signal(false);

  // ── Pull form — image & tag ────────────────────────────────────────────────
  pullImage = signal("");
  pullTag = signal("latest");
  availableTags = signal<string[]>([]);
  pulling = signal(false);

  // ── Pull form — source registry ────────────────────────────────────────────
  /** Selected source mode for the pull. */
  pullSourceMode = signal<PullSourceMode>("dockerhub");
  /** ID of the saved external registry to use as source (mode = "saved"). */
  pullSourceRegistryId = signal<string>("");
  /** Ad-hoc registry host (mode = "adhoc"), e.g. "ghcr.io" or "quay.io". */
  pullSourceHost = signal<string>("");
  /** Ad-hoc registry username (mode = "adhoc"). */
  pullSourceUser = signal<string>("");
  /** Ad-hoc registry password or token (mode = "adhoc"). */
  pullSourcePass = signal<string>("");

  // ── Push state ─────────────────────────────────────────────────────────────
  pushing = signal<string | null>(null);
  pushTargets = signal<Record<string, string>>({});
  pushModes = signal<Record<string, PushMode>>({});
  pushExtRegistryId = signal<Record<string, string>>({});

  // ── External registries (used for both pull source and push destination) ───
  externalRegistries = signal<ExternalRegistry[]>([]);
  readonly globalRegistries = computed(() =>
    this.externalRegistries().filter((r) => r.owner === "global"),
  );
  readonly personalRegistries = computed(() =>
    this.externalRegistries().filter((r) => r.owner !== "global"),
  );

  // ── Folder permission helpers ──────────────────────────────────────────────

  /** True when the current user has admin privileges. */
  readonly isAdmin = computed(
    () => this.authService.currentUser()?.is_admin ?? false,
  );

  /** Folders the current user may pull from (local registry only). */
  allowedPullFolders = signal<string[]>([]);

  /** Folders the current user may push to (local registry only). */
  allowedPushFolders = signal<string[]>([]);

  /** Display host of the local registry (hard-coded for now). */
  readonly localRegistryHost = "localhost:5000";

  /** True when pull source is the local registry based on current inputs. */
  readonly isPullingFromLocal = computed(() => {
    if (this.pullSourceMode() === "adhoc") {
      return this.pullSourceHost() === this.localRegistryHost;
    }
    if (this.pullSourceMode() === "saved") {
      const reg = this.externalRegistries().find(
        (r) => r.id === this.pullSourceRegistryId(),
      );
      return reg ? reg.host === this.localRegistryHost : false;
    }
    return false;
  });

  /** Warning message when the pull image is outside allowed folders. */
  readonly pullFolderWarning = computed(() => {
    if (!this.isPullingFromLocal()) return "";
    const img = this.pullImage().trim();
    if (!img) return "";
    const prefix = img.includes("/") ? img.split("/")[0] : img;
    const allowed = this.allowedPullFolders();
    if (
      !this.isAdmin() &&
      allowed.length > 0 &&
      !allowed.includes(prefix)
    ) {
      return `You only have access to folders: ${allowed.join(", ")}`;
    }
    return "";
  });

  // ── Computed helpers ───────────────────────────────────────────────────────

  /** True when the search panel should be visible (only for Docker Hub source). */
  readonly showDockerHubSearch = computed(
    () => this.pullSourceMode() === "dockerhub",
  );

  /** Display label for the currently selected pull source. */
  readonly pullSourceLabel = computed(() => {
    switch (this.pullSourceMode()) {
      case "saved": {
        const reg = this.externalRegistries().find(
          (r) => r.id === this.pullSourceRegistryId(),
        );
        return reg ? `${reg.name} (${reg.host})` : "Saved registry";
      }
      case "adhoc":
        return this.pullSourceHost() || "Custom registry";
      default:
        return "Docker Hub";
    }
  });

  // ── Lifecycle ──────────────────────────────────────────────────────────────

  ngOnInit(): void {
    // Single unified polling loop — no separate loadJobs() call to avoid
    // the race condition where two concurrent responses could overwrite each other.
    this.startJobsAutoRefresh();
    this.loadExternalRegistries();
    this.loadFolderPermissions();
  }

  /** Load pullable and pushable folders for the current user. */
  private loadFolderPermissions(): void {
    this.registry.getMyFolders().subscribe({
      next: (folders) => this.allowedPullFolders.set(folders),
    });
    this.registry.getPushableFolders().subscribe({
      next: (folders) => this.allowedPushFolders.set(folders),
    });
  }

  /** Options used in the folder dropdown when pushing. */
  readonly pushFolderOptions = computed(() => this.allowedPushFolders());

  // ── Auto-refresh ───────────────────────────────────────────────────────────

  private startJobsAutoRefresh(): void {
    timer(200, 3000)
      .pipe(
        switchMap(() => this.staging.listJobs()),
        takeUntilDestroyed(this.destroyRef),
      )
      .subscribe((jobs) => this.jobs.set(StagingService.sortJobs(jobs)));
  }

  // ── Data loading ───────────────────────────────────────────────────────────

  loadJobs(): void {
    this.staging.listJobs().subscribe({
      next: (jobs) => this.jobs.set(StagingService.sortJobs(jobs)),
    });
  }

  loadExternalRegistries(): void {
    this.externalRegistryService.listRegistries().subscribe({
      next: (regs) => this.externalRegistries.set(regs),
    });
  }

  // ── Pull source mode ───────────────────────────────────────────────────────

  /**
   * Switch the pull source mode and reset related fields.
   * Clears search results and available tags when leaving Docker Hub mode.
   */
  setPullSourceMode(mode: PullSourceMode): void {
    this.pullSourceMode.set(mode);
    this.pullSourceRegistryId.set("");
    this.pullSourceHost.set("");
    this.pullSourceUser.set("");
    this.pullSourcePass.set("");
    this.searchResults.set([]);
    this.availableTags.set([]);
    this.pullImage.set("");
    this.pullTag.set("latest");
  }

  // ── Docker Hub search ──────────────────────────────────────────────────────

  /**
   * Triggered on every keystroke in the search input.
   * Clears results when the query is empty; otherwise calls the API.
   */
  onSearch(): void {
    const q = this.searchQuery().trim();
    if (!q) {
      this.searchResults.set([]);
      return;
    }
    this.searching.set(true);
    this.staging.searchDockerHub(q).subscribe({
      next: ({ results }) => {
        this.searchResults.set(results);
        this.searching.set(false);
      },
      error: () => this.searching.set(false),
    });
  }

  /**
   * Called when the user clicks the explicit Search button.
   * Identical behaviour to onSearch but kept separate for semantic clarity.
   */
  searchDockerHub(): void {
    this.onSearch();
  }

  /**
   * Select an image from the Docker Hub search results.
   * Clears the results list and immediately loads the available tags.
   * Only active when pull source mode is "dockerhub".
   */
  selectImage(name: string): void {
    this.pullImage.set(name);
    // Do NOT clear searchResults — the list must remain displayed
    // Tags are only fetched from Docker Hub; external registries require manual input
    if (this.pullSourceMode() === "dockerhub") {
      this.staging.getDockerHubTags(name).subscribe({
        next: ({ tags }) => {
          this.availableTags.set(tags);
            if (tags.length > 0) {
              this.pullTag.set(tags[0]);
            }
         },
         error: () => this.availableTags.set([]),
      });
    }
  }

  // ── Pull ───────────────────────────────────────────────────────────────────

  startPull(): void {
    if (!this.pullImage()) return;
    this.pulling.set(true);

    const mode = this.pullSourceMode();

    this.staging
      .pullImage({
        image: this.pullImage(),
        tag: this.pullTag() || "latest",

        // Source registry resolution
        source_registry_id:
          mode === "saved" ? this.pullSourceRegistryId() || null : null,
        source_registry_host:
          mode === "adhoc" ? this.pullSourceHost() || null : null,
        source_registry_username:
          mode === "adhoc" ? this.pullSourceUser() || null : null,
        source_registry_password:
          mode === "adhoc" ? this.pullSourcePass() || null : null,

        // Vulnerability scan overrides
        vuln_scan_enabled_override: this.configService.vulnOverride()
          ? this.configService.vulnEnabled()
          : null,
        vuln_severities_override: this.configService.vulnOverride()
          ? this.configService.vulnSeveritiesString()
          : null,
      })
      .subscribe({
        next: (job) => {
          // Prepend the new job immediately — the polling loop will keep it updated
          this.jobs.update((jobs) => StagingService.sortJobs([job, ...jobs]));
          this.pulling.set(false);
          this.pullImage.set("");
          this.pullTag.set("latest");
          this.availableTags.set([]);
        },
        error: () => this.pulling.set(false),
      });
  }

  // ── Push helpers ───────────────────────────────────────────────────────────

  getPushMode(job: StagingJob): PushMode {
    return this.pushModes()[job.job_id] ?? "local";
  }

  setPushMode(job: StagingJob, mode: PushMode): void {
    this.pushModes.update((m) => ({ ...m, [job.job_id]: mode }));
  }

  getExtRegistryId(job: StagingJob): string {
    return this.pushExtRegistryId()[job.job_id] ?? "";
  }

  setExtRegistryId(job: StagingJob, id: string): void {
    this.pushExtRegistryId.update((m) => ({ ...m, [job.job_id]: id }));
  }

  getPushTarget(job: StagingJob, field: string): string {
    return this.pushTargets()[`${job.job_id}_${field}`] ?? "";
  }

  updatePushTarget(job: StagingJob, field: string, value: string): void {
    this.pushTargets.update((t) => ({
      ...t,
      [`${job.job_id}_${field}`]: value,
    }));
  }

  /**
   * Compute the full target image reference for preview in the template.
   * Reflects current push mode, folder, image name, tag and registry.
   */
  pushPreview(job: StagingJob): string {
    const mode = this.getPushMode(job);
    // prefer explicit user input, otherwise fall back to job.folder saved
    let folder = this.getPushTarget(job, "folder").trim();
    if (!folder && job.folder) {
      folder = job.folder;
    }
    const img = (this.getPushTarget(job, "img") || job.image).trim();
    const tag = (this.getPushTarget(job, "tag") || job.tag).trim();
    const path = folder ? `${folder}/${img}` : img;

    if (mode === "local") {
      return `localhost:5000/${path}:${tag}`;
    }
    const regId = this.getExtRegistryId(job);
    const reg = this.externalRegistries().find((r) => r.id === regId);
    const host = reg
      ? reg.host
      : this.getPushTarget(job, "ext_host") || "<registry>";
    return `${host}/${path}:${tag}`;
  }

  // ── Push ───────────────────────────────────────────────────────────────────

  pushImage(job: StagingJob): void {
    this.pushing.set(job.job_id);

    const mode = this.getPushMode(job);
    const isExternal = mode === "external";
    const regId = this.getExtRegistryId(job) || null;

    this.staging
      .pushImage({
        job_id: job.job_id,
        target_image: this.getPushTarget(job, "img") || null,
        target_tag: this.getPushTarget(job, "tag") || null,
        folder: this.getPushTarget(job, "folder") || null,
        external_registry_id: isExternal ? regId : null,
        external_registry_host:
          isExternal && !regId
            ? this.getPushTarget(job, "ext_host") || null
            : null,
        external_registry_username:
          isExternal && !regId
            ? this.getPushTarget(job, "ext_user") || null
            : null,
        external_registry_password:
          isExternal && !regId
            ? this.getPushTarget(job, "ext_pass") || null
            : null,
      })
      .subscribe({
        next: () => {
          this.pushing.set(null);
          // Trigger an immediate refresh so the PUSHING status appears without delay
          this.loadJobs();
        },
        error: () => this.pushing.set(null),
      });
  }

  // ── Delete ─────────────────────────────────────────────────────────────────

  deleteJob(jobId: string): void {
    this.staging.deleteJob(jobId).subscribe({
      next: () => this.loadJobs(),
    });
  }

  // ── Template helpers ───────────────────────────────────────────────────────

  /**
   * Return the display progress for a job.
   * scan_clean / scan_skipped are terminal "ready" states — force 100 %
   * regardless of what the backend reported.
   */
  displayProgress(job: StagingJob): number {
    if (
      [
        "scan_clean",
        "scan_skipped",
        "done",
        "scan_vulnerable",
        "scan_infected",
        "failed",
      ].includes(job.status)
    ) {
      return 100;
    }
    return job.progress;
  }

  /**
   * Allow re-pushing a completed job by resetting its local status to
   * scan_clean so the push form becomes visible again.
   * This is a client-side only change — the backend job stays at "done".
   */
  allowRePush(job: StagingJob): void {
    this.jobs.update((jobs) =>
      jobs.map((j) =>
        j.job_id === job.job_id ? { ...j, status: "scan_clean" as const } : j,
      ),
    );
  }

  /** Return the Bootstrap badge CSS classes for a given job status. */
  getStatusBadgeClass(status: string): string {
    const map: Record<string, string> = {
      pending: "badge bg-secondary-subtle text-secondary",
      pulling: "badge bg-info-subtle text-info",
      scanning: "badge bg-warning-subtle text-warning",
      scan_skipped: "badge bg-secondary-subtle text-secondary",
      vuln_scanning: "badge bg-warning-subtle text-warning",
      scan_clean: "badge bg-success-subtle text-success",
      scan_vulnerable: "badge bg-danger text-white",
      scan_infected: "badge bg-danger text-white",
      pushing: "badge bg-primary-subtle text-primary",
      done: "badge bg-success text-white",
      failed: "badge bg-danger text-white",
    };
    return map[status] ?? "badge bg-secondary";
  }

  /** Format large numbers as K / M for star and pull counts. */
  formatCount(n: number): string {
    if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(1)}M`;
    if (n >= 1_000) return `${(n / 1_000).toFixed(1)}K`;
    return `${n}`;
  }

  /** Return the CVE count for a given severity level on a job. */
  getCveCount(job: StagingJob, severity: string): number {
    return job.vuln_result?.counts?.[severity] ?? 0;
  }

  /** Return the icon emoji for a pull source registry host. */
  sourceRegistryIcon(host: string | null | undefined): string {
    if (!host) return "🐳"; // Docker Hub
    if (host.includes("ghcr.io")) return "🐙";
    if (host.includes("quay.io")) return "🔴";
    if (host.includes("gcr.io") || host.includes("pkg.dev")) return "☁️";
    if (host.includes("amazonaws.com")) return "🟠";
    return "🏢";
  }
}
