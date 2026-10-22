FROM node:24-bookworm-slim AS frontend
WORKDIR /build
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build

FROM python:3.14-slim-bookworm AS backend
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app
COPY backend/requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt && groupadd -g 10001 app && useradd -u 10001 -g app -d /app app && mkdir -p /app/private-media && chown app:app /app/private-media
COPY --chown=app:app backend/ ./
USER app
EXPOSE 8000
CMD ["gunicorn","config.wsgi:application","--no-control-socket","--bind","0.0.0.0:8000","--workers","2","--threads","8","--timeout","120","--max-requests","1000","--max-requests-jitter","100","--access-logfile","-","--error-logfile","-"]

FROM backend AS chat-worker
USER root
COPY deploy/install-codex.py /tmp/install-codex.py
RUN python /tmp/install-codex.py && rm /tmp/install-codex.py && usermod -d /home/app app && mkdir -p /home/app/.codex && chown -R app:app /home/app
ENV HOME=/home/app
USER app
CMD ["python", "manage.py", "chat_worker"]

FROM nginx:1.28-alpine AS web
COPY --from=frontend /build/dist /usr/share/nginx/html
RUN find /usr/share/nginx/html -type d -exec chmod 755 {} + && find /usr/share/nginx/html -type f -exec chmod 644 {} +
COPY deploy/nginx.conf.template /etc/nginx/templates/default.conf.template
