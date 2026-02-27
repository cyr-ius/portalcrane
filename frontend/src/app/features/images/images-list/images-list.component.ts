/**
 * Portalcrane - Images List Component
 * Displays registry images in flat list or hierarchical folder tree view.
 * New feature: viewMode signal toggles between "flat" and "tree".
 */
import { Component, computed, inject, OnInit, signal } from "@angular/core";
import { FormsModule } from "@angular/forms";
import { RouterLink } from "@angular/router";
import {
  ImageInfo,
  PaginatedImages,
  RegistryService,
} from "../../../core/services/registry.service";

/** Available sort fields for the image list. */
type SortField = "name" | "tag_count";

/** Sort direction. */
type SortDir = "asc" | "desc";

/** Tag count filter presets. */
type TagFilter = "all" | "single" | "multi";

/** Display mode for the images page. */
type ViewMode = "flat" | "tree";

/** A folder node in the tree view. */
interface FolderNode {
  /** Folder name, e.g. "app/backend" or "(root)". */
  name: string;
  images: ImageInfo[];
}

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

  // ── View mode ──────────────────────────────────────────────────────────────
  /** Toggle between flat list and hierarchical folder tree. */
  viewMode = signal<ViewMode>("flat");

  /** Set of expanded folder names in tree mode. */
  expandedFolders = signal<Set<string>>(new Set());

  // ── Client-side sort & filter state ────────────────────────────────────────
  sortField = signal<SortField>("name");
  sortDir = signal<SortDir>("asc");
  tagFilter = signal<TagFilter>("all");

  // ── Derived: flat list ─────────────────────────────────────────────────────
  filteredItems = computed(() => {
    const items = this.data()?.items ?? [];
    const tagF = this.tagFilter();
    const filtered =
      tagF === "single"
        ? items.filter((i) => i.tag_count === 1)
        : tagF === "multi"
          ? items.filter((i) => i.tag_count >= 2)
          : items;
    const field = this.sortField();
    const dir = this.sortDir() === "asc" ? 1 : -1;
    return [...filtered].sort((a, b) => {
      if (field === "tag_count") return (a.tag_count - b.tag_count) * dir;
      return a.name.localeCompare(b.name) * dir;
    });
  });

  // ── Derived: folder tree ───────────────────────────────────────────────────
  /**
   * Builds a folder tree from the flat image list.
   * Images without a "/" in their name go to "(root)".
   * Images like "app/backend/api" are grouped under "app/backend".
   */
  folderTree = computed<FolderNode[]>(() => {
    const items = this.data()?.items ?? [];
    const map = new Map<string, ImageInfo[]>();

    for (const img of items) {
      const slashIdx = img.name.lastIndexOf("/");
      const folder =
        slashIdx === -1 ? "(root)" : img.name.substring(0, slashIdx);
      if (!map.has(folder)) map.set(folder, []);
      map.get(folder)!.push(img);
    }

    // Sort: root first, then alphabetical
    const nodes: FolderNode[] = [];
    const sortedKeys = [...map.keys()].sort((a, b) => {
      if (a === "(root)") return -1;
      if (b === "(root)") return 1;
      return a.localeCompare(b);
    });

    for (const key of sortedKeys) {
      nodes.push({
        name: key,
        images: map.get(key)!.sort((a, b) => a.name.localeCompare(b.name)),
      });
    }
    return nodes;
  });

  /**
   * Image name relative to its folder (last segment of the path).
   * Used in tree view to show only "api" under "app/backend" folder.
   */
  imageShortName(img: ImageInfo): string {
    const idx = img.name.lastIndexOf("/");
    return idx === -1 ? img.name : img.name.substring(idx + 1);
  }

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
          // Expand all folders by default in tree mode
          const allFolders = new Set(this.folderTree().map((n) => n.name));
          this.expandedFolders.set(allFolders);
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

  // ── View mode helpers ──────────────────────────────────────────────────────

  setViewMode(mode: ViewMode) {
    this.viewMode.set(mode);
  }

  toggleFolder(folderName: string) {
    this.expandedFolders.update((s) => {
      const next = new Set(s);
      if (next.has(folderName)) {
        next.delete(folderName);
      } else {
        next.add(folderName);
      }
      return next;
    });
  }

  isFolderExpanded(folderName: string): boolean {
    return this.expandedFolders().has(folderName);
  }

  // ── Sort helpers ───────────────────────────────────────────────────────────
  toggleSort(field: SortField) {
    if (this.sortField() === field) {
      this.sortDir.set(this.sortDir() === "asc" ? "desc" : "asc");
    } else {
      this.sortField.set(field);
      this.sortDir.set("asc");
    }
  }

  sortIcon(field: SortField): string {
    if (this.sortField() !== field)
      return "bi-arrow-down-up text-muted opacity-50";
    return this.sortDir() === "asc"
      ? "bi-sort-alpha-down"
      : "bi-sort-alpha-up-alt";
  }

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
