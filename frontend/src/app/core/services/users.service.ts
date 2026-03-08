import { HttpClient } from "@angular/common/http";
import { inject, Injectable } from "@angular/core";
import { Observable } from "rxjs";

export type AuthSource = "local" | "oidc";

export interface LocalUser {
  id: string;
  username: string;
  is_admin: boolean;
  created_at: string;
  auth_source: AuthSource;
}

export interface UpdateUser {
    password?: string;
    is_admin: boolean;
}

@Injectable({ providedIn: "root" })
export class UsersService {
    private http = inject(HttpClient);

    /** Fetch user */
    getUser(): Observable<LocalUser[]> {
        return this.http.get<LocalUser[]>("/api/auth/users")
    }

    createUser(username: string, password: string, is_admin:boolean): Observable<LocalUser> {
        return this.http.post<LocalUser>("/api/auth/users", {
            username: username.trim(),
            password: password,
            is_admin: is_admin,
        })
    }

    updateUser(userId: string, body: UpdateUser): Observable<LocalUser> {
        return this.http.patch<LocalUser>(`/api/auth/users/${userId}`, body)
    }

    deleteUser(userId: string): Observable<void> {
        return this.http.delete<void>(`/api/auth/users/${userId}`)
    }

}
