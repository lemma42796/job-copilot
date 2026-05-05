'use client';

import dynamic from 'next/dynamic';
import * as React from 'react';

// @monaco-editor/react 在挂载时拉 monaco-editor 主包(几 MB),依赖 window;
// Next 15 服务端渲染会爆,统一走 dynamic + ssr:false 包一层。
// 默认导出 = 普通 Editor;DiffEditor 是 named export。

const MonacoEditor = dynamic(
  () => import('@monaco-editor/react').then((m) => m.default),
  {
    ssr: false,
    loading: () => <EditorPlaceholder hint="加载编辑器…" />,
  },
);

const MonacoDiffEditor = dynamic(
  () => import('@monaco-editor/react').then((m) => m.DiffEditor),
  {
    ssr: false,
    loading: () => <EditorPlaceholder hint="加载对比视图…" />,
  },
);

function EditorPlaceholder({ hint }: { hint: string }) {
  return (
    <div className="flex h-72 items-center justify-center rounded-md border border-border bg-black/[0.02] text-xs text-muted">
      {hint}
    </div>
  );
}

const COMMON_OPTIONS = {
  fontSize: 13,
  lineNumbers: 'on' as const,
  minimap: { enabled: false },
  scrollBeyondLastLine: false,
  wordWrap: 'on' as const,
  wrappingIndent: 'same' as const,
  automaticLayout: true,
  tabSize: 2,
  renderWhitespace: 'none' as const,
};

export function MarkdownEditor({
  value,
  onChange,
  height = '24rem',
}: {
  value: string;
  onChange: (next: string) => void;
  height?: string;
}) {
  return (
    <div
      className="overflow-hidden rounded-md border border-border"
      style={{ height }}
    >
      <MonacoEditor
        defaultLanguage="markdown"
        value={value}
        onChange={(v) => onChange(v ?? '')}
        options={{ ...COMMON_OPTIONS, readOnly: false }}
        theme="vs"
      />
    </div>
  );
}

export function MarkdownDiff({
  original,
  modified,
  height = '24rem',
}: {
  original: string;
  modified: string;
  height?: string;
}) {
  return (
    <div
      className="overflow-hidden rounded-md border border-border"
      style={{ height }}
    >
      <MonacoDiffEditor
        original={original}
        modified={modified}
        language="markdown"
        options={{
          ...COMMON_OPTIONS,
          readOnly: true,
          renderSideBySide: true,
          enableSplitViewResizing: false,
        }}
        theme="vs"
      />
    </div>
  );
}
