/**
 * Emit 2 synthetic JD samples to stdout in promptfoo Test JSONL format.
 *
 * Purpose: bootstrap an empty `dataset.jsonl` so the pipeline / CI can be
 * validated before the user has screenshots ready. Synthetic samples are
 * NOT representative of real distribution and should be removed once 5+
 * real samples land.
 *
 * Usage:
 *   pnpm --filter @jobcopilot/evals run prep:synth >> suites/jd_extract/dataset.jsonl
 */

interface Sample {
  description: string;
  vars: {
    jd_text: string;
    expected: {
      title: string;
      hard_skills: { name: string }[];
      salary_min: number | null;
      salary_max: number | null;
    };
  };
}

const SAMPLES: Sample[] = [
  {
    description: 'jd_extract_synth_001 standard cn python',
    vars: {
      jd_text: `[CompanyA] 招聘 Python 高级开发工程师

岗位职责:
1. 负责后端核心服务的设计与开发
2. 参与系统架构设计,优化性能
3. 与产品、前端协作完成需求落地

任职要求:
1. 本科及以上学历,3-5 年 Python 开发经验
2. 熟练掌握 FastAPI 或 Django 框架
3. 熟悉 PostgreSQL、Redis,有微服务经验
4. 熟悉 Docker / Kubernetes 加分

薪资:25-40K · 14 薪
工作地点:北京海淀`,
      expected: {
        title: 'Python 高级开发工程师',
        hard_skills: [
          { name: 'python' },
          { name: 'fastapi' },
          { name: 'django' },
          { name: 'postgresql' },
          { name: 'redis' },
          { name: 'docker' },
          { name: 'kubernetes' },
        ],
        salary_min: 25000,
        salary_max: 40000,
      },
    },
  },
  {
    description: 'jd_extract_synth_002 short en frontend',
    vars: {
      jd_text: `[CompanyB] is hiring a Senior Frontend Engineer (React).

Responsibilities:
- Build and maintain our customer-facing React/Next.js app
- Collaborate with designers on the new design system

Requirements:
- 5+ years React experience
- Strong TypeScript skills
- Familiar with Tailwind CSS

Salary: 30-50K monthly. Remote-friendly.`,
      expected: {
        title: 'Senior Frontend Engineer (React)',
        hard_skills: [
          { name: 'react' },
          { name: 'next.js' },
          { name: 'typescript' },
          { name: 'tailwind css' },
        ],
        salary_min: 30000,
        salary_max: 50000,
      },
    },
  },
];

for (const s of SAMPLES) {
  console.log(JSON.stringify(s));
}
