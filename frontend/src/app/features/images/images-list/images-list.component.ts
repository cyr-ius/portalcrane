/**
 * Portalcrane - Images List Component
 * Displays registry images in flat list or hierarchical folder tree view.
 *
 * Navigation to the image detail page now uses /images/detail with a
 * queryParam ?repository=... instead of /images/:repository path segment,
 * to avoid %2F encoding issues with reverse proxies.
 */
import { Component, computed, inject, OnInit, signal } from "@angular/core";
import { FormsModule } from "@angular/forms";
import { Router } from "@angular/router";
import { AuthService } from "../../../core/services/auth.service";
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
  name: string;
  images: ImageInfo[];
}

@Component({
  selector: "app-images-list",
  imports: [FormsModule],
  templateUrl: "./images-list.component.html",
  styleUrl: "./images-list.component.css",
})
export class ImagesListComponent implements OnInit {
  private registry = inject(RegistryService);
  private router = inject(Router);
  private readonly authService = inject(AuthService);
  private readonly VIEW_MODE_KEY = "pc_images_view_mode";

  // ── Remote data ────────────────────────────────────────────────────────────
  data = signal<PaginatedImages | null>(null);
  loading = signal(false);
  currentPage = signal(1);
  pageSize = 20;
  searchQuery = "";
  private searchTimeout: ReturnType<typeof setTimeout> | null = null;

  // ── View mode ──────────────────────────────────────────────────────────────
  viewMode = signal<ViewMode>(
    (localStorage.getItem(this.VIEW_MODE_KEY) as ViewMode) ?? "flat",
  );
  allowedFolders = signal<string[]>([]);
  expandedFolders = signal<Set<string>>(new Set());

  // ── Client-side sort & filter state ────────────────────────────────────────
  sortField = signal<SortField>("name");
  sortDir = signal<SortDir>("asc");
  tagFilter = signal<TagFilter>("all");

