# registry-config.yml.template
# Configuration for Docker Distribution Registry v3 (CNCF)
version: 0.1

log:
  accesslog:
    disabled: true
  level: debug
  formatter: text

storage:
  filesystem:
    rootdirectory: /workspaces/portalcrane/.data_env/registry
  delete:
    enabled: true
  maintenance:
    uploadpurging:
      enabled: true
      age: 168h
      interval: 24h
      dryrun: false

http:
  addr: 0.0.0.0:5000
  secret: 0123456789
  headers:
    X-Content-Type-Options: [nosniff]
    X-Frame-Options: [SAMEORIGIN]

reporting:
  enabled: false
