FROM python:3.12.11-slim
WORKDIR /srv
COPY requirements.lock requirements-inference.txt requirements-ct-clip.txt requirements-rev-ct.txt /srv/
RUN pip install --no-cache-dir torch==2.8.0 --index-url https://download.pytorch.org/whl/cu126 \
    && pip install --no-cache-dir -r requirements.lock -r requirements-inference.txt \
    && pip check
ARG INSTALL_CT_CLIP=false
RUN if [ "$INSTALL_CT_CLIP" = "true" ]; then \
      apt-get update && apt-get install -y --no-install-recommends git libgl1 libglib2.0-0 \
      && pip install --no-cache-dir -r requirements-ct-clip.txt && pip check \
      && apt-get purge -y git && apt-get autoremove -y && rm -rf /var/lib/apt/lists/*; \
    fi
ARG INSTALL_REV_CT=false
RUN if [ "$INSTALL_REV_CT" = "true" ]; then \
      apt-get update && apt-get install -y --no-install-recommends git \
      && python -m venv --system-site-packages /opt/rev-ct \
      && /opt/rev-ct/bin/pip install --no-cache-dir -r requirements-rev-ct.txt \
      && apt-get purge -y git && apt-get autoremove -y && rm -rf /var/lib/apt/lists/*; \
    fi
COPY app /srv/app
COPY theme.css /srv/theme.css
COPY migrations /srv/migrations
COPY alembic.ini /srv/
COPY tools /srv/tools
RUN useradd --uid 10001 --create-home segmentation \
    && mkdir /work /models && chown segmentation /work /models
USER segmentation
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 TOTALSEG_DISABLE_USAGE_STATS=1 SEG_REV_CT_PYTHON=/opt/rev-ct/bin/python
CMD ["uvicorn", "app.main:from_env", "--factory", "--host", "127.0.0.1", "--port", "8096", "--no-access-log"]
