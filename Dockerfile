# Freebird client — ONE image, every OS (linux/amd64 + linux/arm64).
#
# The whole point of the container is that the customer's ceiling stops being a promise the
# software makes and becomes a fact the KERNEL enforces. --cpus / --memory set cgroup limits, so
# the workers can run flat out and the kernel throttles them — exact, with none of the ~50% yield
# loss that duty-cycle pacing suffered on the bare host.
#
# Hardened by construction: the container runs as an unprivileged user (uid 10001), has no Linux
# capabilities, cannot gain new privileges, and its root filesystem is READ-ONLY. The only writable
# places are the mounted config/status directory and a size-capped tmpfs. It opens no ports — it
# only ever dials OUT to pull work.
FROM python:3.12-slim

LABEL org.opencontainers.image.title="Freebird client" \
      org.opencontainers.image.description="Runs your product's processing on your own machine, inside the ceiling you set." \
      org.opencontainers.image.source="https://freebird.shivelinc.com"

# The client is stdlib-only: no pip install, no build tools, nothing to keep patched.
RUN useradd --create-home --uid 10001 --shell /usr/sbin/nologin freebird

WORKDIR /app
COPY freebird_client.py /app/freebird_client.py

# State lives ONLY in the mounted volume. PYTHONDONTWRITEBYTECODE keeps Python from even trying
# to write .pyc files onto the read-only root filesystem.
ENV FREEBIRD_HOME=/data \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

USER 10001:10001

# No EXPOSE: nothing connects IN. Pull, never push.
ENTRYPOINT ["python3", "/app/freebird_client.py"]
