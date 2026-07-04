import { HttpClient } from "@angular/common/http";
import { inject, Injectable, signal } from "@angular/core";
import { Observable } from "rxjs";
import { RegistryService } from "./registry.service";

export interface FolderPermission {
  group_id: string;
  /** Display name resolved server-side; null when the group was deleted. */
  group_name: string | null;
  can_pull: boolean;
  /** Authorizes pulling images INTO this folder FROM an external registry. */
  can_pull_external: boolean;
  can_push: boolean;
  /** Authorizes pushing this folder's images OUT to an external registry. */
  can_push_external: boolean;
}

export interface Folder {
  id: string;
  name: string;
  description: string;
  created_at: string;
  permissions: FolderPermission[];
}

@Injectable({ providedIn: "root" })
export class FolderService {
  private readonly http = inject(HttpClient);
  private registry = inject(RegistryService);

  private _allowedPullFolders = signal<string[]>([]);
  readonly allowedPullFolders = this._allowedPullFolders.asReadonly();

  private _allowedPushFolders = signal<string[]>([]);
  readonly allowedPushFolders = this._allowedPushFolders.asReadonly();

  private _allowedExternalPullFolders = signal<string[]>([]);
  readonly allowedExternalPullFolders =
    this._allowedExternalPullFolders.asReadonly();

  private _allowedExternalPushFolders = signal<string[]>([]);
  readonly allowedExternalPushFolders =
    this._allowedExternalPushFolders.asReadonly();

  loadPermissions() {
    this.registry.getMyFolders().subscribe({
      next: (folders) => this._allowedPullFolders.set(folders),
    });
    this.registry.getPushableFolders().subscribe({
      next: (folders) => this._allowedPushFolders.set(folders),
    });
    this.registry.getExternalPullableFolders().subscribe({
      next: (folders) => this._allowedExternalPullFolders.set(folders),
    });
    this.registry.getExternalPushableFolders().subscribe({
      next: (folders) => this._allowedExternalPushFolders.set(folders),
    });
  }

  getFolders(): Observable<Folder[]> {
    return this.http.get<Folder[]>("/api/folders");
  }

  getFolderNames(): Observable<string[]> {
    return this.http.get<string[]>("/api/folders/names");
  }

  createFolder(name: string, description: string): Observable<Folder> {
    return this.http.post<Folder>("/api/folders", {
      name: name.trim().toLowerCase(),
      description: description.trim(),
    });
  }

  saveDesc(folderId: string, description: string): Observable<Folder> {
    return this.http.patch<Folder>(`/api/folders/${folderId}`, {
      description: description.trim(),
    });
  }

  deleteFolder(folderId: string): Observable<void> {
    return this.http.delete<void>(`/api/folders/${folderId}`);
  }

  savePerm(
    folderId: string,
    groupId: string,
    can_pull: boolean,
    can_pull_external: boolean,
    can_push: boolean,
    can_push_external: boolean,
  ): Observable<Folder> {
    return this.http.put<Folder>(`/api/folders/${folderId}/permissions`, {
      group_id: groupId,
      can_pull: can_pull,
      can_pull_external: can_pull_external,
      can_push: can_push,
      can_push_external: can_push_external,
    });
  }

  removePerm(folderId: string, groupId: string): Observable<void> {
    return this.http.delete<void>(
      `/api/folders/${folderId}/permissions/${groupId}`,
    );
  }
}
