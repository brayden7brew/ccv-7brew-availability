"""Shared portal module access; existing employee access is preserved."""
from alembic import op
import sqlalchemy as sa
revision = 'k07_portal_modules'
down_revision = 'j06_notification_preferences'
branch_labels = depends_on = None

def upgrade():
    op.add_column('users', sa.Column('availability_access', sa.Boolean(), nullable=False, server_default='true'))
    op.add_column('users', sa.Column('ops_access', sa.Boolean(), nullable=False, server_default='false'))
    op.add_column('users', sa.Column('ops_locations', sa.JSON(), nullable=True))

def downgrade():
    for name in ('ops_locations', 'ops_access', 'availability_access'):
        op.drop_column('users', name)
