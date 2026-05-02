---
title: S3 User/File ORM + /v1/files 上传 — 切片归档
status: ✅ 完成已 push
purpose: sha256 去重 + 软删 + 200MB 配额,见 ADR-0005
---

# 期间踩到的小坑

1. **`pytest.raises` 不能与 `async with` 同行组合**:`async with sessionmaker_() as session, pytest.raises(NotFoundError):` mypy 报 `RaisesExc` 没有 `__aenter__`。修复:拆成 `async with sessionmaker_() as session:` + 内嵌 `with pytest.raises(...):`。
2. **`Result.rowcount` 在 mypy strict 下不可见**:`session.execute(sa.update(...))` 返回 `Result[Any]`,`rowcount` 属性是 `CursorResult` 的。修复:改用 `.returning(File.id)` + `scalar_one_or_none()` 检测命中,无需 cast。
3. **`session.begin_nested()` SAVEPOINT 接 IntegrityError**:dedup INSERT 失败要在外层事务里 SELECT 已有行,直接 `try/except IntegrityError` 会让外层 txn 进入 aborted 状态。修复:`async with session.begin_nested(): session.add(...); flush()` — IntegrityError 时 SAVEPOINT 自动 rollback,外层 txn 仍可用。
4. **CHECK 约束撞配额测试**:`ck_files_size <= 100MB` 与 200MB 配额测试不能用单行;改成两行各 `(USER_QUOTA-100)//2` 各算各的避开 CHECK。
5. **配额测试的 PDF 太短**:`b"%PDF-1.7\n%..."` 只 35 bytes,小于 quota headroom(100 bytes)所以不触发。修复:PDF 常量加长到 ~1KB。
6. **FastAPI Form/UploadFile 需要 `python-multipart`**:M0/M1 没装,S3-C router 跑测试时 `RuntimeError: Form data requires "python-multipart"`。修复:加进 `apps/api/pyproject.toml` 的 dependencies。
7. **uv workspace 的 dev extras**:`uv sync` 默认不带 optional-dependencies。要 `uv sync --package jobcopilot-api --all-extras` 才能装回 pytest/mypy/ruff。否则 `.venv/bin/pytest` 缺失。
8. **ruff `SIM300 Yoda condition` 误判**:`ALLOWED_MIME == frozenset({...})` 被认为是 Yoda 条件(把 frozenset 字面量当作 literal),自动修成 `frozenset({...}) == ALLOWED_MIME`。无害,接受 autofix 即可。
