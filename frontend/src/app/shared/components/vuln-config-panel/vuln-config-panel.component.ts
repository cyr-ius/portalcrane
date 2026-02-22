import { Component, signal, computed, inject, OnInit } from "@angular/core";
import { CommonModule } from "@angular/common";
import {
  VulnConfigService,
  VulnConfig,
  SEVERITIES,
  Severity,
} from "../../../core/services/vuln-config.service";

@Component({
  selector: "app-vuln-config-panel",
  imports: [CommonModule],
  template: `
    <div class="card border-0 mb-3">
      <div
        class="card-header border-0 d-flex align-items-center justify-content-between"
      >
        <h6 class="fw-semibold mb-0">
          <i class="bi bi-shield-exclamation me-2"></i>Vulnerability Scanner
          (Trivy)
        </h6>
        @if (vulnConfig.hasLocalOverrides()) {
          <span
            class="badge bg-warning-subtle text-warning d-flex align-items-center gap-1"
          >
            <i class="bi bi-pencil-fill" style="font-size:0.6rem"></i>
            Custom
          </span>
        } @else {
          <span
            class="badge bg-secondary-subtle text-secondary d-flex align-items-center gap-1"
          >
            <i class="bi bi-server" style="font-size:0.6rem"></i>
            From env
          </span>
        }
      </div>

      <div class="card-body">
        <!-- Enable / Disable toggle -->
        <div class="d-flex align-items-center justify-content-between mb-3">
          <div>
            <div class="fw-semibold small">Enable Trivy scan</div>
            <div class="text-muted" style="font-size:0.75rem">
              Run CVE scan after ClamAV before allowing push
            </div>
          </div>
          <div class="form-check form-switch mb-0">
            <input
              class="form-check-input"
              type="checkbox"
              role="switch"
              id="vulnEnabled"
              [checked]="draft().enabled"
              (change)="setEnabled($any($event.target).checked)"
            />
          </div>
        </div>

        @if (draft().enabled) {
          <!-- Severities blocking policy -->
          <div class="mb-3">
            <label class="form-label small fw-semibold">
              Blocking severities
              <span class="text-muted fw-normal ms-1"
                >(at least one required)</span
              >
            </label>
            <div class="d-flex flex-wrap gap-2">
              @for (sev of allSeverities; track sev) {
                <button
                  class="btn btn-sm sev-btn"
                  [class]="getSevBtnClass(sev)"
                  (click)="toggleSeverity(sev)"
                  [title]="'Toggle ' + sev"
                >
                  {{ sev }}
                </button>
              }
            </div>
            @if (draft().severities.length === 0) {
              <div class="text-danger small mt-1">
                <i class="bi bi-exclamation-triangle me-1"></i>
                Select at least one severity
              </div>
            }
          </div>

          <!-- Ignore unfixed -->
          <div class="d-flex align-items-center justify-content-between mb-3">
            <div>
              <div class="fw-semibold small">Ignore unfixed CVEs</div>
              <div class="text-muted" style="font-size:0.75rem">
                Do not block on CVEs with no available fix
              </div>
            </div>
            <div class="form-check form-switch mb-0">
              <input
                class="form-check-input"
                type="checkbox"
                role="switch"
                id="vulnIgnoreUnfixed"
                [checked]="draft().ignore_unfixed"
                (change)="setIgnoreUnfixed($any($event.target).checked)"
              />
            </div>
          </div>

          <!-- Timeout -->
          <div class="mb-3">
            <label class="form-label small fw-semibold">Scan timeout</label>
            <div class="d-flex gap-2">
              @for (t of timeoutOptions; track t) {
                <button
                  class="btn btn-sm"
                  [class.btn-primary]="draft().timeout === t"
                  [class.btn-outline-secondary]="draft().timeout !== t"
                  (click)="setTimeout(t)"
                >
                  {{ t }}
                </button>
              }
            </div>
          </div>
        }

        <!-- Server defaults comparison -->
        @if (vulnConfig.serverDefaults()) {
          <div class="env-defaults p-2 rounded mb-3" style="font-size:0.72rem">
            <div class="text-muted mb-1 fw-semibold">
              <i class="bi bi-server me-1"></i>Server defaults (env vars)
            </div>
            <div class="font-monospace text-muted">
              enabled={{ vulnConfig.serverDefaults()!.enabled }}&nbsp;
              severities={{
                vulnConfig.serverDefaults()!.severities.join(",")
              }}&nbsp; ignore_unfixed={{
                vulnConfig.serverDefaults()!.ignore_unfixed
              }}&nbsp; timeout={{ vulnConfig.serverDefaults()!.timeout }}
            </div>
          </div>
        }

        <!-- Actions -->
        <div class="d-flex gap-2">
          <button
            class="btn btn-sm btn-primary flex-fill"
            (click)="save()"
            [disabled]="!canSave()"
          >
            <i class="bi bi-floppy me-1"></i>Apply
          </button>
          @if (vulnConfig.hasLocalOverrides()) {
            <button
              class="btn btn-sm btn-outline-secondary"
              (click)="reset()"
              title="Restore server defaults"
            >
              <i class="bi bi-arrow-counterclockwise me-1"></i>Reset to env
            </button>
          }
        </div>

        @if (saved()) {
          <div class="alert alert-success py-2 mt-2 mb-0 small" role="alert">
            <i class="bi bi-check-circle me-1"></i>
            Configuration saved locally.
            @if (vulnConfig.hasLocalOverrides()) {
              Server env vars are overridden for this browser session.
            }
          </div>
        }
      </div>
    </div>
  `,
  styles: [
    `
      .sev-btn {
        font-size: 0.7rem;
        padding: 2px 8px;
        border-radius: 20px;
        font-weight: 600;
        letter-spacing: 0.03em;
      }
      .env-defaults {
        background: var(--pc-main-bg);
        border: 1px dashed var(--pc-border);
      }
    `,
  ],
})
export class VulnConfigPanelComponent implements OnInit {
  vulnConfig = inject(VulnConfigService);

