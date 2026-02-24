import { Component, computed, inject, OnInit, signal } from "@angular/core";
import { FormsModule } from "@angular/forms";
import { RouterLink } from "@angular/router";
import {
  ImageInfo,
  PaginatedImages,
  RegistryService,
} from "../../../core/services/registry.service";

/** Available sort fields for the image list */
type SortField = "name" | "tag_count";

/** Sort direction */
type SortDir = "asc" | "desc";

/** Tag count filter presets */
type TagFilter = "all" | "single" | "multi";

@Component({
  selector: "app-images-list",
  imports: [RouterLink, FormsModule],
  templateUrl: "./images-list.component.html",
  styleUrl: "./images-list.component.css",
})
export class ImagesListComponent implements OnInit {
  private registry = inject(RegistryService);

  // ── Remote data ────────────────────────────────────────────────────────────
  data = signal<PaginatedImages | null>(null);
  loading = signal(false);
  currentPage = signal(1);
  pageSize = 20;
  searchQuery = "";
  private searchTimeout: ReturnType<typeof setTimeout> | null = null;

  // ── Client-side sort & filter state ────────────────────────────────────────
  /** Active sort field — default alphabetical by name */
  sortField = signal<SortField>("name");

  /** Sort direction */
  sortDir = signal<SortDir>("asc");

  /** Tag count filter: all | single (= 1 tag) | multi (≥ 2 tags) */
  tagFilter = signal<TagFilter>("all");

  // ── Derived list (sort + filter applied on top of backend page) ────────────
  /**
   * Applies client-side sort and tag-count filter to the current page items.
   * The backend already handles text search and pagination; this computed
   * signal only post-processes what is already in memory.
   */
  filteredItems = computed(() => {
    const items = this.data()?.items ?? [];

    // Apply tag-count filter
    const tagF = this.tagFilter();
    const filtered =
      tagF === "single"
        ? items.filter((i) => i.tag_count === 1)
        : tagF === "multi"
          ? items.filter((i) => i.tag_count >= 2)
          : items;

    // Apply sort
    const field = this.sortField();
    const dir = this.sortDir() === "asc" ? 1 : -1;
    return [...filtered].sort((a, b) => {
      if (field === "tag_count") {
        return (a.tag_count - b.tag_count) * dir;
      }
      return a.name.localeCompare(b.name) * dir;
    });
  });

  // ── Pagination helpers ─────────────────────────────────────────────────────
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
    const result: number[] = [];
    for (
      let i = Math.max(1, current - delta);
      i <= Math.min(total, current + delta);
      i++
    ) {
      result.push(i);
    }
    return result;
  });

  // ── Delete modal state ─────────────────────────────────────────────────────
  deleteTarget = signal<ImageInfo | null>(null);
  deleting = signal(false);

  // ── Lifecycle ──────────────────────────────────────────────────────────────
  ngOnInit() {
    this.loadImages();
  }

  // ── Data loading ───────────────────────────────────────────────────────────
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

  // ── Sort helpers ───────────────────────────────────────────────────────────
  /**
   * Toggle sort: if the same field is clicked again, reverse direction;
   * otherwise switch to the new field ascending.
   */
  toggleSort(field: SortField) {
    if (this.sortField() === field) {
      this.sortDir.set(this.sortDir() === "asc" ? "desc" : "asc");
    } else {
      this.sortField.set(field);
      this.sortDir.set("asc");
    }
  }

  /** Returns the Bootstrap icon class for the sort indicator of a given column */
  sortIcon(field: SortField): string {
    if (this.sortField() !== field)
      return "bi-arrow-down-up text-muted opacity-50";
    return this.sortDir() === "asc"
      ? "bi-sort-alpha-down"
      : "bi-sort-alpha-up-alt";
  }

  /** Sort icon specifically for numeric column (tag count) */
  sortIconNumeric(field: SortField): string {
    if (this.sortField() !== field)
      return "bi-arrow-down-up text-muted opacity-50";
    return this.sortDir() === "asc"
      ? "bi-sort-numeric-down"
      : "bi-sort-numeric-up-alt";
  }

  // ── Filter helpers ─────────────────────────────────────────────────────────
  setTagFilter(f: TagFilter) {
    this.tagFilter.set(f);
  }

  // ── Delete ─────────────────────────────────────────────────────────────────
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
