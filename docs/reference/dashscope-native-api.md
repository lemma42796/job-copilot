---
title: DashScope 原生 API 参考(百炼 Qwen)
owner: lemma42796
last_updated: 2026-09-05
purpose: 存档阿里云百炼 DashScope 原生协议的端点、请求参数与响应结构,供切换 base_url / 启用原生协议特性时查阅。
---

# 事实边界

本文是**上游官方文档的摘录存档**,不描述 JobCopilot 当前实现。

当前实现走的是 **OpenAI 兼容协议**(`https://dashscope.aliyuncs.com/compatible-mode/v1`),不是本文描述的原生协议。本文记录原生协议是因为:业务空间专属域名(`{WorkspaceId}.{region}.maas.aliyuncs.com`)的示例代码均以原生协议给出,若将来迁移到专属域名或需要原生协议独有的参数,需要这份对照。

以本仓库实际行为为准的文档见 `docs/TECH_DESIGN.md` 与 `docs/STATUS.md`。

# Base URL 与端点

## 共享域名(当前使用)

| 协议 | Base URL |
| --- | --- |
| OpenAI 兼容(华北2) | `https://dashscope.aliyuncs.com/compatible-mode/v1` |
| OpenAI 兼容(新加坡) | `https://dashscope-intl.aliyuncs.com/compatible-mode/v1` |
| rerank(本项目另用) | `https://dashscope.aliyuncs.com/compatible-api/v1/reranks` |

## 业务空间专属域名(官方建议迁移)

官方口径:专属域名"为推理请求提供卓越的性能和更高的稳定性",现有共享域名仍可用。

- 华北2(北京):`https://dashscope.aliyuncs.com` → `https://{WorkspaceId}.cn-beijing.maas.aliyuncs.com`
- 新加坡:`https://dashscope-intl.aliyuncs.com` → `https://{WorkspaceId}.ap-southeast-1.maas.aliyuncs.com`

`{WorkspaceId}` 在百炼控制台「业务空间详情」页查看。

## 原生协议请求地址

各地域格式一致,仅域名段不同:

| 地域 | 域名段 |
| --- | --- |
| 华北2(北京) | `cn-beijing` |
| 新加坡 | `ap-southeast-1` |
| 美国(弗吉尼亚) | `us-east-1` |
| 德国(法兰克福) | `eu-central-1` |
| 日本(东京) | `ap-northeast-1` |

路径按模态区分:

- 纯文本模型:`POST https://{WorkspaceId}.{region}.maas.aliyuncs.com/api/v1/services/aigc/text-generation/generation`
- 多模态模型:`POST https://{WorkspaceId}.{region}.maas.aliyuncs.com/api/v1/services/aigc/multimodal-generation/generation`

**注意**:`qwen3.8-flash` 是多模态模型,原生协议下走 `multimodal-generation` 路径,不是 `text-generation`。

Python SDK 配置:

```python
import dashscope
dashscope.base_http_api_url = "https://{WorkspaceId}.cn-beijing.maas.aliyuncs.com/api/v1"
```

# 与 OpenAI 兼容协议的结构差异

原生协议与 OpenAI 兼容协议**请求体形状不同**,不能只改 base_url 就切过去:

| 项 | OpenAI 兼容 | 原生协议 |
| --- | --- | --- |
| messages 位置 | 顶层 `messages` | `input.messages` |
| 采样与控制参数 | 顶层(`temperature`、`tools` 等) | 全部放进 `parameters` 对象 |
| 多模态 content | `[{"type":"image_url","image_url":{...}}]` | `[{"image":"..."},{"text":"..."}]` |
| 流式开关 | `stream: true` | HTTP header `X-DashScope-SSE: enable` |
| 返回结构 | `choices[0].message` | `output.choices[0].message`,外层带 `status_code` / `request_id` |
| token 用量 | `usage.prompt_tokens` / `completion_tokens` | `usage.input_tokens` / `output_tokens` |

因此若迁移专属域名,`llm/providers/dashscope.py`、`llm/embedders.py`、`agents/answer_judge/agent.py` 三处的请求构造与响应解析都要改,不只是常量。

# 请求参数

## 必选

- **model** `string` — 模型名称。
- **messages** `array` — 上下文,按对话顺序。原生协议下放入 `input` 对象。

## 消息类型

**System Message**:`role` 固定 `system`,`content` 为 string。一般放数组首位。

**User Message**:`role` 固定 `user`。`content` 纯文本时为 string,含多模态数据或启用显式缓存时为 array。array 元素:

