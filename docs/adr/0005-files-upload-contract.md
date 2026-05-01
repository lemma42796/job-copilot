---
adr: 0005
title: 文件上传契约(/v1/files,sha256 去重 / 软删除 / 配额)
owner: lemma42796
status: Accepted
date: 2026-05-01
---

# ADR-0005:文件上传契约

## 上下文

S3 进入 M1"数据入口贯通"的文件层。三处规格留白或冲突,使 S3 起步前仍有 12 个开放问题:

1. 三份文档对 files 的约束不一致:
   - 体积:DATA_MODEL §3.15 CHECK 100MB vs API_SPEC §6.9 20MB
   - `purpose` 取值:DATA_MODEL §3.15 注释举例 `resume_source/jd_source/resume_pdf` vs API_SPEC §6.9 `jd_pdf/jd_image/profile_pdf/other`
   - 删除语义:DATA_MODEL 表有 `deleted_at` + jds/profiles 的 `ON DELETE SET NULL` 暗示软删 vs API_SPEC §6.9 写"硬删除(bytea 删完即释放)"
2. S2-E 因 User ORM 未建临时降级用 DB-only FK,S3 涉及 `files.user_id` 必须建 User ORM,范围(最小集 vs 顺带补 jds/profiles)未定
3. 同 user 同 sha256 的去重语义未定;跨 user 同 sha256 是否共享行未定
4. PDF 文本抽取时机(S3 同步抽 vs S4 抽取层抽)未定 — STATUS Q1 只决了工具(`pypdfium2`),没决时机
5. bytea 流式读写 vs 一次加载未定
6. 200MB 单用户配额 / 5 req/min 限流是否在 M1 实现未定
7. Idempotency-Key 行为(STATUS Q2 已决 M1 跳过,本 ADR 复述以闭合)
8. 下载行为细节(`Content-Disposition` / `ETag` / `Cache-Control` / 中文文件名)未定
9. S3 commit 拆分粒度未定

本 ADR 把以上 12 项一次性锁死,作为 S3 实现的规格依据。

## 决策

### D1. User ORM 范围 = 最小集

S3 只建 User ORM,不顺带补 jds / profiles 等。

理由:S2 已确立"ORM 只声明需要 navigate 的关系,纯约束放 DB"原则。S3 里真正需要 navigate 的只有 `User.files`。jds / profiles 等 ORM 留给 S4 / S7 各自建,避免 S3 一口气把所有 ORM 摊出来导致 mapper 链不闭合或集中改动。

User ORM 字段对齐 0002 migration:`id / email / name / locale / settings / created_at / updated_at / deleted_at`。S3 唯一新增的 navigate 关系:

```python
# models/user.py
class User(Base, IDMixin, TimestampMixin):
    __tablename__ = "users"
    email: Mapped[str]
    name: Mapped[str | None]
    locale: Mapped[str]
    settings: Mapped[dict] = mapped_column("settings", JSONB, ...)
    deleted_at: Mapped[datetime | None]
    files: Mapped[list[File]] = relationship(back_populates="user")
```

`LlmCall.user_id` 仍保持 DB-only FK(S2-E 决策),不在 S3 补 navigate。

### D2. 体积上限 = 20MB(layered defense)

| 层 | 上限 | 角色 |
|---|---|---|
| router 入口 | 20MB | 软上限,用户体验 + 拒早 |
| DB CHECK | 100MB | 硬上限,安全网 |

实现:不读完整体再校验(内存爆),用 starlette `UploadFile` chunked 累积,达到 20MB 即抛 413。

`MAX_UPLOAD_BYTES = 20 * 1024 * 1024` 定义在 `infra/upload.py`。

### D3. MIME 白名单 + magic bytes 二次校验

白名单(继承 API_SPEC §6.9):

```
application/pdf
image/png
image/jpeg
text/plain
text/markdown
application/vnd.openxmlformats-officedocument.wordprocessingml.document   # .docx
```

服务端两次校验:
1. **multipart `Content-Type`** 必须在白名单
2. **magic bytes**:不引入 `python-magic`,自写 5 字节 header sniff
   - PDF: `%PDF`
   - PNG: `\x89PNG\r\n\x1a\n`
   - JPEG: `\xFF\xD8\xFF`
   - DOCX(ZIP): `PK\x03\x04`
   - text/plain / text/markdown:跳 sniff(magic 不稳定)

Content-Type 与 magic 不一致 → 415。

