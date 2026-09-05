'use client';

/**
 * P0 登录 / 注册页。
 *
 * 后端签发的是自包含令牌,没有服务端 session,所以这里拿到 token 就存进
 * localStorage,后续所有请求由 `lib/api.ts` 的 authHeaders 带上。
 * 注册成功会自动送一笔体验额度(`JOBCOPILOT_BILLING_SIGNUP_GRANT_CNY`),
 * 余额为 0 时任何会调用模型的操作都会被扣费闸门拒绝。
 */

import { useRouter, useSearchParams } from 'next/navigation';
import { useState } from 'react';

import { ApiError, login as loginApi, register as registerApi } from '@/lib/api';
import { setSession } from '@/lib/auth';

type Mode = 'login' | 'register';

export default function LoginPage() {
  const router = useRouter();
  const params = useSearchParams();
  const next = params.get('next') || '/';

  const [mode, setMode] = useState<Mode>('login');
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [name, setName] = useState('');
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    setError(null);
    setBusy(true);
    try {
      const res =
        mode === 'login'
          ? await loginApi({ email, password })
          : await registerApi({ email, password, name: name || undefined });
      setSession(res.access_token, email);
      router.replace(next);
    } catch (err) {
      setError(
        err instanceof ApiError
          ? (err.problem.detail ?? err.message)
          : '请求失败，请稍后重试',
      );
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="flex min-h-full items-center justify-center p-8">
      <form
        onSubmit={submit}
        className="w-full max-w-sm space-y-4 rounded-lg border border-border bg-background p-6"
      >
        <h1 className="text-lg font-semibold">
          {mode === 'login' ? '登录 JobCopilot' : '注册 JobCopilot'}
        </h1>

        <label className="block space-y-1">
          <span className="text-sm text-muted-foreground">邮箱</span>
          <input
            type="email"
            required
            autoComplete="email"
            value={email}
            onChange={(e) => setEmail(e.target.value)}
            className="w-full rounded border border-border bg-surface px-3 py-2 text-sm"
          />
        </label>

        <label className="block space-y-1">
          <span className="text-sm text-muted-foreground">密码</span>
          <input
            type="password"
            required
            minLength={8}
            autoComplete={mode === 'login' ? 'current-password' : 'new-password'}
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            className="w-full rounded border border-border bg-surface px-3 py-2 text-sm"
          />
        </label>

        {mode === 'register' && (
          <label className="block space-y-1">
            <span className="text-sm text-muted-foreground">昵称（可选）</span>
            <input
              value={name}
              onChange={(e) => setName(e.target.value)}
              className="w-full rounded border border-border bg-surface px-3 py-2 text-sm"
            />
          </label>
        )}

        {error && <p className="text-sm text-destructive">{error}</p>}

        <button
          type="submit"
          disabled={busy}
          className="w-full rounded bg-primary px-3 py-2 text-sm text-primary-foreground disabled:opacity-50"
        >
          {busy ? '处理中…' : mode === 'login' ? '登录' : '注册'}
        </button>

        <button
          type="button"
          onClick={() => {
            setMode(mode === 'login' ? 'register' : 'login');
            setError(null);
          }}
          className="w-full text-sm text-muted-foreground underline"
        >
          {mode === 'login' ? '还没有账号？去注册' : '已有账号？去登录'}
        </button>
      </form>
    </div>
  );
}
