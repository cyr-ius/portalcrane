/**
 * Portalcrane - TransferModalComponent
 *
 * Modal for transferring images between any registries with integrated Trivy scan.
 * Replaces the simple "Copy" modal and the Sync import/export feature.
 *
 * The vulnerability scan policy is inherited entirely from the server configuration
 * (Settings → Vulnerabilities tab). No per-transfer scan override is exposed here.
 *
 * Features:
 *  - Multi-image + multi-tag selection with tri-state checkboxes
 *  - Destination: local registry or any saved external registry
 *  - Optional destination folder / name / tag override (single selection only)
 *  - Real-time job polling with progress display
 *  - CVE table display when scan finds blocking vulnerabilities
 *
 * Inputs:
 *  @input sourceRegistryId   ID of the source registry ('__local__' for local)
 *  @input preselectedImages  Images pre-selected when opening the modal
 *  @input allImages          All images from the current view (for selection)
 *
 * Outputs:
 *  @output closed            Emitted when user closes the modal
 */
import {
  AfterViewInit,
  Component,
  computed,
  ElementRef,
  inject,
  input,
  OnDestroy,
  OnInit,
  output,
  QueryList,
  signal,
  ViewChildren,
} from "@angular/core";
import { FormsModule } from "@angular/forms";
import { RouterLink } from "@angular/router";
import { firstValueFrom, Subscription, switchMap, timer } from "rxjs";
import { LOCAL_REGISTRY_SYSTEM_ID } from "../../../core/constants/registry.constants";
import { AuthService } from "../../../core/services/auth.service";
import {
  ExternalRegistry,
  ExternalRegistryService,
} from "../../../core/services/external-registry.service";
import { FolderService } from "../../../core/services/folder.service";
import { ImageInfo } from "../../../core/services/registry.service";
import {
  TRANSFER_ACTIVE_STATUSES,
  TransferJob,
  TransferService,
  VulnerabilityEntry,
} from "../../../core/services/transfer.service";
import { TrivyService } from "../../../core/services/trivy.service";

/** Severity display order for CVE badges. */
const SEVERITY_ORDER = ["CRITICAL", "HIGH", "MEDIUM", "LOW", "UNKNOWN"];

/**
 * Sanitise an image name so it can be safely used as an HTML element id.
 * Replaces any character that is not alphanumeric, hyphen or underscore with '-'.
 */
function safeId(value: string): string {
  return value.replace(/[^a-zA-Z0-9\-_]/g, "-");
}

@Component({
  selector: "app-transfer-modal",
  imports: [FormsModule, RouterLink],
  templateUrl: "./transfer-modal.component.html",
  styleUrl: "./transfer-modal.component.css",
})
export class TransferModalComponent implements OnInit, AfterViewInit, OnDestroy {
  private readonly transferSvc = inject(TransferService);
  private readonly extRegSvc = inject(ExternalRegistryService);
  private readonly folderSvc = inject(FolderService);
  private readonly authSvc = inject(AuthService);
  readonly trivySvc = inject(TrivyService);

  // ── Scan policy (read-only, inherited from server settings) ───────────────

  /**
   * True when a server-side override is active (set via Settings → Vulnerabilities).
   * False means the values come from environment variables.
   */
  readonly scanOverrideActive = computed(() => this.trivySvc.vulnOverride());

  /** Effective scan enabled flag (env vars or admin override). */
  readonly scanEnabled = computed(() =>
    this.trivySvc.vulnOverride()
      ? this.trivySvc.vulnEnabled()
      : (this.trivySvc.vulnConfig()?.vuln_scan_enabled ?? true),
  );

  /** Effective severity list as a display string (e.g. "CRITICAL, HIGH"). */
  readonly scanSeverities = computed(() => {
    const raw = this.trivySvc.vulnOverride()
      ? this.trivySvc.vulnSeveritiesString()
      : (this.trivySvc.vulnConfig()?.vuln_scan_severities ?? "CRITICAL,HIGH");
    return raw
      .split(",")
      .map((s) => s.trim().toUpperCase())
      .filter(Boolean)
      .join(", ");
  });

