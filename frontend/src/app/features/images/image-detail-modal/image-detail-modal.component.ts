/**
 * Portalcrane - ImageDetailModalComponent
 *
 * Unified modal for image details, supporting both local and external V2 registries.
 *
 * Replaces:
 *  - The router navigation to /images/detail (local registry)
 *  - The ExternalImageDetailComponent modal (external V2 registry)
 *
 * Both the local embedded registry and external V2 registries expose the same
 * Docker Distribution V2 API surface. This component abstracts the source via
 * the `source` input ('local' or an external registry ID) and delegates to the
 * appropriate RegistryService methods.
 *
 * Features:
 *  - Tag selector list (sorted, latest first)
 *  - Metadata panel: digest, size, layers, architecture, OS, labels, env
 *  - Add tag (retag via manifest copy) — requires push permission
 *  - Delete tag — requires push permission
 *  - Copy docker pull command to clipboard
 *  - Advanced: layer list toggle
 *
 * Inputs:
 *  @input source     'local' | external registry ID string
 *  @input image      ImageInfo object from the parent list
 *  @input canPush    Whether the current user has push rights on this image
 *
 * Outputs:
 *  @output closed       Emitted when the user closes the modal
 *  @output tagsChanged  Emitted after a tag add/delete so the parent can reload
 *
 * Usage:
 *   <app-image-detail-modal
 *     [source]="selectedSource()"
 *     [image]="viewTarget()!"
 *     [canPush]="canPushOnImage()"
 *     (closed)="viewTarget.set(null)"
 *     (tagsChanged)="reloadImages()"
 *   />
 */
import { DatePipe } from "@angular/common";
import {
  Component,
  computed,
  DestroyRef,
  inject,
  input,
  OnInit,
  output,
  signal,
} from "@angular/core";
import { takeUntilDestroyed } from "@angular/core/rxjs-interop";
import { FormsModule } from "@angular/forms";
import { LOCAL_REGISTRY_SYSTEM_ID } from "../../../core/constants/registry.constants";
import { formatBytes } from "../../../core/helpers/storage";
import {
  ImageDetail,
  ImageInfo,
  RegistryService,
} from "../../../core/services/registry.service";

@Component({
  selector: "app-image-detail-modal",
  imports: [FormsModule, DatePipe],
  templateUrl: "./image-detail-modal.component.html",
  styleUrl: "./image-detail-modal.component.css",
})
export class ImageDetailModalComponent implements OnInit {
  private readonly registrySvc = inject(RegistryService);
  private readonly destroyRef = inject(DestroyRef);

  readonly formatBytes = formatBytes

  // ── Inputs / outputs ───────────────────────────────────────────────────────

  readonly source = input.required<string>();
  readonly image = input.required<ImageInfo>();
  readonly canPush = input<boolean>(false);
  readonly closed = output<void>();
  readonly tagsChanged = output<void>();

  // ── Internal state ─────────────────────────────────────────────────────────

  readonly selectedTag = signal<string>("");
  readonly tagDetail = signal<ImageDetail | null>(null);
  readonly loadingDetail = signal<boolean>(false);
  readonly detailError = signal<string | null>(null);
  readonly copyingTag = signal<string | null>(null);
  readonly showAdvanced = signal<boolean>(false);

  // ── Add-tag form state ─────────────────────────────────────────────────────

  readonly showAddForm = signal(false);
  readonly addSourceTag = signal("");
  readonly addNewTag = signal("");
  readonly adding = signal(false);
  readonly addMessage = signal<string | null>(null);
  readonly addSuccess = signal(false);

  // ── Delete confirmation state ──────────────────────────────────────────────

  readonly deleteTarget = signal<string | null>(null);
  readonly deleting = signal(false);
  readonly deleteMessage = signal<string | null>(null);
  readonly deleteSuccess = signal(false);

  // ── Derived ────────────────────────────────────────────────────────────────

  readonly isLocal = computed(() => this.source() === "local");
  readonly localTags = signal<string[]>([]);
  readonly sortedTags = computed<string[]>(() => {
    const tags = [...this.localTags()];
    return tags.sort((a, b) => {
      if (a === "latest") return -1;
      if (b === "latest") return 1;
      return a.localeCompare(b);
    });
  });
  readonly sourceLabel = computed(() =>
    this.isLocal() ? "Local V2 registry — image details" : "External V2 registry — image details",
  );
  readonly sourceIcon = computed(() =>
    this.isLocal() ? "bi bi-hdd-rack me-2 text-primary" : "bi bi-globe me-2 text-info",
  );
  readonly objectKeys = Object.keys;

  // ── Lifecycle ──────────────────────────────────────────────────────────────

  ngOnInit(): void {
    // Initialize local tag list from the image input
    this.localTags.set([...(this.image().tags ?? [])]);
    // Auto-select and load the first available tag
    const first = this.sortedTags()[0] ?? "";
    if (first) {
      this.selectTag(first);
    }
  }

  // ── Tag selection ──────────────────────────────────────────────────────────

