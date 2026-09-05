'use client';

/**
 * P0/P1:标题栏右侧的登录态 + 余额。
 *
 * 余额显示在这里是刻意的 —— 所有会调用模型的操作都要过扣费闸门,余额为 0
 * 时任务会以 `insufficient_balance` 终态中止(已产生的结果保留)。用户需要
 * 在触发长任务之前就能看到余额。
 */

import { useCallback, useEffect, useState } from 'react';

import { getBalance, getCurrentUser } from '@/lib/api';
import { clearSession, getToken, subscribe } from '@/lib/auth';

export function SessionBadge() {
  const [email, setEmail] = useState<string | null>(null);
  const [balance, setBalance] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    if (!getToken()) {
      setEmail(null);
      setBalance(null);
      return;
    }
    try {
      const [me, bal] = await Promise.all([getCurrentUser(), getBalance()]);
      setEmail(me.email);
      setBalance(bal.balance_cny);
    } catch {
      // 401 已由 jsonFetch 统一处理(清 token + 跳登录页)。
      setEmail(null);
      setBalance(null);
    }
  }, []);

  useEffect(() => {
    void refresh();
    return subscribe(() => void refresh());
  }, [refresh]);

  if (!email) {
    return (
      <a href="/login" className="text-[12px] text-muted-foreground underline">
        登录
      </a>
    );
  }

  return (
    <div className="flex items-center gap-3 text-[12px] text-muted-foreground">
      {balance != null && <span>余额 ¥{Number(balance).toFixed(2)}</span>}
      <span>{email}</span>
      <button
        type="button"
        onClick={() => {
          clearSession();
          window.location.href = '/login';
        }}
        className="underline"
      >
        退出
      </button>
    </div>
  );
}