理由:client 撒谎(把 `.exe` 改后缀 `.pdf`)是常见攻击面;`python-magic` 是 libmagic 的 wrapper,引入需要装系统库,本 ADR 不值得为此引依赖。

### D4. `purpose` = StrEnum(应用层强枚举,DB 列保持 VARCHAR(50))

```python
class FilePurpose(StrEnum):
    JD_PDF = "jd_pdf"
    JD_IMAGE = "jd_image"
    PROFILE_PDF = "profile_pdf"
    PROFILE_DOCX = "profile_docx"
    RESUME_PDF = "resume_pdf"      # M3 简历定制产物
    OTHER = "other"
```

DB 列**不**改成 PG ENUM 类型。理由:扩 enum 值需要 ALTER TYPE,M3-M5 还会加(`interview_audio` / `export_zip` 等),维护成本不值。应用层 Pydantic 严格校验,DB 层 VARCHAR(50) 保持灵活。

DATA_MODEL §3.15 的注释举例(`resume_source/jd_source/resume_pdf`)与 API_SPEC §6.9 的取值不一致,**以本 ADR + API_SPEC 为准**;DATA_MODEL §3.15 的注释由后续文档 PR 修正(不阻塞 S3 实现)。

### D5. 去重语义 = `(user_id, sha256)` 唯一,响应带 `replayed: true`

- **同 user 同 sha256** → 返回已有 `file_id`,响应体加 `replayed: true`(物理去重,不是 Idempotency-Key)
- **跨 user 同 sha256** → 各自一行(隐私 + 配额各算各的;ADR-0002 选 Postgres lz4 压缩,可承受重复)

落地:
- 新增 migration `0007_files_unique_user_sha256.py`,加部分唯一索引:
  ```sql
  CREATE UNIQUE INDEX uq_files_user_sha256
  ON files (user_id, sha256)
  WHERE deleted_at IS NULL;
  ```
  部分索引保证软删后允许重传同字节。
- 不动 0002(已 push,不可变更)。
- service 层:`try INSERT → IntegrityError → SELECT 已有行 → return (file, replayed=True)`。竞态可能在 sha256 校验和 INSERT 之间产生,IntegrityError 路径是权威。

API_SPEC §6.9 响应体新增字段:
```json
{
  "id": 123,
  "filename": "...",
  "mime": "...",
  "size_bytes": 102400,
  "sha256": "...",
  "url": "/v1/files/123",
  "replayed": false
}
```

### D6. PDF 文本抽取 = S4 做,S3 不抽

S3 是"通用文件入口",只抽**无业务语义**的元数据(sha256 / size / mime)。

理由:
- 一个文件可能被 jd_parse 用一次、profile_parse 又用一次,文本抽取属"用的人"的领域
- evals(S6/S10)需要"同一文件的不同抽取结果"对比,文本不能定型在 S3
- 单职责:files 表只存原始字节,文本抽取属抽取层

S3 的 `pyproject.toml` **不**引入 `pypdfium2`,等 S4 自己加。

### D7. bytea 一次性读写

20MB 上限下 asyncpg 默认参数无内存压力。

写入:`INSERT INTO files (..., content) VALUES (..., :content)`,一次。
读取:`SELECT content FROM files WHERE id=:id` → bytes → `Response(content=...)`。

100MB chunked 留给 M3+(简历产物 / 数据导出 zip)真正需要时再切。本 ADR 不预设。

### D8. 配额 200MB / 用户在 S3 做;5 req/min 限流推迟

- **配额**:S3 必做。`SELECT COALESCE(SUM(size_bytes), 0) FROM files WHERE user_id=:uid AND deleted_at IS NULL` + 待上传 size > 200MB → 413。**这是 DoS 防线,M1 不做太冒险**。
- **限流**:推迟到 M1 末。`fastapi-limiter` + `pg_advisory_lock` 是横切基础设施,与 jds/profiles/matches 一起做;S3 单做半套(只挂 `/v1/files/upload`)不划算,而且没有限流框架可挂。

### D9. DELETE = 软删除

`UPDATE files SET deleted_at = NOW() WHERE id=:id AND user_id=:uid AND deleted_at IS NULL`。

bytea 不立即释放,留给 M3-M4 加 `/v1/admin/gc` 后台任务硬删 + VACUUM FULL。

行为细节:
- 已软删的文件 GET → **404**(不暴露存在性,与 not_found 同响应)
- 配额计算只看 `deleted_at IS NULL`(D8)
- 部分唯一索引只索引 `deleted_at IS NULL` 的行(D5),软删后允许重传同字节

