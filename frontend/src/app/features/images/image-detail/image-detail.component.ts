/**
 * Portalcrane - Image Detail Component
 *
 * Displays tags and detailed metadata for a single registry image.
 * The repository name is read from the URL query parameter ?repository=...
 * instead of a route path segment, to avoid %2F encoding issues with
 * reverse proxies when the image name contains slashes (e.g. org/image).
 */
import { DatePipe } from "@angular/common";
import { Component, inject, OnInit, signal } from "@angular/core";
import { toSignal } from "@angular/core/rxjs-interop";
import { FormsModule } from "@angular/forms";
import { ActivatedRoute, RouterLink } from "@angular/router";
import { map } from "rxjs/operators";
import {
  ImageDetail,
  RegistryService,
} from "../../../core/services/registry.service";

@Component({
  selector: "app-image-detail",
  imports: [RouterLink, DatePipe, FormsModule],
  templateUrl: "./image-detail.component.html",
  styleUrl: "./image-detail.component.css",
})
export class ImageDetailComponent implements OnInit {
  private route = inject(ActivatedRoute);
  private registry = inject(RegistryService);

  /**
   * Repository name read from the ?repository= query param.
   * Using toSignal so it integrates seamlessly with Signal-based templates.
   */
  readonly repository = toSignal(
    this.route.queryParamMap.pipe(
      map((params) => params.get("repository") ?? ""),
    ),
    { initialValue: "" },
  );

  tags = signal<string[]>([]);
  tagsLoading = signal(false);
  selectedTag = signal<string | null>(null);
  tagDetail = signal<ImageDetail | null>(null);
  detailLoading = signal(false);
  showAddTag = signal(false);
  showAdvanced = signal(false);
  addTagSource = signal("");
  newTagName = signal("");
  addingTag = signal(false);
  deleteTagTarget = signal<string | null>(null);
  deletingTag = signal(false);

  objectKeys = Object.keys;

  ngOnInit() {
    this.loadTags();
  }

  loadTags() {
    const repo = this.repository();
    if (!repo) return;
    this.tagsLoading.set(true);
    this.registry.getImageTags(repo).subscribe({
      next: (data) => {
        this.tags.set(data.tags);
        this.tagsLoading.set(false);
        if (data.tags.length > 0) {
          this.addTagSource.set(data.tags[0]);
        }
      },
      error: () => this.tagsLoading.set(false),
    });
  }

  selectTag(tag: string) {
    this.selectedTag.set(tag);
    this.tagDetail.set(null);
    this.detailLoading.set(true);
    this.registry.getTagDetail(this.repository(), tag).subscribe({
      next: (detail) => {
        this.tagDetail.set(detail);
        this.detailLoading.set(false);
      },
      error: () => this.detailLoading.set(false),
    });
  }

  addTag() {
    // Test signal values (not the signal references which are always truthy)
    if (!this.newTagName() || !this.addTagSource()) return;
    this.addingTag.set(true);
    this.registry
      .addTag(this.repository(), this.addTagSource(), this.newTagName())
      .subscribe({
        next: () => {
          this.newTagName.set("");
          this.showAddTag.set(false);
          this.addingTag.set(false);
          this.loadTags();
        },
        error: () => this.addingTag.set(false),
      });
  }

  confirmDeleteTag(tag: string) {
    this.deleteTagTarget.set(tag);
  }

  deleteTag() {
    const tag = this.deleteTagTarget();
    if (!tag) return;
    this.deletingTag.set(true);
    this.registry.deleteTag(this.repository(), tag).subscribe({
      next: () => {
        if (this.selectedTag() === tag) this.selectedTag.set(null);
        this.deleteTagTarget.set(null);
        this.deletingTag.set(false);
        this.loadTags();
      },
      error: () => this.deletingTag.set(false),
    });
  }

  copyToClipboard(tag: string) {
    const cmd = `docker pull ${window.location.host}/${this.repository()}:${tag}`;
    navigator.clipboard.writeText(cmd);
  }

  formatBytes(bytes: number): string {
    if (!bytes) return "0 B";
    const units = ["B", "KB", "MB", "GB"];
    let size = bytes;
    let i = 0;
    while (size >= 1024 && i < units.length - 1) {
      size /= 1024;
      i++;
    }
    return `${size.toFixed(1)} ${units[i]}`;
  }
}
