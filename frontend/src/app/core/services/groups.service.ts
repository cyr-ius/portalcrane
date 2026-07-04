import { HttpClient } from "@angular/common/http";
import { inject, Injectable } from "@angular/core";
import { Observable } from "rxjs";

/** A named set of usernames used to grant folder permissions. */
export interface Group {
  id: string;
  name: string;
  description: string;
  created_at: string;
  members: string[];
}

@Injectable({ providedIn: "root" })
export class GroupsService {
  private readonly http = inject(HttpClient);

  /** Fetch all groups (admin only). */
  getGroups(): Observable<Group[]> {
    return this.http.get<Group[]>("/api/groups");
  }

  createGroup(name: string, description: string): Observable<Group> {
    return this.http.post<Group>("/api/groups", {
      name: name.trim(),
      description: description.trim(),
    });
  }

  updateGroup(
    groupId: string,
    body: { name?: string; description?: string },
  ): Observable<Group> {
    return this.http.patch<Group>(`/api/groups/${groupId}`, body);
  }

  deleteGroup(groupId: string): Observable<void> {
    return this.http.delete<void>(`/api/groups/${groupId}`);
  }

  addMember(groupId: string, username: string): Observable<Group> {
    return this.http.put<Group>(`/api/groups/${groupId}/members`, {
      username: username.trim(),
    });
  }

  removeMember(groupId: string, username: string): Observable<void> {
    return this.http.delete<void>(`/api/groups/${groupId}/members/${username}`);
  }
}
