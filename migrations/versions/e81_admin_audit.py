"""Record administrative role and approval-location changes."""
from alembic import op
import sqlalchemy as sa
revision = 'e81_admin_audit'
down_revision = 'd73b_weekly'
branch_labels = None
depends_on = None

def upgrade():
    op.create_table('admin_audit',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('actor_id', sa.Integer(), sa.ForeignKey('users.id'), nullable=False),
        sa.Column('target_id', sa.Integer(), sa.ForeignKey('users.id'), nullable=False),
        sa.Column('details', sa.JSON(), nullable=False),
        sa.Column('created', sa.DateTime(timezone=True), nullable=False))

def downgrade():
    op.drop_table('admin_audit')
