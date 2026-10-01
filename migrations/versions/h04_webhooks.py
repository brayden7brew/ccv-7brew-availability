"""Durable authenticated WIW user webhook queue."""
from alembic import op
import sqlalchemy as sa
revision='h04_webhooks'
down_revision='g03_portal_setup'
branch_labels=None
depends_on=None

def upgrade():
    op.create_table('webhook_batches',sa.Column('digest',sa.String(64),primary_key=True),
        sa.Column('account_id',sa.Integer(),nullable=False),sa.Column('user_ids',sa.JSON(),nullable=False),
        sa.Column('status',sa.String(20),nullable=False),sa.Column('attempts',sa.Integer(),nullable=False),
        sa.Column('created',sa.DateTime(timezone=True),nullable=False),
        sa.Column('next_attempt',sa.DateTime(timezone=True),nullable=False),sa.Column('last_error',sa.String(120),nullable=False))

def downgrade(): op.drop_table('webhook_batches')
