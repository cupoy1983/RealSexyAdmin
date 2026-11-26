# Reuse the independently tested browser runtime; never install it in the OA backend venv.
ARG COLLECTOR_BASE_IMAGE=crawler-browser-login:local
FROM ${COLLECTOR_BASE_IMAGE}
COPY --chown=10001:10001 backend/app/plugin/infra/crawler /opt/login/app/plugin/infra/crawler
USER 0:0
RUN mkdir -p /session-health && chown 10001:10001 /session-health && chmod 700 /session-health
USER 10001:10001
CMD ["dbus-run-session", "--", "python", "-m", "app.plugin.infra.crawler.product_jobs.entrypoint"]
