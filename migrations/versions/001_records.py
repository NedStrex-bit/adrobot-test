"""Persistent simulator state, settings and editor drafts."""
from alembic import op
import sqlalchemy as sa
revision = '001'
down_revision = None
branch_labels = None
depends_on = None

def upgrade():
    op.create_table('records', sa.Column('key', sa.String(100), primary_key=True), sa.Column('value', sa.JSON(), nullable=False), sa.Column('version', sa.Integer(), nullable=False), sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False))

def downgrade():
    op.drop_table('records')
