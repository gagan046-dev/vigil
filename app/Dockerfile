# vigil-core: api, notifier, demo-shop and migrate (one image, different commands)

FROM python:3.12.14-slim-bookworm AS build
ENV PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1
RUN python -m venv /opt/venv
COPY requirements.txt /tmp/requirements.txt
RUN /opt/venv/bin/pip install -r /tmp/requirements.txt

FROM python:3.12.14-slim-bookworm
LABEL org.opencontainers.image.source="https://github.com/gagan046-dev/vigil" \
      org.opencontainers.image.title="vigil-core"
ENV PATH=/opt/venv/bin:$PATH \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1
RUN groupadd --system --gid 10001 vigil \
 && useradd --system --uid 10001 --gid 10001 --no-create-home --home-dir /nonexistent --shell /usr/sbin/nologin vigil
COPY --from=build /opt/venv /opt/venv
WORKDIR /app
COPY --chown=root:root vigil /app/vigil
USER 10001:10001
EXPOSE 8000
CMD ["uvicorn", "vigil.api:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers", "--no-server-header"]
