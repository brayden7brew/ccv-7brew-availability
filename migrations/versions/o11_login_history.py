"""Safe administrator login diagnostics."""
from alembic import op
import sqlalchemy as sa
revision = 'o11_login_history'
down_revision = 'n10_time_off'
branch_labels = depends_on = None

def upgrade():
    op.create_table('login_events',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('email', sa.String(254), nullable=False),
        sa.Column('user_id', sa.Integer(), sa.ForeignKey('users.id'), nullable=True),
        sa.Column('method', sa.String(20), nullable=False),
        sa.Column('outcome', sa.String(40), nullable=False),
        sa.Column('created', sa.DateTime(timezone=True), nullable=False))
    op.create_index('ix_login_events_email', 'login_events', ['email'])
    op.create_index('ix_login_events_created', 'login_events', ['created'])

def downgrade():
    op.drop_table('login_events')
