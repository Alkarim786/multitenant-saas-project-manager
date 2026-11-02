"""Row-level security on every tenant table, and the unprivileged role the app connects as.

PostgreSQL superusers and table owners bypass RLS, so this only protects anything if runtime
traffic uses a different role. Migrations run as the owner; web and workers run as pm_app.
"""

import os

from django.db import migrations
from psycopg import sql

TENANT_TABLES = ["core_project", "core_column", "core_issue", "core_comment", "core_auditlog", "core_exportjob"]
APP_ROLE = "pm_app"


def create_role(apps, schema_editor):
    password = os.environ.get("APP_DB_PASSWORD", "pm_app_dev")
    with schema_editor.connection.cursor() as c:
        c.execute("SELECT 1 FROM pg_roles WHERE rolname = %s", [APP_ROLE])
        verb = "ALTER" if c.fetchone() else "CREATE"
        statement = sql.SQL(verb + " ROLE {} LOGIN NOSUPERUSER NOBYPASSRLS PASSWORD {}").format(sql.Identifier(APP_ROLE), sql.Literal(password))
        c.execute(statement.as_string(c.connection))
        db = sql.Identifier(schema_editor.connection.settings_dict["NAME"])
        c.execute(sql.SQL("GRANT CONNECT ON DATABASE {} TO pm_app").format(db).as_string(c.connection))


GRANTS = """
GRANT USAGE ON SCHEMA public TO pm_app;
GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO pm_app;
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO pm_app;
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO pm_app;
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT USAGE, SELECT ON SEQUENCES TO pm_app;
-- The audit log is append-only for the app. actor_id stays updatable because deleting a
-- user sets it to NULL.
REVOKE UPDATE, DELETE ON core_auditlog FROM pm_app;
GRANT UPDATE (actor_id) ON core_auditlog TO pm_app;
"""

ENABLE_RLS = "\n".join(
    f"""
ALTER TABLE {t} ENABLE ROW LEVEL SECURITY;
ALTER TABLE {t} FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON {t}
    USING (tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::bigint)
    WITH CHECK (tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::bigint);
"""
    for t in TENANT_TABLES
)

DISABLE_RLS = "\n".join(
    f"DROP POLICY tenant_isolation ON {t}; ALTER TABLE {t} NO FORCE ROW LEVEL SECURITY; ALTER TABLE {t} DISABLE ROW LEVEL SECURITY;"
    for t in TENANT_TABLES
)


class Migration(migrations.Migration):
    dependencies = [("core", "0001_initial")]

    operations = [
        migrations.RunPython(create_role, migrations.RunPython.noop),
        migrations.RunSQL(GRANTS, migrations.RunSQL.noop),
        migrations.RunSQL(ENABLE_RLS, DISABLE_RLS),
    ]
