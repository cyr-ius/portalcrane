/**
 * Portalcrane - Registry Service
 *
 * All endpoints that target a specific repository pass the repository name
 * as a query parameter (?repository=...) instead of a URL path segment.
 * This avoids %2F encoding issues with reverse proxies (Traefik, HAProxy,
 * Nginx, Caddy) that normalize encoded slashes before forwarding requests.
 */
import { HttpClient, HttpParams } from "@angular/common/http";
import { inject, Injectable } from "@angular/core";
import { Observable } from "rxjs";

export interface PaginatedImages {
  items: ImageInfo[];
  total: number;
  page: number;
  page_size: number;
  total_pages: number;
}

export interface ImageInfo {
  name: string;
  tags: string[];
  tag_count: number;
  total_size: number;
}

export interface ImageDetail {
  name: string;
  tag: string;
  digest: string;
  size: number;
  created: string;
  architecture: string;
  os: string;
  layers: LayerInfo[];
  labels: Record<string, string>;
  env: string[];
  cmd: string[];
  entrypoint: string[];
  exposed_ports: Record<string, unknown>;
}

export interface LayerInfo {
  mediaType: string;
  size: number;
  digest: string;
}

export interface GCStatus {
  status: "idle" | "running" | "done" | "failed";
  started_at: string | null;
  finished_at: string | null;
  output: string;
  freed_bytes: number;
  freed_human: string;
  error: string | null;
}

@Injectable({ providedIn: "root" })
export class RegistryService {
  private readonly BASE = "/api/registry";
  private http = inject(HttpClient);

  getImages(page = 1, pageSize = 20, search = ""): Observable<PaginatedImages> {
    let params = new HttpParams().set("page", page).set("page_size", pageSize);
    if (search) params = params.set("search", search);
    return this.http.get<PaginatedImages>(`${this.BASE}/images`, { params });
  }

  /**
   * Get all tags for a repository.
   * Repository is passed as a query param to survive reverse-proxy URI normalization.
   */
  getImageTags(
    repository: string,
  ): Observable<{ repository: string; tags: string[] }> {
    const params = new HttpParams().set("repository", repository);
    return this.http.get<{ repository: string; tags: string[] }>(
      `${this.BASE}/images/tags`,
      { params },
    );
  }

  /**
   * Get detailed metadata for a specific tag.
   * Both repository and tag are passed as query params.
   */
  getTagDetail(repository: string, tag: string): Observable<ImageDetail> {
    const params = new HttpParams()
      .set("repository", repository)
      .set("tag", tag);
    return this.http.get<ImageDetail>(`${this.BASE}/images/tags/detail`, {
      params,
    });
  }

  /**
   * Delete a specific tag from a repository.
   * Both repository and tag are passed as query params.
   */
  deleteTag(repository: string, tag: string): Observable<{ message: string }> {
    const params = new HttpParams()
      .set("repository", repository)
      .set("tag", tag);
    return this.http.delete<{ message: string }>(`${this.BASE}/images/tags`, {
      params,
    });
  }

  /**
   * Delete all tags (and the image) from a repository.
   * Repository is passed as a query param.
   */
  deleteImage(repository: string): Observable<{ message: string }> {
    const params = new HttpParams().set("repository", repository);
    return this.http.delete<{ message: string }>(`${this.BASE}/images`, {
      params,
    });
  }

  /**
   * Add a new tag to an existing image (retag).
   * Repository is passed as a query param; source/new tag in the request body.
   */
  addTag(
    repository: string,
    sourceTag: string,
    newTag: string,
  ): Observable<{ message: string }> {
    const params = new HttpParams().set("repository", repository);
    return this.http.post<{ message: string }>(
      `${this.BASE}/images/tags`,
      { source_tag: sourceTag, new_tag: newTag },
      { params },
    );
  }

  /**
   * Rename (retag) an image to a new repository/name via skopeo copy.
   * Source repository is passed as a query param; target in the request body.
   */
  renameImage(
    repository: string,
    newRepository: string,
    newTag: string,
  ): Observable<{ message: string }> {
    const params = new HttpParams().set("repository", repository);
    return this.http.post<{ message: string }>(
      `${this.BASE}/images/rename`,
      { new_repository: newRepository, new_tag: newTag },
      { params },
    );
  }

  pingRegistry(): Observable<{ status: string; url: string }> {
    return this.http.get<{ status: string; url: string }>(`${this.BASE}/ping`);
  }

  /**
   * Triggers registry garbage collection.
   * @param dryRun Preview what would be deleted without actually deleting
   */
  startGarbageCollect(dryRun: boolean): Observable<GCStatus> {
    return this.http.post<GCStatus>(`${this.BASE}/gc?dry_run=${dryRun}`, {});
  }

  getGCStatus(): Observable<GCStatus> {
    return this.http.get<GCStatus>(`${this.BASE}/gc`);
  }

  getEmptyRepositories(): Observable<{
    empty_repositories: string[];
    count: number;
  }> {
    return this.http.get<{ empty_repositories: string[]; count: number }>(
      `${this.BASE}/empty-repositories`,
    );
  }

  purgeEmptyRepositories(): Observable<{
    message: string;
    purged: string[];
    errors: any[];
  }> {
    return this.http.delete<{
      message: string;
      purged: string[];
      errors: any[];
    }>(`${this.BASE}/empty-repositories`);
  }
}
