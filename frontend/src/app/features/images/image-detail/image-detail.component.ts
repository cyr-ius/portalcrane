import { CommonModule } from "@angular/common";
import { Component, inject, input, OnInit, signal } from "@angular/core";
import { FormsModule } from "@angular/forms";
import { RouterLink } from "@angular/router";
import {
  ImageDetail,
  RegistryService,
} from "../../../core/services/registry.service";

@Component({
  selector: "app-image-detail",
  imports: [CommonModule, FormsModule, RouterLink],
  templateUrl: "./image-detail.component.html",
  styleUrl: "./image-detail.component.css",
})
export class ImageDetailComponent implements OnInit {
  repository = input.required<string>();

  private registry = inject(RegistryService);

  tags = signal<string[]>([]);
  tagsLoading = signal(false);
  selectedTag = signal<string | null>(null);
  tagDetail = signal<ImageDetail | null>(null);
  detailLoading = signal(false);
  showAddTag = signal(false);
  showAdvanced = signal(false);
  addTagSource = "";
  newTagName = "";
  addingTag = signal(false);
  deleteTagTarget = signal<string | null>(null);
  deletingTag = signal(false);

  objectKeys = Object.keys;

  ngOnInit() {
    this.loadTags();
  }

  loadTags() {
    this.tagsLoading.set(true);
    this.registry.getImageTags(this.repository()).subscribe({
      next: (data) => {
        this.tags.set(data.tags);
        this.tagsLoading.set(false);
        if (data.tags.length > 0) {
          this.addTagSource = data.tags[0];
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
    if (!this.newTagName || !this.addTagSource) return;
    this.addingTag.set(true);
    this.registry
      .addTag(this.repository(), this.addTagSource, this.newTagName)
      .subscribe({
        next: () => {
          this.newTagName = "";
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
