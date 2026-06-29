# 📘 Angular 21 — Standards Portalcrane

Versions : Angular 21 · Bootstrap 5.3.8 · Bootstrap Icons 1.13.1 · Node.js 18+.

## 0. Configuration Zoneless (Performance)

Le mode zoneless désactive NgZone et s'appuie sur les signaux pour la détection de changements.

```typescript
// src/main.ts - Application bootstrap
import { bootstrapApplication } from "@angular/platform-browser";
import { AppComponent } from "./app/app.component";
import { appConfig } from "./app/app.config";

bootstrapApplication(AppComponent, appConfig);
```

```typescript
// src/app/app.config.ts - Application configuration
import { ApplicationConfig, provideZoneChangeDetection } from "@angular/core";
import { provideRouter } from "@angular/router";
import { provideHttpClient } from "@angular/common/http";

export const appConfig: ApplicationConfig = {
  providers: [
    // ✅ Enable zoneless mode for better performance
    provideZoneChangeDetection({ eventCoalescing: true }),
    provideRouter(routes),
    provideHttpClient(),
  ],
};
```

**Avantages :** meilleures performances (pas de zone.js), bundle plus petit, réactivité granulaire avec signaux, moins de cycles de changeDetection.

## 1. Principes Fondamentaux

- ✅ **Signaux** (réactivité granulaire)
- ✅ **Signal Forms** (formulaires réactifs simplifiés)
- ✅ **Zoneless** (sans NgZone)
- ✅ **Standalone Components** (pas de modules)
- ❌ **Directives avec `*`** (dépréciées : `*ngIf`, `*ngFor`, `*ngSwitch`)
- ✅ **Control Flow** (`@if`, `@for`, `@switch`)

## 2. Types de Composants

Tous les composants **doivent avoir des fichiers séparés** pour template et style :

```
app/features/user/
├── user-list/
│   ├── user-list.component.ts      ← Code TypeScript
│   ├── user-list.component.html    ← Template HTML
│   └── user-list.component.css     ← Styles CSS
```

### Structure de Composant (Standalone)

```typescript
import { Component, input, output, OnInit, inject } from "@angular/core";
import { CommonModule } from "@angular/common";
import { signal, computed, effect } from "@angular/core";
import {
  email,
  form,
  FormField,
  required,
  submit,
} from "@angular/forms/signals";

@Component({
  selector: "app-user-profile",
  imports: [CommonModule, FormField],
  templateUrl: "./user-profile.component.html",
  styleUrl: "./user-profile.component.css",
})
export class UserProfileComponent implements OnInit {
  // Injected services
  private userService = inject(UserService);

  // Input signal (receives data from parent)
  userId = input<number>(0);

  // Output signal (sends data to parent)
  userSaved = output<User>();

  // Error signal
  readonly error = signal("");

  // Internal state using signals
  user = signal<User | null>(null);
  isLoading = signal(false);
  errorMessage = signal<string | null>(null);

  // Computed derived state (automatically updates)
  displayName = computed(() => {
    const usr = this.user();
    return usr ? `${usr.firstName} ${usr.lastName}` : "Unknown";
  });

  // Signal Forms
  private readonly userInit = { email: "", phone: "" };
  userModel = signal({ ...this.userInit });

  userForm = form(this.userModel, (schemaPath) => {
    required(schemaPath.email);
    required(schemaPath.phone);
    email(schemaPath.email);
  });

  constructor() {
    // Load user when userId input changes
    effect(() => {
      const id = this.userId();
      if (id > 0) {
        this.loadUser(id);
      }
    });
  }

  private async loadUser(id: number): Promise<void> {
    this.isLoading.set(true);
    try {
      const userData = await this.userService.getUser(id).toPromise();
      this.user.set(userData);
    } catch (error) {
      this.errorMessage.set("Failed to load user");
    } finally {
      this.isLoading.set(false);
    }
  }

  onSubmit(event: Event): void {
    event.preventDefault();

    submit(this.userForm, async (f) => {
      const formData = f().value();
      try {
        this.userSaved.emit(formData);
      } catch (err: unknown) {
        const httpErr = err as { error?: { detail?: string } };
        this.error.set(httpErr.error?.detail ?? "Authentication failed");
      }
    });
  }
}
```

## 3. Control Flow (Directives de contrôle de flux)

**❌ ANCIEN (Déprécié) :** `*ngIf`, `*ngFor`, `*ngSwitch`.

**✅ NOUVEAU (Angular 21) :**

