# vigil-frontend: static logbook UI + /api proxy, served by unprivileged nginx on :8080.
FROM nginxinc/nginx-unprivileged:1.30.4-alpine

# Files stay root-owned and read-only; nginx runs as uid 101 and only needs to read them.
COPY nginx.conf /etc/nginx/conf.d/default.conf
COPY security-headers.conf /etc/nginx/snippets/security-headers.conf
COPY index.html app.js styles.css favicon.svg /usr/share/nginx/html/

USER 101
EXPOSE 8080

HEALTHCHECK --interval=30s --timeout=3s --retries=3 \
  CMD wget -q -O /dev/null http://127.0.0.1:8080/healthz || exit 1