  // ── Inputs / outputs ───────────────────────────────────────────────────────

  readonly sourceRegistryId = input.required<string>();
  readonly preselectedImages = input<ImageInfo[]>([]);
  readonly allImages = input<ImageInfo[]>([]);
  readonly closed = output<void>();

  // ── Selection state ────────────────────────────────────────────────────────

  /** Set of "repository:tag" strings currently selected for transfer. */
  readonly selectedKeys = signal<Set<string>>(new Set());

  // ── Destination state ──────────────────────────────────────────────────────

  readonly destRegistryId = signal<string | null>(null);
  readonly destFolder = signal("");
  readonly destNameOverride = signal("");
  readonly destTagOverride = signal("");

  // ── Transfer state ─────────────────────────────────────────────────────────

  readonly transferring = signal(false);
  readonly transferError = signal<string | null>(null);
  readonly activeJobs = signal<TransferJob[]>([]);
  private _jobPollSub: Subscription | null = null;

  // ── Indeterminate checkbox references ──────────────────────────────────────

  /** Query all image-level checkboxes to manage indeterminate state manually. */
  @ViewChildren("imgCheckbox")
  imgCheckboxRefs!: QueryList<ElementRef<HTMLInputElement>>;

  // ── External registries list ───────────────────────────────────────────────

  readonly externalRegistries = computed<ExternalRegistry[]>(() =>
    this.extRegSvc.userRegistries(),
  );

  // ── Computed helpers ───────────────────────────────────────────────────────

  readonly isAdmin = computed(
    () => this.authSvc.currentUser()?.is_admin ?? false,
  );

  readonly pushFolders = computed(() => this.folderSvc.allowedPushFolders());

  /** Selected image refs ordered as they appear in the list. */
  readonly selectedImageRefs = computed(() => {
    const refs: { repository: string; tag: string }[] = [];
    const keys = this.selectedKeys();
    for (const img of this.allImages()) {
      for (const tag of img.tags) {
        const key = `${img.name}:${tag}`;
        if (keys.has(key)) {
          refs.push({ repository: img.name, tag });
        }
      }
    }
    return refs;
  });

  /** True when exactly one image+tag is selected (enables name/tag overrides). */
  readonly isSingleSelection = computed(
    () => this.selectedImageRefs().length === 1,
  );

  /** Preview of the destination path shown in the info banner. */
  readonly destPreview = computed(() => {
    const refs = this.selectedImageRefs();
    if (refs.length === 0) return "";
    const folder = this.destFolder().trim();
    const destId = this.destRegistryId();
    const reg = this.externalRegistries().find((r) => r.id === destId);
    const host = reg ? reg.host : "local";

    /**
     * Mirror the backend naming rules:
     *   - local registry (destId null)     → keep full source path
     *   - external + username configured   → replace namespace with username
     *   - external without username        → keep full source path
     *   - dest_name_override (single only) → use override as-is
     */
    const resolveDestName = (repository: string): string => {
      if (refs.length === 1 && this.destNameOverride().trim()) {
        return this.destNameOverride().trim();
      }
      if (destId === null) {
        // Local registry: preserve the full path
        return repository;
      }
      const username = reg?.username?.trim() ?? "";
      if (username) {
        // External with username: replace namespace with username
        const leaf = repository.includes("/")
          ? repository.split("/").pop()!
          : repository;
        return `${username}/${leaf}`;
      }
      // External without username: preserve the full path
      return repository;
    };

    if (refs.length === 1) {
      const destName = resolveDestName(refs[0].repository);
      const tag = this.destTagOverride().trim() || refs[0].tag;
      const path = folder ? `${folder}/${destName}` : destName;
      return `${host}/${path}:${tag}`;
    }
    return `${host}/${folder ? folder + "/" : ""}[${refs.length} images]`;
  });

  /** Jobs still running (not yet terminal). */
  readonly runningJobs = computed(() =>
    this.activeJobs().filter((j) => TRANSFER_ACTIVE_STATUSES.has(j.status)),
  );

  readonly SEVERITY_ORDER = SEVERITY_ORDER;

  // ── Lifecycle ──────────────────────────────────────────────────────────────