```html
<!-- If statement -->
@if (isVisible) {
<div>Content</div>
} @else if (isLoading) {
<p>Loading...</p>
} @else {
<p>Hidden</p>
}

<!-- For loop -->
@for (item of items; track item.id) {
<div>{{ item.name }}</div>
}

<!-- Switch statement -->
@switch (status) { @case ('active') {
<span class="badge bg-success">Active</span> } @case ('inactive') {
<span class="badge bg-secondary">Inactive</span> } @default {
<span class="badge bg-warning">Unknown</span> } }

<!-- Empty state handling -->
@if (items.length > 0) {
<ul>
  @for (item of items; track item.id) {
  <li>{{ item.name }}</li>
  }
</ul>
} @else {
<p class="text-muted">No items found</p>
}
```

## 4. Signaux et Réactivité

```typescript
import { signal, computed, effect } from "@angular/core";

export class ShoppingCartComponent {
  cartItems = signal<CartItem[]>([]);
  quantity = signal(0);
  discountPercent = signal(0);

  // Computed signal (derived state)
  subtotal = computed(() =>
    this.cartItems().reduce((sum, item) => sum + item.price * item.qty, 0),
  );
  discountAmount = computed(
    () => this.subtotal() * (this.discountPercent() / 100),
  );
  total = computed(() => this.subtotal() - this.discountAmount());

  constructor() {
    // Effect: runs whenever dependencies change
    effect(() => {
      const total = this.total();
      this.logToAnalytics(total);
    });
  }

  addItem(item: CartItem): void {
    // Update by creating new array (immutable pattern)
    this.cartItems.update((items) => [...items, item]);
    this.quantity.update((q) => q + 1);
  }

  removeItem(itemId: number): void {
    this.cartItems.update((items) => items.filter((i) => i.id !== itemId));
  }

  applyDiscount(percent: number): void {
    this.discountPercent.set(percent);
  }

  private logToAnalytics(total: number): void {
    /* ... */
  }
}
```

## 5. Signal Forms

