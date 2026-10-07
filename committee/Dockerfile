FROM python:3.12-slim-bookworm@sha256:7753c33391fc9f01d1984375bf375eb6686d52ba10db6043a86634a5ccf90dcf AS dependencies
COPY requirements.txt /requirements.txt
COPY .local/committee-wheels/ /wheels/
RUN pip install --no-cache-dir --no-index --find-links=/wheels --prefix=/install -r /requirements.txt

FROM postgres:17-bookworm@sha256:3645570cccdfa447589da9f57dd740faa29b30938e861289a5574b6ca6b03826 AS postgres_tools
RUN mkdir -p /export/bin /export/lib && for program in pg_dump pg_restore; do \
    cp /usr/lib/postgresql/17/bin/$program /export/bin/; \
    ldd /usr/lib/postgresql/17/bin/$program | awk '/=> \// {print $3}' | while read library; do \
    case "$library" in */libc.so.*|*/libm.so.*|*/libpthread.so.*|*/libdl.so.*) ;; *) cp -L "$library" /export/lib/;; esac; done; done

FROM python:3.12-slim-bookworm@sha256:7753c33391fc9f01d1984375bf375eb6686d52ba10db6043a86634a5ccf90dcf
COPY --from=dependencies /install /usr/local
COPY --from=postgres_tools /export /pg-tools
RUN printf '#!/bin/sh\nLD_LIBRARY_PATH=/pg-tools/lib exec /pg-tools/bin/pg_dump "$@"\n' > /usr/local/bin/pg_dump \
    && printf '#!/bin/sh\nLD_LIBRARY_PATH=/pg-tools/lib exec /pg-tools/bin/pg_restore "$@"\n' > /usr/local/bin/pg_restore \
    && chmod 755 /usr/local/bin/pg_dump /usr/local/bin/pg_restore
WORKDIR /app
COPY config/ config/
COPY portal/ portal/
COPY static/ static/
COPY templates/ templates/
COPY scripts/ scripts/
COPY committee/entrypoint.sh /entrypoint.sh
COPY manage.py requirements.txt ./
COPY --chown=1000:1000 .local/committee-reference/ /app/reference/
RUN DJANGO_SECRET_KEY=build-static-assets-only-no-runtime-use python manage.py collectstatic --noinput \
    && chmod -R a+rX /app /pg-tools && chmod 644 /entrypoint.sh \
    && chmod 700 /app/reference && chmod 600 /app/reference/* \
    && groupadd --gid 1000 wash && useradd --uid 1000 --gid 1000 --no-create-home wash \
    && mkdir -p /committee && chown wash:wash /committee && chmod 700 /committee
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 DJANGO_SETTINGS_MODULE=config.committee WASH_COMMITTEE_DEMO=1 WASH_COMMITTEE_STATE=/committee PGSSLMODE=disable
USER wash
EXPOSE 8080
ENTRYPOINT ["/bin/bash", "/entrypoint.sh"]
