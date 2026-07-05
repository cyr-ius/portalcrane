import { Component, computed, inject, OnInit, signal } from "@angular/core";
import { TranslatePipe, TranslateService } from "@ngx-translate/core";
import { AuditEvent, SystemService } from "../../../core/services/system.service";

/** A pull or push made of several registry requests, collapsed into one row. */
export interface AuditGroup {
  event: string;
  /** Extracted image repository (grouping key), or the raw path for singles. */
  image: string;
  username: string | null;
  /** Client IP address of the request(s). */
  clientIp: string;
  count: number;
  totalBytes: number;
  /** Worst HTTP status of the group so any failure surfaces in red. */
  httpStatus: number;
  /** Most recent timestamp of the group. */
  timestamp: string;
  entries: AuditEvent[];
}

/** Sortable/filterable columns of the audit table, in display order. */
export type AuditColumn =
  | "timestamp"
  | "username"
  | "clientIp"
  | "event"
  | "image"
  | "count"
  | "httpStatus";

export const AUDIT_COLUMNS: AuditColumn[] = [
  "timestamp",
  "username",
  "clientIp",
  "event",
  "image",
  "count",
  "httpStatus",
];

type SortDirection = "asc" | "desc";

const GROUPABLE_EVENTS = new Set(["registry_pull", "registry_push"]);
const IMAGE_PATH_MARKERS = [
  "/manifests/",
  "/blobs/",
  "/tags/",
  "/uploads/",
  "/uploads",
];

@Component({
  selector: "app-audit-config-panel",
  imports: [TranslatePipe],
  templateUrl: "./audit-config-panel.component.html",
  styleUrl: "./audit-config-panel.component.css",
})
export class AuditConfigPanelComponent implements OnInit {
  private systemService = inject(SystemService);
  private translate = inject(TranslateService);

  auditLogs = signal<AuditEvent[]>([]);
  loadingAuditLogs = signal(false);
  auditLogError = signal<string | null>(null);

  /** Audit events with consecutive pull/push requests coalesced per operation. */
  auditGroups = computed<AuditGroup[]>(() => this.groupEvents(this.auditLogs()));

  readonly columns = AUDIT_COLUMNS;
  sortColumn = signal<AuditColumn>("timestamp");
  sortDirection = signal<SortDirection>("desc");
  /** Per-column substring filters, keyed by column. */
  filters = signal<Record<AuditColumn, string>>({
    timestamp: "",
    username: "",
    clientIp: "",
    event: "",
    image: "",
    count: "",
    httpStatus: "",
  });

  /** Groups after per-column filtering and sorting are applied. */
  displayedGroups = computed<AuditGroup[]>(() =>
    this.sortGroups(this.filterGroups(this.auditGroups())),
  );

  ngOnInit(): void {
    this.loadAuditLogs();
  }

  /** Toggle direction when re-selecting a column, else sort by the new one. */
  toggleSort(column: AuditColumn): void {
    if (this.sortColumn() === column) {
      this.sortDirection.update((d) => (d === "asc" ? "desc" : "asc"));
      return;
    }
    this.sortColumn.set(column);
    // Numeric and time columns feel more useful newest/highest first.
    this.sortDirection.set(
      column === "timestamp" || column === "count" || column === "httpStatus"
        ? "desc"
        : "asc",
    );
  }

  setFilter(column: AuditColumn, value: string): void {
    this.filters.update((f) => ({ ...f, [column]: value }));
  }

  /** Displayable text of a column, used for both filtering and text sorting. */
  columnText(group: AuditGroup, column: AuditColumn): string {
    switch (column) {
      case "timestamp":
        return this.formatAuditTimestamp(group.timestamp);
      case "username":
        return group.username ?? "";
      case "clientIp":
        return group.clientIp ?? "";
      case "event":
        return `${this.eventLabel(group.event)} ${group.event}`;
      case "image":
        return group.image;
      case "count":
        return String(group.count);
      case "httpStatus":
        return String(group.httpStatus);
    }
  }