Le template doit normalement être dans un fichier dédié (template inline ici uniquement pour l'exemple).

```typescript
import { Component } from "@angular/core";
import {
  form,
  email,
  FormField,
  FormRoot,
  required,
  submit,
  validate,
} from "@angular/forms/signals";
import { signal } from "@angular/core";

@Component({
  selector: "app-registration-form",
  standalone: true,
  imports: [FormRoot, FormField],
  template: `
    <form [formRoot]="registrationForm">
      <input [formField]="registrationForm.username" />
      <input type="email" [formField]="registrationForm.email" />
      <input type="password" [formField]="registrationForm.password" />
      <input type="password" [formField]="registrationForm.confirmPassword" />
      <input type="checkbox" [formField]="registrationForm.acceptTerms" />
      <button type="submit">Register</button>
    </form>
  `,
})
export class RegistrationFormComponent {
  private readonly registrationInit = {
    username: "",
    email: "",
    password: "",
    confirmPassword: "",
    acceptTerms: false,
  };
  registrationModel = signal({ ...this.registrationInit });

  registrationForm = form(
    this.registrationModel,
    (schemaPath) => {
      required(schemaPath.username);
      required(schemaPath.email);
      required(schemaPath.password);
      required(schemaPath.confirmPassword);
      required(schemaPath.acceptTerms);
      email(schemaPath.email);
      validate(schemaPath.confirmPassword, ({ value, valueOf }) => {
        const confirmPassword = value();
        const password = valueOf(schemaPath.password);
        if (confirmPassword !== password) {
          return {
            kind: "passwordMismatch",
            message: "Passwords do not match",
          };
        }
      });
    },
    { submission: { action: async (f) => this.submitToServer(f) } },
  );

  private submitToServer(f: unknown) {
    const formData = this.registrationForm().value();
    this.registrationForm().reset({ ...this.registrationInit });
  }
}
```

## 6. Services et Injection de Dépendances

```typescript
import { Injectable, inject, signal } from "@angular/core";
import { HttpClient } from "@angular/common/http";
import { Observable } from "rxjs";
import { environment } from "../../../environments/environment";

interface User {
  id: number;
  username: string;
  email: string;
}

@Injectable({ providedIn: "root" })
export class UserService {
  private http = inject(HttpClient);
  private apiUrl = `${environment.apiUrl}/users`;

  // State management using signals
  users = signal<User[]>([]);
  selectedUser = signal<User | null>(null);
  isLoading = signal(false);

  getUser(id: number): Observable<User> {
    return this.http.get<User>(`${this.apiUrl}/${id}`);
  }
  createUser(user: Omit<User, "id">): Observable<User> {
    return this.http.post<User>(this.apiUrl, user);
  }
  updateUser(id: number, user: Partial<User>): Observable<User> {
    return this.http.put<User>(`${this.apiUrl}/${id}`, user);
  }
  deleteUser(id: number): Observable<void> {
    return this.http.delete<void>(`${this.apiUrl}/${id}`);
  }
}
```

## 7. Template HTML (fichier séparé)

```html
<!-- user-list.component.html -->
<div class="container mt-5">
  <!-- Loading state -->
  @if (userService.isLoading()) {
  <div class="alert alert-info">
    <i class="bi bi-hourglass-split"></i> Loading users...
  </div>
  }

  <!-- Error state -->
  @if (errorMessage(); as error) {
  <div class="alert alert-danger alert-dismissible fade show" role="alert">
    <i class="bi bi-exclamation-triangle"></i> {{ error }}
    <button type="button" class="btn-close" (click)="clearError()"></button>
  </div>
  }

  <!-- Users table -->
  @if (filteredUsers().length > 0) {
  <table class="table table-striped table-hover">
    <thead class="table-light">
      <tr>
        <th>ID</th>
        <th>Username</th>
        <th>Email</th>
        <th>Actions</th>
      </tr>
    </thead>
    <tbody>
      @for (user of filteredUsers(); track user.id) {
      <tr>
        <td>{{ user.id }}</td>
        <td>{{ user.username }}</td>
        <td>{{ user.email }}</td>
        <td>
          <button
            class="btn btn-sm btn-info"
            (click)="editUser(user)"
            title="Edit"
          >
            <i class="bi bi-pencil"></i>
          </button>
          <button
            class="btn btn-sm btn-danger"
            (click)="deleteUser(user.id)"
            title="Delete"
          >
            <i class="bi bi-trash"></i>
          </button>
        </td>
      </tr>
      }
    </tbody>
  </table>
  } @else {
  <div class="alert alert-warning text-center">
    <i class="bi bi-inbox"></i> No users found
  </div>
  }
</div>
```

## 8. CSS Styles (fichier séparé)

Le style va dans un `.component.css` dédié (Bootstrap 5.3 pour la base, surcharges locales pour le composant). Exemple :

```css
/* user-list.component.css */
.btn {
  transition: all 0.3s ease;
}
.btn-primary:hover {
  transform: translateY(-2px);
  box-shadow: 0 2px 8px rgba(13, 110, 253, 0.3);
}
.table-responsive {
  border-radius: 0.25rem;
  overflow: hidden;
}
.table thead {
  background-color: #f8f9fa;
  font-weight: 600;
  text-transform: uppercase;
}
.alert {
  border-radius: 0.5rem;
  display: flex;
  align-items: center;
  gap: 10px;
}
input:focus,
textarea:focus,
select:focus {
  border-color: #0d6efd;
  outline: none;
  box-shadow: 0 0 0 0.2rem rgba(13, 110, 253, 0.25);
}
```

## 9. Architecture des Dossiers

```
frontend/src/app/
├── core/                    # Singleton services, guards, interceptors
│   ├── services/            # auth, api, theme...
│   ├── guards/              # auth.guard.ts
│   ├── interceptors/        # auth.interceptor.ts (JWT)
│   ├── models/              # *.models.ts
│   └── constants/
├── features/                # Feature components (lazy loaded)
│   ├── auth/login/          # login.component.{ts,html,css}
│   └── dashboard/
├── shared/                  # Reusable components, pipes, directives
│   ├── components/
│   ├── pipes/
│   └── directives/
├── app.config.ts            # Angular configuration
├── app.routes.ts            # Route definitions
├── app.component.ts         # Root component
└── main.ts                  # Entry point
```

## 10. Routage

```typescript
// app.routes.ts
import { Routes } from "@angular/router";
import { AuthGuard } from "./core/guards/auth.guard";

export const routes: Routes = [
  { path: "", pathMatch: "full", redirectTo: "dashboard" },
  {
    path: "login",
    loadComponent: () =>
      import("./features/auth/login/login.component").then(
        (m) => m.LoginComponent,
      ),
  },
  {
    path: "dashboard",
    loadComponent: () =>
      import("./features/dashboard/dashboard.component").then(
        (m) => m.DashboardComponent,
      ),
    canActivate: [AuthGuard],
  },
  { path: "**", redirectTo: "dashboard" },
];
```

## ⚠️ Erreurs courantes à éviter

- ❌ Anciennes directives `*ngIf`/`*ngFor` → ✅ `@if`/`@for`.
- ❌ Propriétés non réactives (`isLoading = false`) → ✅ `signal(false)`, `computed()`.
- ❌ HTML/CSS inlinés massivement → ✅ fichiers `.html`/`.css` séparés.
- ❌ Type `any` → ✅ types/interfaces spécifiques.
- ❌ Oubli de désabonnement RxJS → ✅ `takeUntilDestroyed(inject(DestroyRef))` ou signaux.

## Ressources

- [Angular 21 Docs](https://angular.dev/overview) · [Signals](https://angular.dev/guide/signals) · [Forms](https://angular.dev/guide/forms) · [Control Flow](https://angular.dev/guide/control-flow) · [DI](https://angular.dev/guide/di)
- [Bootstrap 5.3](https://getbootstrap.com/docs/5.3/) · [Bootstrap Icons](https://icons.getbootstrap.com/)
