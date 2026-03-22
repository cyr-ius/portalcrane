/**
 * Portalcrane - RegistryService
 *
 * Migration note: all local registry operations now route through the unified
 * V2 provider layer instead of the legacy /api/registry/* endpoints:
 *
 *   Image browsing   → /api/external/registries/__local__/browse
 *   Tag management   → /api/external/registries/__local__/browse/tags
 *   Tag detail       → /api/external/registries/__local__/browse/tags/detail
 *   Delete image     → /api/external/registries/__local__/browse/image
 *   Ping             → /api/external/registries/__local__/ping
 *   Empty repos      → /api/external/registries/__local__/empty-repositories*
 *   Copy image       → /api/system/copy
 *   GC               → /api/system/gc
 *   Folder access    → /api/folders/mine  /api/folders/pushable
 *
 * The LOCAL_REGISTRY_SYSTEM_ID constant ('__local__') is the canonical
 * identifier for the embedded local registry across all Angular services.
 */
import { HttpClient, HttpParams } from "@angular/common/http";
import { inject, Injectable } from "@angular/core";
import { Observable } from "rxjs";

// ── Shared interfaces ──────────────────────────────────────────────────────

/** Basic image / repository information returned by the list endpoint. */
export interface ImageInfo {
  name: string;
  tags: string[];
  tag_count: number;
  total_size: number;
}

/**
 * A single layer entry inside an ImageDetail manifest.
 */
export interface ImageLayer {
  digest: string;
  size: number;
  mediaType?: string;
}

/** Detailed image metadata returned by the tag-detail endpoint. */
export interface ImageDetail {
  name: string;
  tag: string;
  digest: string;
  size: number;
  created: string;
  architecture: string;
  os: string;
  layers: ImageLayer[];
  labels: Record<string, string>;
  env: string[];
  cmd: string[];
  entrypoint: string[];
  exposed_ports: Record<string, unknown>;
}

/** Paginated response for the image list. */
export interface PaginatedImages {
  items: ImageInfo[];
  total: number;
  page: number;
  page_size: number;
  total_pages: number;
  error?: string | null;
}

/**
 * Paginated response when browsing an external registry.
 * Extends PaginatedImages with an explicit error field.
 */
export interface ExternalPaginatedImages extends PaginatedImages {
  error: string | null;
}

/** Garbage-collection job status. */
export interface GCStatus {
  status: string;
  started_at: string | null;
  finished_at: string | null;
  output: string;
  freed_bytes: number;
  freed_human: string;
  error: string | null;
}

/** Copy image request payload. */
export interface CopyImageRequest {
  source_repository: string;
  source_tag: string;
  dest_repository: string;
  dest_tag?: string | null;
}

// ── Service ────────────────────────────────────────────────────────────────

@Injectable({ providedIn: "root" })
export class RegistryService {
  private readonly FOLDERS = "/api/folders";
  private readonly EXTERNAL = "/api/external";
  private readonly SYSTEM = "/api/system";

  private http = inject(HttpClient);

  // ── Image list ─────────────────────────────────────────────────────────────

  /**
   * Browse images from a registry (local or external).
   *
   * @param registryId  ID of the registry (use __local__ for local).
   * @param page        Page number (1-based).
   * @param pageSize    Number of items per page.
   * @param search      Optional search string.
   */
  getExternalImages(
    registryId: string,
    page = 1,
    pageSize = 20,
    search = "",
  ): Observable<ExternalPaginatedImages> {
    let params = new HttpParams()
      .set("page", page)
      .set("page_size", pageSize);
    if (search?.trim()) {
      params = params.set("search", search.trim());
    }
    return this.http.get<ExternalPaginatedImages>(
      `${this.EXTERNAL}/registries/${registryId}/browse`,
      { params },
    );
  }

  // ── Tags ───────────────────────────────────────────────────────────────────

  /**
   * Fetch all tags for a repository in any registry.
   *
   * @param registryId  ID of the registry (use __local__ for local).
   * @param repository  Repository name.
   */
  getExternalImageTags(
    registryId: string,
    repository: string,
  ): Observable<{ repository: string; tags: string[] }> {
    const params = new HttpParams().set("repository", repository);
    return this.http.get<{ repository: string; tags: string[] }>(
      `${this.EXTERNAL}/registries/${registryId}/browse/tags`,
      { params },
    );
  }

  /**
   * Fetch detailed metadata for a specific tag in any registry.
   *
   * @param registryId  ID of the registry.
   * @param repository  Repository name.
   * @param tag         Tag name.
   */
  getExternalTagDetail(
    registryId: string,
    repository: string,
    tag: string,
  ): Observable<ImageDetail> {
    const params = new HttpParams()
      .set("repository", repository)
      .set("tag", tag);
    return this.http.get<ImageDetail>(
      `${this.EXTERNAL}/registries/${registryId}/browse/tags/detail`,
      { params },
    );
  }

