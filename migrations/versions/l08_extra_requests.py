"""One-use administrator request allowances."""
from alembic import op
import sqlalchemy as sa
revision = 'l08_extra_requests'
down_revision = 'k07_portal_modules'
branch_labels = depends_on = None

def upgrade():
    op.add_column('users', sa.Column('extra_request_credits', sa.Integer(), nullable=False, server_default='0'))
    op.add_column('changes', sa.Column('request_limit_exempt', sa.Boolean(), nullable=False, server_default='false'))

def downgrade():
    op.drop_column('changes', 'request_limit_exempt')
    op.drop_column('users', 'extra_request_credits')
