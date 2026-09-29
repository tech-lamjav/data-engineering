# Mesmo runtime do deploy do sync-bq-to-postgres: Linux, Python 3.13, psycopg pinado
# (Linux é o que importa: PREFER_FLUSH do psycopg só liga no macOS).
FROM python:3.13-slim
ARG PSYCOPG=3.2.3
RUN pip install --no-cache-dir "psycopg[binary]==${PSYCOPG}" google-cloud-bigquery==3.27.0 python-dotenv==1.0.0
