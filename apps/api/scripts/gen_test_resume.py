"""生成一份覆盖各种边界的中文测试简历 PDF,用于 dogfood 简历解析。

跑法(reportlab 临时拉,不污染项目依赖):
    uv run --with reportlab python apps/api/scripts/gen_test_resume.py

输出到 <项目根>/fixtures/resumes/test-resume-bugs.pdf(fixtures/ 已在 .gitignore)
"""

from __future__ import annotations

from pathlib import Path

from reportlab.lib.enums import TA_LEFT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import cm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import (
    Paragraph,
    SimpleDocTemplate,
    Spacer,
)

CN_FONT_PATH = "/System/Library/Fonts/STHeiti Medium.ttc"
CN_FONT = "STHeiti"

# 项目根 = 当前文件 ../../../  (apps/api/scripts/gen_test_resume.py → JobCopilot/)
PROJECT_ROOT = Path(__file__).resolve().parents[3]
OUT = PROJECT_ROOT / "fixtures" / "resumes" / "test-resume-bugs.pdf"


def _register_font() -> None:
    pdfmetrics.registerFont(TTFont(CN_FONT, CN_FONT_PATH, subfontIndex=0))


def _styles() -> dict[str, ParagraphStyle]:
    base = getSampleStyleSheet()
    body = ParagraphStyle(
        "body",
        parent=base["BodyText"],
        fontName=CN_FONT,
        fontSize=10,
        leading=14,
        alignment=TA_LEFT,
    )
    h1 = ParagraphStyle(
        "h1",
        parent=base["Heading1"],
        fontName=CN_FONT,
        fontSize=18,
        leading=22,
        spaceAfter=4,
    )
    h2 = ParagraphStyle(
        "h2",
        parent=base["Heading2"],
        fontName=CN_FONT,
        fontSize=13,
        leading=17,
        spaceBefore=10,
        spaceAfter=4,
    )
    h3 = ParagraphStyle(
        "h3",
        parent=base["Heading3"],
        fontName=CN_FONT,
        fontSize=11,
        leading=15,
        spaceBefore=6,
        spaceAfter=2,
    )
    return {"body": body, "h1": h1, "h2": h2, "h3": h3}


