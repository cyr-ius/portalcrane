/**
 * Portalcrane - Folders Configuration Panel
 *
 * Displays and manages registry folders (path prefixes) with per-group permissions.
 *
 * Special handling for the __root__ folder:
 *  - Always present (created automatically at backend startup).
 *  - Displayed with a distinct visual style and a descriptive label.
 *  - Cannot be deleted — the delete button is hidden for this folder.
 *  - Description edit is still allowed so admins can customise the label.
 *
 * Forms use Angular Signal Forms (form / FormField):
 *   1. folderForm  — create a new folder (name + description)
 *   2. permForm    — add a group permission to a folder (groupId + can_pull + can_push)
 */
import { Component, inject, OnInit, signal } from "@angular/core";
import { form, FormField, required, submit } from "@angular/forms/signals";
import { TranslatePipe, TranslateService } from "@ngx-translate/core";
import { firstValueFrom } from "rxjs";
import { Folder, FolderService } from "../../../core/services/folder.service";
import { Group, GroupsService } from "../../../core/services/groups.service";

/** Reserved name for the root namespace folder. */
const ROOT_FOLDER_NAME = "__root__";

/** Shape of the folder creation form model. */
interface FolderFormModel {
  name: string;
  description: string;
}

/** Shape of the add-permission form model. */
interface PermFormModel {
  groupId: string;
  canPull: boolean;
  canPush: boolean;
}

@Component({
  selector: "app-folders-config-panel",
  // FormField is required for [formField] bindings in the template
  imports: [FormField, TranslatePipe],
  templateUrl: "./folders-config-panel.component.html",
  styleUrl: "./folders-config-panel.component.css",
})
export class FoldersConfigPanel implements OnInit {
  private readonly folderSvc = inject(FolderService);
  private readonly groupsSvc = inject(GroupsService);
  private readonly translate = inject(TranslateService);

  // ── Folder list ────────────────────────────────────────────────────────────
  readonly folders = signal<Folder[]>([]);
  readonly loading = signal(false);
  readonly error = signal<string | null>(null);

  // ── Group list (for the group selector in the permission form) ─────────────
  readonly groups = signal<Group[]>([]);

  // ── Expanded folder (accordion) ────────────────────────────────────────────
  readonly expandedId = signal<string | null>(null);

  // ── Edit description state ─────────────────────────────────────────────────
  readonly editingDescId = signal<string | null>(null);
  readonly editDesc = signal("");
  readonly savingDesc = signal(false);

  // ── Delete folder state ────────────────────────────────────────────────────
  readonly deletingId = signal<string | null>(null);

  // ── Create folder form ─────────────────────────────────────────────────────
  readonly showCreateForm = signal(false);
  readonly creating = signal(false);
  readonly createError = signal<string | null>(null);

  private readonly folderInit: FolderFormModel = { name: "", description: "" };
  readonly folderModel = signal<FolderFormModel>({ ...this.folderInit });

  /**
   * Signal Form for folder creation.
   * Only name is required; description is optional.
   */
  readonly folderForm = form(this.folderModel, (p) => {
    required(p.name);
  });

  // ── Add-permission form ────────────────────────────────────────────────────
  /** Which folder is currently showing the add-permission form (null = none). */
  readonly addPermFolderId = signal<string | null>(null);
  readonly savingPerm = signal(false);
  readonly permError = signal<string | null>(null);

  private readonly permInit: PermFormModel = {
    groupId: "",
    canPull: false,
    canPush: false,
  };
  readonly permModel = signal<PermFormModel>({ ...this.permInit });

  /**
   * Signal Form for adding a group permission.
   * The group is required; the checkboxes default to false.
   */
  readonly permForm = form(this.permModel, (p) => {
    required(p.groupId);
  });

  // ──────────────────────────────────────────────────────────────────────────

  ngOnInit(): void {
    this.loadFolders();
    this.loadGroups();
  }

  /** Fetch the folder list from the backend. */
  loadFolders(): void {
    this.loading.set(true);
    this.error.set(null);
    this.folderSvc.getFolders().subscribe({
      next: (folders) => {
        // Sort: __root__ always first, then alphabetical
        const sorted = [...folders].sort((a, b) => {
          if (a.name === ROOT_FOLDER_NAME) return -1;
          if (b.name === ROOT_FOLDER_NAME) return 1;
          return a.name.localeCompare(b.name);
        });
        this.folders.set(sorted);
        this.loading.set(false);
      },
      error: (err) => {
        this.error.set(
          err?.error?.detail ?? this.translate.instant("FOLDERS.ERR_LOAD"),
        );
        this.loading.set(false);
      },
    });
  }

