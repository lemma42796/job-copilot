"""Rename chunk fields to plain evidence/scoring names.

Revision ID: 0023
Revises: 0022
Create Date: 2026-05-17
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0023"
down_revision: str | Sequence[str] | None = "0022"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.alter_column(
        "questions",
        "source_chunk_ids",
        new_column_name="evidence_chunk_ids",
    )
    op.alter_column(
        "questions",
        "reference_chunk_ids",
        new_column_name="reference_answer_chunk_ids",
    )
    op.alter_column("questions", "reference_points", new_column_name="scoring_points")
    op.execute(
        "ALTER INDEX ix_questions_source_chunks RENAME TO ix_questions_evidence_chunks"
    )
    op.execute(
        """
        UPDATE questions
        SET scoring_points = (
          SELECT COALESCE(
            jsonb_agg(
              CASE
                WHEN elem ? 'evidence_chunk_ids'
                THEN (elem - 'evidence_chunk_ids')
                  || jsonb_build_object(
                    'supporting_chunk_ids',
                    elem->'evidence_chunk_ids'
                  )
                ELSE elem
              END
              ORDER BY ord
            ),
            '[]'::jsonb
          )
          FROM jsonb_array_elements(scoring_points)
            WITH ORDINALITY AS item(elem, ord)
        )
        WHERE scoring_points IS NOT NULL
        """
    )
    op.execute(
        """
        UPDATE session_answers
        SET fidelity_evidence = jsonb_set(
          fidelity_evidence,
          '{claims}',
          (
            SELECT COALESCE(
              jsonb_agg(
                CASE
                  WHEN elem ? 'chunk_ids'
                  THEN (elem - 'chunk_ids')
                    || jsonb_build_object(
                      'supporting_chunk_ids',
                      elem->'chunk_ids'
                    )
                  ELSE elem
                END
                ORDER BY ord
              ),
              '[]'::jsonb
            )
            FROM jsonb_array_elements(fidelity_evidence->'claims')
              WITH ORDINALITY AS item(elem, ord)
          ),
          true
        )
        WHERE jsonb_typeof(fidelity_evidence->'claims') = 'array'
        """
    )


def downgrade() -> None:
    op.execute(
        """
        UPDATE session_answers
        SET fidelity_evidence = jsonb_set(
          fidelity_evidence,
          '{claims}',
          (
            SELECT COALESCE(
              jsonb_agg(
                CASE
                  WHEN elem ? 'supporting_chunk_ids'
                  THEN (elem - 'supporting_chunk_ids')
                    || jsonb_build_object(
                      'chunk_ids',
                      elem->'supporting_chunk_ids'
                    )
                  ELSE elem
                END
                ORDER BY ord
              ),
              '[]'::jsonb
            )
            FROM jsonb_array_elements(fidelity_evidence->'claims')
              WITH ORDINALITY AS item(elem, ord)
          ),
          true
        )
        WHERE jsonb_typeof(fidelity_evidence->'claims') = 'array'
        """
    )
    op.execute(
        """
        UPDATE questions
        SET scoring_points = (
          SELECT COALESCE(
            jsonb_agg(
              CASE
                WHEN elem ? 'supporting_chunk_ids'
                THEN (elem - 'supporting_chunk_ids')
                  || jsonb_build_object(
                    'evidence_chunk_ids',
                    elem->'supporting_chunk_ids'
                  )
                ELSE elem
              END
              ORDER BY ord
            ),
            '[]'::jsonb
          )
          FROM jsonb_array_elements(scoring_points)
            WITH ORDINALITY AS item(elem, ord)
        )
        WHERE scoring_points IS NOT NULL
        """
    )
    op.execute(
        "ALTER INDEX ix_questions_evidence_chunks RENAME TO ix_questions_source_chunks"
    )
    op.alter_column(
        "questions",
        "scoring_points",
        new_column_name="reference_points",
    )
    op.alter_column(
        "questions",
        "reference_answer_chunk_ids",
        new_column_name="reference_chunk_ids",
    )
    op.alter_column(
        "questions",
        "evidence_chunk_ids",
        new_column_name="source_chunk_ids",
    )
