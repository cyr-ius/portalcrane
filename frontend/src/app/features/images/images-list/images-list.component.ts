/**
 * Portalcrane - Images List Component
 *
 * Displays registry images in flat list or hierarchical folder tree view.
 *
 * Architecture change (local registry as system V2 registry):
 *   The embedded local registry is now exposed as a hidden system entry with
 *   id="__local__". This component uses the unified V2 browse/tag-detail
 *   infrastructure for ALL sources (local + external) via the ExternalImages
 *   API. The legacy getImages() / getTagDetail() local-only paths are kept
 *   for backward compatibility but are no longer the primary browse path.
 *
 *   Source routing:
 *     - "__local__"  → local embedded registry via V2Provider (new default)
 *     - other IDs    → external registries (existing behaviour)
 *
 *   The local system entry appears in the Images source selector as
 *   "Local Registry" (first button, always visible) but is filtered out of
 *   the External Registries settings panel via userRegistries computed signal.
 *
 * Changes (Évolution 1):
 *   - New signal selectedSource: '__local__' | string (external registry ID).
 *   - selectedSource defaults to LOCAL_REGISTRY_SYSTEM_ID ('__local__').
 *   - All sources use getExternalImages() + getExternalTagDetail() internally.
 *
 * Changes (catalog availability — refactor):
 *   - _loadBrowsableRegistries() no longer calls GET catalog-check for every
 *     registry on each page visit. The backend probes /v2/_catalog when a
 *     registry is created/updated. The component reads browsableRegistries
 *     (which includes the local system entry) from ExternalRegistryService.
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
import { debounceTime, distinctUntilChanged, Subject } from "rxjs";
import { LOCAL_REGISTRY_SYSTEM_ID } from "../../../core/constants/registry.constants";
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
import { ImageDetailModalComponent } from "../image-detail-modal/image-detail-modal.component";

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
 * '__local__' means the embedded Portalcrane registry (system entry).
 * Any other string is the ID of a saved external registry.
 */
type SourceId = typeof LOCAL_REGISTRY_SYSTEM_ID | string;

@Component({
  selector: "app-images-list",
  imports: [FormsModule, ImageDetailModalComponent],
  templateUrl: "./images-list.component.html",
  styleUrl: "./images-list.component.css",
})
export class ImagesListComponent implements OnInit {
  private registry = inject(RegistryService);
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

  /**
   * Current selected source. Defaults to the local system registry.
   * The string 'local' is kept as an alias for backward compat but
   * LOCAL_REGISTRY_SYSTEM_ID ('__local__') is now the canonical value.
   */
  readonly selectedSource = signal<SourceId>(LOCAL_REGISTRY_SYSTEM_ID);

  /**
   * Browsable registries including the local system entry.
   * The local entry is always first (it has system=true, injected by backend).
   */
  readonly externalRegistries = computed<ExternalRegistry[]>(
    () => this.extRegSvc.browsableRegistries(),
  );

  readonly checkingCatalog = signal(false);

  /** True when the currently selected source is an external (non-local) registry. */
  readonly isExternalSource = computed(
    () => this.selectedSource() !== LOCAL_REGISTRY_SYSTEM_ID,
  );

  /** True when the source is the local system registry. */
  readonly isLocalSource = computed(
    () => this.selectedSource() === LOCAL_REGISTRY_SYSTEM_ID,
  );

