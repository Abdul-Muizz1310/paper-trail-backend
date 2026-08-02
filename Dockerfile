FROM python:3.12-slim

WORKDIR /app

# Install the resolver, not the app. Everything below is installed from uv.lock,
# so the image gets the exact versions CI resolved and tested. `pip install .`
# on its own would re-resolve the open-ended `>=` ranges in pyproject.toml at
# build time, meaning two builds of the same commit can ship different
# dependency sets — and neither is the set the green CI run gated.
RUN pip install --no-cache-dir uv==0.9.17

COPY pyproject.toml uv.lock README.md alembic.ini ./
COPY src ./src
COPY alembic ./alembic

# --frozen: fail rather than silently re-lock if uv.lock is stale.
# --require-hashes: the exported pins carry hashes, so a swapped artifact fails
#   the build instead of reaching production.
# --no-deps for the project itself: its dependencies are already pinned above.
RUN uv export --frozen --no-dev --no-emit-project -o /tmp/requirements.txt \
    && pip install --no-cache-dir --require-hashes -r /tmp/requirements.txt \
    && pip install --no-cache-dir --no-deps . \
    && pip uninstall -y uv \
    && rm -f /tmp/requirements.txt

EXPOSE 8000
# --proxy-headers + --forwarded-allow-ips: the container is only reachable
# through Render's edge proxy, so without these every request's peer address is
# the proxy and per-IP logic (rate limiting, logs) sees one caller for the whole
# internet. See core/rate_limit.client_identifier for the same reasoning.
CMD ["uvicorn", "paper_trail.main:app", \
     "--host", "0.0.0.0", "--port", "8000", \
     "--proxy-headers", "--forwarded-allow-ips", "*"]
