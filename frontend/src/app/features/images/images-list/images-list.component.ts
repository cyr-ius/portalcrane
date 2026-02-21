import { Component, signal, inject, OnInit, computed } from "@angular/core";
import { CommonModule } from "@angular/common";
import { RouterLink } from "@angular/router";
import { FormsModule } from "@angular/forms";
import {
  RegistryService,
  PaginatedImages,
  ImageInfo,
} from "../../../core/services/registry.service";

@Component({
  selector: "app-images-list",
  imports: [CommonModule, RouterLink, FormsModule],
  template: `
    <div class="p-4">
      <!-- Header -->
      <div class="d-flex align-items-center justify-content-between mb-4">
        <div>
          <h2 class="fw-bold mb-1">Images</h2>
          <p class="text-muted small mb-0">
            Browse and manage your registry images
          </p>
        </div>
      </div>

      <!-- Search & Controls -->
      <div class="card border-0 mb-3">
        <div class="card-body py-2">
          <div class="row g-2 align-items-center">
            <div class="col">
              <div class="input-group">
                <span class="input-group-text"
                  ><i class="bi bi-search"></i
                ></span>
                <input
                  type="text"
                  class="form-control"
                  placeholder="Search images..."
                  [(ngModel)]="searchQuery"
                  (ngModelChange)="onSearch()"
                />
                @if (searchQuery) {
                  <button
                    class="btn btn-outline-secondary"
                    (click)="clearSearch()"
                  >
                    <i class="bi bi-x-lg"></i>
                  </button>
                }
              </div>
            </div>
            <div class="col-auto">
              <select
                class="form-select form-select-sm"
                [(ngModel)]="pageSize"
                (ngModelChange)="onPageSizeChange()"
              >
                <option [value]="10">10 per page</option>
                <option [value]="20">20 per page</option>
                <option [value]="50">50 per page</option>
                <option [value]="100">100 per page</option>
              </select>
            </div>
            <div class="col-auto">
              <button
                class="btn btn-sm btn-outline-secondary"
                (click)="loadImages()"
              >
                <i class="bi bi-arrow-clockwise"></i>
              </button>
            </div>
          </div>
        </div>
      </div>

      <!-- Table -->
      <div class="card border-0">
        @if (loading()) {
          <div class="d-flex justify-content-center py-5">
            <div class="spinner-border text-primary"></div>
          </div>
        } @else if (data()?.items?.length === 0) {
          <div class="text-center py-5 text-muted">
            <i class="bi bi-inbox display-4 d-block mb-3"></i>
            No images found in the registry
          </div>
        } @else {
          <div class="table-responsive">
            <table class="table table-hover mb-0">
              <thead>
                <tr>
                  <th class="border-0 text-muted fw-semibold small">
                    REPOSITORY
                  </th>
                  <th class="border-0 text-muted fw-semibold small">TAGS</th>
                  <th class="border-0 text-muted fw-semibold small">
                    TAG LIST
                  </th>
                  <th class="border-0 text-muted fw-semibold small text-end">
                    ACTIONS
                  </th>
                </tr>
              </thead>
              <tbody>
                @for (image of data()?.items; track image.name) {
                  <tr>
                    <td class="align-middle">
                      <div class="d-flex align-items-center gap-2">
                        <div class="image-icon">
                          <i class="bi bi-box-seam"></i>
                        </div>
                        <a
                          [routerLink]="['/images', image.name]"
                          class="fw-semibold text-decoration-none link-body-emphasis"
                        >
                          {{ image.name }}
                        </a>
                      </div>
                    </td>
                    <td class="align-middle">
                      <span class="badge bg-primary-subtle text-primary">{{
                        image.tag_count
                      }}</span>
                    </td>
                    <td class="align-middle">
                      <div class="d-flex flex-wrap gap-1">
                        @for (tag of image.tags.slice(0, 4); track tag) {
                          <span
                            class="badge bg-secondary-subtle text-secondary font-monospace"
                            >{{ tag }}</span
                          >
                        }
                        @if (image.tags.length > 4) {
                          <span class="badge bg-secondary-subtle text-muted"
                            >+{{ image.tags.length - 4 }} more</span
                          >
                        }
                      </div>
                    </td>
                    <td class="align-middle text-end">
                      <div class="d-flex gap-1 justify-content-end">
                        <a
                          [routerLink]="['/images', image.name]"
                          class="btn btn-sm btn-outline-primary"
                        >
                          <i class="bi bi-eye"></i>
                        </a>
                        <button
                          class="btn btn-sm btn-outline-danger"
                          (click)="confirmDeleteImage(image)"
                          [title]="'Delete ' + image.name"
                        >
                          <i class="bi bi-trash"></i>
                        </button>
                      </div>
                    </td>
                  </tr>
                }
              </tbody>
            </table>
          </div>

          <!-- Pagination -->
          <div
            class="card-footer border-0 d-flex align-items-center justify-content-between py-2"
          >
            <span class="text-muted small">
              Showing {{ pageStart() }}–{{ pageEnd() }} of
              {{ data()?.total }} images
            </span>
            <nav>
              <ul class="pagination pagination-sm mb-0">
                <li class="page-item" [class.disabled]="currentPage() === 1">
                  <button
                    class="page-link"
                    (click)="goToPage(currentPage() - 1)"
                  >
                    <i class="bi bi-chevron-left"></i>
                  </button>
                </li>
                @for (p of pages(); track p) {
                  <li class="page-item" [class.active]="p === currentPage()">
                    <button class="page-link" (click)="goToPage(p)">
                      {{ p }}
                    </button>
                  </li>
                }
                <li
                  class="page-item"
                  [class.disabled]="currentPage() === data()?.total_pages"
                >
                  <button
                    class="page-link"
                    (click)="goToPage(currentPage() + 1)"
                  >
                    <i class="bi bi-chevron-right"></i>
                  </button>
                </li>
              </ul>
            </nav>
          </div>
        }
      </div>
    </div>

    <!-- Delete confirmation modal -->
    @if (deleteTarget()) {
      <div class="modal-backdrop fade show"></div>
      <div class="modal show d-block" tabindex="-1">
        <div class="modal-dialog modal-dialog-centered">
          <div class="modal-content border-0 shadow">
            <div class="modal-header border-0">
              <h5 class="modal-title text-danger">
                <i class="bi bi-exclamation-triangle-fill me-2"></i>
                Delete Image
              </h5>
              <button
                class="btn-close"
                (click)="deleteTarget.set(null)"
              ></button>
            </div>
            <div class="modal-body">
              <p>
                Are you sure you want to delete
                <strong>{{ deleteTarget()?.name }}</strong> and all its tags?
              </p>
              <p class="text-muted small">This action cannot be undone.</p>
            </div>
            <div class="modal-footer border-0">
              <button
                class="btn btn-outline-secondary"
                (click)="deleteTarget.set(null)"
              >
                Cancel
              </button>
              <button
                class="btn btn-danger"
                (click)="deleteImage()"
                [disabled]="deleting()"
              >
                @if (deleting()) {
                  <span class="spinner-border spinner-border-sm me-1"></span>
                }
                Delete
              </button>
            </div>
          </div>
        </div>
      </div>
    }
  `,
  styles: [
    `
      .card {
        background: var(--pc-card-bg);
        border-radius: 12px;
      }
      .image-icon {
        width: 32px;
        height: 32px;
        border-radius: 8px;
        background: var(--pc-accent-soft);
        color: var(--pc-accent);
        display: flex;
        align-items: center;
        justify-content: center;
        font-size: 0.875rem;
        flex-shrink: 0;
      }
      .table > :not(caption) > * > * {
        background: transparent;
        padding: 0.875rem 1rem;
      }
      thead th {
        font-size: 0.7rem;
        letter-spacing: 0.05em;
      }
    `,
  ],
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