  ngOnInit(): void {
    // Pre-select all tags of preselected images
    const keys = new Set<string>();
    for (const img of this.preselectedImages()) {
      for (const tag of img.tags) {
        keys.add(`${img.name}:${tag}`);
      }
    }
    this.selectedKeys.set(keys);

    // Load registries if cache is empty
    if (this.extRegSvc.externalRegistries().length === 0) {
      this.extRegSvc.loadRegistries();
    }

    // Load effective vulnerability scan config from the server
    // (env vars or admin override from Settings → Vulnerabilities)
    if (!this.trivySvc.vulnConfig()) {
      this.trivySvc.loadConfig().subscribe();
    }
  }

  ngAfterViewInit(): void {
    // Sync indeterminate state after initial render
    this._syncIndeterminate();

    // Re-sync whenever the rendered checkbox list changes
    this.imgCheckboxRefs.changes.subscribe(() => {
      this._syncIndeterminate();
    });
  }

  ngOnDestroy(): void {
    this._jobPollSub?.unsubscribe();
  }

  // ── Selection helpers ──────────────────────────────────────────────────────

  /** Sanitised element id for an image-level checkbox (handles slashes in names). */
  imgCheckboxId(img: ImageInfo): string {
    return `img-chk-${safeId(img.name)}`;
  }

  /** Sanitised element id for a tag-level checkbox. */
  tagCheckboxId(imgName: string, tag: string): string {
    return `tag-chk-${safeId(imgName)}-${safeId(tag)}`;
  }

  isTagSelected(repository: string, tag: string): boolean {
    return this.selectedKeys().has(`${repository}:${tag}`);
  }

  toggleTag(repository: string, tag: string): void {
    const key = `${repository}:${tag}`;
    const keys = new Set(this.selectedKeys());
    if (keys.has(key)) {
      keys.delete(key);
    } else {
      keys.add(key);
    }
    this.selectedKeys.set(keys);
    this._syncIndeterminate();
  }

  isImageFullySelected(img: ImageInfo): boolean {
    return (
      img.tags.length > 0 &&
      img.tags.every((t) => this.selectedKeys().has(`${img.name}:${t}`))
    );
  }

  isImagePartiallySelected(img: ImageInfo): boolean {
    return (
      !this.isImageFullySelected(img) &&
      img.tags.some((t) => this.selectedKeys().has(`${img.name}:${t}`))
    );
  }

  toggleImage(img: ImageInfo): void {
    const keys = new Set(this.selectedKeys());
    if (this.isImageFullySelected(img)) {
      // All tags selected → deselect all
      img.tags.forEach((t) => keys.delete(`${img.name}:${t}`));
    } else {
      // None or partial → select all tags
      img.tags.forEach((t) => keys.add(`${img.name}:${t}`));
    }
    this.selectedKeys.set(keys);
    this._syncIndeterminate();
  }

  selectAll(): void {
    const keys = new Set<string>();
    for (const img of this.allImages()) {
      for (const tag of img.tags) {
        keys.add(`${img.name}:${tag}`);
      }
    }
    this.selectedKeys.set(keys);
    this._syncIndeterminate();
  }

  clearAll(): void {
    this.selectedKeys.set(new Set());
    this._syncIndeterminate();
  }

  /**
   * Manually set the `indeterminate` DOM property on image-level checkboxes.
   * Angular has no built-in [indeterminate] binding so we update the DOM
   * directly after every selection change, keyed by list index which matches
   * the allImages() order exactly.
   */
  private _syncIndeterminate(): void {
    if (!this.imgCheckboxRefs) return;
    const imgs = this.allImages();
    this.imgCheckboxRefs.forEach((ref, index) => {
      const img = imgs[index];
      if (img) {
        ref.nativeElement.indeterminate = this.isImagePartiallySelected(img);
      }
    });
  }

  // ── Transfer action ────────────────────────────────────────────────────────

