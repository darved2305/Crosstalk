"""Create durable orchestration tables."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0001_orchestration"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _timestamps() -> list[sa.Column[object]]:
    return [
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
    ]


def upgrade() -> None:
    op.create_table(
        "experiments",
        sa.Column("id", sa.String(26), primary_key=True),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("config", postgresql.JSONB(), nullable=False),
        sa.Column("config_hash", sa.String(64), nullable=False),
        sa.Column("cancel_requested", sa.Boolean(), server_default=sa.false(), nullable=False),
        *_timestamps(),
    )
    op.create_table(
        "sessions",
        sa.Column("id", sa.String(26), primary_key=True),
        sa.Column(
            "experiment_id",
            sa.String(26),
            sa.ForeignKey("experiments.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("state", sa.String(20), nullable=False),
        sa.Column("config", postgresql.JSONB(), nullable=False),
        sa.Column("cancel_requested", sa.Boolean(), server_default=sa.false(), nullable=False),
        *_timestamps(),
        sa.CheckConstraint(
            "state IN ('PLANNED','COLLECTING','COLLECTED','RECONSTRUCTED','VALID','INVALID','PARTIAL','CANCELLED')",
            name="ck_sessions_state",
        ),
    )
    op.create_table(
        "jobs",
        sa.Column("id", sa.String(26), primary_key=True),
        sa.Column(
            "experiment_id",
            sa.String(26),
            sa.ForeignKey("experiments.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "session_id",
            sa.String(26),
            sa.ForeignKey("sessions.id", ondelete="CASCADE"),
        ),
        sa.Column("kind", sa.String(64), nullable=False),
        sa.Column("queue", sa.String(16), nullable=False),
        sa.Column("payload", postgresql.JSONB(), nullable=False),
        sa.Column("state", sa.String(16), nullable=False),
        sa.Column("attempts", sa.Integer(), server_default="0", nullable=False),
        sa.Column("max_attempts", sa.Integer(), server_default="5", nullable=False),
        sa.Column("run_after", sa.DateTime(timezone=True), nullable=False),
        sa.Column("lease_until", sa.DateTime(timezone=True)),
        sa.Column("worker_id", sa.String(200)),
        sa.Column("stage_key", sa.String(64), nullable=False),
        sa.Column("error", postgresql.JSONB()),
        *_timestamps(),
        sa.UniqueConstraint("stage_key", name="uq_jobs_stage_key"),
        sa.CheckConstraint("queue IN ('collect','attack','evaluate')", name="ck_jobs_queue"),
        sa.CheckConstraint(
            "state IN ('queued','running','succeeded','failed','dead','cancelled')",
            name="ck_jobs_state",
        ),
        sa.CheckConstraint("attempts >= 0 AND max_attempts > 0", name="ck_jobs_attempts"),
    )
    op.create_index("ix_jobs_claim", "jobs", ["queue", "state", "run_after", "id"])
    op.create_index("ix_jobs_lease", "jobs", ["state", "lease_until"])
    op.create_table(
        "artifacts",
        sa.Column("id", sa.String(26), primary_key=True),
        sa.Column("job_id", sa.String(26), sa.ForeignKey("jobs.id"), nullable=False),
        sa.Column("path", sa.Text(), nullable=False),
        sa.Column("sha256", sa.String(64), nullable=False),
        sa.Column("size_bytes", sa.BigInteger(), nullable=False),
        sa.Column("metadata", postgresql.JSONB(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.UniqueConstraint("job_id", "path", name="uq_artifacts_job_path"),
    )
    op.create_table(
        "events",
        sa.Column("id", sa.String(26), primary_key=True),
        sa.Column(
            "experiment_id",
            sa.String(26),
            sa.ForeignKey("experiments.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("session_id", sa.String(26)),
        sa.Column("job_id", sa.String(26)),
        sa.Column("kind", sa.String(64), nullable=False),
        sa.Column("data", postgresql.JSONB(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
    )


def downgrade() -> None:
    op.drop_table("events")
    op.drop_table("artifacts")
    op.drop_index("ix_jobs_lease", table_name="jobs")
    op.drop_index("ix_jobs_claim", table_name="jobs")
    op.drop_table("jobs")
    op.drop_table("sessions")
    op.drop_table("experiments")
