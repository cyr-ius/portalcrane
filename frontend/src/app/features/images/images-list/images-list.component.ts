import { CommonModule } from "@angular/common";
import { Component, computed, inject, OnInit, signal } from "@angular/core";
import { FormsModule } from "@angular/forms";
import { RouterLink } from "@angular/router";
import {
  ImageInfo,
  PaginatedImages,
  RegistryService,
} from "../../../core/services/registry.service";

@Component({
  selector: "app-images-list",
  imports: [CommonModule, RouterLink, FormsModule],
  templateUrl: "./images-list.component.html",
  styleUrl: "./images-list.component.css",
})
export class ImagesListComponent implements OnInit {
  private registry = inject(RegistryService);

  data = signal<PaginatedImages | null>(null);
  loading = signal(false);
  currentPage = signal(1);
  pageSize = 20;
  searchQuery = "";
  deleteTarget = signal<ImageInfo | null>(null);
  deleting = signal(false);
  private searchTimeout: ReturnType<typeof setTimeout> | null = null;

  pageStart = computed(() => {
    const d = this.data();
    if (!d) return 0;
    return (d.page - 1) * d.page_size + 1;
  });

  pageEnd = computed(() => {
    const d = this.data();
    if (!d) return 0;
    return Math.min(d.page * d.page_size, d.total);
  });

  pages = computed(() => {
    const d = this.data();
    if (!d) return [];
    const total = d.total_pages;
    const current = d.page;
    const delta = 2;
    const pages: number[] = [];
    for (
      let i = Math.max(1, current - delta);
      i <= Math.min(total, current + delta);
      i++
    ) {
      pages.push(i);
    }
    return pages;
  });

  ngOnInit() {
    this.loadImages();
  }

  loadImages() {
    this.loading.set(true);
    this.registry
      .getImages(this.currentPage(), this.pageSize, this.searchQuery)
      .subscribe({
        next: (data) => {
          this.data.set(data);
          this.loading.set(false);
        },
        error: () => this.loading.set(false),
      });
  }

  onSearch() {
    if (this.searchTimeout) clearTimeout(this.searchTimeout);
    this.searchTimeout = setTimeout(() => {
      this.currentPage.set(1);
      this.loadImages();
    }, 400);
  }

  clearSearch() {
    this.searchQuery = "";
    this.currentPage.set(1);
    this.loadImages();
  }

  onPageSizeChange() {
    this.currentPage.set(1);
    this.loadImages();
  }

  goToPage(page: number) {
    const total = this.data()?.total_pages ?? 1;
    if (page < 1 || page > total) return;
    this.currentPage.set(page);
    this.loadImages();
  }

  confirmDeleteImage(image: ImageInfo) {
    this.deleteTarget.set(image);
  }

  deleteImage() {
    const target = this.deleteTarget();
    if (!target) return;
    this.deleting.set(true);
    this.registry.deleteImage(target.name).subscribe({
      next: () => {
        this.deleteTarget.set(null);
        this.deleting.set(false);
        this.loadImages();
      },
      error: () => this.deleting.set(false),
    });
  }
}
