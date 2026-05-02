---
title: M0 基础设施 — 切片归档
status: ✅ 完成已 push
purpose: M0 期间踩到的两个坑,影响 docker compose / 前端 SSR 配置
---

# M0 期间踩到的两个坑

1. 选错 postgres 镜像 `ghcr.io/tembo-io/tembo-pg-cnpg`(私有镜像,registry 拒绝匿名拉取)。改用公开 `pgvector/pgvector:pg16`。**pgmq 不在该镜像中,推迟到 M2 用自定义 Dockerfile 装(届时基于 pgvector 镜像 + tembo-io/pgmq 的 .deb 安装)。** `docker/postgres/init.sql` 与 `docker-compose.yml` 已注释。

2. Next.js 服务端组件在容器内 SSR 时通过 `localhost:8000` 调 API 失败 —— 容器内 `localhost` 指向 web 自己。修复:在 `apps/web/src/lib/api.ts` 区分 `INTERNAL_API_BASE_URL=http://api:8000`(SSR)与 `NEXT_PUBLIC_API_BASE_URL=http://localhost:8000`(浏览器)。
