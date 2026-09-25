"""Track embedding compatibility without guessing the model of existing vectors."""

import sqlalchemy as sa
from alembic import op

revision = "8f2a4c6d901b"
down_revision = "301c2a9fdade"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("document_chunks", sa.Column("embedding_model", sa.String(255), nullable=True))
    op.add_column("document_chunks", sa.Column("embedding_dimensions", sa.Integer(), nullable=True))


def downgrade() -> None:
    op.drop_column("document_chunks", "embedding_dimensions")
    op.drop_column("document_chunks", "embedding_model")