  /**
   * Create a new tag by copying a manifest in any registry.
   *
   * @param registryId  ID of the registry.
   * @param repository  Repository name.
   * @param sourceTag   Existing tag to copy from.
   * @param newTag      New tag name to create.
   */
  addExternalTag(
    registryId: string,
    repository: string,
    sourceTag: string,
    newTag: string,
  ): Observable<{ success: boolean; message: string }> {
    const params = new HttpParams().set("repository", repository);
    return this.http.post<{ success: boolean; message: string }>(
      `${this.EXTERNAL}/registries/${registryId}/browse/tags`,
      { source_tag: sourceTag, new_tag: newTag },
      { params },
    );
  }

  /**
   * Delete a single tag from any registry.
   *
   * @param registryId  ID of the registry.
   * @param repository  Repository name.
   * @param tag         Tag name to delete.
   */
  deleteExternalTag(
    registryId: string,
    repository: string,
    tag: string,
  ): Observable<{ success: boolean; message: string }> {
    const params = new HttpParams()
      .set("repository", repository)
      .set("tag", tag);
    return this.http.delete<{ success: boolean; message: string }>(
      `${this.EXTERNAL}/registries/${registryId}/browse/tags`,
      { params },
    );
  }

  // ── Image management ───────────────────────────────────────────────────────

  /**
   * Delete all tags of a repository in any registry.
   *
   * @param registryId  ID of the registry.
   * @param repository  Repository name.
   */
  deleteExternalImage(
    registryId: string,
    repository: string,
  ): Observable<{
    repository: string;
    deleted_tags: string[];
    failed_tags: string[];
    message: string;
  }> {
    const params = new HttpParams().set("repository", repository);
    return this.http.delete<{
      repository: string;
      deleted_tags: string[];
      failed_tags: string[];
      message: string;
    }>(`${this.EXTERNAL}/registries/${registryId}/browse/image`, { params });
  }

  /**
   * Check local registry connectivity.
   *
   */
  pingRegistry(registryId: string,): Observable<{ status: string; url: string }> {
    return this.http.get<{ status: string; url: string }>(
      `${this.EXTERNAL}/registries/${registryId}/ping`,
    );
  }

  /**
   * Copy an image to a new repository path within the local registry.
   *
   *
   * @param sourceRepository  Source repository name.
   * @param sourceTag         Source tag name.
   * @param destRepository    Destination repository path.
   * @param destTag           Destination tag (defaults to sourceTag when omitted).
   */
  copyImage(
    sourceRepository: string,
    sourceTag: string,
    destRepository: string,
    destTag?: string,
  ): Observable<{ message: string }> {
    return this.http.post<{ message: string }>(`${this.EXTERNAL}/copy`, {
      source_repository: sourceRepository,
      source_tag: sourceTag,
      dest_repository: destRepository,
      dest_tag: destTag ?? null,
    });
  }

  // ── Folders / access control ───────────────────────────────────────────────

  /**
   * Return the list of folder names the current user can pull from.
   * Admins receive an empty list (meaning full access).
   *
   */
  getMyFolders(): Observable<string[]> {
    return this.http.get<string[]>(`${this.FOLDERS}/mine`);
  }

  /**
   * Return the list of folder names the current user can push to.
   * Admins receive an empty list (meaning full access).
   *
   */
  getPushableFolders(): Observable<string[]> {
    return this.http.get<string[]>(`${this.FOLDERS}/pushable`);
  }

  // ── Garbage collection ─────────────────────────────────────────────────────

  /**
   * Fetch the current garbage-collection job status.
   *
   */
  getGCStatus(): Observable<GCStatus> {
    return this.http.get<GCStatus>(`${this.SYSTEM}/gc`);
  }

  /**
   * Start a garbage-collection run (admin only).
   *
   * @param dryRun  When true, runs without actually deleting blobs.
   */
  startGarbageCollect(dryRun = false): Observable<GCStatus> {
    const params = new HttpParams().set("dry_run", dryRun);
    return this.http.post<GCStatus>(`${this.SYSTEM}/gc`, null, { params });
  }

  // ── Ghost / empty repositories ─────────────────────────────────────────────

  /**
   * List repositories that have no tags (ghost / empty repositories).
   *
   */
  getEmptyRepositories(): Observable<{
    empty_repositories: string[];
    count: number;
  }> {
    return this.http.get<{ empty_repositories: string[]; count: number }>(
      `${this.EXTERNAL}/registries/__local__/empty-repositories`,
    );
  }

  /**
   * Purge all empty repositories from the local filesystem.
   *
   */
  purgeEmptyRepositories(): Observable<{
    message: string;
    purged: string[];
    errors: { repo: string; error: string }[];
  }> {
    return this.http.delete<{
      message: string;
      purged: string[];
      errors: { repo: string; error: string }[];
    }>(`${this.EXTERNAL}/registries/__local__/empty-repositories`);
  }

}
