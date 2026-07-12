# 🐳 Portalcrane

[English](README.md) · [Français](README.fr.md) · **Español**

**Portalcrane** es un gestor de registro Docker autoalojado.
Ofrece una interfaz moderna e intuitiva para navegar, buscar y gestionar imágenes
y etiquetas, con un proceso de preparación que incluye el análisis de
vulnerabilidades.
También permite declarar registros externos y realizar transferencias entre ellos.
El registro interno de Portalcrane permite organizar las imágenes en directorios.
Un modelo RBAC permite controlar el uso de las imágenes.

<img width="1432" height="942" alt="image" src="https://github.com/user-attachments/assets/a6fa3b39-e603-4562-b784-2fb5483b795c" />

---

## Tabla de contenidos

- [Funcionalidades](#funcionalidades)
- [Arquitectura](#arquitectura)
- [Requisitos previos](#requisitos-previos)
- [Inicio rápido](#inicio-rápido)
- [Puertos](#puertos)
- [Healthcheck](#healthcheck)
- [Notas de seguridad](#notas-de-seguridad)
- [Variables de entorno](#variables-de-entorno)
- [Panel de control](#panel-de-control)
- [Gestión de usuarios](#gestión-de-usuarios)
- [Tokens de acceso personal](#tokens-de-acceso-personal)
- [Control de acceso por directorio](#control-de-acceso-por-directorio)
- [Pipeline de preparación](#pipeline-de-preparación)
- [Registros externos y sincronización](#registros-externos-y-sincronización)
- [Persistencia de datos](#persistencia-de-datos)
- [Desarrollo](#desarrollo)
- [CI / CD](#ci--cd)
- [Capturas de pantalla](#capturas-de-pantalla)
- [Licencia](#licencia)

## Funcionalidades

- 🎨 Interfaz moderna con temas claro / oscuro / automático
- 🔐 Autenticación local (admin + cuentas por usuario) con soporte OIDC opcional
- 👥 Gestión multiusuario con permisos granulares pull / push
- 📁 Control de acceso por directorio (permisos pull/push locales y externos por directorio sobre los espacios de nombres de imágenes)
- 📦 Navegar, buscar y paginar imágenes y etiquetas
- 🗑️ Eliminar imágenes o etiquetas individuales
- 🏷️ Retag: añadir nuevas etiquetas a imágenes existentes
- 🚀 Pipeline de preparación: búsqueda en Docker Hub → Pull → análisis CVE con Trivy (opcional) → Push al registro
- 📊 Panel de control con estadísticas en tiempo real (número de imágenes, uso de disco, imagen más grande, número de usuarios y admins)
- 🔍 Modo avanzado: metadatos detallados de las imágenes (capas, etiquetas, variables de entorno, arquitectura…)
- 🌐 Registros externos: gestión CRUD + prueba de conectividad
- 🔄 Sincronización: enviar imágenes locales a registros externos (completa o por imagen)
- 📡 Soporte syslog en la pestaña Red
- 📋 Registros de auditoría: historial completo de operaciones de la API
- 🔒 Proxy de registro con aplicación de la autenticación
- ℹ️ Panel Acerca de con verificación de versión frente a la última release de GitHub
- 🐳 Despliegue en un único contenedor (frontend + backend + registro en una sola imagen)

---

## Arquitectura

| Capa                             | Tecnología                                                           |
| -------------------------------- | -------------------------------------------------------------------- |
| **Frontend**                     | Angular 22 — Signals, Signal Forms, Zoneless, componentes standalone |
| **Estilos**                      | Bootstrap 5 + Bootstrap Icons                                        |
| **Backend**                      | FastAPI + Python 3.14 (totalmente asíncrono)                         |
| **Validación**                   | Pydantic v2                                                          |
| **Registro**                     | Distribution (CNCF) v3 embebido                                      |
| **Análisis de vulnerabilidades** | Trivy (embebido)                                                     |
| **Transferencia de imágenes**    | skopeo (pull/push/copy sin demonio)                                  |
| **Contenedor**                   | Imagen única — supervisord orquesta todos los procesos               |
| **Plataformas**                  | `linux/amd64`, `linux/arm64` (Raspberry Pi, Apple Silicon)           |

---

## Requisitos previos

- Docker 24+ (o compatible)
- Docker Compose v2 (opcional, para el uso con compose)

## Inicio rápido

### Docker CLI

```bash
docker run -d \
  --name portalcrane \
  -p 8000:8000 \
  -v /portalcrane_data:/var/lib/portalcrane \
  cyrius44/portalcrane:latest
```

No es necesario proporcionar credenciales. En el primer arranque, se generan
automáticamente una contraseña de admin segura y una clave JWT `SECRET_KEY`, que se
persisten en el volumen de datos. La contraseña de admin se **muestra una sola vez
en los logs del contenedor** — el usuario por defecto es `admin`:

```bash
docker logs portalcrane | grep -A5 "initial admin account"
```

Abra **http://localhost:8080** e inicie sesión con `admin` y esa contraseña.

> **Nota:** montar un volumen persistente en `/var/lib/portalcrane` es obligatorio —
> la contraseña generada y la clave secreta se almacenan allí. Sin él, ambas se
> regeneran en cada reinicio.

### Docker Compose

```yaml
services:
  portalcrane:
    image: cyrius44/portalcrane:latest
    container_name: portalcrane
    ports:
      - "8000:8000"
    volumes:
      - portalcrane_data:/var/lib/portalcrane
    restart: unless-stopped

volumes:
  portalcrane_data:
```

### Uso

Acceda a la interfaz web (`http(s)://<ip o nombre>:8000`) para controlar, descargar
o subir sus imágenes, o directamente con comandos Docker mediante el proxy de registro.

```bash
docker login <host>:8000
docker pull <image>:<tag>
docker push <image>:<tag>
docker logout
```

Para un acceso completo sin autenticación, establezca la variable
`REGISTRY_PROXY_AUTH_ENABLED` en `false`.
Si utiliza la stack de desarrollo y desea acceso directo al registro, use
`<host>:5000`.

### Docker Compose (stack de desarrollo, desde este repositorio)

Esta stack construye la imagen local y también inicia un registro dedicado en el
puerto `5000`. Los datos del registro se almacenan en el volumen `registry_data`.

```bash
docker compose up -d
```

---

## Puertos

- `8000` — Interfaz + API de Portalcrane + proxy de registro
- `5000` — Registro Docker (solo stack de desarrollo)

## Healthcheck

`GET /api/health` devuelve una carga útil JSON de estado.

## Notas de seguridad

- La contraseña de admin se genera y se muestra en los logs en el primer arranque
  (usuario por defecto: `admin`). Se persiste bajo `DATA_DIR` y se reutiliza entre
  reinicios. Recupérela de los logs para el primer inicio de sesión — no puede
  establecerse mediante el entorno. Para rotarla, elimine
  `DATA_DIR/admin_password.hash` y reinicie.
- `SECRET_KEY` se genera automáticamente y se persiste bajo `DATA_DIR` en el primer
  arranque. Defínala explícitamente solo para compartir un secreto fijo entre
  varias instancias.
- Monte un volumen persistente en `DATA_DIR` (`/var/lib/portalcrane`) para que la
  contraseña generada y la clave secreta sobrevivan a los reinicios.
- Si expone la interfaz públicamente, active HTTPS — ya sea a nivel del reverse
  proxy o de forma nativa estableciendo `PRIVATE_KEY` / `PUBLIC_KEY` (véase TLS / SSL
  más abajo).

## Variables de entorno

### Autenticación

| Variable         | Descripción                                                  | Por defecto  |
| ---------------- | ------------------------------------------------------------ | ------------ |
| `ADMIN_USERNAME` | Nombre de usuario admin integrado                            | `admin`      |
| `SECRET_KEY`     | Secreto de firma JWT — generado y persistido si no se define | _(generado)_ |

> La contraseña de admin no es una variable de entorno: se genera automáticamente
> en el primer arranque y se muestra en los logs (véase
> [Notas de seguridad](#notas-de-seguridad)).

### TLS / SSL (opcional)

Ambas variables deben apuntar a archivos PEM montados dentro del contenedor. Cuando
se definen, el backend FastAPI (uvicorn) sirve directamente en HTTPS mediante
`--ssl-keyfile` / `--ssl-certfile`. Déjelas sin definir para servir en HTTP simple
y terminar TLS en un reverse proxy.

| Variable      | Descripción                                             | Por defecto |
| ------------- | ------------------------------------------------------- | ----------- |
| `PRIVATE_KEY` | Ruta del archivo de clave privada TLS (`--ssl-keyfile`) | —           |
| `PUBLIC_KEY`  | Ruta del archivo de certificado TLS (`--ssl-certfile`)  | —           |

### Registro

| Variable                      | Descripción                                          | Por defecto |
| ----------------------------- | ---------------------------------------------------- | ----------- |
| `REGISTRY_PROXY_AUTH_ENABLED` | Imponer autenticación en el proxy de registro `/v2/` | `true`      |

### API, Swagger y tokens de acceso personal

| Variable           | Descripción                                           | Por defecto |
| ------------------ | ----------------------------------------------------- | ----------- |
| `SWAGGER_ENABLED`  | Exponer la interfaz Swagger en `/api/docs`            | `false`     |
| `API_KEYS_ENABLED` | Habilitar la funcionalidad de tokens personales (PAT) | `true`      |

Con `API_KEYS_ENABLED=false`, los PAT quedan totalmente desactivados: los
endpoints de tokens devuelven `403`, las claves existentes con scope API son
rechazadas en la API REST y el panel de generación se oculta del perfil.

### Reverse proxy y limitación de tasa

| Variable                          | Descripción                                                                        | Por defecto       |
| --------------------------------- | ---------------------------------------------------------------------------------- | ----------------- |
| `TRUSTED_PROXIES`                 | Rangos CIDR (o IP sueltas), separados por comas, de los reverse proxies delanteros | —                 |
| `RATE_LIMIT_ENABLED`              | Activar el limitador de tasa por IP en las rutas `/api/*`                          | `true`            |
| `RATE_LIMIT_WINDOW_SECONDS`       | Duración de la ventana deslizante, en segundos                                     | `60`              |
| `RATE_LIMIT_MAX_REQUESTS`         | Máximo de solicitudes por IP y ventana, todas las rutas `/api/*`                   | `100`             |
| `RATE_LIMIT_LOGIN_PATH`           | Ruta del endpoint de login, limitada en su propio contador                         | `/api/auth/login` |
| `RATE_LIMIT_LOGIN_WINDOW_SECONDS` | Duración de la ventana deslizante del contador de login, en segundos               | `300`             |
| `RATE_LIMIT_LOGIN_MAX_ATTEMPTS`   | Presupuesto más estricto por IP y ventana de login                                 | `5`               |

`TRUSTED_PROXIES` define la frontera de confianza de los reverse proxies. Las IP
de cliente reenviadas (`Forwarded` / `X-Forwarded-For` / `X-Real-IP`) solo se
tienen en cuenta **si** el par TCP directo pertenece a uno de estos rangos; de lo
contrario las cabeceras se consideran falsificables y se ignoran, recurriendo a
la dirección real del par. Esta IP de cliente resuelta alimenta **tanto** el
registro de auditoría como el limitador de tasa por IP.

> Dejarla vacía (por defecto) indexa cada solicitud por el par TCP real —
> seguro, pero cuando la aplicación está detrás de un reverse proxy **todos** los
> clientes comparten entonces la IP del proxy, y por tanto un único contador de
> límite. Ajústela a la red de su proxy (p. ej. `TRUSTED_PROXIES=10.0.0.0/8,172.16.0.0/12`)
> para que la limitación por cliente y la auditoría vean la dirección real del
> cliente. Liste solo los proxies que controle realmente — confiar en un rango
> permite que cualquier cosa dentro de él falsifique las IP de cliente.

El estado de la limitación reside en la memoria del proceso: es adecuado para el
despliegue de un solo contenedor (un único proceso Uvicorn), pero **no** se
comparte entre workers ni réplicas.

### OIDC (opcional)

| Variable                        | Descripción                                               | Por defecto            |
| ------------------------------- | --------------------------------------------------------- | ---------------------- |
| `OIDC_ENABLED`                  | Activar el inicio de sesión OIDC                          | `false`                |
| `OIDC_ISSUER`                   | URL del emisor OIDC                                       | —                      |
| `OIDC_CLIENT_ID`                | ID de cliente OIDC                                        | —                      |
| `OIDC_CLIENT_SECRET`            | Secreto de cliente OIDC                                   | —                      |
| `OIDC_REDIRECT_URI`             | URI de redirección OIDC                                   | —                      |
| `OIDC_POST_LOGOUT_REDIRECT_URI` | URI de redirección tras cierre de sesión                  | —                      |
| `OIDC_RESPONSE_TYPE`            | Tipo de respuesta OIDC                                    | `code`                 |
| `OIDC_SCOPE`                    | Scopes OIDC                                               | `openid profile email` |
| `OIDC_ONLY`                     | Desactivar todo inicio de sesión local (modo solo OIDC)   | `false`                |
| `OIDC_ADMIN_GROUP_CLAIM`        | Claim OIDC que contiene los grupos/roles del usuario      | —                      |
| `OIDC_ADMIN_GROUP`              | Valor de grupo que concede permisos de admin              | —                      |
| `OIDC_USER_GROUP_CLAIM`         | Claim OIDC que contiene los grupos/roles del usuario      | —                      |
| `OIDC_USER_GROUP`               | Valor de grupo que concede acceso de usuario estándar     | —                      |
| `OIDC_RESTRICT_TO_GROUPS`       | Restringir el acceso a los grupos mapeados (lista blanca) | `false`                |

Estos valores pueden sobrescribirse desde la interfaz (**Ajustes → OIDC**).

#### Paquete de CA personalizado (PKI privada)

Cuando su proveedor OIDC está protegido por una CA privada (autofirmada o PKI
interna), proporcione la cadena de CA (intermedia + raíz, concatenadas en un solo
archivo PEM) mediante la variable de entorno estándar `SSL_CERT_FILE` o
`REQUESTS_CA_BUNDLE` apuntando a un archivo montado. Las llamadas OIDC salientes
confían entonces en esa cadena en lugar del paquete certifi predeterminado. Deje
ambas sin definir para conservar la verificación predeterminada.

#### Modo solo OIDC y arranque del admin

Por defecto, el inicio de sesión OIDC se ofrece **junto** al inicio de sesión local
admin/usuario. Establezca `OIDC_ONLY=true` para **desactivar todo inicio de sesión
local por contraseña — incluido el admin de entorno integrado** — y autenticarse
únicamente mediante su proveedor.

Como no hay solución de emergencia, el modo solo OIDC requiere el mapeo del grupo de
admin para no bloquearse fuera. Los permisos de admin se reevalúan en **cada** inicio
de sesión SSO (promoción/degradación en vivo), mediante el mapeo de claim de grupo:

- **Mapeo de claim de grupo** — `OIDC_ADMIN_GROUP_CLAIM=groups` y
  `OIDC_ADMIN_GROUP=registry-admins` (asegúrese de que el scope exponga ese claim).

La interfaz se niega a activar el modo solo OIDC hasta que esto esté configurado.

#### Restringir el acceso a usuarios específicos (lista blanca)

Por defecto, **cada** usuario SSO autenticado se aprovisiona como usuario estándar.
Para restringir el acceso, mapee los usuarios estándar de la misma manera que los
admins — mediante un mapeo de claim de grupo:

- **Mapeo de claim de grupo** — `OIDC_USER_GROUP_CLAIM=groups` y
  `OIDC_USER_GROUP=registry-users`.

Luego establezca `OIDC_RESTRICT_TO_GROUPS=true` (o marque **Restringir el acceso a
los grupos mapeados** en la interfaz) para convertir el acceso OIDC en una lista
blanca: solo los admins y usuarios que coincidan con los mapeos podrán iniciar
sesión. A cualquier usuario SSO que **no** pertenezca **ni** al grupo de admin **ni**
al grupo de usuario se le **deniega el acceso** (`403`) y su cuenta **no se crea**.
Los claims de los grupos de admin y de usuario pueden apuntar a claims distintos;
ambos se leen al iniciar sesión. El acceso se reevalúa en **cada** inicio de sesión
SSO.

#### Protección anti-suplantación

Las identidades OIDC se mantienen estrictamente separadas de las cuentas locales para
evitar la escalada de privilegios: un inicio de sesión SSO se **rechaza (403)** cuando
el nombre de usuario resuelto coincide con el admin de entorno integrado o con
cualquier cuenta local existente (basada en contraseña). Esto impide que un proveedor
que devuelva `preferred_username=admin` — o cualquier nombre que colisione con un
usuario local — herede los permisos de esa cuenta.

### Análisis de vulnerabilidades (Trivy)

| Variable               | Descripción                                                          | Por defecto     |
| ---------------------- | -------------------------------------------------------------------- | --------------- |
| `TRIVY_ENABLED`        | Interruptor maestro — inicia el servidor Trivy embebido y el escaneo | `true`          |
| `VULN_SCAN_ENABLED`    | Activar el análisis CVE con Trivy en el pipeline de preparación      | `true`          |
| `VULN_SCAN_SEVERITIES` | Niveles de severidad bloqueantes (separados por comas)               | `CRITICAL,HIGH` |
| `VULN_IGNORE_UNFIXED`  | Ignorar los CVE sin corrección disponible                            | `false`         |
| `VULN_SCAN_TIMEOUT`    | Tiempo de espera del análisis Trivy                                  | `5m`            |

> Establezca `TRIVY_ENABLED=false` (también acepta `0`, `no`, `off`) para desarmar
> completamente Trivy: el servidor embebido no lo inicia supervisord, se omite la
> actualización de la base de datos, no se ejecuta ningún análisis y los endpoints
> de escaneo devuelven `503`. Los demás valores `VULN_*` se ignoran entonces.

Estos valores pueden sobrescribirse desde la interfaz.

### Proxy HTTP (opcional)

| Variable      | Descripción                        | Por defecto           |
| ------------- | ---------------------------------- | --------------------- |
| `HTTP_PROXY`  | URL del proxy HTTP                 | —                     |
| `HTTPS_PROXY` | URL del proxy HTTPS                | —                     |
| `NO_PROXY`    | Hosts no-proxy separados por comas | `localhost,127.0.0.1` |

### Registro (logging)

| Variable           | Descripción                                                | Por defecto |
| ------------------ | ---------------------------------------------------------- | ----------- |
| `LOG_LEVEL`        | Nivel de log (`DEBUG`, `INFO`, `WARNING`, `ERROR`)         | `INFO`      |
| `AUDIT_MAX_EVENTS` | Número máximo de eventos de auditoría conservados en disco | `100`       |

### Correo electrónico (opcional)

Envía el registro de auditoría por correo mediante SMTP. Todos estos valores se
pueden sobrescribir desde la interfaz (**Ajustes → Red → Correo**), que también
ofrece un botón de **prueba**.

| Variable             | Descripción                                          | Por defecto             |
| -------------------- | ---------------------------------------------------- | ----------------------- |
| `EMAIL_ENABLED`      | Habilitar el envío de correos                        | `false`                 |
| `EMAIL_HOST`         | Host del servidor SMTP                               | —                       |
| `EMAIL_PORT`         | Puerto del servidor SMTP                             | `587`                   |
| `EMAIL_SECURITY`     | Seguridad de la conexión (`none`, `starttls`, `ssl`) | `starttls`              |
| `EMAIL_USERNAME`     | Usuario SMTP (vacío para anónimo)                    | —                       |
| `EMAIL_PASSWORD`     | Contraseña SMTP                                      | —                       |
| `EMAIL_FROM_ADDRESS` | Dirección del remitente                              | —                       |
| `EMAIL_TO_ADDRESSES` | Destinatarios separados por comas                    | —                       |
| `EMAIL_SUBJECT`      | Prefijo del asunto                                   | `Portalcrane audit log` |
| `EMAIL_NOTIFY_LOGIN` | Enviar un correo en cada conexión / desconexión      | `false`                 |
| `EMAIL_NOTIFY_AUDIT` | Enviar un correo en cada otro evento de auditoría    | `false`                 |

`EMAIL_NOTIFY_LOGIN` y `EMAIL_NOTIFY_AUDIT` son dos opciones independientes de
envío **automático por evento**; son distintas de la exportación bajo demanda del
registro de auditoría, que sigue disponible desde el panel de Red.

### Almacenamiento (debug)

| Variable   | Descripción                                    | Por defecto            |
| ---------- | ---------------------------------------------- | ---------------------- |
| `DATA_DIR` | Directorio de datos base dentro del contenedor | `/var/lib/portalcrane` |

---

## Panel de control

El panel de control muestra una vista general en tiempo real del registro:

- **Total de imágenes** — número de repositorios en el registro
- **Tamaño del registro** — total de datos almacenados (legible por humanos)
- **Uso de disco** — porcentaje de uso de disco con una barra de progreso codificada por colores
- **Usuarios** — número total de cuentas y número de administradores (el contador de admins se pone en rojo cuando supera los 5)

También expone acciones rápidas (navegar imágenes, descargar desde Docker Hub) y
operaciones de mantenimiento (Garbage Collection).

---

## Gestión de usuarios

Portalcrane admite dos tipos de cuentas:

- **Admin integrado** — la cuenta `admin` cuya contraseña se genera automáticamente
  y se muestra en los logs en el primer arranque. No puede eliminarse ni modificarse
  desde la interfaz.
- **Usuarios locales** — creados mediante el panel **Ajustes → Cuentas**. A cada
  usuario se le puede asignar:
  - Rol de admin (acceso completo)
  - Permiso pull (leer imágenes del registro)
  - Permiso push (escribir / eliminar imágenes en el registro)

---

## Tokens de acceso personal

Todo usuario autenticado puede generar **tokens de acceso personal** desde el panel **menú de la cuenta → Tokens de acceso personal**. Son especialmente útiles para los usuarios OIDC que no tienen contraseña local. El valor sin procesar del token se muestra **solo una vez** en el momento de la creación (se almacena como hash bcrypt y se identifica internamente mediante un `jti` único y firmado).

Cada token se crea con **uno de dos scopes mutuamente excluyentes**:

| Scope      | `docker login` | API REST / Swagger | Token corto 16 car. |
| ---------- | :------------: | :----------------: | :-----------------: |
| **Docker** |       ✅       |         ❌         |         ✅          |
| **API**    |       ❌       |         ✅         |         ❌          |

Un token creado para un scope se rechaza en el otro: así, una credencial Docker de CI nunca puede alcanzar la API REST, y una clave API nunca puede usarse para descargar/subir imágenes.

### Tokens con scope Docker

Use el token como contraseña para `docker login` — ya sea el token completo o el token corto de 16 caracteres mostrado en la creación:

```bash
docker login <host>:8000 -u <username> -p <token>
```

### Tokens con scope API (Swagger / API REST)

Use el token como clave API enviándolo en la cabecera `Authorization`:

```bash
curl -H "Authorization: Bearer <token>" http(s)://<host>:8000/api/auth/me
```

En **Swagger UI** (`/api/docs`, habilitado con `SWAGGER_ENABLED=true`): haga clic en **Authorize**, elija **PersonalAccessToken**, pegue el token, y todas las solicitudes se autenticarán en su nombre.

Los tokens pueden revocarse en cualquier momento desde el mismo panel; los tokens caducados o revocados se rechazan de inmediato. Al eliminar un usuario también se revocan todos sus tokens.

> Los endpoints del proxy de registro (`/v2/...`) implementan la API HTTP del Docker Registry y están ocultos intencionadamente de Swagger, ya que solo son relevantes para el CLI de Docker.

---

## Control de acceso por directorio

Los administradores pueden definir **directorios** (prefijos de espacios de nombres
de imágenes, p. ej. `production/`) y asignar permisos en cada directorio. Los usuarios
no admin solo pueden acceder a las imágenes cuya ruta coincida con un directorio al
que se les haya concedido acceso.

Se pueden conceder cuatro permisos independientes por directorio:

- **Pull** — leer imágenes del registro **local** integrado.
- **Push** — escribir / eliminar imágenes en el registro **local** integrado.
- **Pull externo** — traer imágenes **hacia** Portalcrane **desde** un registro
  realmente externo (Docker Hub, registros guardados o ad-hoc) desde la página de preparación.
- **Push externo** — enviar imágenes **fuera** de Portalcrane **hacia** un registro
  realmente externo desde la página de preparación.

Los permisos pull / push externos son independientes de sus equivalentes locales:
rigen las transferencias con registros externos, no las lecturas y escrituras en el
registro local.

---

## Pipeline de preparación

1. **Buscar** una imagen en Docker Hub
2. **Seleccionar** una imagen y una etiqueta
3. **Pull** — skopeo descarga la imagen como un layout OCI (sin necesidad de demonio Docker)
4. **Análisis** — Trivy analiza la imagen en busca de CVE (opcional, umbrales de severidad configurables)
5. **Push** — skopeo copia el layout OCI al registro privado con un nombre y una etiqueta elegidos

---

## Registros externos y sincronización

- Añadir registros externos compatibles con Docker (Docker Hub, GHCR, Quay, autoalojados…)
- Probar la conectividad y la autenticación directamente desde la interfaz
- Sincronizar imágenes locales a un registro externo (registro completo o por imagen/directorio)

---

## Persistencia de datos

Todos los datos persistentes se almacenan en **/var/lib/portalcrane** dentro del
contenedor. Monte un volumen en esta ruta para conservar los datos entre reinicios
del contenedor:

```
-v /portalcrane_data:/var/lib/portalcrane
```

Los datos almacenados incluyen: el hash de la contraseña de admin generada
(`admin_password.hash`), el secreto de firma JWT (`secret_key`), los usuarios
locales, la configuración OIDC, los permisos de directorios, los registros externos,
los registros de auditoría y los propios datos de imagen del registro.

---

## Desarrollo

### Backend

```bash
cd backend
pip install -r requirements.txt
uvicorn main:app --reload --port 8000
```

### Frontend

```bash
cd frontend
npm install
ng serve --port 4200 --proxy-config proxy.conf.json
```

---

## CI / CD

El workflow de GitHub Actions (`.github/workflows/docker-publish.yml`) automáticamente:

1. Construye una imagen (`linux/amd64`) en cada push a `main` y en las etiquetas de versión
2. Publica en **Docker Hub** (`cyrius44/portalcrane`) y **GHCR** (`ghcr.io/cyr-ius/portalcrane`)
3. Actualiza la descripción de Docker Hub a partir de este README

Las etiquetas de imagen siguen el versionado semántico: `latest`, `edge`, `X`, `X.Y`, `X.Y.Z`, `sha-<commit>`.

### Publicar una release

Use el script de ayuda — incrementa la versión del frontend, la confirma, etiqueta **ese** commit y hace push, de modo que la etiqueta siempre contenga una versión coherente:

```bash
scripts/release.sh patch     # o minor / major / una versión explícita X.Y.Z
```

El push de la etiqueta `X.Y.Z` desencadena la release y la construcción de la imagen. Use `--no-push` para preparar el commit y la etiqueta localmente sin hacer push.

---

## Capturas de pantalla

<img width="1435" height="942" alt="image" src="https://github.com/user-attachments/assets/18e00fb2-76e2-4ece-8ece-fc11fad16eff" />

<img width="1435" height="942" alt="image" src="https://github.com/user-attachments/assets/ebf83322-14ba-4331-a9d3-72d04737d9a5" />

<img width="1435" height="942" alt="image" src="https://github.com/user-attachments/assets/7ba30cdf-ee0f-4693-a796-bec6a17f692e" />

---

## Licencia

MIT — véase [LICENSE](LICENSE) para más detalles.

## Acerca de

Autor: [@cyr-ius](https://github.com/cyr-ius) — Patrocinador: [GitHub Sponsors](https://github.com/sponsors/cyr-ius)