- `text` `string` — 文本。
- `image` `string` — 公网 URL / `data:image/<format>;base64,<data>` / 本地绝对路径。
- `video` `array 或 string` — 图像列表为 array,视频文件为 string。
- `audio` `string` — 音频理解模型必选。
- `cache_control` `object` — `{"type":"ephemeral"}`,开启显式缓存。

**Assistant Message**:`role` 固定 `assistant`。`content` 在指定 `tool_calls` 时可省。`partial` 开启前缀续写。`tool_calls` 由上一轮响应获得,含 `id` / `type` / `function{name,arguments}` / `index`。

**Tool Message**:`role` 固定 `tool`,`content` 必须是字符串,`tool_call_id` 标记对应工具。

## 视觉参数(本项目未用)

`fps`(抽帧频率,\[0.1,10\],默认 2.0)、`max_frames`(qwen3.8 系列上限与默认均 8000)、`min_pixels` / `max_pixels` / `total_pixels`(像素阈值)、`vl_high_resolution_images`、`vl_enable_image_hw_output`。

qwen3.8 系列图像默认 `min_pixels=65536`、`max_pixels=2621440`(上限 16777216)。OpenAI 兼容 API 不支持自定义 `max_frames`。

## 采样参数

**temperature** `float`,\[0,2)。qwen3.8-flash 思考模式默认:文本输入 1.0、视觉理解 0.6,**低于 0.6 的值会被默认改为 0.6**。非思考模式默认 0.7。

**top_p** `float`,(0,1.0\]。qwen3.8 思考模式默认 0.95,非思考模式 0.8。

**top_k** `integer`,默认 20(qwen3.8 系列)。大于 100 或 None 表示不启用。

**repetition_penalty** `float`,大于 0,默认 1.05(qwen-flash 系列)。

**presence_penalty** `float`,\[-2.0,2.0\]。qwen3.8 非思考模式默认 1.5,思考模式 0.0。

**seed** `integer`,\[0, 2^31−1\],默认 1234。

## 思考模式参数(qwen3.8 相关,重点)

**enable_thinking** `boolean` — 是否开启思考。开启后思考内容走 `reasoning_content` 字段返回。

**preserve_thinking** `boolean` — 是否把历史 assistant 消息的 `reasoning_content` 拼进模型输入。默认 `false`,但 **qwen3.8-max / qwen3.8-flash 默认为 `true`**。

> **重要**:qwen3.8 系列下 `preserve_thinking` 默认开启,必须把历史对话中所有 `reasoning_content` 完整回传,**不支持把 reasoning_content 拼进 content 字段回传**。历史中不含该字段不会报错。开启后历史 `reasoning_content` 计入输入 Token 并计费。

这条对本项目有直接影响:`agents/answer_judge/agent.py` 是多轮 tool-calling 循环,若不回传 `reasoning_content`,行为与官方默认不一致;若回传,输入 token 会随轮次累积增长,成本估算需要重算。

**thinking_budget** `integer` — 思考过程最大长度,默认为模型最大思维链长度。

**reasoning_effort** `string` — qwen3.8 系列默认 `xhigh`。可选 `xhigh` / `medium` / `low`;`max` 与 `high` 映射为 `xhigh`,`minimal` 映射为 `low`,`none` 映射为 `enable_thinking=False`。传其他值报错。

> **qwen3.8 系列不支持 `reasoning_effort` 与 `thinking_budget` 同时设置,同时设置会报错。** 两者互转规则:
> - 只设 `reasoning_effort`:`low`→4096,`medium`→16384,`xhigh`→262144
> - 只设 `thinking_budget`:0~4096→`low`,4097~16384→`medium`,16385~262144→`xhigh`
> - 都不设:`thinking_budget=131072`,`reasoning_effort=xhigh`

## 输出长度

**max_tokens** `integer` — **即将废弃**,新接入用 `max_completion_tokens`。对多数模型含义是"模型回答的最大 Token 数(不含思维链)"。

**max_completion_tokens** `integer` — 输出最大长度,**包含思维链和回答**。超出时 `finish_reason` 为 `length`。千问 Flash 自 Qwen3.5-Flash 起支持。思考类模型推荐用它。实际输出与设定值最多有 10 Token 误差。

> 本项目 `llm/tiers.py` 用的是 `default_max_tokens`(走 `max_tokens`)。切到 qwen3.8-flash 且开思考后,该参数不限制思维链,思维链可能吃掉预算之外的 token,建议评估是否改用 `max_completion_tokens`。

## 输出格式

