"""Personal administrator notification preferences."""
from alembic import op
import sqlalchemy as sa
revision='j06_notification_preferences'
down_revision='i05_employee_rules'
branch_labels=None
depends_on=None

def upgrade():
    op.add_column('users',sa.Column('notifications_enabled',sa.Boolean(),nullable=False,server_default='true'))
    op.add_column('users',sa.Column('notification_locations',sa.JSON(),nullable=True))

def downgrade():
    op.drop_column('users','notification_locations')
    op.drop_column('users','notifications_enabled')
