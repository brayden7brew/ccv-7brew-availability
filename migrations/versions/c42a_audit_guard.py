"""Reject audit history modification in PostgreSQL."""
from alembic import op
revision = 'c42a_audit_guard'
down_revision = 'b2dd4b2ded65'
branch_labels = None
depends_on = None

def upgrade():
    if op.get_bind().dialect.name == 'postgresql':
        op.execute("""CREATE FUNCTION deny_audit_mutation() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN RAISE EXCEPTION 'audit rows are append-only'; END; $$""")
        op.execute('CREATE TRIGGER audit_append_only BEFORE UPDATE OR DELETE ON audit FOR EACH ROW EXECUTE FUNCTION deny_audit_mutation()')

def downgrade():
    if op.get_bind().dialect.name == 'postgresql':
        op.execute('DROP TRIGGER audit_append_only ON audit')
        op.execute('DROP FUNCTION deny_audit_mutation()')
