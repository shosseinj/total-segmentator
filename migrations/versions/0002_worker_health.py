from alembic import op
from app.db.models import WorkerHealth
revision='0002'
down_revision='0001'
branch_labels=None
depends_on=None

def upgrade():WorkerHealth.__table__.create(op.get_bind(),checkfirst=True)
def downgrade():WorkerHealth.__table__.drop(op.get_bind())
