FROM python:3.12-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

RUN addgroup --system eastmed && adduser --system --ingroup eastmed eastmed

WORKDIR /app
COPY pyproject.toml README.md ./
COPY apps/api ./apps/api
COPY packages ./packages
COPY config ./config
COPY prompts ./prompts
COPY infra/postgres ./infra/postgres
RUN pip install --no-cache-dir .
RUN mkdir -p /app/data/snapshots && chown -R eastmed:eastmed /app/data

USER eastmed
EXPOSE 8000

CMD ["uvicorn", "eastmed_api.main:app", "--host", "0.0.0.0", "--port", "8000"]
