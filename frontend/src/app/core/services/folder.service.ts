import { HttpClient } from "@angular/common/http";
import { inject, Injectable } from "@angular/core";
import { Observable } from "rxjs";

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

    /** Fetch user */
    getUserSummaries(): Observable<UserSummary[]> {
        return this.http.get<UserSummary[]>("/api/auth/users")
    }

    /** Fetch the folder list from the backend. */
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
