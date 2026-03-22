/**
 * Portalcrane - Staging Component
 * Pull pipeline: source registry selection → image pull → CVE scan → push
 * (local or external registry with optional folder prefix).
 *
 */
import {
  Component,
  computed,
  inject,
  OnInit,
  signal
} from "@angular/core";
import { RouterLink } from "@angular/router";
import { LOCAL_REGISTRY_SYSTEM_ID } from "../../core/constants/registry.constants";
import { AuthService } from "../../core/services/auth.service";
import {
  ExternalRegistry,
  ExternalRegistryService
} from "../../core/services/external-registry.service";
import { FolderService } from "../../core/services/folder.service";
import { JobService } from "../../core/services/job.service";
import {
  DockerHubResult,
  StagingService
} from "../../core/services/staging.service";
import { TrivyService } from "../../core/services/trivy.service";
import { JobsListComponent } from "./jobs-list/jobs-list.component";


export type PullSourceMode = "dockerhub" | "saved" | "adhoc";

@Component({
  selector: "app-staging",
  imports: [RouterLink, JobsListComponent],
  templateUrl: "./staging.component.html",
  styleUrl: "./staging.component.css",
})
export class StagingComponent implements OnInit {
  private staging = inject(StagingService);
  private extRegistrySvc = inject(ExternalRegistryService);
  private authService = inject(AuthService);
  private jobSvc = inject(JobService);
  private folderSvc = inject(FolderService);
  trivySvc = inject(TrivyService);

  readonly externalRegistries = computed<ExternalRegistry[]>(() => this.extRegistrySvc.externalRegistries());

  searchQuery = signal("");
  searchResults = signal<DockerHubResult[]>([]);
  searching = signal(false);

  pullImage = signal("");
  pullTag = signal("latest");
  availableTags = signal<string[]>([]);
  pulling = signal(false);
  pullSourceMode = signal<PullSourceMode>("dockerhub");
  pullSourceRegistryId = signal<string>("");
  pullSourceHost = signal<string>("");
  pullSourceUser = signal<string>("");
  pullSourcePass = signal<string>("");

  readonly globalRegistries = computed(() =>
    this.externalRegistries().filter((r) => r.owner === "global"),
  );
  readonly personalRegistries = computed(() =>
    this.externalRegistries().filter((r) => r.owner !== "global"),
  );

  readonly isAdmin = computed(
    () => this.authService.currentUser()?.is_admin ?? false,
  );

  /**
   * Bare host:port of the local embedded registry.
   * Sourced from the shared constant that mirrors REGISTRY_HOST on the backend.
   * Used to detect when a saved or ad-hoc source points to the local registry
   * so that folder access rules are enforced before the pull is started.
   */
  readonly isPullingFromLocal = computed(() => {
    if (this.pullSourceMode() === "adhoc") {
      return false;
    }
    if (this.pullSourceMode() === "saved") {
      return this.pullSourceRegistryId() === LOCAL_REGISTRY_SYSTEM_ID;
    }
    return false;
  });

  readonly pullFolderWarning = computed(() => {
    if (!this.isPullingFromLocal()) return "";
    const img = this.pullImage().trim();
    if (!img) return "";
    const prefix = img.includes("/") ? img.split("/")[0] : img;
    const allowed = this.folderSvc.allowedPullFolders();
    if (
      !this.isAdmin() &&
      allowed.length > 0 &&
      !allowed.includes(prefix)
    ) {
      return `You only have access to folders: ${allowed.join(", ")}`;
    }
    return "";
  });

  readonly showDockerHubSearch = computed(
    () => this.pullSourceMode() === "dockerhub",
  );

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

  readonly pullHostPreview = computed(() => {
    switch (this.pullSourceMode()) {
      case "saved": {
        const reg = this.externalRegistries().find(
          (r) => r.id === this.pullSourceRegistryId(),
        );
        return reg ? `${reg.host}/` : "";
      }
      case "adhoc":
        return `${this.pullSourceHost()}/` || "";
      default:
        return "";
    }
  });

  readonly pushFolderOptions = computed(() => this.folderSvc.allowedPushFolders());

  ngOnInit(): void {
    this.trivySvc.loadConfig().subscribe();
    this.extRegistrySvc.loadRegistries();
    this.folderSvc.loadPermissions();

    // Ensure the background polling loop is running (idempotent on re-entry).
    this.jobSvc.startPolling();

    // Restart the polling timer from zero so an immediate fetch happens now.
    // Using triggerRefresh() (BehaviorSubject emit + switchMap) ensures there
    // is always exactly ONE active HTTP request — no concurrent calls that
    // could race and flash the list empty.
    this.jobSvc.triggerRefresh();
  }

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

  selectImage(name: string): void {
    this.pullImage.set(name);
    // Do NOT clear searchResults — the list must remain displayed after selection
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
        vuln_scan_enabled_override: this.trivySvc.vulnOverride()
          ? this.trivySvc.vulnEnabled()
          : null,
        vuln_severities_override: this.trivySvc.vulnOverride()
          ? this.trivySvc.vulnSeveritiesString()
          : null,
      })
      .subscribe({
        next: (job) => {
          this.jobSvc.updateJob(job);
          this.pulling.set(false);
          this.pullImage.set("");
          this.pullTag.set("latest");
          this.availableTags.set([]);
        },
        error: () => this.pulling.set(false),
      });
  }

  formatCount(n: number): string {
    if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(1)}M`;
    if (n >= 1_000) return `${(n / 1_000).toFixed(1)}K`;
    return `${n}`;
  }
}
