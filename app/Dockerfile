# ── Production image ─────────────────────────────────────────────────────────
FROM python:3.12-slim AS base

WORKDIR /app

# Non-root user for security
RUN addgroup --system appgroup && adduser --system --ingroup appgroup appuser

# Install deps first (layer cache)
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

# Copy all application code
COPY --chown=appuser:appgroup . .

USER appuser

EXPOSE 3000
ENV PORT=3000 \
    ENVIRONMENT=production \
    PYTHONDONTWRITEBYTECODE=1

# Health checking is handled by the ECS/ALB target group — no HEALTHCHECK needed here
CMD ["sh", "-c", "gunicorn wsgi:app --bind 0.0.0.0:${PORT} --workers 2 --timeout 120 --access-logfile -"]

# ── Development image (used by docker-compose.yml) ───────────────────────────
FROM base AS development
USER root
RUN pip install --no-cache-dir flask[async] watchdog
USER appuser
ENV ENVIRONMENT=development \
    FLASK_DEBUG=1
CMD ["flask", "--app", "app:app", "run", "--host=0.0.0.0", "--port=3000", "--debug"]