  private filterGroups(groups: AuditGroup[]): AuditGroup[] {
    const active = (Object.entries(this.filters()) as [AuditColumn, string][])
      .map(([column, value]) => [column, value.trim().toLowerCase()] as const)
      .filter(([, value]) => value !== "");
    if (active.length === 0) return groups;
    return groups.filter((group) =>
      active.every(([column, value]) =>
        this.columnText(group, column).toLowerCase().includes(value),
      ),
    );
  }

  private sortGroups(groups: AuditGroup[]): AuditGroup[] {
    const column = this.sortColumn();
    const factor = this.sortDirection() === "asc" ? 1 : -1;
    return [...groups].sort((a, b) => factor * this.compareGroups(a, b, column));
  }

  private compareGroups(a: AuditGroup, b: AuditGroup, column: AuditColumn): number {
    switch (column) {
      case "count":
        return a.count - b.count;
      case "httpStatus":
        return a.httpStatus - b.httpStatus;
      case "timestamp":
        return (
          new Date(a.timestamp).getTime() - new Date(b.timestamp).getTime()
        );
      default:
        return this.columnText(a, column).localeCompare(
          this.columnText(b, column),
        );
    }
  }

  async loadAuditLogs() {
    this.loadingAuditLogs.set(true);
    this.auditLogError.set(null);
    try {
      const logs = await this.systemService.getAuditLogs(200);
      this.auditLogs.set(logs);
    } catch {
      this.auditLogError.set(this.translate.instant("AUDIT.ERR_LOAD"));
    } finally {
      this.loadingAuditLogs.set(false);
    }
  }

  formatAuditTimestamp(timestamp: string): string {
    const date = new Date(timestamp);
    if (Number.isNaN(date.getTime())) return timestamp;
    return date.toLocaleString();
  }

  /** Friendly label for a known event type, falling back to the raw name. */
  eventLabel(event: string): string {
    const key = `AUDIT.EVENTS.${event}`;
    const label = this.translate.instant(key);
    return label === key ? event : label;
  }

  prettyAuditPayload(group: AuditGroup): string {
    if (group.entries.length === 1) {
      return JSON.stringify(group.entries[0], null, 2);
    }
    return JSON.stringify(
      {
        event: group.event,
        image: group.image,
        username: group.username,
        requests: group.count,
        total_bytes: group.totalBytes,
        entries: group.entries,
      },
      null,
      2,
    );
  }

  /** Extract the image repository from a v2 path, mirroring the backend. */
  private extractImage(path: string): string {
    for (const marker of IMAGE_PATH_MARKERS) {
      const idx = path.indexOf(marker);
      if (idx !== -1) return path.slice(0, idx);
    }
    return path;
  }

  /**
   * Collapse consecutive pull/push requests that target the same image for the
   * same user into a single group. Events arrive newest-first, so a run of
   * matching entries belongs to the same docker operation.
   */
  private groupEvents(events: AuditEvent[]): AuditGroup[] {
    const groups: AuditGroup[] = [];

    for (const entry of events) {
      const groupable = GROUPABLE_EVENTS.has(entry.event);
      const image = groupable ? this.extractImage(entry.path) : entry.path;
      const last = groups[groups.length - 1];

      if (
        groupable &&
        last &&
        last.event === entry.event &&
        last.image === image &&
        last.username === entry.username
      ) {
        last.count += 1;
        last.totalBytes += entry.bytes ?? 0;
        last.httpStatus = Math.max(last.httpStatus, entry.http_status);
        last.entries.push(entry);
        continue;
      }

      groups.push({
        event: entry.event,
        image,
        username: entry.username,
        clientIp: entry.client_ip,
        count: 1,
        totalBytes: entry.bytes ?? 0,
        httpStatus: entry.http_status,
        timestamp: entry.timestamp,
        entries: [entry],
      });
    }

    return groups;
  }
}
