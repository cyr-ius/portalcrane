/**
 * Portalcrane - PersonalTokensPanelComponent
 * Allows any authenticated user to manage their personal access tokens.
 * Tokens are used as password substitutes for `docker login` — especially
 * useful for OIDC users who have no local password.
 *
 * MIGRATION: Token creation form now uses Angular Signal Forms (form / FormField)
 * instead of bare signal-per-field bindings with manual validation guards.
 */

import { Component, inject, OnInit, signal } from "@angular/core";
import { form, FormField, minLength, required, submit } from "@angular/forms/signals";
import { firstValueFrom } from "rxjs";

import {
  PersonalToken,
  PersonalTokenCreated,
  PersonalTokensService,
} from "../../../core/services/personal-tokens.service";

/** Shape of the token creation form model. */
interface TokenFormModel {
  name: string;
  expiresInDays: number;
}

@Component({
  selector: "app-personal-tokens-panel",
  // FormField directive is required for [formField] bindings in the template
  imports: [FormField],
  templateUrl: "./personal-tokens-panel.component.html",
  styleUrl: "./personal-tokens-panel.component.css",
})
export class PersonalTokensPanelComponent implements OnInit {
  private readonly svc = inject(PersonalTokensService);

  // ── Token list ─────────────────────────────────────────────────────────────
  readonly tokens = signal<PersonalToken[]>([]);
  readonly loading = signal(false);
  readonly listError = signal<string | null>(null);

  // ── Create form visibility ─────────────────────────────────────────────────
  readonly showCreateForm = signal(false);
  readonly creating = signal(false);
  readonly createError = signal<string | null>(null);

  // ── Newly created token (shown once after creation) ────────────────────────
  readonly createdToken = signal<PersonalTokenCreated | null>(null);
  readonly copied = signal(false);

  // ── Revoke state ───────────────────────────────────────────────────────────
  readonly revokingId = signal<string | null>(null);

  // ── Signal Form – token creation ───────────────────────────────────────────

  /** Default values; spread to avoid mutating the constant on reset. */
  private readonly tokenInit: TokenFormModel = {
    name: "",
    expiresInDays: 90,
  };

  /** Reactive model backing the Signal Form. */
  readonly tokenModel = signal<TokenFormModel>({ ...this.tokenInit });

  /**
   * Signal Form definition.
   * - name: required, min 3 characters
   * - expiresInDays: required (must be a positive number)
   */
  readonly tokenForm = form(this.tokenModel, (p) => {
    required(p.name);
    minLength(p.name, 3, { message: "Token name must be at least 3 characters" });
    required(p.expiresInDays);
  });

  // ──────────────────────────────────────────────────────────────────────────

  ngOnInit(): void {
    this.loadTokens();
  }

  /** Fetch the current user's token list from the backend. */
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

  /** Open the creation form and reset its state. */
  openCreateForm(): void {
    this.tokenModel.set({ ...this.tokenInit });
    this.createError.set(null);
    this.createdToken.set(null);
    this.showCreateForm.set(true);
  }

  /** Close the creation form without submitting. */
  cancelCreate(): void {
    this.showCreateForm.set(false);
    this.createError.set(null);
  }

  /**
   * Submit the token creation form via Signal Forms.
   * On success, appends the new token to the list and shows the one-time banner.
   */
  createToken(): void {
    submit(this.tokenForm, async (f) => {
      const { name, expiresInDays } = f().value();
      this.creating.set(true);
      this.createError.set(null);

      try {
        const created = await firstValueFrom(
          this.svc.create({
            name: name!,
            expires_in_days: expiresInDays ?? 90,
          }),
        );

        // Append metadata (without raw_token) to the displayed list
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
        f().reset({ ...this.tokenInit });
      } catch (err: unknown) {
        const httpErr = err as { error?: { detail?: string } };
        this.createError.set(httpErr?.error?.detail ?? "Failed to create token");
      } finally {
        this.creating.set(false);
      }
    });
  }

  /** Copy the raw token value to the clipboard and show confirmation briefly. */
  async copyToken(): Promise<void> {
    const token = this.createdToken();
    if (!token) return;
    try {
      await navigator.clipboard.writeText(token.raw_token);
      this.copied.set(true);
      setTimeout(() => this.copied.set(false), 3000);
    } catch {
      // Clipboard API unavailable — user can select the text manually
    }
  }

  /** Dismiss the one-time token banner. */
  dismissCreated(): void {
    this.createdToken.set(null);
  }

  /** Revoke a token by its ID and remove it from the list. */
  revokeToken(tokenId: string): void {
    this.revokingId.set(tokenId);
    this.svc.revoke(tokenId).subscribe({
      next: () => {
        this.tokens.update((list) => list.filter((t) => t.id !== tokenId));
        this.revokingId.set(null);
        // Also clear the new-token banner if it was for this token
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

  /** Format an ISO date string into a human-readable short form. */
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

  /** Return true when the token's expiry date is in the past. */
  isExpired(token: PersonalToken): boolean {
    if (!token.expires_at) return false;
    return new Date(token.expires_at) < new Date();
  }
}