**stream** `boolean` — 原生协议下通过 header `X-DashScope-SSE: enable` 实现。

**incremental_output** `boolean` — 流式增量输出,推荐 `true`。默认 `false`(Qwen3-Max / Qwen3-VL / Qwen3 开源版 / QwQ / QVQ 默认 `true`)。

**response_format** `object` — `{"type":"text"}` 或 `{"type":"json_object"}`。指定 json_object 时**必须在提示词里明确要求输出 JSON,否则报错**。

**result_format** `string` — `text` 或 `message`,推荐 `message`。用 `tools` 时必须设为 `message`。

**logprobs** / **top_logprobs** — 对数概率,\[0,5\]。qwen-flash 系列不在支持列表内。

**n** `integer` — 生成响应个数 1-4,传 `tools` 时固定为 1。

**stop** `string 或 array` — 停止词,不可混用字符串与 token_id。

## 工具调用

**tools** `array` — 每项 `{type:"function", function:{name, description, parameters}}`。`name` 限字母数字下划线短划线,最长 64。`parameters` 需为合法 JSON Schema。使用时 `result_format` 必须为 `message`。发起调用和提交工具结果时都必须带 `tools`。

**tool_choice** — 默认 `auto`;`none` 禁用;`{"type":"function","function":{"name":"..."}}` 强制指定。**思考模式的模型不支持强制调用某个工具。**

**parallel_tool_calls** `boolean` — 默认 `false`。

**tool_stream** `boolean` — 默认 `false`,仅影响复杂工具参数(含 array / object 类型参数)的流式输出,仅流式调用生效。qwen3.8-flash 全模态在支持列表内。`false` 时复杂参数一次性输出、格式更准确;`true` 时流式输出、无超时风险。

## 联网搜索(本项目禁用)

**enable_search** `boolean`,默认 `false`。**search_options** 含 `enable_source` / `enable_citation` / `citation_format` / `forced_search` / `search_strategy`(`turbo` / `max` / `agent` / `agent_max`)/ `enable_search_extension` / `prepend_search_result`。会增加 Token 消耗,单独计费。

## 其他

**enable_code_interpreter** `boolean`,默认 `false`。

**X-DashScope-DataInspection** — 请求头,值 `{"input":"cip","output":"cip"}` 开启内容安全增强识别。

**skill** `array` — 仅 `qwen-doc-turbo` 支持的 PPT 生成,`stream` 必须为 `true`。

# 响应结构(原生协议)

流式与非流式格式一致。

顶层:

- **status_code** `string` — 200 成功。Java SDK 不返回该字段,失败抛异常。
- **request_id** `string` — 本次调用唯一标识。
- **code** `string` — 错误码,成功时为空。仅 Python SDK 返回。
- **output** `object` — 调用结果。
- **usage** `map` — Token 用量。

`output` 内:

- **text** — `result_format=text` 时的回复。
- **finish_reason** — `null`(生成中)/ `stop` / `length` / `tool_calls`。
- **choices** `array` — `result_format=message` 时返回。每项含 `finish_reason` 与 `message{role, content, reasoning_content, tool_calls}`。
- **search_info** — 联网搜索结果。

`usage` 内(**与 OpenAI 兼容协议字段名不同**):

- **input_tokens** / **output_tokens** / **total_tokens**
- **input_tokens_details** — `text_tokens` / `image_tokens` / `video_tokens`
- **output_tokens_details** — `text_tokens` / `reasoning_tokens`(仅推理模型)/ `audio_tokens`
- **prompt_tokens_details.cached_tokens** — 命中 Context Cache 的 Token 数
- **cache_creation** — `ephemeral_5m_input_tokens`
- **cache_creation_input_tokens** — 创建显式缓存的 Token 长度
- **cache_type** — 用显式缓存时为 `ephemeral`,否则字段不存在

> `llm/pricing.py` 的成本计算依赖 `cached_tokens` 与 `cache_creation_input_tokens`;原生协议下这两个字段路径与 OpenAI 兼容协议不同,迁移时 `llm/providers/dashscope.py` 的 `_read_cached_tokens` 需要重写。

响应示例:

```json
{
  "status_code": 200,
  "request_id": "902fee3b-f7f0-9a8c-96a1-6b4ea25af114",
  "code": "",
  "message": "",
  "output": {
    "text": null,
    "finish_reason": null,
    "choices": [
      {
        "finish_reason": "stop",
        "message": {
          "role": "assistant",
          "content": "我是阿里云开发的一款超大规模语言模型,我叫千问。"
        }
      }
    ]
  },
  "usage": {
    "input_tokens": 22,
    "output_tokens": 17,
    "total_tokens": 39
  }
}
```

