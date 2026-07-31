# Dashboard

The dashboard (`GET /api/dashboard/stats`) is the landing page after login
and gives a live overview of the registry's health and size.

## What it shows

| Metric              | Description                                                                                                                                        |
| ------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------- |
| **Total Images**    | Number of repositories in the local registry                                                                                                       |
| **Total Tags**      | Sum of all tags across every repository                                                                                                            |
| **Registry Size**   | Total stored data, human-readable (e.g. `4.2 GB`)                                                                                                  |
| **Largest Image**   | The single largest `repository:tag` and its size                                                                                                   |
| **Disk Usage**      | Container filesystem usage, shown as a colour-coded progress bar                                                                                   |
| **Users**           | Total account count and number of administrators — the admin count turns red once it exceeds 5, as a nudge to review who really needs admin rights |
| **Registry Status** | `ok` or `unreachable` — the embedded registry is pinged live                                                                                       |
| **Trivy**           | Whether vulnerability scanning is enabled, the running Trivy version, and when its CVE database was last updated                                   |

If the embedded registry is temporarily unreachable (e.g. still starting
up), the dashboard doesn't error out: it returns zeroed counters and
`registry_status: "unreachable"` so the rest of the UI stays usable.

## Quick actions

From the dashboard you can jump straight into the two most common
workflows:

- **Browse images** — opens the image browser (see
  [Image & Tag Management](image-management.md)).
- **Pull from Docker Hub** — opens the [Staging Pipeline](staging-pipeline.md)
  with the Docker Hub search preselected.

## Maintenance shortcut

The dashboard also surfaces **Garbage Collection** as a one-click
maintenance action — see
[System Maintenance → Garbage collection](system-maintenance.md#garbage-collection)
for what it actually does under the hood and when you should run it.