def build_story(s: dict[str, ParagraphStyle]) -> list:
    story: list = []

    # ===== 头部 =====
    story.append(Paragraph("张明远 / Zhang Mingyuan", s["h1"]))
    story.append(
        Paragraph(
            "📞 138-0013-8888 &nbsp;&nbsp;|&nbsp;&nbsp; ✉ zhang.mingyuan@example.com "
            "&nbsp;&nbsp;|&nbsp;&nbsp; 📍 上海 · 浦东新区",
            s["body"],
        )
    )
    story.append(Spacer(1, 6))

    # ===== 求职意向 / Summary =====
    # 边界:特殊字符 (引号、括号、%、emoji)、长句、中英混排
    story.append(Paragraph("求职意向 Target", s["h2"]))
    story.append(
        Paragraph(
            "目标岗位:<b>大模型应用工程师 / LLM Application Engineer / "
            "AI 全栈工程师</b>(欢迎远程或上海)",
            s["body"],
        )
    )
    story.append(
        Paragraph(
            "期望薪资:35K - 55K · 13 薪",
            s["body"],
        )
    )
    story.append(Spacer(1, 4))

    story.append(Paragraph("个人简介 Summary", s["h2"]))
    story.append(
        Paragraph(
            "6 年后端 + 2 年大模型应用经验,主导过日均 800w 调用的 RAG 系统(P99 延迟"
            " < 1.2s,命中率 92%);熟悉 LangChain / LlamaIndex,在 \"长文本压缩\" "
            "和 Agent 编排上有较深积累 🚀。曾任「硅基流动」核心团队成员,目前关注"
            " AI Coding 与多模态 Agent 方向。",
            s["body"],
        )
    )

    # ===== 工作经历 =====
    # 边界:
    #  - 多段(3段)
    #  - 当前在职(end_date 至今)
    #  - 部分日期(YYYY-M, YYYY)
    #  - 公司中英混 + 缺 location
    #  - tech_stack 与 bullets 分离
    #  - achievements 量化
    story.append(Paragraph("工作经历 Experience", s["h2"]))

    story.append(
        Paragraph(
            "<b>硅基流动 SiliconFlow</b> · 高级算法工程师 · 北京 "
            "&nbsp;&nbsp;<font color='#888'>2024-1 — 至今</font>",
            s["h3"],
        )
    )
    story.append(
        Paragraph(
            "技术栈:Python, FastAPI, PostgreSQL/pgvector, Redis, "
            "LangChain, vLLM, Kubernetes",
            s["body"],
        )
    )
    story.append(Paragraph("• 主导内部 RAG 平台从 0 到 1 建设,日均处理 800w 次检索请求", s["body"]))
    story.append(Paragraph("• 设计 hybrid search(BM25 + 向量),Top-5 命中率从 78% 提升到 92%", s["body"]))
    story.append(Paragraph("• 推动长文本压缩方案落地,在保持回答质量前提下 token 成本下降 43%", s["body"]))
    story.append(Paragraph("• 作为 tech lead 带 4 人小组,牵头季度 OKR 拆解与 code review", s["body"]))
    story.append(Spacer(1, 2))

    story.append(
        Paragraph(
            "<b>字节跳动 ByteDance</b> · 后端开发工程师 · 上海 "
            "&nbsp;&nbsp;<font color='#888'>2020-03 — 2023-12</font>",
            s["h3"],
        )
    )
    story.append(
        Paragraph(
            "技术栈:Go, Kitex, MySQL, Redis, Kafka, ClickHouse",
            s["body"],
        )
    )
    story.append(Paragraph("• 负责抖音电商订单中台核心链路,支撑双十一峰值 12w QPS", s["body"]))
    story.append(Paragraph("• 主导冷热数据分层方案,DB 存储成本年节省 ~280 万元", s["body"]))
    story.append(Paragraph("• 优化幂等中间件,将订单重复落库率从 0.03% 降到 0.0001%", s["body"]))
    story.append(Spacer(1, 2))

    # 这一段刻意写 YYYY 形式 + location 缺失
    story.append(
        Paragraph(
            "<b>美团 Meituan</b> · 软件开发工程师 "
            "&nbsp;&nbsp;<font color='#888'>2018 — 2020</font>",
            s["h3"],
        )
    )
    story.append(Paragraph("• 参与外卖搜索召回链路改造,QPS 提升 35%", s["body"]))
    story.append(Paragraph("• 编写自动化部署工具,发布耗时从 25 分钟缩短到 6 分钟", s["body"]))

    # ===== 项目经历 =====
    # 边界:
    #  - repo_url / demo_url / 无 url 三种
    #  - role 字段
    #  - bullets vs achievements 分离
    story.append(Paragraph("项目经历 Projects", s["h2"]))

    story.append(
        Paragraph(
            "<b>JobCopilot</b> · 全栈作者 "
            "&nbsp;&nbsp;<font color='#888'>2026-02 — 至今</font>",
            s["h3"],
        )
    )
    story.append(
        Paragraph(
            "代码:https://github.com/example/jobcopilot &nbsp;&nbsp;|&nbsp;&nbsp; "
            "Demo:https://jobcopilot.example.com",
            s["body"],
        )
    )
    story.append(
        Paragraph(
            "技术栈:FastAPI, Next.js, PostgreSQL + pgvector, "
            "Qwen3-Coder-Plus, Alembic",
            s["body"],
        )
    )
    story.append(Paragraph("• 设计基于 LLM 的 JD 解析与简历定制 Agent 流水线", s["body"]))
    story.append(Paragraph("• 实现 hybrid retrieval(BM25 + 1024 维向量)+ LLM rerank", s["body"]))
    story.append(Paragraph("成果:dogfood 13 张 BOSS JD,parse 成功率 100%,定位 26 类 bug 全修复", s["body"]))
    story.append(Spacer(1, 2))

    story.append(
        Paragraph(
            "<b>Long-Context Compressor</b> · 技术负责人 "
            "&nbsp;&nbsp;<font color='#888'>2024-06 — 2024-11</font>",
            s["h3"],
        )
    )
    story.append(
        Paragraph(
            "代码:github.com/example/lc-compressor",
            s["body"],
        )
    )
    story.append(Paragraph("• 提出基于 \"语义块抽取 + 重要度打分\" 的两阶段压缩算法", s["body"]))
    story.append(Paragraph("• 在 LongBench 上 F1 与原文相当(<1pt 差距),token 减少 60%", s["body"]))

    # 第三个项目无 url
    story.append(
        Paragraph(
            "<b>分布式爬虫调度框架</b> &nbsp;&nbsp;<font color='#888'>2019</font>",
            s["h3"],
        )
    )
    story.append(Paragraph("• Redis-based 任务队列 + 失败重试 + IP 池", s["body"]))

    # ===== 技能 =====
    # 边界:6 个 category 覆盖、同名不同写法、不同 level、years
    story.append(Paragraph("技能 Skills", s["h2"]))
    story.append(
        Paragraph(
            "<b>编程语言</b>:Python(精通,8 年)、Go(熟练,4 年)、"
            "TypeScript(熟练,3 年)、Rust(入门,0.5 年)",
            s["body"],
        )
    )
    story.append(
        Paragraph(
            "<b>框架</b>:FastAPI、Next.js / React、LangChain、LlamaIndex、Kitex、Gin",
            s["body"],
        )
    )
    story.append(
        Paragraph(
            "<b>数据库</b>:PostgreSQL(含 pgvector)、MySQL、Redis、ClickHouse、"
            "MongoDB",
            s["body"],
        )
    )
    story.append(
        Paragraph(
            "<b>云与基础设施</b>:Kubernetes、Docker、AWS(EKS / RDS / S3)、"
            "阿里云(ACK / OSS)",
            s["body"],
        )
    )
    story.append(
        Paragraph(
            "<b>工具</b>:Git、Linear、Datadog、Grafana、pytest",
            s["body"],
        )
    )
    story.append(
        Paragraph(
            "<b>其他</b>:Prompt Engineering、Agent 编排、Embeddings、Evals",
            s["body"],
        )
    )

    # ===== 教育 =====
    # 边界:GPA、honors 多个
    story.append(Paragraph("教育背景 Education", s["h2"]))
    story.append(
        Paragraph(
            "<b>上海交通大学</b> · 计算机科学与技术 · 硕士 "
            "&nbsp;&nbsp;<font color='#888'>2015-09 — 2018-06</font>",
            s["h3"],
        )
    )
    story.append(Paragraph("GPA:3.8 / 4.0 &nbsp;&nbsp;|&nbsp;&nbsp; 荣誉:国家奖学金、校优秀毕业生", s["body"]))

    story.append(
        Paragraph(
            "<b>武汉大学</b> · 软件工程 · 学士 "
            "&nbsp;&nbsp;<font color='#888'>2011 — 2015</font>",
            s["h3"],
        )
    )
    story.append(Paragraph("GPA:3.7 / 4.0 &nbsp;&nbsp;|&nbsp;&nbsp; 荣誉:三好学生、ACM 校赛金牌", s["body"]))

    return story


def main() -> None:
    _register_font()
    s = _styles()

    OUT.parent.mkdir(parents=True, exist_ok=True)
    doc = SimpleDocTemplate(
        str(OUT),
        pagesize=A4,
        leftMargin=2 * cm,
        rightMargin=2 * cm,
        topMargin=1.5 * cm,
        bottomMargin=1.5 * cm,
        title="测试简历 - JobCopilot dogfood",
        author="JobCopilot",
    )
    doc.build(build_story(s))
    print(f"✅ 已生成: {OUT}")


if __name__ == "__main__":
    main()
