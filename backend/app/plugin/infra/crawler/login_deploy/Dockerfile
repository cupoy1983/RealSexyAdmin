FROM python:3.12-slim-bookworm
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PLAYWRIGHT_BROWSERS_PATH=/ms-playwright DISPLAY=:99
WORKDIR /opt/login
COPY backend/app/plugin/infra/crawler/requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir -r /tmp/requirements.txt \
    && python -m playwright install --with-deps chromium \
    && apt-get update && apt-get install -y --no-install-recommends xvfb x11vnc novnc websockify nginx-light apache2-utils \
    && rm -rf /var/lib/apt/lists/* \
    && useradd --uid 10001 --create-home collector \
    && mkdir -p /tmp/.X11-unix /session-health && chmod 1777 /tmp/.X11-unix \
    && chown 10001:10001 /session-health && chmod 700 /session-health
COPY --chown=10001:10001 backend/app/plugin/infra/crawler /opt/login/app/plugin/infra/crawler
COPY --chown=10001:10001 backend/app/plugin/infra/crawler/login_deploy/index.html /opt/login/web/index.html
COPY --chown=10001:10001 backend/app/plugin/infra/crawler/login_deploy/nginx.conf /opt/login/nginx.conf
COPY --chown=10001:10001 backend/app/plugin/infra/crawler/login_deploy/entrypoint.py /opt/login/entrypoint.py
USER collector
CMD ["python", "/opt/login/entrypoint.py"]