  readonly allSeverities = SEVERITIES;
  readonly timeoutOptions = ["1m", "3m", "5m", "10m", "15m", "30m"];

  draft = signal<VulnConfig>({ ...this.vulnConfig.config() });
  saved = signal(false);

  canSave = computed(
    () => !this.draft().enabled || this.draft().severities.length > 0,
  );

  ngOnInit() {
    // Sync le draft avec la config chargée
    this.draft.set({ ...this.vulnConfig.config() });
  }

  setEnabled(value: boolean) {
    this.draft.update((d) => ({ ...d, enabled: value }));
    this.saved.set(false);
  }

  toggleSeverity(sev: Severity) {
    this.draft.update((d) => {
      const has = d.severities.includes(sev);
      const updated = has
        ? d.severities.filter((s) => s !== sev)
        : [...d.severities, sev];
      return { ...d, severities: updated };
    });
    this.saved.set(false);
  }

  setIgnoreUnfixed(value: boolean) {
    this.draft.update((d) => ({ ...d, ignore_unfixed: value }));
    this.saved.set(false);
  }

  setTimeout(value: string) {
    this.draft.update((d) => ({ ...d, timeout: value }));
    this.saved.set(false);
  }

  save() {
    if (!this.canSave()) return;
    this.vulnConfig.saveConfig({ ...this.draft() });
    this.saved.set(true);
    setTimeout(() => this.saved.set(false), 3000);
  }

  reset() {
    this.vulnConfig.resetToDefaults();
    this.draft.set({ ...this.vulnConfig.config() });
    this.saved.set(false);
  }

  getSevBtnClass(sev: Severity): string {
    const active = this.draft().severities.includes(sev);
    const colorMap: Record<Severity, string> = {
      CRITICAL: active ? "btn-danger" : "btn-outline-danger",
      HIGH: active ? "btn-danger" : "btn-outline-danger",
      MEDIUM: active ? "btn-warning" : "btn-outline-warning",
      LOW: active ? "btn-info" : "btn-outline-info",
      UNKNOWN: active ? "btn-secondary" : "btn-outline-secondary",
    };
    return colorMap[sev];
  }
}
