import { Injectable } from "@angular/core";
import { HttpClient, HttpParams } from "@angular/common/http";
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

  constructor(private http: HttpClient) {}

  getImages(page = 1, pageSize = 20, search = ""): Observable<PaginatedImages> {
    let params = new HttpParams().set("page", page).set("page_size", pageSize);
    if (search) params = params.set("search", search);
    return this.http.get<PaginatedImages>(`${this.BASE}/images`, { params });
  }

  getImageTags(
    repository: string,
  ): Observable<{ repository: string; tags: string[] }> {
    return this.http.get<{ repository: string; tags: string[] }>(
      `${this.BASE}/images/${encodeURIComponent(repository)}/tags`,
    );
  }

  getTagDetail(repository: string, tag: string): Observable<ImageDetail> {
    return this.http.get<ImageDetail>(
      `${this.BASE}/images/${encodeURIComponent(repository)}/tags/${tag}/detail`,
    );
  }

  deleteTag(repository: string, tag: string): Observable<{ message: string }> {
    return this.http.delete<{ message: string }>(
      `${this.BASE}/images/${encodeURIComponent(repository)}/tags/${tag}`,
    );
  }

  deleteImage(repository: string): Observable<{ message: string }> {
    return this.http.delete<{ message: string }>(
      `${this.BASE}/images/${encodeURIComponent(repository)}`,
    );
  }

  addTag(
    repository: string,
    sourceTag: string,
    newTag: string,
  ): Observable<{ message: string }> {
    return this.http.post<{ message: string }>(
      `${this.BASE}/images/${encodeURIComponent(repository)}/tags`,
      { source_tag: sourceTag, new_tag: newTag },
    );
  }

  pingRegistry(): Observable<{ status: string; url: string }> {
    return this.http.get<{ status: string; url: string }>(`${this.BASE}/ping`);
  }

  startGarbageCollect(): Observable<GCStatus> {
    return this.http.post<GCStatus>(`${this.BASE}/gc`, {});
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
