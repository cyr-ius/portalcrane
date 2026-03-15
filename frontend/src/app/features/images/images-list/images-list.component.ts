/**
 * Portalcrane - Images List Component
 *
 * Displays registry images in flat list or hierarchical folder tree view.
 *
 * Changes (Évolution 1):
 *   - New signal selectedSource: 'local' | string (external registry ID).
 *   - When an external registry is selected the component calls
 *     RegistryService.getExternalImages() instead of getImages().
 *   - In external mode:
 *     • The list is read-only (delete / copy / retag actions are hidden).
 *     • Sort, tag-filter chips and pagination still work.
 *     • A read-only badge appears in the toolbar.
 *   - External registries are loaded once at init via ExternalRegistryService.
 *   - Switching source resets pagination and search.
 *
 * Changes (catalog availability — refactor):
 *   - _loadBrowsableRegistries() no longer calls GET catalog-check for every
 *     registry on each page visit. The backend already probes /v2/_catalog
 *     when a registry is created or updated and persists the result in the
 *     `browsable` field. The component now reads ExternalRegistryService
 *     .browsableRegistries (a computed signal filtering on browsable !== false)
 *     after the shared registry cache is populated via a single listRegistries()
 *     call.
 *   - externalRegistries is now a computed signal (was a writable signal)
 *     derived from the service cache so all components stay in sync.
 *   - checkingCatalog is now only true for the brief GET /registries call
 *     (not for N parallel catalog-check probes).
 *   - Removed unused imports: forkJoin, of, catchError.
 */
