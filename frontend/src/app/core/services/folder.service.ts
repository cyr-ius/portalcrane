import { HttpClient } from "@angular/common/http";
import { inject, Injectable, signal } from "@angular/core";
import { Observable } from "rxjs";
import { RegistryService } from "./registry.service";

export interface UserSummary {
  id: string;
  username: string;
}

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

@Injectable({ providedIn: "root" })
export class FolderService {
    private readonly http = inject(HttpClient);
    private registry = inject(RegistryService);

    private _allowedPullFolders = signal<string[]>([]);
    readonly allowedPullFolders = this._allowedPullFolders.asReadonly();

    private _allowedPushFolders = signal<string[]>([]);
    readonly allowedPushFolders = this._allowedPushFolders.asReadonly();


    getUserSummaries(): Observable<UserSummary[]> {
        return this.http.get<UserSummary[]>("/api/auth/users")
    }

    loadPermissions() {
        this.registry.getMyFolders().subscribe({
        next: (folders) => this._allowedPullFolders.set(folders),
        });
        this.registry.getPushableFolders().subscribe({
        next: (folders) => this._allowedPushFolders.set(folders),
        });
    }

    getFolders(): Observable<Folder[]> {
        return this.http.get<Folder[]>("/api/folders");
    }

    getFolderNames(): Observable<string[]> {
        return this.http.get<string[]>("/api/folders/names");
    }

    createFolder(name: string , description: string): Observable<Folder> {
        return  this.http.post<Folder>("/api/folders", {
        name: name.trim().toLowerCase(),
        description: description.trim(),
        })
    }

    saveDesc(folderId: string, description: string): Observable<Folder> {
        return this.http.patch<Folder>(`/api/folders/${folderId}`, {
        description: description.trim(),
      })
    }

    deleteFolder(folderId: string): Observable<void>  {
        return this.http.delete<void>(`/api/folders/${folderId}`)
    }

    savePerm(folderId: string, username: string, can_pull: boolean, can_push: boolean): Observable<Folder> {
        return this.http.put<Folder>(`/api/folders/${folderId}/permissions`, {
        username: username.trim(),
        can_pull: can_pull,
        can_push: can_push,
      })
    }

    removePerm(folderId: string, username: string): Observable<void>  {
        return this.http.delete<void>(`/api/folders/${folderId}/permissions/${username}`)
    }

}
