/**
 * Portalcrane - Folders Configuration Panel
 *
 * Manages registry folders (path prefixes) and their per-user pull/push permissions.
 * Admin users can create, rename (description), delete folders and manage user access.
 *
 */
import { HttpClient } from "@angular/common/http";
import {
  ChangeDetectionStrategy,
  Component,
  computed,
  inject,
  OnInit,
  signal,
} from "@angular/core";

// ── Models ────────────────────────────────────────────────────────────────────

export interface FolderPermission {
  username: string;
  can_pull: boolean;
  can_push: boolean;
}

export interface Folder {
  id: string;
  name: string;
  description: string;
  created_at: string;
  permissions: FolderPermission[];
}

// ── Component ─────────────────────────────────────────────────────────────────

@Component({
  selector: "app-folders-config-panel",
  imports: [],
  templateUrl: "./folders-config-panel.component.html",
  styleUrl: "./folders-config-panel.component.css",
  changeDetection: ChangeDetectionStrategy.OnPush,
})
export class FoldersConfigPanel implements OnInit {
  private http = inject(HttpClient);

  // ── Folders list ───────────────────────────────────────────────────────────
  readonly folders = signal<Folder[]>([]);
  readonly loading = signal(false);
  readonly error = signal<string | null>(null);

  // ── Create form ────────────────────────────────────────────────────────────
  readonly showCreateForm = signal(false);
  readonly newName = signal("");
  readonly newDescription = signal("");
  readonly creating = signal(false);
  readonly createError = signal<string | null>(null);

  /** True when the create form has valid inputs. */
  readonly canCreate = computed(() => this.newName().trim().length > 0);

  // ── Expanded folder (shows permissions detail) ─────────────────────────────
  readonly expandedId = signal<string | null>(null);

  // ── Edit description ───────────────────────────────────────────────────────
  readonly editingDescId = signal<string | null>(null);
  readonly editDesc = signal("");
  readonly savingDesc = signal(false);

  // ── Delete folder ──────────────────────────────────────────────────────────
  readonly deletingId = signal<string | null>(null);

  // ── Add permission form (per folder) ──────────────────────────────────────
  readonly addPermFolderId = signal<string | null>(null);
  readonly addPermUsername = signal("");
  readonly addPermCanPull = signal(false);
  readonly addPermCanPush = signal(false);
  readonly savingPerm = signal(false);
  readonly permError = signal<string | null>(null);

  /** True when add-permission form has valid inputs. */
  readonly canAddPerm = computed(
    () => this.addPermUsername().trim().length > 0,
  );

  ngOnInit(): void {
    this.loadFolders();
  }

  /** Fetch the folder list from the backend. */
  loadFolders(): void {
    this.loading.set(true);
    this.error.set(null);
    this.http.get<Folder[]>("/api/folders").subscribe({
      next: (folders) => {
        this.folders.set(folders);
        this.loading.set(false);
      },
      error: (err) => {
        this.error.set(err?.error?.detail ?? "Failed to load folders");
        this.loading.set(false);
      },
    });
  }

  // ── Create folder ──────────────────────────────────────────────────────────

  openCreateForm(): void {
    this.newName.set("");
    this.newDescription.set("");
    this.createError.set(null);
    this.showCreateForm.set(true);
  }

  cancelCreate(): void {
    this.showCreateForm.set(false);
  }

  createFolder(): void {
    if (!this.canCreate()) return;
    this.creating.set(true);
    this.createError.set(null);
    this.http
      .post<Folder>("/api/folders", {
        name: this.newName().trim().toLowerCase(),
        description: this.newDescription().trim(),
      })
      .subscribe({
        next: (folder) => {
          this.folders.update((list) => [...list, folder]);
          this.showCreateForm.set(false);
          this.creating.set(false);
        },
        error: (err) => {
          this.createError.set(err?.error?.detail ?? "Failed to create folder");
          this.creating.set(false);
        },
      });
  }