import {
  Component,
  computed,
  DestroyRef,
  inject,
  OnInit,
  signal,
} from "@angular/core";
import { takeUntilDestroyed } from "@angular/core/rxjs-interop";
import { FormsModule } from "@angular/forms";
import { Router } from "@angular/router";
import { debounceTime, distinctUntilChanged, Subject } from "rxjs";
import { AuthService } from "../../../core/services/auth.service";
import {
  ExternalRegistry,
  ExternalRegistryService,
} from "../../../core/services/external-registry.service";
import { FolderService } from "../../../core/services/folder.service";
import {
  ExternalPaginatedImages,
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

/**
 * Identifier for the image source.
 * 'local' means the embedded Portalcrane registry.
 * Any other string is the ID of a saved external registry.
 */
type SourceId = "local" | string;

@Component({
  selector: "app-images-list",
  imports: [FormsModule],
  templateUrl: "./images-list.component.html",
  styleUrl: "./images-list.component.css",
})
export class ImagesListComponent implements OnInit {
  private registry = inject(RegistryService);
  private router = inject(Router);
  private readonly folderSvc = inject(FolderService);
  private readonly authService = inject(AuthService);
  private readonly extRegSvc = inject(ExternalRegistryService);
  private readonly destroyRef = inject(DestroyRef);
  private readonly VIEW_MODE_KEY = "pc_images_view_mode";

  // ── Remote data ────────────────────────────────────────────────────────────
  data = signal<PaginatedImages | null>(null);
  loading = signal(false);
  currentPage = signal(1);
  pageSize = 20;
  searchQuery = "";

  /** Non-null error message returned by the external browse endpoint. */
  browseError = signal<string | null>(null);

  private readonly searchQuery$ = new Subject<string>();

  // ── Source selection ───────────────────────────────────────────────────────

  readonly selectedSource = signal<SourceId>("local");
  readonly externalRegistries = computed<ExternalRegistry[]>(
    () => this.extRegSvc.browsableRegistries(),
  );
  readonly checkingCatalog = signal(false);
  readonly isExternalSource = computed(
    () => this.selectedSource() !== "local",
  );

  readonly isGithubMode = computed<boolean>(() => {
    const src = this.selectedSource();
    if (src === "local") return false;
    const reg = this.externalRegistries().find((r) => r.id === src);
    if (!reg) return false;
    const host = (reg.host ?? "").toLowerCase().replace(/^https?:\/\//, "").split("/")[0];
    return host === "ghcr.io";
  });

  readonly isDockerHubMode = computed<boolean>(() => {
    const src = this.selectedSource();
    if (src === "local") return false;
    const reg = this.externalRegistries().find((r) => r.id === src);
    if (!reg) return false;
    const host = (reg.host ?? "").toLowerCase().replace(/^https?:\/\//, "").split("/")[0];
    return (
      host === "docker.io" ||
      host === "index.docker.io" ||
      host === "registry-1.docker.io"
    );
  });

  readonly activeSourceLabel = computed(() => {
    const src = this.selectedSource();
    if (src === "local") return "Local Registry";
    const reg = this.externalRegistries().find((r) => r.id === src);
    return reg ? reg.name : src;
  });

  // ── View mode ──────────────────────────────────────────────────────────────
  viewMode = signal<ViewMode>(
    (localStorage.getItem(this.VIEW_MODE_KEY) as ViewMode) ?? "flat",
  );
  allowedFolders = signal<string[]>([]);
  expandedFolders = signal<Set<string>>(new Set());
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
  sourceTagOptions = signal<string[]>([]);
  isAdmin = computed(() => this.authService.currentUser()?.is_admin ?? false);

  // ── Delete modal state ─────────────────────────────────────────────────────
  deleteTarget = signal<ImageInfo | null>(null);
  deleting = signal(false);

  // ── View modal state (external read-only details) ─────────────────────────
  viewTarget = signal<ImageInfo | null>(null);

  // ── Accessible items (folder access filter applied once) ──────────────────
  accessibleItems = computed(() => {
    const items = this.data()?.items ?? [];
    const allowed = this.allowedFolders();
    const isAdmin = this.authService.currentUser()?.is_admin ?? false;

    // In external mode there is no folder filtering
    if (this.isExternalSource()) return items;

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
      if (field === "name") return dir * a.name.localeCompare(b.name);
      return dir * (a.tag_count - b.tag_count);
    });
  });

  // ── Derived: folder tree ───────────────────────────────────────────────────
  folderTree = computed<FolderNode[]>(() => {
    const items = this.accessibleItems();
    const map = new Map<string, ImageInfo[]>();
    for (const img of items) {
      const folder = this.folderNameForImage(img.name);
      const list = map.get(folder) ?? [];
      list.push(img);
      map.set(folder, list);
    }
    return [...map.entries()]
      .sort(([a], [b]) => {
        if (a === "(root)") return -1;
        if (b === "(root)") return 1;
        return a.localeCompare(b);
      })
      .map(([name, images]) => ({ name, images }));
  });

  // ── Copy preview ───────────────────────────────────────────────────────────
  readonly copyDestPreview = computed(() => {
    const folder = this.copyDestFolder().trim();
    const name = this.copyDestName().trim();
    const tag = this.copyDestTag().trim();
    return folder && name && tag
      ? `${folder}/${name}:${tag}`
      : name && tag
        ? `${name}:${tag}`
        : "";
  });

  // ── Pagination helpers ─────────────────────────────────────────────────────

  /** Index of the first item displayed on the current page (1-based). */
  pageStart = computed(() => {
    const d = this.data();
    if (!d) return 0;
    return (d.page - 1) * d.page_size + 1;
  });

  /** Index of the last item displayed on the current page (1-based). */
  pageEnd = computed(() => {
    const d = this.data();
    if (!d) return 0;
    return Math.min(d.page * d.page_size, d.total);
  });

  /**
   * Sliding window of page numbers to display in the pagination bar.
   * Shows up to 5 pages centred around the current page (delta = 2).
   */
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

  // ── Lifecycle ──────────────────────────────────────────────────────────────

  ngOnInit(): void {
    this.setupSearchDebounce();

    // Populate the shared registry cache then derive browsable registries
    // from the persisted `browsable` field — no per-registry catalog-check
    // HTTP calls are made here.
    this._loadBrowsableRegistries();

    // Load configured folder names first so folderTree grouping is accurate.
    this.folderSvc.getFolderNames().subscribe({
      next: (names) => {
        this.configuredFolderNames.set(names);
        this._loadFoldersAndImages();
      },
      error: () => this._loadFoldersAndImages(),
    });
  }

  // ── Browsable registries (refactored — no catalog-check HTTP calls) ────────

  /**
   * Populate the shared ExternalRegistryService cache with a single
   * GET /api/external/registries call. The computed signal externalRegistries
   * (which reads browsableRegistries from the service) then reflects the
   * up-to-date browsable list automatically.
   *
   * If the cache is already populated (e.g. by the Staging component or the
   * Settings panel at an earlier point in the session), the signal is already
   * up to date and we skip the HTTP call entirely.
   */
  private _loadBrowsableRegistries(): void {
    // Cache already warm — browsableRegistries computed signal is ready.
    if (this.extRegSvc.externalRegistries().length > 0) {
      return;
    }

    this.checkingCatalog.set(true);
    this.extRegSvc.listRegistries().subscribe({
      next: (regs) => {
        // Update the shared service cache so all consumers react.
        this.extRegSvc.setRegistriesCache(regs);
        this.checkingCatalog.set(false);
      },
      error: () => {
        this.checkingCatalog.set(false);
      },
    });
  }

  // ── Search debounce pipeline ───────────────────────────────────────────────

  private setupSearchDebounce(): void {
    this.searchQuery$
      .pipe(
        debounceTime(400),
        distinctUntilChanged(),
        takeUntilDestroyed(this.destroyRef),
      )
      .subscribe(() => {
        this.currentPage.set(1);
        this.loadImages();
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

    this.registry.getPushableFolders().subscribe({
      next: (folders) => this.pushableFolders.set(folders),
    });
  }

  // ── Source selection ───────────────────────────────────────────────────────

  selectSource(sourceId: SourceId): void {
    if (this.selectedSource() === sourceId) return;
    this.selectedSource.set(sourceId);
    this.currentPage.set(1);
    this.searchQuery = "";
    this.searchQuery$.next("");
    this.browseError.set(null);
    this.data.set(null);
    this.loadImages();
  }

  // ── Data loading ───────────────────────────────────────────────────────────

  loadImages(): void {
    this.loading.set(true);
    const src = this.selectedSource();

    if (src === "local") {
      this.registry
        .getImages(this.currentPage(), this.pageSize, this.searchQuery)
        .subscribe({
          next: (data) => {
            this.data.set(data);
            this.browseError.set(null);
            const allFolders = new Set(this.folderTree().map((n) => n.name));
            this.expandedFolders.set(allFolders);
            this.loading.set(false);
          },
          error: () => this.loading.set(false),
        });
    } else {
      this.registry
        .getExternalImages(src, this.currentPage(), this.pageSize, this.searchQuery)
        .subscribe({
          next: (data: ExternalPaginatedImages) => {
            this.data.set(data);
            this.browseError.set(data.error ?? null);
            const allFolders = new Set(this.folderTree().map((n) => n.name));
            this.expandedFolders.set(allFolders);
            this.loading.set(false);
          },
          error: () => this.loading.set(false),
        });
    }
  }

  onSearch(): void {
    this.searchQuery$.next(this.searchQuery);
  }

  clearSearch(): void {
    this.searchQuery = "";
    this.searchQuery$.next("");
    this.currentPage.set(1);
    this.loadImages();
  }

  onPageSizeChange(): void {
    this.currentPage.set(1);
    this.loadImages();
  }

  goToPage(page: number): void {
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

  // ── Image helpers ──────────────────────────────────────────────────────────

  goToDetail(imageName: string): void {
    if (this.isExternalSource()) {
      const image =
        this.filteredItems().find((i) => i.name === imageName) ??
        this.accessibleItems().find((i) => i.name === imageName) ??
        null;
      this.viewTarget.set(image);
      return;
    }

    this.router.navigate(["/images/detail"], {
      queryParams: { repository: imageName },
    });
  }

  /**
   * Image display name inside its visual folder.
   * - No slash → show full name (e.g. "nginx")
   * - Known folder prefix → show only suffix (e.g. "sia/nginx" → "nginx")
   * - Unknown prefix → show full name (e.g. "cyr-ius/wireguard-ui")
   */
  imageShortName(img: ImageInfo): string {
    const idx = img.name.indexOf("/");
    if (idx === -1) return img.name;
    const prefix = img.name.substring(0, idx);
    const knownFolders = new Set(this.configuredFolderNames());
    if (!knownFolders.has(prefix)) return img.name;
    return img.name.substring(idx + 1);
  }

  private folderNameForImage(imageName: string): string {
    const slashIdx = imageName.indexOf("/");
    if (slashIdx === -1) return "(root)";
    const prefix = imageName.substring(0, slashIdx);
    return this.configuredFolderNames().includes(prefix) ? prefix : "(root)";
  }

  canDeleteImage(image: ImageInfo): boolean {
    if (this.isExternalSource()) return this.isAdmin();
    if (this.isAdmin()) return true;
    const folderName = this.folderNameForImage(image.name);
    return this.pushableFolders().includes(folderName);
  }

  canCopyImage(_image: ImageInfo): boolean {
    if (this.isExternalSource()) return this.isAdmin() || this.pushableFolders().length > 0;
    if (this.isAdmin()) return true;
    return this.pushableFolders().length > 0;
  }

  // ── Copy modal ─────────────────────────────────────────────────────────────

  openCopyModal(image: ImageInfo, tag: string): void {
    if (!this.canCopyImage(image)) return;

    this.copySource.set({ image, tag });
    this.copyDestName.set(image.name);
    this.copyDestTag.set(tag);
    this.copyDestFolder.set("");
    this.copyMessage.set(null);

    if (this.isExternalSource()) {
      const sourceId = this.selectedSource();
      if (sourceId !== "local") {
        this.registry.getExternalImageTags(sourceId, image.name).subscribe({
          next: (res) => {
            const tags = res.tags?.length ? res.tags : image.tags;
            this.sourceTagOptions.set(tags);
            this.copySource.update((s) => (s ? { ...s, tag: tags[0] ?? tag } : s));
            this.copyDestTag.set(tags[0] ?? tag);
          },
          error: () => this.sourceTagOptions.set(image.tags),
        });
      }
    } else {
      this.sourceTagOptions.set(image.tags);
    }
  }

  closeCopyModal(): void {
    this.copySource.set(null);
  }

  executeCopy(): void {
    const src = this.copySource();
    if (!src) return;
    if (!this.isExternalSource() && !this.copyDestName().trim()) return;
    this.copying.set(true);
    this.copyMessage.set(null);

    if (this.isExternalSource()) {
      const sourceRegistryId = this.selectedSource();
      if (sourceRegistryId === "local") {
        this.copying.set(false);
        return;
      }

      const sourceImage = `${src.image.name}:${src.tag}`;
      const destFolder = this.copyDestFolder().trim() || null;
      this.extRegSvc
        .startImport({
          source_registry_id: sourceRegistryId,
          source_image: sourceImage,
          dest_folder: destFolder,
        })
        .subscribe({
          next: (res) => {
            this.copying.set(false);
            this.copyMessage.set(`Import started (job ${res.job_id})`);
            this.loadImages();
          },
          error: (err) => {
            this.copying.set(false);
            this.copyMessage.set(err?.error?.detail ?? "Import failed");
          },
        });
      return;
    }

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
    if (!this.canDeleteImage(image)) return;
    this.deleteTarget.set(image);
  }

  deleteImage(): void {
    const target = this.deleteTarget();
    if (!target) return;
    this.deleting.set(true);

    if (this.isExternalSource()) {
      const sourceRegistryId = this.selectedSource();
      if (sourceRegistryId === "local") {
        this.deleting.set(false);
        return;
      }
      this.registry.deleteExternalImage(sourceRegistryId, target.name).subscribe({
        next: () => {
          this.deleteTarget.set(null);
          this.deleting.set(false);
          this.loadImages();
        },
        error: () => this.deleting.set(false),
      });
      return;
    }

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
