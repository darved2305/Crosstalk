from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    CheckConstraint,
    Column,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    MetaData,
    String,
    Table,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB

metadata = MetaData()
json_type = JSON().with_variant(JSONB(), "postgresql")

experiments = Table(
    "experiments",
    metadata,
    *(
        Column("id", String(26), primary_key=True),
        Column("name", String(200), nullable=False),
        Column("config", json_type, nullable=False),
        Column("config_hash", String(64), nullable=False),
        Column("cancel_requested", Boolean, nullable=False, default=False),
        Column("created_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
        Column("updated_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
    ),
)

sessions = Table(
    "sessions",
    metadata,
    Column("id", String(26), primary_key=True),
    Column(
        "experiment_id",
        String(26),
        ForeignKey("experiments.id", ondelete="CASCADE"),
        nullable=False,
    ),
    Column("state", String(20), nullable=False),
    Column("config", json_type, nullable=False),
    Column("cancel_requested", Boolean, nullable=False, default=False),
    Column("created_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
    Column("updated_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
    CheckConstraint(
        "state IN ('PLANNED','COLLECTING','COLLECTED','RECONSTRUCTED','VALID','INVALID','PARTIAL','CANCELLED')",
        name="ck_sessions_state",
    ),
)

jobs = Table(
    "jobs",
    metadata,
    Column("id", String(26), primary_key=True),
    Column(
        "experiment_id",
        String(26),
        ForeignKey("experiments.id", ondelete="CASCADE"),
        nullable=False,
    ),
    Column("session_id", String(26), ForeignKey("sessions.id", ondelete="CASCADE"), nullable=True),
    Column("kind", String(64), nullable=False),
    Column("queue", String(16), nullable=False),
    Column("payload", json_type, nullable=False),
    Column("state", String(16), nullable=False),
    Column("attempts", Integer, nullable=False, default=0),
    Column("max_attempts", Integer, nullable=False, default=5),
    Column("run_after", DateTime(timezone=True), nullable=False),
    Column("lease_until", DateTime(timezone=True)),
    Column("worker_id", String(200)),
    Column("stage_key", String(64), nullable=False),
    Column("error", json_type),
    Column("created_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
    Column("updated_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
    UniqueConstraint("stage_key", name="uq_jobs_stage_key"),
    CheckConstraint("queue IN ('collect','attack','evaluate')", name="ck_jobs_queue"),
    CheckConstraint(
        "state IN ('queued','running','succeeded','failed','dead','cancelled')",
        name="ck_jobs_state",
    ),
    CheckConstraint("attempts >= 0 AND max_attempts > 0", name="ck_jobs_attempts"),
)
Index("ix_jobs_claim", jobs.c.queue, jobs.c.state, jobs.c.run_after, jobs.c.id)
Index("ix_jobs_lease", jobs.c.state, jobs.c.lease_until)

artifacts = Table(
    "artifacts",
    metadata,
    Column("id", String(26), primary_key=True),
    Column("job_id", String(26), ForeignKey("jobs.id"), nullable=False),
    Column("path", Text, nullable=False),
    Column("sha256", String(64), nullable=False),
    Column("size_bytes", BigInteger, nullable=False),
    Column("metadata", json_type, nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
    UniqueConstraint("job_id", "path", name="uq_artifacts_job_path"),
)

events = Table(
    "events",
    metadata,
    Column("id", String(26), primary_key=True),
    Column(
        "experiment_id",
        String(26),
        ForeignKey("experiments.id", ondelete="CASCADE"),
        nullable=False,
    ),
    Column("session_id", String(26)),
    Column("job_id", String(26)),
    Column("kind", String(64), nullable=False),
    Column("data", json_type, nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
)

idempotency_keys = Table(
    "idempotency_keys",
    metadata,
    Column(
        "experiment_id",
        String(26),
        ForeignKey("experiments.id", ondelete="CASCADE"),
        primary_key=True,
    ),
    Column("key", String(200), primary_key=True),
    Column("response", json_type, nullable=False),
    Column("expires_at", DateTime(timezone=True), nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
)
