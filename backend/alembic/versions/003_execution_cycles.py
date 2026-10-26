"""Add execution_cycle and total_attempt_count to jobs table

Revision ID: 003_execution_cycles
Revises: 002_failure_diagnostics
Create Date: 2026-10-26 19:55:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '003_execution_cycles'
down_revision: Union[str, None] = '002_failure_diagnostics'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        'jobs',
        sa.Column('execution_cycle', sa.Integer(), nullable=False, server_default='1')
    )
    op.add_column(
        'jobs',
        sa.Column('total_attempt_count', sa.Integer(), nullable=False, server_default='0')
    )


def downgrade() -> None:
    op.drop_column('jobs', 'total_attempt_count')
    op.drop_column('jobs', 'execution_cycle')
