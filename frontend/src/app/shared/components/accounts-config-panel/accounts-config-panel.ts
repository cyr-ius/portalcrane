/**
 * Portalcrane - AccountsConfigPanel
 * Displays and manages local and OIDC-provisioned user accounts.
 *
 * Key behaviors:
 * - OIDC users show a provider badge and cannot have their password changed.
 * - The password field is hidden in the edit row for OIDC accounts.
 * - Deleting an OIDC user also revokes their SSO access (handled by backend).
 * - The env-admin row is always read-only.
 */

import { Component, inject, OnInit, signal } from "@angular/core";
import {
  form,
  FormField,
  minLength,
  required,
  submit,
} from "@angular/forms/signals";
import { TranslatePipe, TranslateService } from "@ngx-translate/core";
import { firstValueFrom } from "rxjs";
import { LocalUser, UpdateUser, UsersService } from "../../../core/services/users.service";


@Component({
  selector: "app-accounts-config-panel",
  imports: [FormField, TranslatePipe],
  templateUrl: "./accounts-config-panel.html",
  styleUrl: "./accounts-config-panel.css",
})
export class AccountsConfigPanel implements OnInit {
  private usersSvc = inject(UsersService)
  private translate = inject(TranslateService)

  // ── Users list ─────────────────────────────────────────────────────────────
  readonly users = signal<LocalUser[]>([]);
  readonly loading = signal(false);
  readonly error = signal<string | null>(null);

  // ── Create form ────────────────────────────────────────────────────────────
  readonly showCreateForm = signal(false);
  readonly showNewPassword = signal(false);
  readonly creating = signal(false);
  readonly createError = signal<string | null>(null);

  // ── Edit state ─────────────────────────────────────────────────────────────
  readonly editingId = signal<string | null>(null);
  readonly showEditPassword = signal(false);
  readonly saving = signal(false);
  readonly saveError = signal<string | null>(null);

  // ── Delete state ───────────────────────────────────────────────────────────
  readonly deletingId = signal<string | null>(null);

  // ── Create form model (username + password + isAdmin) ─────────────────────
  createModel = signal({
    username: "",
    password: "",
    isAdmin: false,
  });
  createModelOrig = structuredClone(this.createModel());
  createForm = form(this.createModel, (p) => {
    required(p.username);
    required(p.password);
    minLength(p.password, 8, {
      message: this.translate.instant("ACCOUNTS.PWD_MIN"),
    });
  });

  // ── Update form model (isAdmin only — password hidden for OIDC users) ─────
  updateModel = signal({
    password: "",
    isAdmin: false,
  });
  updateModelOrig = structuredClone(this.updateModel());
  updateForm = form(this.updateModel, (p) => {
    minLength(p.password, 8, {
      message: this.translate.instant("ACCOUNTS.PWD_MIN"),
    });
  });

  ngOnInit(): void {
    this.loadUsers();
  }

  /** Fetch the user list from the backend. */
  loadUsers(): void {
    this.loading.set(true);
    this.error.set(null);
    this.usersSvc.getUser().subscribe({
      next: (users) => {
        this.users.set(users);
        this.loading.set(false);
      },
      error: (err) => {
        this.error.set(err?.error?.detail ?? this.translate.instant("ACCOUNTS.ERR_LOAD"));
        this.loading.set(false);
      },
    });
  }

  /** Return true when the user was provisioned via OIDC. */
  isOidcUser(user: LocalUser): boolean {
    return user.auth_source === "oidc";
  }

  /** Return true for the built-in env-admin account (password-only edit). */
  isEnvAdmin(user: LocalUser): boolean {
    return user.id === "env-admin";
  }

  /** Open the create form (only for local accounts). */
  openCreateForm(): void {
    this.createModel.set(this.createModelOrig);
    this.createForm().reset();
    this.createError.set(null);
    this.showCreateForm.set(true);
  }

  cancelCreate(): void {
    this.createModel.set(this.createModelOrig);
    this.createForm().reset();
    this.showCreateForm.set(false);
  }

  /** Submit the new user form (creates a local account with a password). */
  createUser(): void {
    this.creating.set(true);
    this.createError.set(null);

    submit(this.createForm, async (form) => {
      const formData = form().value();
      try {
        const user = await firstValueFrom(
          this.usersSvc.createUser(
            formData.username.trim(),
            formData.password,
            formData.isAdmin,
          )
        );
        this.users.update((list) => [...list, user]);
        this.showCreateForm.set(false);
        this.creating.set(false);
      } catch (err: any) {
        this.createError.set(err?.error?.detail ?? this.translate.instant("ACCOUNTS.ERR_CREATE"));
        this.creating.set(false);
      }
    });
  }

  /** Open the inline edit row. For OIDC users the password field is hidden. */
  openEdit(user: LocalUser): void {
    this.updateModel.set({
      password: "",
      isAdmin: user.is_admin,
    });
    this.editingId.set(user.id);
    this.showEditPassword.set(false);
    this.saveError.set(null);
  }

  cancelEdit(): void {
    this.editingId.set(null);
  }

  /** Save admin-role changes (and optionally password for local users). */
  saveEdit(userId: string): void {
    this.saving.set(true);
    this.saveError.set(null);

    submit(this.updateForm, async (form) => {
      const formData = form().value();

      // The env-admin edit only changes the password, so it must be provided.
      if (userId === "env-admin" && formData.password.length === 0) {
        this.saveError.set(this.translate.instant("ACCOUNTS.PWD_REQUIRED"));
        this.saving.set(false);
        return;
      }

      if (formData.password.length > 0 && formData.password.length < 8) {
        this.saveError.set(this.translate.instant("ACCOUNTS.PWD_MIN"));
        this.saving.set(false);
        return;
      }

      // Only send password when the field was actually filled in
      const body: UpdateUser = {
        is_admin: formData.isAdmin,
      };
      if (formData.password) {
        body["password"] = formData.password;
      }

      try {
        const updated = await firstValueFrom(
          this.usersSvc.updateUser(userId, body)
        );
        this.users.update((list) =>
          list.map((u) => (u.id === userId ? updated : u)),
        );
        this.editingId.set(null);
        this.saving.set(false);
      } catch (err: any) {
        this.saveError.set(err?.error?.detail ?? this.translate.instant("ACCOUNTS.ERR_SAVE"));
        this.saving.set(false);
      }
    });
  }

  /**
   * Delete a user. For OIDC accounts the backend also adds the username to
   * the revocation list so the next SSO callback returns 403.
   */
  deleteUser(userId: string): void {
    if (userId === "env-admin") return;
    this.deletingId.set(userId);
    this.usersSvc.deleteUser(userId).subscribe({
      next: () => {
        this.users.update((list) => list.filter((u) => u.id !== userId));
        this.deletingId.set(null);
      },
      error: (err) => {
        this.error.set(err?.error?.detail ?? this.translate.instant("ACCOUNTS.ERR_DELETE"));
        this.deletingId.set(null);
      },
    });
  }

  /** Format ISO date string to a short readable form. */
  formatDate(iso: string): string {
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
}