  readonly isGithubMode = computed<boolean>(() => {
    const src = this.selectedSource();
    if (src === LOCAL_REGISTRY_SYSTEM_ID) return false;
    const reg = this.externalRegistries().find((r) => r.id === src);
    if (!reg) return false;
    const host = (reg.host ?? "").toLowerCase().replace(/^https?:\/\//, "").split("/")[0];
    return host === "ghcr.io";
  });

  readonly isDockerHubMode = computed<boolean>(() => {
    const src = this.selectedSource();
    if (src === LOCAL_REGISTRY_SYSTEM_ID) return false;
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
    if (src === LOCAL_REGISTRY_SYSTEM_ID) return "Local Registry";
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

  // ── View modal state (detail modal) ───────────────────────────────────────
  viewTarget = signal<ImageInfo | null>(null);

  // ── Accessible items (folder access filter applied once) ──────────────────
  accessibleItems = computed(() => {
    const items = this.data()?.items ?? [];
    const allowed = this.allowedFolders();
    const isAdmin = this.authService.currentUser()?.is_admin ?? false;

    // External registries have no folder filtering
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

  /**
   * True when the current user has push access on the selected external registry.
   */
  readonly canPushExternal = computed<boolean>(() => {
    const user = this.authService.currentUser();
    if (user?.is_admin) return true;
    return this.pushableFolders().length > 0;
  });

  // ── Lifecycle ──────────────────────────────────────────────────────────────

  ngOnInit(): void {
    this.setupSearchDebounce();
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

  // ── Browsable registries ───────────────────────────────────────────────────

  private _loadBrowsableRegistries(): void {
    // Cache already warm — browsableRegistries computed signal is ready.
    if (this.extRegSvc.externalRegistries().length > 0) {
      return;
    }

    this.checkingCatalog.set(true);
    this.extRegSvc.listRegistries().subscribe({
      next: (regs) => {
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

  /**
   * Load images for the currently selected source.
   *
   * All sources now use getExternalImages() via the unified V2 provider path.
   * The local system registry (__local__) is handled transparently by the backend
   * which maps it to a V2Provider pointed at localhost:5000.
   *
   * The legacy getImages() path is kept for the local source as a fallback
   * in case the system registry entry is not available (e.g. during first load).
   */
  loadImages(refreshTargetName: string | null = null): void {
    this.loading.set(true);
    const src = this.selectedSource();

    // Use the unified external images endpoint for all sources
    this.registry
      .getExternalImages(src, this.currentPage(), this.pageSize, this.searchQuery)
      .subscribe({
        next: (data: ExternalPaginatedImages) => {
          this.data.set(data);

          if (refreshTargetName) {
            const refreshed = data.items.find((i) => i.name === refreshTargetName) ?? null;
            if (refreshed) {
              this.viewTarget.set(refreshed);
            }
          }

          this.browseError.set(data.error ?? null);
          const allFolders = new Set(this.folderTree().map((n) => n.name));
          this.expandedFolders.set(allFolders);
          this.loading.set(false);
        },
        error: () => this.loading.set(false),
      });
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

  canPushOnLocalImage(image: ImageInfo): boolean {
    if (this.isAdmin()) return true;
    const folder = image.name.includes("/")
      ? image.name.split("/")[0]
      : "(root)";
    return this.pushableFolders().includes(folder);
  }

  goToDetail(name: string): void {
    const image = this.data()?.items.find((i) => i.name === name) ?? null;
    this.viewTarget.set(image);
  }

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
    // Local registry: use push permission on the image's folder
    if (this.isLocalSource()) {
      if (this.isAdmin()) return true;
      const folderName = this.folderNameForImage(image.name);
      return this.pushableFolders().includes(folderName);
    }
    // External registry: admins only
    return this.isAdmin();
  }

  canCopyImage(_image: ImageInfo): boolean {
    if (this.isExternalSource()) return this.isAdmin() || this.pushableFolders().length > 0;
    if (this.isAdmin()) return true;
    return this.pushableFolders().length > 0;
  }

  reloadImages(): void {
    this.loadImages();
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
      this.registry.getExternalImageTags(sourceId, image.name).subscribe({
        next: (res) => {
          const tags = res.tags?.length ? res.tags : image.tags;
          this.sourceTagOptions.set(tags);
          this.copySource.update((s) => (s ? { ...s, tag: tags[0] ?? tag } : s));
          this.copyDestTag.set(tags[0] ?? tag);
        },
        error: () => this.sourceTagOptions.set(image.tags),
      });
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

    // Local registry copy
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

    // Local registry delete via standard endpoint
    this.registry.deleteExternalImage(LOCAL_REGISTRY_SYSTEM_ID, target.name).subscribe({
      next: () => {
        this.deleteTarget.set(null);
        this.deleting.set(false);
        this.loadImages();
      },
      error: () => this.deleting.set(false),
    });
  }
}
