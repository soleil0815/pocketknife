# POCKET KNIFE – reproducible build
#
# All inputs are pinned once tools/pin-versions.sh has run:
#   PYTHON_IMAGE      base image with exact Python version AND digest (sha256)
#   DEBIAN_SNAPSHOT   point in time on snapshot.debian.org – apt always delivers the same Debian packages
#   docker/apt-*.txt  Debian packages with version (package=version)
#   requirements.lock all Python packages incl. dependencies with exact version (==)
#   build-requirements.txt  build tools (Cython, setuptools) with exact version
# Without requirements.lock the build uses requirements.txt (minimum versions) and prints a warning.
ARG PYTHON_IMAGE=python:3.12.15-slim-trixie@sha256:a0f57064cbdcd6285fa45f51237ab0b35ea8ebaed06a1f5973e89e19dfb86704
ARG DEBIAN_SNAPSHOT=20261026T090907Z

# --- Build: build Python packages as wheels (gssapi/krb5 need a compiler and Kerberos headers) ---
FROM ${PYTHON_IMAGE} AS build
ARG DEBIAN_SNAPSHOT
ENV PIP_DISABLE_PIP_VERSION_CHECK=1
COPY docker/apt-install.sh docker/apt-build.txt /tmp/pk/
RUN sh /tmp/pk/apt-install.sh "$DEBIAN_SNAPSHOT" /tmp/pk/apt-build.txt
COPY requirements.txt requirements.lock requirements.caps build-requirements.txt /tmp/pk/
# Install the build tools first (so pin-versions.sh can read their versions) and use exactly these versions
# via PIP_CONSTRAINT in the isolated build environments of gssapi/krb5 as well
RUN set -e; cd /tmp/pk; \
    pip install --no-cache-dir -r build-requirements.txt; \
    if grep -q '^[A-Za-z0-9].*==' requirements.lock; then \
      PIP_CONSTRAINT=/tmp/pk/build-requirements.txt pip wheel --no-cache-dir --no-deps --wheel-dir /wheels -r requirements.lock; \
    else \
      echo "WARNING: requirements.lock is empty – versions not pinned (run tools/pin-versions.sh)."; \
      PIP_CONSTRAINT=/tmp/pk/build-requirements.txt pip wheel --no-cache-dir --wheel-dir /wheels -r requirements.txt -c requirements.caps; \
    fi

# --- Runtime -------------------------------------------------------------------
FROM ${PYTHON_IMAGE}
ARG DEBIAN_SNAPSHOT

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PK_DATA_DIR=/data \
    TZ=Europe/Berlin \
    DEBIAN_FRONTEND=noninteractive

COPY docker/apt-install.sh docker/apt-runtime.txt /tmp/pk/
RUN sh /tmp/pk/apt-install.sh "$DEBIAN_SNAPSHOT" /tmp/pk/apt-runtime.txt \
 && rm -f /etc/krb5.conf \
 && groupadd --system --gid 10001 pocketknife \
 && useradd --system --uid 10001 --gid 10001 --home-dir /nonexistent --no-create-home --shell /usr/sbin/nologin pocketknife

WORKDIR /opt/pocketknife
COPY requirements.txt requirements.lock requirements.caps ./
COPY --from=build /wheels /tmp/wheels
RUN set -e; \
    if grep -q '^[A-Za-z0-9].*==' requirements.lock; then \
      pip install --no-cache-dir --no-index --no-deps --find-links /tmp/wheels -r requirements.lock; \
    else \
      pip install --no-cache-dir --no-index --find-links /tmp/wheels -r requirements.txt -c requirements.caps; \
    fi; \
    pip check; \
    python -m pip uninstall -y -q pip; \
    rm -rf /tmp/wheels /tmp/pk /usr/local/lib/python3*/ensurepip/_bundled
# pip wird zur Laufzeit nicht gebraucht – entfernt, damit keine veraltete Komponente im Image bleibt (2.29.4)

# Temporary files in the data directory (the root file system is read-only in operation, /tmp is small)
# HOME: für root (Healthcheck, docker exec) ein Ort, an dem niemand etwas ablegen kann – sonst könnte der App-Benutzer
# über $HOME/.local (usercustomize.py, *.pth) Code als root unterschieben; der Dienst selbst setzt HOME=/tmp
ENV TMPDIR=/data/tmp \
    HOME=/nonexistent

COPY app ./app
COPY run.py healthcheck.py VERSION CHANGELOG.md ./
COPY tools/selftest.py ./tools/selftest.py
# 2.33: the files for the Docker host that belong to exactly this version – update.sh takes a newer docker-compose.yml
# from here (nothing else of the program is ever placed on the host)
COPY docker-compose.yml .env.example update.sh /opt/pocketknife/host/

VOLUME ["/data"]
EXPOSE 8443

# Starts as root only to hand /data over to the user "pocketknife" (UID 10001) once; run.py drops the
# privileges immediately – the web server and all access run without root (see app/privileges.py).
HEALTHCHECK --interval=60s --timeout=10s --start-period=30s CMD ["python", "-I", "/opt/pocketknife/healthcheck.py"]
CMD ["python", "run.py"]
