# Edge proxy: serves collected static files and forwards everything else to Django.
#
#   docker build -f docker/nginx/Dockerfile -t watchgrid-nginx docker/nginx
FROM nginx:1.27-alpine
COPY nginx.conf /etc/nginx/conf.d/default.conf
COPY proxy_params.inc /etc/nginx/conf.d/proxy_params.inc
EXPOSE 8080
HEALTHCHECK --interval=30s --timeout=5s CMD wget -qO- http://127.0.0.1:8080/nginx-health || exit 1
