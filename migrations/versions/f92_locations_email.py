"""Two employee locations, location choices and durable email outbox."""
from alembic import op
import sqlalchemy as sa
revision='f92_locations_email'
down_revision='e81_admin_audit'
branch_labels=None
depends_on=None

def upgrade():
    for table in ('users','changes'):
        op.add_column(table,sa.Column('secondary_location',sa.String(120),nullable=False,server_default=''))
    op.add_column('users',sa.Column('notification_email',sa.String(254),nullable=False,server_default=''))
    op.create_table('locations',sa.Column('name',sa.String(120),primary_key=True))
    op.execute("INSERT INTO locations (name) SELECT location FROM users UNION SELECT location FROM manager_scopes")
    op.create_table('email_outbox',
        sa.Column('id',sa.Integer(),primary_key=True),
        sa.Column('change_id',sa.Integer(),sa.ForeignKey('changes.id'),nullable=False),
        sa.Column('recipient_id',sa.Integer(),sa.ForeignKey('users.id'),nullable=False),
        sa.Column('event',sa.String(40),nullable=False),sa.Column('recipient',sa.String(254),nullable=False),
        sa.Column('subject',sa.String(254),nullable=False),sa.Column('body',sa.Text(),nullable=False),
        sa.Column('status',sa.String(30),nullable=False),sa.Column('attempts',sa.Integer(),nullable=False),
        sa.Column('last_error',sa.String(120),nullable=False),
        sa.Column('created',sa.DateTime(timezone=True),nullable=False),
        sa.Column('next_attempt',sa.DateTime(timezone=True),nullable=False),
        sa.UniqueConstraint('change_id','recipient_id','event'))

def downgrade():
    op.drop_table('email_outbox')
    op.drop_table('locations')
    op.drop_column('users','notification_email')
    for table in ('users','changes'):op.drop_column(table,'secondary_location')
