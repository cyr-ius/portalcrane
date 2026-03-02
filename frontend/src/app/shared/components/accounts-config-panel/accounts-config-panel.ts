import { HttpClient } from "@angular/common/http";
import {
  ChangeDetectionStrategy,
  Component,
  inject,
  OnInit,
  signal,
} from "@angular/core";
import {
  form,
  FormField,
  minLength,
  required,
  submit,
} from "@angular/forms/signals";
import { firstValueFrom } from "rxjs";

/** Local user as returned by the API. */
export interface LocalUser {
  id: string;
  username: string;
  is_admin: boolean;
  can_pull_images: boolean;
  can_push_images: boolean;
  created_at: string;
}

@Component({
  selector: "app-accounts-config-panel",
  imports: [FormField],
  templateUrl: "./accounts-config-panel.html",
  styleUrl: "./accounts-config-panel.css",
})
export class AccountsConfigPanel implements OnInit {
  private http = inject(HttpClient);

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
  /** ID of the user currently being edited (null = none). */
  readonly editingId = signal<string | null>(null);
  readonly showEditPassword = signal(false);
  readonly saving = signal(false);
  readonly saveError = signal<string | null>(null);

  // ── Delete state ───────────────────────────────────────────────────────────
  readonly deletingId = signal<string | null>(null);

  createModel = signal({
    username: "",
    password: "",
    isAdmin: false,
    canPullImages: false,
    canPushImages: false,
  });
  createModelOrig = structuredClone(this.createModel());
  createForm = form(this.createModel, (p) => {
    required(p.username);
    required(p.password);
    minLength(p.password, 8, {
      message: "Password must be at least 8 characters",
    });
  });

  updateModel = signal({
    password: "",
    isAdmin: false,
    canPullImages: false,
    canPushImages: false,
  });
  updateModelOrig = structuredClone(this.updateModel());
  updateForm = form(this.updateModel, (p) => {
    minLength(p.password, 8, {
      message: "Password must be at least 8 characters",
    });
  });

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

  /** Submit the new user form. */
  createUser(): void {
    this.creating.set(true);
    this.createError.set(null);

    submit(this.createForm, async (form) => {
      const formData = form().value();
      try {
        const user = await firstValueFrom(
          this.http.post<LocalUser>("/api/auth/users", {
            username: formData.username.trim(),
            password: formData.password,
            is_admin: formData.isAdmin,
            can_pull_images: formData.canPullImages,
            can_push_images: formData.canPushImages,
          }),
        );
        this.users.update((list) => [...list, user]);
        this.showCreateForm.set(false);
        this.creating.set(false);
      } catch (err: any) {
        this.createError.set(err?.error?.detail ?? "Failed to create user");
        this.creating.set(false);
      }
    });
  }

  /** Open the inline edit row for a given user. */
  openEdit(user: LocalUser): void {
    this.updateModel.set({
      password: "",
      isAdmin: user.is_admin,
      canPullImages: user.can_pull_images,
      canPushImages: user.can_push_images,
    });
    this.editingId.set(user.id);
    this.showEditPassword.set(false);
    this.saveError.set(null);
  }

  cancelEdit(): void {
    this.editingId.set(null);
  }

  /** Save the edited user. */
  saveEdit(userId: string): void {
    this.saving.set(true);
    this.saveError.set(null);

    submit(this.updateForm, async (form) => {
      const formData = form().value();

      if (formData.password.length > 0) {
        if (formData.password.length < 8) {
          this.saveError.set("Password must be at least 8 characters");
          return;
        }
      }

      const body = {
        password: formData.password ? formData.password : null,
        is_admin: formData.isAdmin,
        can_pull_images: formData.canPullImages,
        can_push_images: formData.canPushImages,
      };

      try {
        const updated = await firstValueFrom(
          this.http.patch<LocalUser>(`/api/auth/users/${userId}`, body),
        );
        this.users.update((list) =>
          list.map((u) => (u.id === userId ? updated : u)),
        );
        this.editingId.set(null);
        this.saving.set(false);
      } catch (err: any) {
        this.saveError.set(err?.error?.detail ?? "Failed to save user");
        this.saving.set(false);
      }
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
