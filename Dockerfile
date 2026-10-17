FROM python:3.12.11-slim
WORKDIR /srv
COPY requirements.lock requirements-inference.txt /srv/
RUN pip install --no-cache-dir torch==2.8.0 --index-url https://download.pytorch.org/whl/cu126 \
    && pip install --no-cache-dir -r requirements.lock -r requirements-inference.txt \
    && pip check
COPY app /srv/app
COPY theme.css /srv/theme.css
COPY migrations /srv/migrations
COPY alembic.ini /srv/
COPY tools /srv/tools
RUN useradd --uid 10001 --create-home segmentation \
    && mkdir /work /models && chown segmentation /work /models
USER segmentation
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 TOTALSEG_DISABLE_USAGE_STATS=1
CMD ["uvicorn", "app.main:from_env", "--factory", "--host", "127.0.0.1", "--port", "8096", "--no-access-log"]
