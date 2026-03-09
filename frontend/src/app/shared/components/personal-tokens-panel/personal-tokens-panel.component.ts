/**
 * Portalcrane - PersonalTokensPanelComponent
 * Allows any authenticated user to manage their personal access tokens.
 * Tokens are used as password substitutes for `docker login` — especially
 * useful for OIDC users who have no local password.
 *
 * Integrated into the account modal drawer.
 */

import { Component, inject, OnInit, signal } from "@angular/core";
import { firstValueFrom } from "rxjs";

import {
  PersonalToken,
  PersonalTokenCreated,
  PersonalTokensService,
} from "../../../core/services/personal-tokens.service";

@Component({
  selector: "app-personal-tokens-panel",
  templateUrl: "./personal-tokens-panel.component.html",
  styleUrl: "./personal-tokens-panel.component.css",
})
export class PersonalTokensPanelComponent implements OnInit {
  private readonly svc = inject(PersonalTokensService);

  // ── Token list ─────────────────────────────────────────────────────────────
  readonly tokens = signal<PersonalToken[]>([]);
  readonly loading = signal(false);
  readonly listError = signal<string | null>(null);

  // ── Create form ────────────────────────────────────────────────────────────
  readonly showCreateForm = signal(false);
  readonly newTokenName = signal("");
  readonly newTokenExpiry = signal<number>(90);
  readonly creating = signal(false);
  readonly createError = signal<string | null>(null);

  // ── Newly created token (shown once) ──────────────────────────────────────
  readonly createdToken = signal<PersonalTokenCreated | null>(null);
  readonly copied = signal(false);

  // ── Revoke state ───────────────────────────────────────────────────────────
  readonly revokingId = signal<string | null>(null);

  ngOnInit(): void {
    this.loadTokens();
  }

  loadTokens(): void {
    this.loading.set(true);
    this.listError.set(null);
    this.svc.list().subscribe({
      next: (list) => {
        this.tokens.set(list);
        this.loading.set(false);
      },
      error: (err) => {
        this.listError.set(err?.error?.detail ?? "Failed to load tokens");
        this.loading.set(false);
      },
    });
  }

  openCreateForm(): void {
    this.newTokenName.set("");
    this.newTokenExpiry.set(90);
    this.createError.set(null);
    this.createdToken.set(null);
    this.showCreateForm.set(true);
  }

  cancelCreate(): void {
    this.showCreateForm.set(false);
    this.createError.set(null);
  }

  async createToken(): Promise<void> {
    const name = this.newTokenName().trim();
    if (!name) {
      this.createError.set("Token name is required");
      return;
    }
    this.creating.set(true);
    this.createError.set(null);

    try {
      const created = await firstValueFrom(
        this.svc.create({
          name,
          expires_in_days: this.newTokenExpiry(),
        }),
      );
      this.tokens.update((list) => [
        ...list,
        {
          id: created.id,
          name: created.name,
          created_at: created.created_at,
          expires_at: created.expires_at,
          last_used_at: null,
          short_token_hint: created.short_token_hint,
        },
      ]);
      this.createdToken.set(created);
      this.showCreateForm.set(false);
      this.copied.set(false);
    } catch (err: any) {
      this.createError.set(err?.error?.detail ?? "Failed to create token");
    } finally {
      this.creating.set(false);
    }
  }

  /** Copy the raw token to the clipboard and show confirmation. */
  async copyToken(): Promise<void> {
    const token = this.createdToken();
    if (!token) return;
    try {
      await navigator.clipboard.writeText(token.raw_token);
      this.copied.set(true);
      setTimeout(() => this.copied.set(false), 3000);
    } catch {
      // Clipboard API not available — user can select manually
    }
  }

  /** Dismiss the newly-created token banner. */
  dismissCreated(): void {
    this.createdToken.set(null);
  }

  revokeToken(tokenId: string): void {
    this.revokingId.set(tokenId);
    this.svc.revoke(tokenId).subscribe({
      next: () => {
        this.tokens.update((list) => list.filter((t) => t.id !== tokenId));
        this.revokingId.set(null);
        // Also clear the newly-created banner if it was that token
        if (this.createdToken()?.id === tokenId) {
          this.createdToken.set(null);
        }
      },
      error: (err) => {
        this.listError.set(err?.error?.detail ?? "Failed to revoke token");
        this.revokingId.set(null);
      },
    });
  }

  /** Format an ISO date string to a short readable form. */
  formatDate(iso: string | null): string {
    if (!iso) return "—";
    try {
      return new Date(iso).toLocaleDateString(undefined, {
        year: "numeric",
        month: "short",
        day: "numeric",
      });
    } catch {
      return iso;
    }
  }

  /** Return true when the token is past its expiry date. */
  isExpired(token: PersonalToken): boolean {
    if (!token.expires_at) return false;
    return new Date(token.expires_at) < new Date();
  }
}
