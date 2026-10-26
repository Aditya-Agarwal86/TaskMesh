"""Add failure_diagnostics table for Gemini AI analysis

Revision ID: 002_failure_diagnostics
Revises: 001_initial_schema
Create Date: 2026-10-26 04:22:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = '002_failure_diagnostics'
down_revision: Union[str, None] = '001_initial_schema'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'failure_diagnostics',
        sa.Column('id', sa.UUID(), nullable=False),
        sa.Column('job_id', sa.UUID(), nullable=False),
        sa.Column('attempt', sa.Integer(), nullable=False, server_default='1'),
        sa.Column('model', sa.String(length=128), nullable=False),
        sa.Column('error_category', sa.String(length=128), nullable=False),
        sa.Column('likely_cause', sa.Text(), nullable=False),
        sa.Column('confidence', sa.String(length=32), nullable=False, server_default='MEDIUM'),
        sa.Column('recommended_next_steps', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column('retry_may_help', sa.Boolean(), nullable=False, server_default=sa.text('false')),
        sa.Column('reasoning_summary', sa.Text(), nullable=False),
        sa.Column('raw_response', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(['job_id'], ['jobs.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_index('ix_failure_diagnostics_job_id', 'failure_diagnostics', ['job_id'])
    op.create_index('ix_failure_diagnostics_created_at', 'failure_diagnostics', ['created_at'])


def downgrade() -> None:
    op.drop_table('failure_diagnostics')
