FROM python:3.12-slim

WORKDIR /app

# Non-root user for security
RUN addgroup --system appgroup && adduser --system --ingroup appgroup appuser

# Install deps first (layer cache)
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY --chown=appuser:appgroup app.py wsgi.py db.py auth.py ./

USER appuser

EXPOSE 3000
ENV PORT=3000 \
    ENVIRONMENT=production

# Health checking is handled by the ECS/ALB target group — no HEALTHCHECK needed here
CMD ["sh", "-c", "gunicorn wsgi:app --bind 0.0.0.0:${PORT} --workers 1 --timeout 60 --access-logfile -"]
