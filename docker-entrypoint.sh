#!/bin/sh
# Container entrypoint: bring the schema to head, then start the server
# (docs/specs/06-persistence.md, "Deploy-time migrations").
#
# Render's free instance type does not run pre-deploy commands (they need a
# paid instance), so a Blueprint `preDeployCommand` silently never ran. The
# container is the only place a migration step executes on every deploy.
# `alembic upgrade head` is idempotent: alembic_version records the applied
# revision, so on a database already at head it is a no-op.
#
# The image installs the app with pip (no uv at runtime), so `alembic` is the
# console script on PATH, run from /app where alembic.ini lives.
#
# A migration failure aborts startup on purpose: Render keeps the previous
# deploy serving instead of a new one running against an un-migrated schema.
#
# Only Postgres has a migration chain (0001 creates the pgvector extension);
# any other DATABASE_URL, or none, skips it.
set -e

case "${DATABASE_URL:-}" in
  postgres://* | postgresql://* | postgresql+*://*)
    echo "[entrypoint] applying Alembic migrations (alembic upgrade head)"
    alembic upgrade head
    echo "[entrypoint] migrations applied"
    ;;
  *)
    echo "[entrypoint] DATABASE_URL is not Postgres - skipping Alembic"
    ;;
esac

exec "$@"