# 调用示例

## 文本输入(Python,多模态端点)

```python
import os
import dashscope

dashscope.base_http_api_url = "https://{WorkspaceId}.cn-beijing.maas.aliyuncs.com/api/v1"

messages = [
    {"role": "system", "content": [{"text": "You are a helpful assistant."}]},
    {"role": "user", "content": [{"text": "你是谁?"}]},
]

response = dashscope.MultiModalConversation.call(
    api_key=os.getenv("DASHSCOPE_API_KEY"),
    model="qwen3.8-flash",
    messages=messages,
)
print(response)
```

## curl

```bash
curl --location "https://{WorkspaceId}.cn-beijing.maas.aliyuncs.com/api/v1/services/aigc/multimodal-generation/generation" \
  --header "Authorization: Bearer $DASHSCOPE_API_KEY" \
  --header "Content-Type: application/json" \
  --data '{
    "model": "qwen3.8-flash",
    "input": {
      "messages": [
        {"role": "system", "content": [{"text": "You are a helpful assistant."}]},
        {"role": "user", "content": [{"text": "你是谁?"}]}
      ]
    },
    "parameters": {"result_format": "message"}
  }'
```

## 流式(纯文本端点,curl)

```bash
curl --location "https://{WorkspaceId}.cn-beijing.maas.aliyuncs.com/api/v1/services/aigc/text-generation/generation" \
  --header "Authorization: Bearer $DASHSCOPE_API_KEY" \
  --header "Content-Type: application/json" \
  --header "X-DashScope-SSE: enable" \
  --data '{
    "model": "qwen-plus",
    "input": {"messages": [{"role": "user", "content": "你是谁?"}]},
    "parameters": {"result_format": "message", "incremental_output": true}
  }'
```

## 工具调用(curl)

```bash
curl --location "https://{WorkspaceId}.cn-beijing.maas.aliyuncs.com/api/v1/services/aigc/multimodal-generation/generation" \
  --header "Authorization: Bearer $DASHSCOPE_API_KEY" \
  --header "Content-Type: application/json" \
  --data '{
    "model": "qwen3.8-flash",
    "input": {"messages": [{"role": "user", "content": [{"text": "杭州天气怎么样"}]}]},
    "parameters": {
      "result_format": "message",
      "tools": [{
        "type": "function",
        "function": {
          "name": "get_current_weather",
          "description": "当你想查询指定城市的天气时非常有用。",
          "parameters": {
            "type": "object",
            "properties": {
              "location": {"type": "string", "description": "城市或县区,比如北京市、杭州市、余杭区等。"}
            }
          },
          "required": ["location"]
        }
      }]
    }
  }'
```

## 异步调用(Python SDK ≥ 1.19.0)

```python
import asyncio
import os
from dashscope.aigc.multimodal_conversation import AioMultiModalConversation


async def main():
    response = await AioMultiModalConversation.call(
        api_key=os.getenv("DASHSCOPE_API_KEY"),
        model="qwen3.8-flash",
        messages=[{"role": "user", "content": [{"text": "你是谁"}]}],
    )
    print(response)


asyncio.run(main())
```

# 对本项目的待办

以下是本文事实与当前实现之间已知的落差,尚未处理:

1. **`preserve_thinking` 在 qwen3.8 系列默认为 `true`**,要求完整回传历史 `reasoning_content`。`agents/answer_judge/agent.py` 的多轮 tool-calling 循环需要确认是否回传,以及回传后输入 token 累积对成本估算的影响。
2. **`max_tokens` 即将废弃**,且对思考模型不覆盖思维链。`llm/tiers.py` 的 `default_max_tokens` 需评估是否改用 `max_completion_tokens`。
3. **`reasoning_effort` 与 `thinking_budget` 互斥**,同时设置报错。当前代码未设置这两个参数,走默认值(`thinking_budget=131072`,`reasoning_effort=xhigh`)——这是一个相当大的思维链预算,对成本有直接影响。
4. **思考模式不支持强制指定工具**。`tool_choice` 若有强制指定的用法,在 STANDARD / PREMIUM(thinking on)下会失效。
5. **是否迁移业务空间专属域名**未决。迁移需改三处客户端的请求构造与响应解析,不是改常量。

# 错误码

调用失败的错误码见上游文档「错误码」章节:<https://help.aliyun.com/zh/model-studio/developer-reference/error-code>
