"""Pilot time off and stand blackout periods."""
from alembic import op
import sqlalchemy as sa
revision='n10_time_off'
down_revision='m09_wiw_token_refresh'
branch_labels=depends_on=None

def upgrade():
    op.add_column('users',sa.Column('time_off_access',sa.Boolean(),nullable=False,server_default=sa.false()))
    op.execute("UPDATE users SET time_off_access=true WHERE lower(email)='brayden@rva7brew.com' AND role='admin' AND active=true")
    table=op.create_table('time_off_settings',sa.Column('id',sa.Integer(),primary_key=True),
        sa.Column('enabled',sa.Boolean(),nullable=False,server_default=sa.true()),
        sa.Column('everyone',sa.Boolean(),nullable=False,server_default=sa.false()))
    op.bulk_insert(table,[{'id':1,'enabled':True,'everyone':False}])
    op.create_table('time_off_blackouts',sa.Column('id',sa.Integer(),primary_key=True),
        sa.Column('location',sa.String(120),nullable=False),sa.Column('day',sa.Date(),nullable=False),
        sa.Column('period',sa.String(20),nullable=False),sa.Column('maximum',sa.Integer(),nullable=False),
        sa.Column('explanation',sa.String(500),nullable=False),
        sa.Column('evening_end',sa.Integer(),nullable=False,server_default='23'),
        sa.UniqueConstraint('location','day','period'))
    op.create_index('ix_time_off_blackouts_location','time_off_blackouts',['location'])
    op.create_index('ix_time_off_blackouts_day','time_off_blackouts',['day'])

def downgrade():
    op.drop_table('time_off_blackouts')
    op.drop_table('time_off_settings')
    op.drop_column('users','time_off_access')