  /** Fetch the group list so the permission form can offer a group selector. */
  loadGroups(): void {
    this.groupsSvc.getGroups().subscribe({
      next: (groups) => this.groups.set(groups),
      error: () => this.groups.set([]), // Silently ignore when not admin
    });
  }

  // ── Root folder helpers ────────────────────────────────────────────────────

  /**
   * Returns true when the given folder is the reserved __root__ folder.
   * Used in the template to conditionally hide destructive actions.
   */
  isRootFolder(folder: Folder): boolean {
    return folder.name === ROOT_FOLDER_NAME;
  }

  // ── Create folder ──────────────────────────────────────────────────────────

  openCreateForm(): void {
    this.folderModel.set({ ...this.folderInit });
    this.createError.set(null);
    this.showCreateForm.set(true);
  }

  cancelCreate(): void {
    this.showCreateForm.set(false);
  }

  /** Submit the folder creation form via Signal Forms. */
  createFolder(): void {
    submit(this.folderForm, async (f) => {
      const { name, description } = f().value();
      this.creating.set(true);
      this.createError.set(null);

      try {
        const folder = await firstValueFrom(
          this.folderSvc.createFolder(
            name!.trim().toLowerCase(),
            description?.trim() ?? "",
          ),
        );
        // Insert new folder in alphabetical order (after __root__)
        this.folders.update((list) => {
          const updated = [...list, folder].sort((a, b) => {
            if (a.name === ROOT_FOLDER_NAME) return -1;
            if (b.name === ROOT_FOLDER_NAME) return 1;
            return a.name.localeCompare(b.name);
          });
          return updated;
        });
        this.showCreateForm.set(false);
        f().reset({ ...this.folderInit });
      } catch (err: unknown) {
        const httpErr = err as { error?: { detail?: string } };
        this.createError.set(
          httpErr?.error?.detail ??
            this.translate.instant("FOLDERS.ERR_CREATE"),
        );
      } finally {
        this.creating.set(false);
      }
    });
  }

  // ── Expand / collapse ──────────────────────────────────────────────────────

  toggleExpand(folderId: string): void {
    this.expandedId.update((id) => (id === folderId ? null : folderId));
    // Close the permission form when the folder collapses
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
    this.folderSvc.saveDesc(folderId, this.editDesc().trim()).subscribe({
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
    this.folderSvc.deleteFolder(folderId).subscribe({
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

  // ── Add permission ─────────────────────────────────────────────────────────

  openAddPerm(folderId: string): void {
    this.addPermFolderId.set(folderId);
    this.permModel.set({ ...this.permInit });
    this.permError.set(null);
  }

  cancelAddPerm(): void {
    this.addPermFolderId.set(null);
  }

  /** Submit the add-permission form via Signal Forms. */
  savePerm(folderId: string): void {
    submit(this.permForm, async (f) => {
      const { groupId, canPull, canPush } = f().value();
      this.savingPerm.set(true);
      this.permError.set(null);

      try {
        const updated = await firstValueFrom(
          this.folderSvc.savePerm(
            folderId,
            groupId!.trim(),
            canPull ?? false,
            canPush ?? false,
          ),
        );
        this.folders.update((list) =>
          list.map((folder) => (folder.id === updated.id ? updated : folder)),
        );
        this.addPermFolderId.set(null);
        f().reset({ ...this.permInit });
      } catch (err: unknown) {
        const httpErr = err as { error?: { detail?: string } };
        this.permError.set(
          httpErr?.error?.detail ?? this.translate.instant("FOLDERS.ERR_PERM"),
        );
      } finally {
        this.savingPerm.set(false);
      }
    });
  }

  // ── Update existing permission (inline checkboxes in the table) ────────────

  updatePerm(
    folderId: string,
    groupId: string,
    can_pull: boolean,
    can_push: boolean,
  ): void {
    this.folderSvc.savePerm(folderId, groupId, can_pull, can_push).subscribe({
      next: (updated) => {
        this.folders.update((list) =>
          list.map((f) => (f.id === updated.id ? updated : f)),
        );
      },
    });
  }

  // ── Remove permission ──────────────────────────────────────────────────────

  removePerm(folderId: string, groupId: string): void {
    this.folderSvc.removePerm(folderId, groupId).subscribe({
      next: () => {
        this.folders.update((list) =>
          list.map((f) =>
            f.id === folderId
              ? {
                  ...f,
                  permissions: f.permissions.filter(
                    (p) => p.group_id !== groupId,
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
