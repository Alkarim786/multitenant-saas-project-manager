#!/usr/bin/env sh
# Environment for running the backend tests against the Compose Postgres. Source this file.
# Tests connect as the owner so they can create the test database, then SET ROLE pm_app.
export DJANGO_SECRET_KEY=test-only-test-only-test-only-test-only-test-only
export DB_USER=postgres
export DB_PASSWORD="${POSTGRES_PASSWORD:-postgres_dev}"
export DB_HOST="${DB_HOST:-localhost}"
export DB_PORT="${POSTGRES_PORT:-5432}"
export DB_NAME=pm
export CHANNEL_LAYER=memory
export CELERY_EAGER=true
export AUTH_THROTTLE=100000/min