  // ── Derived: flat list ─────────────────────────────────────────────────────
  filteredItems = computed(() => {
    const items = this.data()?.items ?? [];
    const allowed = this.allowedFolders();
    const isAdmin = this.authService.currentUser()?.is_admin ?? false;

    // Apply folder restriction for non-admins
    const accessible =
      isAdmin || allowed.length === 0
        ? items
        : items.filter((img) => {
            const folder = img.name.includes("/")
              ? img.name.split("/")[0]
              : "(root)";
            return allowed.includes(folder);
          });

    const tagF = this.tagFilter();
    const filtered =
      tagF === "single"
        ? accessible.filter((i) => i.tag_count === 1)
        : tagF === "multi"
          ? accessible.filter((i) => i.tag_count >= 2)
          : accessible;

    const field = this.sortField();
    const dir = this.sortDir() === "asc" ? 1 : -1;
    return [...filtered].sort((a, b) =>
      field === "tag_count"
        ? (a.tag_count - b.tag_count) * dir
        : a.name.localeCompare(b.name) * dir,
    );
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
      const slashIdx = img.name.indexOf("/");
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
    const idx = img.name.indexOf("/");
    return idx === -1 ? img.name : img.name.substring(idx + 1);
  }

  /**
   * Navigate to the image detail page using queryParams.
   * Avoids %2F issues — repository name is safe in query string.
   */
  goToDetail(imageName: string): void {
    this.router.navigate(["/images/detail"], {
      queryParams: { repository: imageName },
    });
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

  // ── Copy modal state ───────────────────────────────────────────────────────
  copySource = signal<{ image: ImageInfo; tag: string } | null>(null);
  pushableFolders = signal<string[]>([]);
  copyDestFolder = signal("");
  copyDestName = signal("");
  copyDestTag = signal("");
  copying = signal(false);
  copyMessage = signal<string | null>(null);
  isAdmin = computed(() => this.authService.currentUser()?.is_admin ?? false);
  sourceTagOptions = signal<string[]>([]);

  // ── Sort helpers ───────────────────────────────────────────────────────────
  toggleSort(field: SortField): void {
    if (this.sortField() === field) {
      this.sortDir.set(this.sortDir() === "asc" ? "desc" : "asc");
    } else {
      this.sortField.set(field);
      this.sortDir.set("asc");
    }
  }

  sortIcon(field: SortField): string {
    if (this.sortField() !== field) return "bi-arrow-down-up text-muted";
    return this.sortDir() === "asc" ? "bi-sort-alpha-down" : "bi-sort-alpha-up";
  }

  sortIconNumeric(field: SortField): string {
    if (this.sortField() !== field) return "bi-arrow-down-up text-muted";
    return this.sortDir() === "asc"
      ? "bi-sort-numeric-down"
      : "bi-sort-numeric-up";
  }

  setTagFilter(filter: TagFilter): void {
    this.tagFilter.set(filter);
  }

  setViewMode(mode: ViewMode): void {
    this.viewMode.set(mode);
    localStorage.setItem(this.VIEW_MODE_KEY, mode);
  }

  // ── Folder tree helpers ────────────────────────────────────────────────────
  isFolderExpanded(folderName: string): boolean {
    return this.expandedFolders().has(folderName);
  }

  toggleFolder(folderName: string): void {
    const set = new Set(this.expandedFolders());
    if (set.has(folderName)) {
      set.delete(folderName);
    } else {
      set.add(folderName);
    }
    this.expandedFolders.set(set);
  }

  // ── Delete modal state ─────────────────────────────────────────────────────
  deleteTarget = signal<ImageInfo | null>(null);
  deleting = signal(false);

  confirmDeleteImage(image: ImageInfo): void {
    this.deleteTarget.set(image);
  }

  deleteImage(): void {
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

  // ── Lifecycle ──────────────────────────────────────────────────────────────
  ngOnInit() {
    // Load allowed folders first, then images
    this.registry.getMyFolders().subscribe({
      next: (folders) => {
        this.allowedFolders.set(folders);
        this.loadImages();
      },
      error: () => this.loadImages(), // fallback
    });
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

  // ── Copy modal state ───────────────────────────────────────────────────────
  openCopyModal(image: ImageInfo, tag: string): void {
    this.copySource.set({ image, tag });
    this.sourceTagOptions.set(image.tags);
    // Pre-fill with current name (without folder prefix)
    const shortName = image.name.includes("/")
      ? image.name.split("/").slice(1).join("/")
      : image.name;
    this.copyDestName.set(shortName);
    this.copyDestTag.set(tag);
    this.copyDestFolder.set("");
    this.copyMessage.set(null);

    // Load pushable folders
    this.registry.getPushableFolders().subscribe({
      next: (folders) => this.pushableFolders.set(folders),
    });
  }

  closeCopyModal(): void {
    this.copySource.set(null);
  }

  // Computed preview of the destination path
  copyDestPreview = computed(() => {
    const folder = this.copyDestFolder().trim();
    const name = this.copyDestName().trim();
    const tag = this.copyDestTag().trim();
    if (!name) return "";
    return folder ? `${folder}/${name}:${tag}` : `${name}:${tag}`;
  });

  executeCopy(): void {
    const src = this.copySource();
    if (!src || !this.copyDestName().trim()) return;
    this.copying.set(true);
    this.copyMessage.set(null);

    const destRepo = this.copyDestFolder().trim()
      ? `${this.copyDestFolder().trim()}/${this.copyDestName().trim()}`
      : this.copyDestName().trim();

    this.registry
      .copyImage(
        src.image.name,
        src.tag,
        destRepo,
        this.copyDestTag().trim() || undefined,
      )
      .subscribe({
        next: (res) => {
          this.copying.set(false);
          this.copyMessage.set(res.message);
          this.loadImages();
        },
        error: (err) => {
          this.copying.set(false);
          this.copyMessage.set(err?.error?.detail ?? "Copy failed");
        },
      });
  }
}
