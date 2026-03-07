/**
 * Portalcrane - PersonalTokensService
 * HTTP client for the personal access tokens API.
 * Used by the account modal to list, create and revoke PATs.
 */

import { HttpClient } from "@angular/common/http";
import { inject, Injectable } from "@angular/core";
import { Observable } from "rxjs";

/** Token metadata returned by the API (no raw secret). */
export interface PersonalToken {
  id: string;
  name: string;
  created_at: string;
  expires_at: string | null;
  last_used_at: string | null;
  short_token_hint: string | null;
}

/** Returned once at creation time — contains the raw token shown once. */
export interface PersonalTokenCreated extends PersonalToken {
  raw_token: string;
  short_token: string;
}

/** Request body to create a new token. */
export interface CreateTokenRequest {
  name: string;
  expires_in_days: number | null;
}

@Injectable({ providedIn: "root" })
export class PersonalTokensService {
  private readonly http = inject(HttpClient);

  /** List all tokens belonging to the current user. */
  list(): Observable<PersonalToken[]> {
    return this.http.get<PersonalToken[]>("/api/auth/tokens");
  }

  /** Create a new personal access token. The raw_token field is shown once. */
  create(request: CreateTokenRequest): Observable<PersonalTokenCreated> {
    return this.http.post<PersonalTokenCreated>("/api/auth/tokens", request);
  }

  /** Revoke a token by its ID. */
  revoke(tokenId: string): Observable<void> {
    return this.http.delete<void>(`/api/auth/tokens/${tokenId}`);
  }
}