  async startTransfer(): Promise<void> {
    const refs = this.selectedImageRefs();
    if (refs.length === 0) return;

    this.transferring.set(true);
    this.transferError.set(null);

    // null → local embedded registry (same convention as staging endpoints)
    const sourceId =
      this.sourceRegistryId() === LOCAL_REGISTRY_SYSTEM_ID
        ? null
        : this.sourceRegistryId();

    const destId = this.destRegistryId();
    const folder = this.destFolder().trim() || null;
    const nameOverride = this.isSingleSelection()
      ? this.destNameOverride().trim() || null
      : null;
    const tagOverride = this.isSingleSelection()
      ? this.destTagOverride().trim() || null
      : null;

    try {
      const result = await firstValueFrom(
        this.transferSvc.startTransfer({
          images: refs,
          source_registry_id: sourceId,
          dest_registry_id: destId,
          dest_folder: folder,
          dest_name_override: nameOverride,
          dest_tag_override: tagOverride,
          // Scan policy is always inherited from the server (Settings → Vulnerabilities)
          vuln_scan_enabled_override: null,
          vuln_severities_override: null,
        }),
      );

      this._startJobPolling(result.job_ids);
    } catch (err: unknown) {
      const httpErr = err as { error?: { detail?: string } };
      this.transferError.set(
        httpErr?.error?.detail ?? "Failed to start transfer",
      );
    } finally {
      this.transferring.set(false);
    }
  }

  private _startJobPolling(jobIds: string[]): void {
    this._jobPollSub?.unsubscribe();
    this._jobPollSub = timer(0, 2500)
      .pipe(switchMap(() => this.transferSvc.listJobs()))
      .subscribe((allJobs) => {
        const relevant = allJobs.filter((j) => jobIds.includes(j.job_id));
        this.activeJobs.set(relevant);

        // Stop polling once all jobs have reached a terminal status
        const allDone = relevant.every(
          (j) => !TRANSFER_ACTIVE_STATUSES.has(j.status),
        );
        if (allDone && relevant.length === jobIds.length) {
          this._jobPollSub?.unsubscribe();
          this._jobPollSub = null;
        }
      });
  }

  deleteJob(jobId: string): void {
    this.transferSvc.deleteJob(jobId).subscribe({
      next: () => {
        this.activeJobs.update((jobs) =>
          jobs.filter((j) => j.job_id !== jobId),
        );
      },
    });
  }

  // ── Display helpers ────────────────────────────────────────────────────────

  getStatusBadgeClass(status: string): string {
    const map: Record<string, string> = {
      pending: "badge bg-secondary-subtle text-secondary",
      pulling: "badge bg-info-subtle text-info",
      scanning: "badge bg-warning-subtle text-warning",
      scan_clean: "badge bg-success-subtle text-success",
      scan_skipped: "badge bg-secondary-subtle text-secondary",
      scan_vulnerable: "badge bg-danger text-white",
      pushing: "badge bg-primary-subtle text-primary",
      done: "badge bg-success text-white",
      failed: "badge bg-danger text-white",
    };
    return map[status] ?? "badge bg-secondary";
  }

  isActive(status: string): boolean {
    return TRANSFER_ACTIVE_STATUSES.has(status as TransferJob["status"]);
  }

  severityBadgeClass(sev: string): string {
    const map: Record<string, string> = {
      CRITICAL: "badge bg-danger",
      HIGH: "badge bg-warning text-dark",
      MEDIUM: "badge bg-primary",
      LOW: "badge bg-info text-dark",
      UNKNOWN: "badge bg-secondary",
    };
    return map[sev.toUpperCase()] ?? "badge bg-secondary";
  }

  getCveCount(job: TransferJob, severity: string): number {
    return job.vuln_result?.counts?.[severity] ?? 0;
  }

  getVulnerabilities(job: TransferJob): VulnerabilityEntry[] {
    return job.vuln_result?.vulnerabilities ?? [];
  }

  /**
   * Sanitize a value for use in an HTML id attribute.
   * Slashes and other special characters in image names are invalid in id values
   * and break label-input associations. Replace all non-alphanumeric chars with
   * hyphens to produce a valid id.
   */
  safeId(value: string): string {
    return value.replace(/[^a-zA-Z0-9]/g, "-");
  }

  close(): void {
    this._jobPollSub?.unsubscribe();
    this.closed.emit();
  }
}
