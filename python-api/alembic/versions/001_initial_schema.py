"""Initial schema migration - creates all system tables."""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers
revision = "001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Create enum types
    sa.Enum("user", "admin", name="user_role").create(op.get_bind())
    sa.Enum("active", "pending", "blocked", name="user_status").create(op.get_bind())
    sa.Enum("active", "ingesting", "error", "archived", name="collection_status").create(op.get_bind())
    sa.Enum("pending", "running", "completed", "failed", "cancelled", name="ingest_status").create(op.get_bind())
    
    # Users table
    op.create_table(
        "users",
        sa.Column("id", sa.String(36), nullable=False),
        sa.Column("google_sub", sa.String(255), nullable=False),
        sa.Column("email", sa.String(255), nullable=False),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("avatar_url", sa.String(511), nullable=True),
        sa.Column("role", sa.Enum("user", "admin", name="user_role"), nullable=False, server_default="user"),
        sa.Column("status", sa.Enum("active", "pending", "blocked", name="user_status"), nullable=False, server_default="pending"),
        sa.Column("created_at", sa.Integer, nullable=False),
        sa.Column("last_login", sa.Integer, nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("google_sub"),
        sa.UniqueConstraint("email"),
    )
    op.create_index("ix_users_google_sub", "users", ["google_sub"])
    op.create_index("ix_users_email", "users", ["email"])
    
    # Collections table
    op.create_table(
        "collections",
        sa.Column("id", sa.String(36), nullable=False),
        sa.Column("user_id", sa.String(36), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("description", sa.Text, nullable=True),
        sa.Column("folder_path", sa.String(511), nullable=False),
        sa.Column("status", sa.Enum("active", "ingesting", "error", "archived", name="collection_status"), nullable=False, server_default="active"),
        sa.Column("doc_count", sa.Integer, nullable=False, server_default="0"),
        sa.Column("created_at", sa.Integer, nullable=False),
        sa.Column("updated_at", sa.Integer, nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_collections_user_id", "collections", ["user_id"])
    
    # Ingest jobs table
    op.create_table(
        "ingest_jobs",
        sa.Column("id", sa.String(36), nullable=False),
        sa.Column("collection_id", sa.String(36), sa.ForeignKey("collections.id", ondelete="CASCADE"), nullable=False),
        sa.Column("status", sa.Enum("pending", "running", "completed", "failed", "cancelled", name="ingest_status"), nullable=False, server_default="pending"),
        sa.Column("progress", sa.Float, nullable=False, server_default="0"),
        sa.Column("total_docs", sa.Integer, nullable=False, server_default="0"),
        sa.Column("processed_docs", sa.Integer, nullable=False, server_default="0"),
        sa.Column("error_msg", sa.Text, nullable=True),
        sa.Column("started_at", sa.Integer, nullable=True),
        sa.Column("completed_at", sa.Integer, nullable=True),
        sa.Column("created_at", sa.Integer, nullable=False),
        sa.Column("options", sa.Text, nullable=True),
        sa.Column("last_completed_file", sa.String(511), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_ingest_jobs_collection_id", "ingest_jobs", ["collection_id"])
    
    # Revoked tokens table
    op.create_table(
        "revoked_tokens",
        sa.Column("jti", sa.String(36), nullable=False),
        sa.Column("revoked_at", sa.Integer, nullable=False),
        sa.Column("expires_at", sa.Integer, nullable=False),
        sa.PrimaryKeyConstraint("jti"),
    )
    
    # Drive watch channels table
    op.create_table(
        "drive_watch_channels",
        sa.Column("id", sa.String(36), nullable=False),
        sa.Column("channel_id", sa.String(255), nullable=False),
        sa.Column("resource_id", sa.String(255), nullable=False),
        sa.Column("collection_id", sa.String(36), sa.ForeignKey("collections.id", ondelete="CASCADE"), nullable=False),
        sa.Column("folder_id", sa.String(255), nullable=False),
        sa.Column("access_token", sa.String(511), nullable=False),
        sa.Column("expiry_ms", sa.Integer, nullable=False),
        sa.Column("created_at", sa.Integer, nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("channel_id"),
    )
    op.create_index("ix_drive_watch_channels_collection_id", "drive_watch_channels", ["collection_id"])
    
    # Ontologies table
    op.create_table(
        "ontologies",
        sa.Column("id", sa.String(36), nullable=False),
        sa.Column("collection_id", sa.String(36), sa.ForeignKey("collections.id", ondelete="CASCADE"), nullable=False),
        sa.Column("version", sa.String(50), nullable=False),
        sa.Column("schema_json", sa.Text, nullable=False),
        sa.Column("is_active", sa.Boolean, nullable=False, server_default="true"),
        sa.Column("created_at", sa.Integer, nullable=False),
        sa.Column("updated_at", sa.Integer, nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("collection_id"),
    )
    op.create_index("ix_ontologies_collection_id", "ontologies", ["collection_id"])
    
    # User feedback table
    op.create_table(
        "user_feedback",
        sa.Column("id", sa.String(36), nullable=False),
        sa.Column("user_id", sa.String(36), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("collection_id", sa.String(36), sa.ForeignKey("collections.id", ondelete="CASCADE"), nullable=False),
        sa.Column("entity_type", sa.String(50), nullable=False),
        sa.Column("entity_id", sa.String(36), nullable=False),
        sa.Column("feedback_type", sa.String(50), nullable=False),
        sa.Column("previous_value", sa.Text, nullable=True),
        sa.Column("new_value", sa.Text, nullable=True),
        sa.Column("created_at", sa.Integer, nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_user_feedback_user_id", "user_feedback", ["user_id"])
    op.create_index("ix_user_feedback_collection_id", "user_feedback", ["collection_id"])
    op.create_index("ix_user_feedback_entity", "user_feedback", ["entity_type", "entity_id"])
    
    # Enable Row-Level Security (RLS)
    op.execute("ALTER TABLE users ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE collections ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE ingest_jobs ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE ontologies ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE user_feedback ENABLE ROW LEVEL SECURITY")
    
    # RLS Policies - users can only see their own data
    op.execute("""
        CREATE POLICY users_isolation ON users
        USING (id = current_setting('app.current_user_id', true)::UUID OR current_setting('app.is_admin', true)::BOOLEAN = true)
    """)
    
    op.execute("""
        CREATE POLICY collections_isolation ON collections
        USING (user_id = current_setting('app.current_user_id', true)::UUID OR current_setting('app.is_admin', true)::BOOLEAN = true)
    """)
    
    op.execute("""
        CREATE POLICY ingest_jobs_isolation ON ingest_jobs
        USING (
            collection_id IN (
                SELECT id FROM collections 
                WHERE user_id = current_setting('app.current_user_id', true)::UUID
            )
            OR current_setting('app.is_admin', true)::BOOLEAN = true
        )
    """)
    
    op.execute("""
        CREATE POLICY ontologies_isolation ON ontologies
        USING (
            collection_id IN (
                SELECT id FROM collections 
                WHERE user_id = current_setting('app.current_user_id', true)::UUID
            )
            OR current_setting('app.is_admin', true)::BOOLEAN = true
        )
    """)
    
    op.execute("""
        CREATE POLICY user_feedback_isolation ON user_feedback
        USING (user_id = current_setting('app.current_user_id', true)::UUID OR current_setting('app.is_admin', true)::BOOLEAN = true)
    """)


def downgrade() -> None:
    # Drop RLS policies
    op.execute("DROP POLICY IF EXISTS users_isolation ON users")
    op.execute("DROP POLICY IF EXISTS collections_isolation ON collections")
    op.execute("DROP POLICY IF EXISTS ingest_jobs_isolation ON ingest_jobs")
    op.execute("DROP POLICY IF EXISTS ontologies_isolation ON ontologies")
    op.execute("DROP POLICY IF EXISTS user_feedback_isolation ON user_feedback")
    
    # Disable RLS
    op.execute("ALTER TABLE user_feedback DISABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE ontologies DISABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE ingest_jobs DISABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE collections DISABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE users DISABLE ROW LEVEL SECURITY")
    
    # Drop tables in reverse order
    op.drop_table("user_feedback")
    op.drop_table("ontologies")
    op.drop_table("drive_watch_channels")
    op.drop_table("revoked_tokens")
    op.drop_table("ingest_jobs")
    op.drop_table("collections")
    op.drop_table("users")
    
    # Drop enum types
    sa.Enum(name="ingest_status").drop(op.get_bind())
    sa.Enum(name="collection_status").drop(op.get_bind())
    sa.Enum(name="user_status").drop(op.get_bind())
    sa.Enum(name="user_role").drop(op.get_bind())
