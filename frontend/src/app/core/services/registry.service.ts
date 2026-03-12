/**
 * Portalcrane - RegistryService
 *
 * Angular service for all local registry API calls:
 * images, tags, garbage collection, copy, delete, retag.
 *
 * Folder access queries (/mine and /pushable) are delegated to
 * /api/folders which is the correct backend prefix.
 *
 * NOTE: This file also exports the shared interfaces (ImageInfo, ImageDetail,
 * PaginatedImages, ExternalPaginatedImages, GCStatus, ImageLayer) consumed by
 * dashboard, images-list, image-detail, sync-config-panel,
 * vuln-config-panel and folder.service.
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
 * Using explicit named properties (not an index signature) so Angular
 * templates can access layer.digest and layer.size directly without TS4111.
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

/** Paginated response for the local registry image list. */
export interface PaginatedImages {
  items: ImageInfo[];
  total: number;
  page: number;
  page_size: number;
  total_pages: number;
  /** Present only when browsing an external registry — null for local. */
  error?: string | null;
}

/**
 * Paginated response when browsing an external registry.
 * Extends PaginatedImages with an explicit error field so callers
 * can distinguish a partial result from a hard failure.
 */
export interface ExternalPaginatedImages extends PaginatedImages {
  error: string | null;
}

/** Garbage-collection job status returned by GET /api/registry/gc. */
export interface GCStatus {
  status: string;
  started_at: string | null;
  finished_at: string | null;
  output: string;
  freed_bytes: number;
  freed_human: string;
  error: string | null;
}

// ── Service ────────────────────────────────────────────────────────────────

@Injectable({ providedIn: "root" })
export class RegistryService {
  /** Base URL for all local registry endpoints (main.py: prefix="/api/registry"). */
  private readonly BASE = "/api/registry";

  /** Base URL for folder permission endpoints (main.py: prefix="/api/folders"). */
  private readonly FOLDERS = "/api/folders";

  /** Base URL for external registry endpoints (main.py: prefix="/api/external"). */
  private readonly EXTERNAL = "/api/external";

  private http = inject(HttpClient);

  // ── Image list ─────────────────────────────────────────────────────────────

  /**
   * Fetch a paginated, optionally filtered list of local registry images.
   *
   * Backend: GET /api/registry/images
   *
   * @param page      Page number (1-based).
   * @param pageSize  Number of items per page.
   * @param search    Optional search string to filter by repository name.
   */
  getImages(
    page = 1,
    pageSize = 20,
    search = "",
  ): Observable<PaginatedImages> {
    let params = new HttpParams()
      .set("page", page)
      .set("page_size", pageSize);
    if (search?.trim()) {
      params = params.set("search", search.trim());
    }
    return this.http.get<PaginatedImages>(`${this.BASE}/images`, { params });
  }

  /**
   * Browse images from a saved external registry.
   *
   * Backend: GET /api/external/registries/{id}/browse
   *
   * @param registryId  ID of the saved external registry.
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
   * Fetch all tags for a repository.
   *
   * Backend: GET /api/registry/images/tags?repository=…
   *
   * @param repository  Repository name, e.g. "biocontainers/swarm".
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
   * Fetch detailed metadata for a specific image tag.
   *
   * Backend: GET /api/registry/images/tags/detail?repository=…&tag=…
   *
   * @param repository  Repository name.
   * @param tag         Tag name, e.g. "latest".
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
   * Add a new tag to an existing image (retag via manifest copy).
   *
   * Backend: POST /api/registry/images/tags?repository=…
   *
   * @param repository  Repository name.
   * @param sourceTag   Existing tag to copy from.
   * @param newTag      New tag name to create.
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
   * Delete a specific tag from a repository.
   *
   * Backend: DELETE /api/registry/images/tags?repository=…&tag=…
   *
   * @param repository  Repository name.
   * @param tag         Tag to delete.
   */
  deleteTag(
    repository: string,
    tag: string,
  ): Observable<{ message: string }> {
    const params = new HttpParams()
      .set("repository", repository)
      .set("tag", tag);
    return this.http.delete<{ message: string }>(
      `${this.BASE}/images/tags`,
      { params },
    );
  }


  /**
   * Fetch tags for a repository from an external registry.
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
   * Delete all tags of a repository from an external registry.
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

  // ── Image management ───────────────────────────────────────────────────────

  /**
   * Delete all tags of an image (effectively deletes the repository).
   *
   * Backend: DELETE /api/registry/images?repository=…
   *
   * @param repository  Repository name to delete entirely.
   */
  deleteImage(repository: string): Observable<{ message: string }> {
    const params = new HttpParams().set("repository", repository);
    return this.http.delete<{ message: string }>(`${this.BASE}/images`, {
      params,
    });
  }

  /**
   * Copy an image to a new repository path via skopeo.
   *
   * Backend: POST /api/registry/images/copy
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
    return this.http.post<{ message: string }>(`${this.BASE}/images/copy`, {
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
   * Backend: GET /api/folders/mine
   */
  getMyFolders(): Observable<string[]> {
    return this.http.get<string[]>(`${this.FOLDERS}/mine`);
  }

  /**
   * Return the list of folder names the current user can push to.
   * Admins receive an empty list (meaning full access).
   *
   * Backend: GET /api/folders/pushable
   */
  getPushableFolders(): Observable<string[]> {
    return this.http.get<string[]>(`${this.FOLDERS}/pushable`);
  }

  // ── Garbage collection ─────────────────────────────────────────────────────

  /**
   * Fetch the current garbage-collection job status.
   *
   * Backend: GET /api/registry/gc
   */
  getGCStatus(): Observable<GCStatus> {
    return this.http.get<GCStatus>(`${this.BASE}/gc`);
  }

  /**
   * Start a garbage-collection run (admin only).
   *
   * Backend: POST /api/registry/gc?dry_run=…
   *
   * @param dryRun  When true, runs without actually deleting blobs.
   */
  startGarbageCollect(dryRun = false): Observable<GCStatus> {
    const params = new HttpParams().set("dry_run", dryRun);
    return this.http.post<GCStatus>(`${this.BASE}/gc`, null, { params });
  }

  // ── Ghost / empty repositories ─────────────────────────────────────────────

  /**
   * List repositories that have no tags (ghost / empty repositories).
   *
   * Backend: GET /api/registry/empty-repositories
   */
  getEmptyRepositories(): Observable<{
    empty_repositories: string[];
    count: number;
  }> {
    return this.http.get<{ empty_repositories: string[]; count: number }>(
      `${this.BASE}/empty-repositories`,
    );
  }

  /**
   * Purge all empty repositories from the local filesystem.
   *
   * Backend: DELETE /api/registry/empty-repositories
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
    }>(`${this.BASE}/empty-repositories`);
  }

  // ── Registry ping ──────────────────────────────────────────────────────────

  /**
   * Check registry connectivity.
   *
   * Backend: GET /api/registry/ping
   */
  ping(): Observable<{ status: string; url: string }> {
    return this.http.get<{ status: string; url: string }>(
      `${this.BASE}/ping`,
    );
  }
}
