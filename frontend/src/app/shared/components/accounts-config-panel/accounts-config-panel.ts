import { HttpClient } from "@angular/common/http";
import {
  ChangeDetectionStrategy,
  Component,
  computed,
  inject,
  OnInit,
  signal,
} from "@angular/core";

/** Local user as returned by the API. */
export interface LocalUser {
  id: string;
  username: string;
  is_admin: boolean;
  created_at: string;
}

@Component({
  selector: "app-accounts-config-panel",
  imports: [],
  templateUrl: "./accounts-config-panel.html",
  styleUrl: "./accounts-config-panel.css",
  changeDetection: ChangeDetectionStrategy.OnPush,
})
export class AccountsConfigPanel implements OnInit {
  private http = inject(HttpClient);

  // ── Users list ─────────────────────────────────────────────────────────────
  readonly users = signal<LocalUser[]>([]);
  readonly loading = signal(false);
  readonly error = signal<string | null>(null);

  // ── Create form ────────────────────────────────────────────────────────────
  readonly showCreateForm = signal(false);
  readonly newUsername = signal("");
  readonly newPassword = signal("");
  readonly newIsAdmin = signal(false);
  readonly showNewPassword = signal(false);
  readonly creating = signal(false);
  readonly createError = signal<string | null>(null);

  // ── Edit state ─────────────────────────────────────────────────────────────
  /** ID of the user currently being edited (null = none). */
  readonly editingId = signal<string | null>(null);
  readonly editPassword = signal("");
  readonly editIsAdmin = signal(false);
  readonly showEditPassword = signal(false);
  readonly saving = signal(false);
  readonly saveError = signal<string | null>(null);

  // ── Delete state ───────────────────────────────────────────────────────────
  readonly deletingId = signal<string | null>(null);

  /** True when the create form has valid inputs. */
  readonly canCreate = computed(
    () =>
      this.newUsername().trim().length > 0 && this.newPassword().length >= 8,
  );

  ngOnInit(): void {
    this.loadUsers();
  }

  /** Fetch the user list from the backend. */
  loadUsers(): void {
    this.loading.set(true);
    this.error.set(null);
    this.http.get<LocalUser[]>("/api/auth/users").subscribe({
      next: (users) => {
        this.users.set(users);
        this.loading.set(false);
      },
      error: (err) => {
        this.error.set(err?.error?.detail ?? "Failed to load users");
        this.loading.set(false);
      },
    });
  }

  /** Open the create form. */
  openCreateForm(): void {
    this.newUsername.set("");
    this.newPassword.set("");
    this.newIsAdmin.set(false);
    this.showNewPassword.set(false);
    this.createError.set(null);
    this.showCreateForm.set(true);
  }

  cancelCreate(): void {
    this.showCreateForm.set(false);
  }

  /** Submit the new user form. */
  createUser(): void {
    if (!this.canCreate()) return;
    this.creating.set(true);
    this.createError.set(null);

    this.http
      .post<LocalUser>("/api/auth/users", {
        username: this.newUsername().trim(),
        password: this.newPassword(),
        is_admin: this.newIsAdmin(),
      })
      .subscribe({
        next: (user) => {
          this.users.update((list) => [...list, user]);
          this.showCreateForm.set(false);
          this.creating.set(false);
        },
        error: (err) => {
          this.createError.set(err?.error?.detail ?? "Failed to create user");
          this.creating.set(false);
        },
      });
  }

  /** Open the inline edit row for a given user. */
  openEdit(user: LocalUser): void {
    this.editingId.set(user.id);
    this.editPassword.set("");
    this.editIsAdmin.set(user.is_admin);
    this.showEditPassword.set(false);
    this.saveError.set(null);
  }

  cancelEdit(): void {
    this.editingId.set(null);
  }

  /** Save the edited user. */
  saveEdit(userId: string): void {
    const body: Record<string, unknown> = {};
    if (this.editPassword().length > 0) {
      if (this.editPassword().length < 8) {
        this.saveError.set("Password must be at least 8 characters");
        return;
      }
      body["password"] = this.editPassword();
    }
    body["is_admin"] = this.editIsAdmin();

    this.saving.set(true);
    this.saveError.set(null);

    this.http.patch<LocalUser>(`/api/auth/users/${userId}`, body).subscribe({
      next: (updated) => {
        this.users.update((list) =>
          list.map((u) => (u.id === userId ? updated : u)),
        );
        this.editingId.set(null);
        this.saving.set(false);
      },
      error: (err) => {
        this.saveError.set(err?.error?.detail ?? "Failed to save user");
        this.saving.set(false);
      },
    });
  }

  /** Delete a user after confirmation. */
  deleteUser(userId: string): void {
    if (userId === "env-admin") return;
    this.deletingId.set(userId);
    this.http.delete(`/api/auth/users/${userId}`).subscribe({
      next: () => {
        this.users.update((list) => list.filter((u) => u.id !== userId));
        this.deletingId.set(null);
      },
      error: (err) => {
        this.error.set(err?.error?.detail ?? "Failed to delete user");
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
