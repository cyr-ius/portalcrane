/**
 * Portalcrane - ExternalImageDetailComponent
 *
 * Modal panel showing full image metadata for a repository hosted in a
 * standard V2-compatible external registry (not Docker Hub, not GHCR).
 *
 * Features:
 *  - Tag selector: click a tag to load its manifest detail
 *  - Metadata display: digest, size, layers, architecture, OS, labels, env
 *  - Add tag: copy a manifest to a new tag name (requires push permission)
 *  - Delete tag: remove a single tag by digest (requires push permission)
 *
 * The component is opened by images-list when:
 *   isExternalSource() === true AND !isGithubMode() AND !isDockerHubMode()
 *
 * It receives the registry ID and the ImageInfo as inputs so it can fetch
 * additional data without coupling to the parent component.
 *
 * Usage:
 *   <app-external-image-detail
 *     [registryId]="selectedSource()"
 *     [image]="viewTarget()!"
 *     [canPush]="canPushExternal()"
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
import { ImageDetail, ImageInfo, RegistryService } from "../../../core/services/registry.service";

@Component({
  selector: "app-external-image-detail",
  imports: [FormsModule, DatePipe],
  templateUrl: "./external-image-detail.component.html",
  styleUrl: "./external-image-detail.component.css",
})
export class ExternalImageDetailComponent implements OnInit {
  private readonly registrySvc = inject(RegistryService);
  private readonly destroyRef = inject(DestroyRef);


  // ── Inputs / outputs ───────────────────────────────────────────────────────

  readonly registryId = input.required<string>();
  readonly image = input.required<ImageInfo>();
  readonly canPush = input<boolean>(false);
  readonly closed = output<void>();
  readonly tagsChanged = output<void>();

  // ── State ──────────────────────────────────────────────────────────────────

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

  // ── Delete-tag confirm state ───────────────────────────────────────────────

  readonly deleteTarget = signal<string | null>(null);
  readonly deleting = signal(false);
  readonly deleteMessage = signal<string | null>(null);
  readonly deleteSuccess = signal(false);

  // ── Derived ────────────────────────────────────────────────────────────────

  readonly sortedTags = computed<string[]>(() => {
    const tags = [...(this.image().tags ?? [])];
    return tags.sort((a, b) => {
      if (a === "latest") return -1;
      if (b === "latest") return 1;
      return a.localeCompare(b);
    });
  });

  // ── Lifecycle ──────────────────────────────────────────────────────────────

  ngOnInit(): void {
    // Auto-select the first tag and load its details
    const first = this.sortedTags()[0] ?? "";
    if (first) {
      this.selectTag(first);
    }
  }

  // ── Actions ────────────────────────────────────────────────────────────────

  selectTag(tag: string): void {
    if (this.selectedTag() === tag && this.tagDetail()) return;
    this.selectedTag.set(tag);
    this.addSourceTag.set(tag);
    this.tagDetail.set(null);
    this.detailError.set(null);
    this.loadingDetail.set(true);

    this.registrySvc
      .getExternalTagDetail(this.registryId(), this.image().name, tag)
      .pipe(takeUntilDestroyed(this.destroyRef))
      .subscribe({
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

  openAddForm(): void {
    this.showAddForm.set(true);
    this.addSourceTag.set(this.selectedTag());
    this.addNewTag.set("");
    this.addMessage.set(null);
  }

  submitAddTag(): void {
    const src = this.addSourceTag().trim();
    const newT = this.addNewTag().trim();
    if (!src || !newT) return;
    this.adding.set(true);
    this.addMessage.set(null);

    this.registrySvc
      .addExternalTag(this.registryId(), this.image().name, src, newT)
      .pipe(takeUntilDestroyed(this.destroyRef))
      .subscribe({
        next: (res) => {
          this.adding.set(false);
          this.addSuccess.set(true);
          this.addMessage.set(res.message);
          this.showAddForm.set(false);
          this.addNewTag.set("");
          // Notify parent to refresh the list
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

  confirmDeleteTag(tag: string): void {
    this.deleteTarget.set(tag);
    this.deleteMessage.set(null);
  }

  executeDeleteTag(): void {
    const tag = this.deleteTarget();
    if (!tag) return;
    this.deleting.set(true);
    this.deleteMessage.set(null);

    this.registrySvc
      .deleteExternalTag(this.registryId(), this.image().name, tag)
      .pipe(takeUntilDestroyed(this.destroyRef))
      .subscribe({
        next: (res) => {
          this.deleting.set(false);
          this.deleteSuccess.set(true);
          this.deleteMessage.set(res.message);
          this.deleteTarget.set(null);
          // If the deleted tag was selected, reset detail
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

  cancelDelete(): void {
    this.deleteTarget.set(null);
  }

  copyToClipboard(tag: string) {
    if (this.copyingTag()) return;

    const cmd = `docker pull ${window.location.host}/${this.image().name}:${tag}`;
    this.copyingTag.set(tag);
    navigator.clipboard
      .writeText(cmd)
      .catch(() => {
        // no-op: keep current UX if clipboard is unavailable
      })
      .finally(() => {
        window.setTimeout(() => this.copyingTag.set(null), 450);
      });
  }

  formatBytes(bytes: number): string {
    if (!bytes || bytes === 0) return "0 B";
    const units = ["B", "KB", "MB", "GB"];
    let value = bytes;
    let unitIndex = 0;
    while (value >= 1024 && unitIndex < units.length - 1) {
      value /= 1024;
      unitIndex++;
    }
    return `${value.toFixed(1)} ${units[unitIndex]}`;
  }

  readonly objectKeys = Object.keys;

  close(): void {
    this.closed.emit();
  }
}
