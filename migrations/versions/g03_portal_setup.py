"""One-time portal password setup."""
from alembic import op
import sqlalchemy as sa
revision='g03_portal_setup'
down_revision='f92_locations_email'
branch_labels=None
depends_on=None

def upgrade():
    op.create_table('portal_setup',
        sa.Column('user_id',sa.Integer(),sa.ForeignKey('users.id'),primary_key=True),
        sa.Column('digest',sa.String(64),nullable=False,unique=True),
        sa.Column('email',sa.String(254),nullable=False),
        sa.Column('expires',sa.DateTime(timezone=True),nullable=False),
        sa.Column('credential_version',sa.String(64),nullable=False))

def downgrade():
    op.drop_table('portal_setup')