  /**
   * Select a tag and fetch its detailed metadata.
   * Skips the fetch if the tag is already selected and detail is loaded.
   */
  selectTag(tag: string): void {
    if (this.selectedTag() === tag && this.tagDetail()) return;
    this.selectedTag.set(tag);
    this.addSourceTag.set(tag);
    this.tagDetail.set(null);
    this.detailError.set(null);
    this.loadingDetail.set(true);

    const fetch$ = this.isLocal()
      ? this.registrySvc.getExternalTagDetail(LOCAL_REGISTRY_SYSTEM_ID, this.image().name, tag)
      : this.registrySvc.getExternalTagDetail(this.source(), this.image().name, tag);

    fetch$.pipe(takeUntilDestroyed(this.destroyRef)).subscribe({
      next: (detail) => {
        this.tagDetail.set(detail);
        this.loadingDetail.set(false);
      },
      error: (err) => {
        this.detailError.set(
          err?.error?.detail ?? err?.message ?? "Failed to load tag detail",
        );
        this.loadingDetail.set(false);
      },
    });
  }

  // ── Add tag ────────────────────────────────────────────────────────────────

  /** Open the add-tag inline form, pre-selecting the currently viewed tag. */
  openAddForm(): void {
    this.showAddForm.set(true);
    this.addSourceTag.set(this.selectedTag());
    this.addNewTag.set("");
    this.addMessage.set(null);
    this.addSuccess.set(false);
  }

  /** Submit the add-tag operation to the appropriate API endpoint. */
  submitAddTag(): void {
    const src = this.addSourceTag().trim();
    const newT = this.addNewTag().trim();
    if (!src || !newT) return;
    this.adding.set(true);
    this.addMessage.set(null);

    const add$ = this.isLocal()
      ? this.registrySvc.addExternalTag(LOCAL_REGISTRY_SYSTEM_ID, this.image().name, src, newT)
      : this.registrySvc.addExternalTag(this.source(), this.image().name, src, newT);

    add$.pipe(takeUntilDestroyed(this.destroyRef)).subscribe({
      next: (res) => {
        this.adding.set(false);
        this.addSuccess.set(true);
        this.addMessage.set(res.message);
        this.showAddForm.set(false);
        this.addNewTag.set("");
        this.localTags.update(tags => [...tags, newT]);
        this.tagsChanged.emit();
      },
      error: (err) => {
        this.adding.set(false);
        this.addSuccess.set(false);
        this.addMessage.set(
          err?.error?.detail ?? err?.message ?? "Failed to add tag",
        );
      },
    });
  }

  /** Cancel the add-tag form without making any API call. */
  cancelAddForm(): void {
    this.showAddForm.set(false);
    this.addMessage.set(null);
  }

  // ── Delete tag ─────────────────────────────────────────────────────────────

  /** Show the delete confirmation inline for the given tag. */
  confirmDeleteTag(tag: string): void {
    this.deleteTarget.set(tag);
    this.deleteMessage.set(null);
    this.deleteSuccess.set(false);
  }

  /** Execute the tag deletion after confirmation. */
  executeDeleteTag(): void {
    const tag = this.deleteTarget();
    if (!tag) return;
    this.deleting.set(true);
    this.deleteMessage.set(null);

    const del$ = this.isLocal()
      ? this.registrySvc.deleteExternalTag(LOCAL_REGISTRY_SYSTEM_ID, this.image().name, tag)
      : this.registrySvc.deleteExternalTag(this.source(), this.image().name, tag);

    del$.pipe(takeUntilDestroyed(this.destroyRef)).subscribe({
      next: (res) => {
        this.deleting.set(false);
        this.deleteSuccess.set(true);
        this.deleteMessage.set(res.message);
        this.deleteTarget.set(null);
        this.localTags.update(tags => tags.filter(t => t !== tag));
        if (this.selectedTag() === tag) {
          this.tagDetail.set(null);
          this.selectedTag.set("");
        }
        this.tagsChanged.emit();
      },
      error: (err) => {
        this.deleting.set(false);
        this.deleteSuccess.set(false);
        this.deleteMessage.set(
          err?.error?.detail ?? err?.message ?? "Failed to delete tag",
        );
      },
    });
  }

  /** Cancel the pending delete without making any API call. */
  cancelDelete(): void {
    this.deleteTarget.set(null);
  }

  // ── Clipboard ──────────────────────────────────────────────────────────────

  /**
   * Copy a ready-to-use docker pull command to the clipboard.
   * For the local registry, the host is the current window location.
   * For external registries, the image name already contains the full path.
   */
  copyToClipboard(tag: string): void {
    if (this.copyingTag()) return;

    const cmd = `docker pull ${window.location.host}/${this.image().name}:${tag}`;
    this.copyingTag.set(tag);
    navigator.clipboard
      .writeText(cmd)
      .catch(() => {
        // no-op: clipboard API unavailable (insecure context, etc.)
      })
      .finally(() => {
        window.setTimeout(() => this.copyingTag.set(null), 450);
      });
  }

  // ── Utilities ──────────────────────────────────────────────────────────────

  /** Close the modal and notify the parent. */
  close(): void {
    this.closed.emit();
  }
}
