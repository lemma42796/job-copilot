"""记录 RabbitMQ 已确认的任务投递。"""
import sqlalchemy as sa
from alembic import op

revision = "0027"
down_revision = "0026"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("jobs", sa.Column("published_at", sa.DateTime(timezone=True)))
    op.create_index("ix_jobs_unpublished", "jobs", ["id"],
                    postgresql_where=sa.text("status = 'queued' AND published_at IS NULL"))


def downgrade() -> None:
    op.drop_index("ix_jobs_unpublished", table_name="jobs")
    op.drop_column("jobs", "published_at")
