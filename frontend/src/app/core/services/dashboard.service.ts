import { inject, Injectable } from "@angular/core";
import { HttpClient } from "@angular/common/http";
import { Observable } from "rxjs";

export interface DashboardStats {
  total_images: number;
  total_tags: number;
  total_size_bytes: number;
  total_size_human: string;
  largest_image: {
    name: string;
    size: number;
    size_human: string;
  };
  disk_total_bytes: number;
  disk_used_bytes: number;
  disk_free_bytes: number;
  disk_usage_percent: number;
  registry_status: string;
}

@Injectable({ providedIn: "root" })
export class DashboardService {
  private http = inject(HttpClient);

  getStats(): Observable<DashboardStats> {
    return this.http.get<DashboardStats>("/api/dashboard/stats");
  }
}
