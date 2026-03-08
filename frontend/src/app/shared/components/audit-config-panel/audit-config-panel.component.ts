import { Component, inject, OnInit, signal } from "@angular/core";
import { AuditEvent, SystemService } from "../../../core/services/system.service";

@Component({
  selector: "app-audit-config-panel",
  imports: [],
  templateUrl: "./audit-config-panel.component.html",
  styleUrl: "./audit-config-panel.component.css",
})
export class AuditConfigPanelComponent implements OnInit {
  private systemService = inject(SystemService);

  auditLogs = signal<AuditEvent[]>([]);
  loadingAuditLogs = signal(false);
  auditLogError = signal<string | null>(null);

  ngOnInit(): void {
    this.loadAuditLogs()
  }

  async loadAuditLogs() {
    this.loadingAuditLogs.set(true);
    this.auditLogError.set(null);
    try {
      const logs = await this.systemService.getAuditLogs(200);
      this.auditLogs.set(logs);
    } catch {
      this.auditLogError.set("Unable to load audit logs.");
    } finally {
      this.loadingAuditLogs.set(false);
    }
  }

  formatAuditTimestamp(timestamp: string): string {
    const date = new Date(timestamp);
    if (Number.isNaN(date.getTime())) return timestamp;
    return date.toLocaleString();
  }

  prettyAuditPayload(entry: AuditEvent): string {
    return JSON.stringify(entry, null, 2);
  }

}
