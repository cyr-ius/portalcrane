# 🐳 Portalcrane

![License](https://img.shields.io/badge/License-MIT-blue)
![Python](https://img.shields.io/badge/Python-3.12%2B-blue)
![Angular](https://img.shields.io/badge/Angular-22-green)
[![ci::status]][ci::github]
[![docker::pulls]][docker::hub]
[![documentation::badge]][documentation::web]

[ci::status]: https://img.shields.io/github/actions/workflow/status/cyr-ius/portalcrane/docker-publish.yml?color=blue&logo=github
[ci::github]: https://github.com/cyr-ius/portalcrane/actions
[docker::pulls]: https://img.shields.io/docker/pulls/cyrius44/portalcrane.svg?logo=docker
[docker::hub]: https://hub.docker.com/r/cyrius44/portalcrane
[documentation::badge]: https://img.shields.io/badge/DOCUMENTATION-GH%20PAGES-0078D4?logo=googledocs
[documentation::web]: https://cyr-ius.github.io/portalcrane/

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

Imagen única, sin base de datos externa, `linux/amd64`/`linux/arm64` — véase la
página [Architecture](https://cyr-ius.github.io/portalcrane/architecture/)
(en inglés) para el detalle completo de la pila tecnológica.

---

## Inicio rápido

```bash
docker run -d \
  --name portalcrane \
  -p 8000:8000 \
  -v /portalcrane_data:/var/lib/portalcrane \
  cyrius44/portalcrane:latest
```

No es necesario proporcionar credenciales. En el primer arranque se genera
automáticamente una contraseña de admin segura, que se **muestra una sola vez
en los logs del contenedor** — el usuario por defecto es `admin`:

```bash
docker logs portalcrane | grep -A5 "initial admin account"
```

Abra **http://localhost:8000** e inicie sesión con `admin` y esa contraseña.

> **Nota:** montar un volumen persistente en `/var/lib/portalcrane` es obligatorio —
> la contraseña generada y la clave secreta JWT se almacenan allí. Sin él, ambas se
> regeneran en cada reinicio.

O con Docker Compose:

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

Una vez en marcha, use la interfaz web o directamente el CLI de Docker a través
del proxy de registro:

```bash
docker login <host>:8000
docker pull <image>:<tag>
docker push <image>:<tag>
docker logout
```

Para la guía de instalación completa (TLS, reverse proxy, stack de desarrollo,
notas de seguridad), véase **[Getting Started](https://cyr-ius.github.io/portalcrane/getting-started/installation/)**
(en inglés).

---

## Documentación

La documentación completa —referencia de configuración, cada funcionalidad
explicada con ejemplos, la API REST y cómo contribuir— está publicada en
**[cyr-ius.github.io/portalcrane](https://cyr-ius.github.io/portalcrane/)**
(en inglés).

| Busca...                                                     | Vaya a                                                                                                  |
| ------------------------------------------------------------ | ------------------------------------------------------------------------------------------------------- |
| Todas las variables de entorno                               | [Environment Variables](https://cyr-ius.github.io/portalcrane/configuration/environment-variables/)     |
| La configuración de OIDC / SSO                               | [Authentication & OIDC](https://cyr-ius.github.io/portalcrane/configuration/authentication/)            |
| Usuarios, grupos, permisos por directorio                    | [Users, Groups & Permissions](https://cyr-ius.github.io/portalcrane/features/users-groups-permissions/) |
| El pipeline de preparación y el análisis de vulnerabilidades | [Staging Pipeline](https://cyr-ius.github.io/portalcrane/features/staging-pipeline/)                    |
| Los registros externos y las transferencias entre registros  | [External Registries & Transfers](https://cyr-ius.github.io/portalcrane/features/external-registries/)  |
| Los endpoints de la API REST                                 | [API Reference](https://cyr-ius.github.io/portalcrane/api-reference/)                                   |
| Ejecutar el frontend/backend en local, publicar una release  | [Development](https://cyr-ius.github.io/portalcrane/development/)                                       |

---

## Licencia

MIT — véase [LICENSE](LICENSE) para más detalles.

## Acerca de

Autor: [@cyr-ius](https://github.com/cyr-ius) — Patrocinador: [GitHub Sponsors](https://github.com/sponsors/cyr-ius)
