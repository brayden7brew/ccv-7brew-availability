"""Shared encrypted WIW service credential."""
from alembic import op
import sqlalchemy as sa
revision = 'm09_wiw_token_refresh'
down_revision = 'l08_extra_requests'
branch_labels = depends_on = None

def upgrade():
    table = op.create_table('wiw_credentials',
        sa.Column('id',sa.Integer(),primary_key=True),
        sa.Column('seed_hash',sa.String(64),nullable=False,server_default=''),
        sa.Column('encrypted_token',sa.Text(),nullable=False,server_default=''),
        sa.Column('next_attempt',sa.DateTime(timezone=True),nullable=True),
        sa.Column('last_success',sa.DateTime(timezone=True),nullable=True),
        sa.Column('error',sa.String(300),nullable=False,server_default=''))
    op.bulk_insert(table,[{'id':1}])

def downgrade():
    op.drop_table('wiw_credentials')