  // ── Toggle expanded view ───────────────────────────────────────────────────

  toggleExpand(folderId: string): void {
    this.expandedId.update((id) => (id === folderId ? null : folderId));
    // Reset add-permission form when collapsing
    if (this.expandedId() !== folderId) {
      this.addPermFolderId.set(null);
    }
  }

  // ── Edit description ───────────────────────────────────────────────────────

  startEditDesc(folder: Folder): void {
    this.editingDescId.set(folder.id);
    this.editDesc.set(folder.description);
  }

  cancelEditDesc(): void {
    this.editingDescId.set(null);
  }

  saveDesc(folderId: string): void {
    this.savingDesc.set(true);
    this.http
      .patch<Folder>(`/api/folders/${folderId}`, {
        description: this.editDesc().trim(),
      })
      .subscribe({
        next: (updated) => {
          this.folders.update((list) =>
            list.map((f) => (f.id === updated.id ? updated : f)),
          );
          this.editingDescId.set(null);
          this.savingDesc.set(false);
        },
        error: () => {
          this.savingDesc.set(false);
        },
      });
  }

  // ── Delete folder ──────────────────────────────────────────────────────────

  deleteFolder(folderId: string): void {
    this.deletingId.set(folderId);
    this.http.delete(`/api/folders/${folderId}`).subscribe({
      next: () => {
        this.folders.update((list) => list.filter((f) => f.id !== folderId));
        if (this.expandedId() === folderId) this.expandedId.set(null);
        this.deletingId.set(null);
      },
      error: () => {
        this.deletingId.set(null);
      },
    });
  }

  // ── Permissions ────────────────────────────────────────────────────────────

  openAddPerm(folderId: string): void {
    this.addPermFolderId.set(folderId);
    this.addPermUsername.set("");
    this.addPermCanPull.set(false);
    this.addPermCanPush.set(false);
    this.permError.set(null);
  }

  cancelAddPerm(): void {
    this.addPermFolderId.set(null);
  }

  savePerm(folderId: string): void {
    if (!this.canAddPerm()) return;
    this.savingPerm.set(true);
    this.permError.set(null);
    this.http
      .put<Folder>(`/api/folders/${folderId}/permissions`, {
        username: this.addPermUsername().trim(),
        can_pull: this.addPermCanPull(),
        can_push: this.addPermCanPush(),
      })
      .subscribe({
        next: (updated) => {
          this.folders.update((list) =>
            list.map((f) => (f.id === updated.id ? updated : f)),
          );
          this.addPermFolderId.set(null);
          this.savingPerm.set(false);
        },
        error: (err) => {
          this.permError.set(err?.error?.detail ?? "Failed to save permission");
          this.savingPerm.set(false);
        },
      });
  }

  updatePerm(
    folderId: string,
    username: string,
    can_pull: boolean,
    can_push: boolean,
  ): void {
    this.http
      .put<Folder>(`/api/folders/${folderId}/permissions`, {
        username,
        can_pull,
        can_push,
      })
      .subscribe({
        next: (updated) => {
          this.folders.update((list) =>
            list.map((f) => (f.id === updated.id ? updated : f)),
          );
        },
      });
  }

  removePerm(folderId: string, username: string): void {
    this.http
      .delete(`/api/folders/${folderId}/permissions/${username}`)
      .subscribe({
        next: () => {
          this.folders.update((list) =>
            list.map((f) =>
              f.id === folderId
                ? {
                    ...f,
                    permissions: f.permissions.filter(
                      (p) => p.username !== username,
                    ),
                  }
                : f,
            ),
          );
        },
      });
  }

  // ── Utilities ──────────────────────────────────────────────────────────────

  formatDate(iso: string): string {
    if (!iso) return "—";
    return new Date(iso).toLocaleDateString("en-GB", {
      day: "2-digit",
      month: "short",
      year: "numeric",
    });
  }
}
