"""Weekly approvals and ownership of WIW-generated events."""
from alembic import op
import sqlalchemy as sa
revision = 'd73b_weekly'
down_revision = 'c42a_audit_guard'
branch_labels = None
depends_on = None

def upgrade():
    op.create_table('weekly_schedules',
        sa.Column('id',sa.Integer(),primary_key=True),
        sa.Column('change_id',sa.Integer(),sa.ForeignKey('changes.id'),nullable=False,unique=True),
        sa.Column('employee_id',sa.Integer(),sa.ForeignKey('users.id'),nullable=False),
        sa.Column('effective_date',sa.Date(),nullable=False),
        sa.Column('days',sa.JSON(),nullable=False),sa.Column('dry_run',sa.Boolean(),nullable=False))
    op.create_index('ix_weekly_schedules_employee_id','weekly_schedules',['employee_id'])
    op.create_table('managed_events',
        sa.Column('id',sa.Integer(),primary_key=True),
        sa.Column('employee_id',sa.Integer(),sa.ForeignKey('users.id'),nullable=False),
        sa.Column('event_id',sa.Integer(),nullable=False,unique=True),
        sa.Column('snapshot',sa.JSON(),nullable=False),sa.Column('active',sa.Boolean(),nullable=False))
    op.create_index('ix_managed_events_employee_id','managed_events',['employee_id'])

def downgrade():
    op.drop_table('managed_events')
    op.drop_table('weekly_schedules')
