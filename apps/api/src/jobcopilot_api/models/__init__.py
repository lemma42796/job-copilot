"""SQLAlchemy ORM models.

沿用层(M0 砍 v1 后保留):base + LLM cost / cache / prompt 版本表(沿用 v1
alembic 0006 + 0015)。

v2 业务表(M0 alembic 0016 落地,本 __init__.py re-export 保证 alembic env.py
通过 Base.metadata 看到所有模型):
- 笔记 SR 闭环:Note / NoteChunk / Question / QuizSession / SessionAnswer / KnowledgeGap(M1-M3)
- M2.1 Agent harness:SessionEvent
- JD 累积分析:Jd / JdAnalysis(M2.5)
- 简历诊断:Resume / ResumeAnalysis(M3)

P0-P3 并发改造新增:
- 用户体系:User(所有业务表带 user_id)
- 余额账本:UserBalance / BalanceTransaction
- 异步任务:Job / JobEvent
"""

from __future__ import annotations

from jobcopilot_api.models.balance import BalanceTransaction, UserBalance
from jobcopilot_api.models.base import Base, IDMixin, TimestampMixin
from jobcopilot_api.models.job import Job, JobEvent
from jobcopilot_api.models.jd import Jd
from jobcopilot_api.models.jd_analysis import JdAnalysis
from jobcopilot_api.models.knowledge_gap import KnowledgeGap
from jobcopilot_api.models.llm_call import LlmCall
from jobcopilot_api.models.llm_response_cache import LlmResponseCache
from jobcopilot_api.models.note import Note
from jobcopilot_api.models.note_chunk import NoteChunk
from jobcopilot_api.models.prompt_version import PromptVersion
from jobcopilot_api.models.question import Question
from jobcopilot_api.models.quiz_session import QuizSession
from jobcopilot_api.models.resume import Resume
from jobcopilot_api.models.resume_analysis import ResumeAnalysis
from jobcopilot_api.models.session_answer import SessionAnswer
from jobcopilot_api.models.session_event import SessionEvent
from jobcopilot_api.models.user import User

__all__ = [
    "BalanceTransaction",
    "Base",
    "IDMixin",
    "Jd",
    "JdAnalysis",
    "Job",
    "JobEvent",
    "KnowledgeGap",
    "LlmCall",
    "LlmResponseCache",
    "Note",
    "NoteChunk",
    "PromptVersion",
    "Question",
    "QuizSession",
    "Resume",
    "ResumeAnalysis",
    "SessionAnswer",
    "SessionEvent",
    "TimestampMixin",
    "User",
    "UserBalance",
]
