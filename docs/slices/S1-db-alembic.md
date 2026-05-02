---
title: S1 DB + Alembic + 通用列/触发器/枚举 — 切片归档
status: ✅ 完成已 push
date: 2026-05-01
purpose: 5 条 migration 落 9 张表(DATA_MODEL §3.1-3.8 + §3.15)
---

# 产出

```
apps/api/alembic/versions/
├── 0001_extensions_and_helpers.py  # vector + pg_trgm + set_updated_at() 触发器函数
├── 0002_users_and_files.py         # users + files(lz4 压缩 + sha256 索引 + 100MB CHECK)
├── 0003_jds.py                     # jd_source / jd_status ENUM + jds(GENERATED tsvector + GIN + salary CHECK)
├── 0004_profiles.py                # skill_level ENUM + profiles + 4 张子表(experiences/projects/skills/educations)
└── 0005_profile_chunks.py          # chunk_granularity ENUM + profile_chunks(vector(1024) + HNSW m=16,ef_construction=64 + GIN tsv)
```

ORM/基础设施:
```
apps/api/src/jobcopilot_api/
├── models/
│   ├── base.py                     # DeclarativeBase + IDMixin + TimestampMixin
│   └── __init__.py                 # 导出 Base
└── infra/
    └── db.py                       # 懒加载 async engine + sessionmaker + get_session FastAPI 依赖
```

集成测试:`tests/integration/test_migrations.py` 用 testcontainers 拉 `pgvector/pgvector:pg16`,跑 `upgrade head → downgrade base → upgrade head`,断言扩展 / 9 张表 / HNSW 索引 / 7 个触发器存在。

# 设计决策(实现细节)

- **alembic.ini 的 URL 是占位符**,真正的 URL 由 `env.py` 按优先级解析:`-x dburl=...` > 配置中非占位 URL > `settings.database_url`
- **ENUM 显式 `.create()` / `.drop()`**(`create_type=False`),避免与 `op.create_table()` 隐式交互
- **`tsvector` / `Vector(1024)` 用 `sa.Computed(persisted=True)`** 表达 `GENERATED ALWAYS AS ... STORED`
- **`set_updated_at()` 是单一 PL/pgSQL 函数**,7 张需要 `updated_at` 维护的表共用,触发器名 `tg_<table>_set_updated_at`
- **HNSW 参数**:`vector_cosine_ops`,`m=16`,`ef_construction=64`(沿用 DATA_MODEL §3.8,M1 不调)
- **`metadata` 列**:目前在 migration 里直接叫 `metadata` 没问题;**后续做 ORM 模型时要用 `meta_data: Mapped[dict] = mapped_column("metadata", JSONB, ...)`** 避免与 `Base.metadata` 撞名

# 期间踩到的小坑

1. `alembic.ini` 的 `script_location = alembic` 是相对路径;集成测试在仓库根目录跑时找不到。修复:测试里 `cfg.set_main_option("script_location", str(ALEMBIC_INI.parent / "alembic"))`。
2. alembic 1.18 提示 `path_separator` 需显式;已在 `alembic.ini` 加 `path_separator = os`。
3. ruff `N818` 要求异常类名 `*Error` 后缀:`ValidationFailed` → `ValidationError`。
4. structlog processor 的入参类型是 `MutableMapping[str, Any]`,不是 `dict`;mypy strict 会报。修复:`_redact` 签名换成 `MutableMapping`。
