[supervisord]
nodaemon=true
logfile=/dev/null
loglevel=info

[inet_http_server]
port=127.0.0.1:9001

[rpcinterface:supervisor]
supervisor.rpcinterface_factory = supervisor.rpcinterface:make_main_rpcinterface

[supervisorctl]
serverurl=http://127.0.0.1:9001

; Registry — permanent background service
[program:registry]
command=/usr/local/bin/registry serve /etc/registry/config.yml
autostart=true
autorestart=true
autorestart_delay=2
startretries=3
stdout_logfile=/dev/stdout
stdout_logfile_maxbytes=0
stderr_logfile=/dev/stderr
stderr_logfile_maxbytes=0
environment=OTEL_TRACES_EXPORTER="none",OTEL_METRICS_EXPORTER="none",OTEL_LOGS_EXPORTER="none"

; Trivy server — persistent HTTP API on localhost:4954
[program:trivy-server]
command=/usr/local/bin/trivy server --listen 127.0.0.1:4954 --cache-dir ${DATA_DIR}/cache/trivy
autostart=true
autorestart=true
stdout_logfile=/dev/stdout
stdout_logfile_maxbytes=0
stderr_logfile=/dev/stderr
stderr_logfile_maxbytes=0

; Portalcrane FastAPI backend
[program:portalcrane]
command=uvicorn app.main:app --host 0.0.0.0  --port 8000 --workers 2
directory=/app
autostart=true
autorestart=true
stdout_logfile=/dev/stdout
stdout_logfile_maxbytes=0
stderr_logfile=/dev/stderr
stderr_logfile_maxbytes=0
