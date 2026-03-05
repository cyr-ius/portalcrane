/**
 * Portalcrane - Images List Component
 * Displays registry images in flat list or hierarchical folder tree view.
 *
 * Navigation to the image detail page now uses /images/detail with a
 * queryParam ?repository=... instead of /images/:repository path segment,
 * to avoid %2F encoding issues with reverse proxies.
 *
 * Changes vs original:
 *  - configuredFolderNames signal: list of real Portalcrane folder names
 *    fetched from GET /api/folders/names (accessible to all authenticated users).
 *  - folderTree now applies the same logic as the backend get_folder_for_path():
 *      * first segment matches a configured folder → visual folder = that segment
 *      * first segment unknown OR no slash → visual folder = "(root)"
 *    This ensures the visual tree reflects actual permission boundaries.
 *  - imageShortName: unknown prefix → full name shown, known prefix → suffix only.
 */
import { HttpClient } from "@angular/common/http";
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
  private readonly http = inject(HttpClient);
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

  /**
   * Names of all Portalcrane folders configured by the admin.
   * Fetched from GET /api/folders/names (accessible to all authenticated users).
   * Used by folderTree to apply the same grouping logic as the backend:
   *   - known prefix  → own visual folder
   *   - unknown prefix OR no slash → "(root)"
   */
  configuredFolderNames = signal<string[]>([]);

  // ── Client-side sort & filter state ────────────────────────────────────────
  sortField = signal<SortField>("name");
  sortDir = signal<SortDir>("asc");
  tagFilter = signal<TagFilter>("all");

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

  // ── Delete modal state ─────────────────────────────────────────────────────
  deleteTarget = signal<ImageInfo | null>(null);
  deleting = signal(false);

  // ── Accessible items (folder access filter applied once) ──────────────────
  accessibleItems = computed(() => {
    const items = this.data()?.items ?? [];
    const allowed = this.allowedFolders();
    const isAdmin = this.authService.currentUser()?.is_admin ?? false;

    // Empty allowedFolders means admin or no folders configured → no filter
    if (isAdmin || allowed.length === 0) return items;

    return items.filter((img) => {
      const folder = img.name.includes("/") ? img.name.split("/")[0] : "(root)";
      return allowed.includes(folder);
    });
  });

  // ── Derived: flat list (uses accessibleItems) ─────────────────────────────
  filteredItems = computed(() => {
    const items = this.accessibleItems();
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

  // ── Derived: folder tree (uses accessibleItems + configuredFolderNames) ───
  folderTree = computed<FolderNode[]>(() => {
    const items = this.accessibleItems();
    const knownFolders = new Set(this.configuredFolderNames());
    const map = new Map<string, ImageInfo[]>();

    for (const img of items) {
      const slashIdx = img.name.indexOf("/");
      let visualFolder: string;

      if (slashIdx === -1) {
        // No slash → always root (e.g. "nginx")
        visualFolder = "(root)";
      } else {
        const prefix = img.name.substring(0, slashIdx);
        // Apply the same logic as the backend get_folder_for_path():
        // known prefix → own visual folder, unknown prefix → root
        visualFolder = knownFolders.has(prefix) ? prefix : "(root)";
      }

      if (!map.has(visualFolder)) map.set(visualFolder, []);
      map.get(visualFolder)!.push(img);
    }

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

  // ── Copy destination preview ───────────────────────────────────────────────
  copyDestPreview = computed(() => {
    const folder = this.copyDestFolder().trim();
    const name = this.copyDestName().trim();
    const tag = this.copyDestTag().trim();
    if (!name) return "";
    return folder ? `${folder}/${name}:${tag}` : `${name}:${tag}`;
  });

  // ── Lifecycle ──────────────────────────────────────────────────────────────
  ngOnInit() {
    // Load configured folder names first so folderTree grouping is accurate
    this.http.get<string[]>("/api/folders/names").subscribe({
      next: (names) => {
        this.configuredFolderNames.set(names);
        this._loadFoldersAndImages();
      },
      error: () => this._loadFoldersAndImages(),
    });
  }

  private _loadFoldersAndImages(): void {
    this.registry.getMyFolders().subscribe({
      next: (folders) => {
        this.allowedFolders.set(folders);
        this.loadImages();
      },
      error: () => this.loadImages(),
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

  // ── Image name helpers ─────────────────────────────────────────────────────

  /**
   * Navigate to the image detail page using queryParams.
   * Avoids %2F encoding issues with reverse proxies.
   */
  goToDetail(imageName: string): void {
    this.router.navigate(["/images/detail"], {
      queryParams: { repository: imageName },
    });
  }

  /**
   * Image display name inside its visual folder.
   * - No slash → show full name (e.g. "nginx")
   * - Known folder prefix → show only suffix (e.g. "sia/nginx" → "nginx")
   * - Unknown prefix → show full name so the user sees "cyr-ius/wireguard-ui"
   *   (it lives in root visually but the registry path must stay visible)
   */
  imageShortName(img: ImageInfo): string {
    const idx = img.name.indexOf("/");
    if (idx === -1) return img.name;
    const prefix = img.name.substring(0, idx);
    const knownFolders = new Set(this.configuredFolderNames());
    if (!knownFolders.has(prefix)) return img.name;
    return img.name.substring(idx + 1);
  }

  // ── Copy modal ─────────────────────────────────────────────────────────────

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

    // Load pushable folders for non-admin users
    this.registry.getPushableFolders().subscribe({
      next: (folders) => this.pushableFolders.set(folders),
    });
  }

  closeCopyModal(): void {
    this.copySource.set(null);
  }

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

  // ── Delete modal ───────────────────────────────────────────────────────────

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
}