API_SPEC §6.9 原文"硬删除(bytea 删完即释放)"是 schema 实情的违背(jds.raw_file_id / profiles.raw_file_id `ON DELETE SET NULL` 等价软删除契约),**本 ADR 改正,API_SPEC §6.9 同 PR 修文**。

### D10. 下载行为(GET /v1/files/{id})

| 头 | 取值 |
|---|---|
| `Content-Type` | `files.mime_type` |
| `Content-Disposition` | `attachment; filename*=UTF-8''<percent-encoded>`(RFC 6266,中文文件名兜底) |
| `ETag` | `"<sha256>"`(content 不可变,sha256 是稳定 ETag) |
| `Cache-Control` | `private, max-age=86400` |

客户端带 `If-None-Match: "<sha256>"` 命中 → 304 无 body。

软删的文件、不属当前 user 的文件:都 → 404(不区分,避免存在性泄露)。

### D11. Idempotency-Key

继承 STATUS Q2:M1 不做。

D5 的物理去重已覆盖"用户重复点上传按钮"的常见场景(同字节 → 同 file_id + replayed)。M2/M3 切片若需要再加。

### D12. S3 commit 拆分

S3 拆 1 个 docs commit + 3 个实现 commit(对照 S2 的 `docs: lock` + C/D/E):

| Commit | 内容 |
|---|---|
| **docs**(独立,本 ADR + API_SPEC §6.9 修文 + STATUS 同步) | ADR-0005 落地 + API_SPEC §6.9 改 4 处(硬删→软删 / 响应加 `replayed` / `purpose` 取值收敛 / 体积保留 20MB)+ STATUS 标 S3 规划已锁 |
| **S3-A** | `models/user.py` + `models/file.py` + `schemas/files.py`(Pydantic 入参/出参)+ migration `0007_files_unique_user_sha256.py` + 集成测试(testcontainers 验证唯一约束 + upgrade/downgrade) |
| **S3-B** | `services/file_service.py`(upload / download / soft_delete / dedup / 配额)+ `infra/upload.py`(chunked reader + magic sniff)+ 单测全用 `BytesIO` mock session(覆盖:dedup 命中 / 配额 413 / size 413 / MIME 415 / magic 不符 415 / 跨 user 同 sha256 各算 / 软删后重传) |
| **S3-C** | `routers/files.py`(POST / GET / DELETE)+ `main.py` 注册 + 集成测试(testcontainers + httpx,golden path + 6 错误分支 + ETag 304 + 软删 404) |

为什么 S3-A 不需要建 users / files 表的 migration:0002 已落地。S3-A 只补一条**部分唯一索引**(0007),应用层 ORM/schemas/Pydantic 才是 A 的主体。这是与 S2-C 的本质区别(S2-C 是建表)。

## LLM 调用?

S3 不调 LLM,本 ADR 不影响 ADR-0004 / `llm_calls` 表行为。

## 复审条件

满足任一条件需重新评审本 ADR:

1. evals(S6 / S10)显示 20MB 挡住了真实简历用例(罕见,通常 < 5MB)— D2 上调
2. M2 出现需要跨 user 共享 bytes 的需求(隐私评估通过后)— D5 跨 user 一行
3. lz4 压缩下软删延期硬删导致 DB 占用失控 — D9 GC 任务提前
4. 限流框架在 M1 末上时,如果选型变了(非 fastapi-limiter / 不再用 pg_advisory_lock)— D8 重新评估

## 相关

- ADR-0002:Postgres 一把梭(bytea + lz4 选型)
- ADR-0004:LLM 抽象层契约(commit 拆分 + ORM 边界原则参考)
- 3-DATA_MODEL §3.2 / §3.3 / §3.15:files 表 + 引用方
- 4-API_SPEC §6.9:/v1/files endpoints(本 ADR 修正:硬删→软删 / 响应加 `replayed` / `purpose` 取值)
- 7-ROADMAP M1 §S3
- STATUS Q1(PDF 工具,本 ADR D6 决时机)/ Q2(Idempotency,本 ADR D11 复述)/ Q5(BYOK,M5 才做)

## 不在本 ADR 范围

- PDF 文本抽取(S4)
- 图片 OCR / qwen3.6-vl-flash(M1 末,STATUS Q4)
- 单用户限流横切(M1 末)
- 硬删 GC 后台任务(M3-M4)
- 跨 user bytes 去重(隐私评估未做)
- BYOK 头(M5)
