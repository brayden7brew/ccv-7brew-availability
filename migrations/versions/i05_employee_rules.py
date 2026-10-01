"""Per-employee availability requirements; existing employees receive standard notice."""
from alembic import op
import sqlalchemy as sa
revision='i05_employee_rules'
down_revision='h04_webhooks'
branch_labels=None
depends_on=None

def upgrade():
    for name,typ,default in [('minimum_hours_enabled',sa.Boolean(),'true'),('minimum_available_minutes',sa.Integer(),'900'),('notice_enabled',sa.Boolean(),'true'),('notice_days',sa.Integer(),'14'),('first_request_notice_exception',sa.Boolean(),'false')]:
        op.add_column('users',sa.Column(name,typ,nullable=False,server_default=default))

def downgrade():
    for name in ('first_request_notice_exception','notice_days','notice_enabled','minimum_available_minutes','minimum_hours_enabled'):op.drop_column('users',name)
