from alembic import op
from sqlalchemy import inspect,Column,Integer,Float
revision='0003'
down_revision='0002'
branch_labels=None
depends_on=None

def upgrade():
    columns={c['name'] for c in inspect(op.get_bind()).get_columns('scan_objects')}
    if 'retry_count' not in columns:op.add_column('scan_objects',Column('retry_count',Integer,nullable=False,server_default='0'))
    if 'retry_at' not in columns:op.add_column('scan_objects',Column('retry_at',Float))

def downgrade():
    op.drop_column('scan_objects','retry_at');op.drop_column('scan_objects','retry_count')
