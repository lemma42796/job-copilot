# 反向代理负载均衡与 TLS 终止

## 目的

后端服务上线通常会经过反向代理或负载均衡:

- Nginx。
- Envoy。
- HAProxy。
- 云 LB。
- API Gateway。

LLM 应用里,SSE、长请求、大 response、provider 代理都容易被代理层配置影响。

## 正向代理与反向代理

正向代理代表客户端访问外部服务:

```text
client -> forward proxy -> internet
```

反向代理代表服务端接收用户请求:

```text
client -> reverse proxy -> backend service
```

不要混淆。开发机配置 provider 代理是正向代理;Nginx 转发用户请求到 FastAPI 是反向代理。

## 反向代理作用

- TLS 终止。
- 路由。
- 负载均衡。
- 静态文件。
- gzip/br 压缩。
- 限流。
- WAF。
- 日志。
- 超时控制。

应用服务不必直接暴露公网。

## 负载均衡算法

常见:

- round robin。
- weighted round robin。
- least connections。
- consistent hash。
- IP hash。

选择:

- 普通无状态 API:round robin。
- 长连接:SSE/WebSocket 可考虑 least connections。
- 会话粘性:IP hash 或 cookie,但更推荐服务无状态。

JobCopilot session 状态应落数据库,不要依赖同一个用户永远打到同一实例。

## 健康检查

LB 通过 health check 判断实例是否可用。

健康检查要区分:

- 进程活着。
- DB 可连接。
- 依赖 provider 可用。
- 是否应接收流量。

过重的 health check 会放大故障。一般 readiness 检查关键依赖,liveness 检查进程是否卡死。

## TLS 终止

TLS 可以在 LB/Nginx 终止,后端走内网 HTTP。

优点:

- 证书集中管理。
- 后端简化。
- 统一安全策略。

注意:

- 后端需要知道原始 scheme,通过 `X-Forwarded-Proto`。
- 客户端真实 IP 通过 `X-Forwarded-For` 或 `Forwarded`。
- 只能信任来自可信代理的 forwarded headers。

否则应用可能生成错误回调 URL 或记录错误 IP。

## 超时配置

代理层常见超时:

- connect timeout。
- read timeout。
- send timeout。
- idle timeout。
- upstream timeout。

SSE 需要:

- 更长 read timeout。
- 禁用代理响应缓冲。
- 心跳事件。

Nginx 示例概念:

```text
proxy_buffering off
proxy_read_timeout 300s
```

具体配置取决于部署环境。

## 请求体大小

文件上传可能被代理限制:

```text
client_max_body_size
```

如果上传失败返回 413,可能是代理层而不是应用层。

JobCopilot 当前不做 zip 上传,但 JSON 批量导入也可能遇到 request body 限制。

## Header 大小

大 cookie、大 JWT、复杂 tracing headers 可能超过代理 header 限制。

症状:

- 400 Bad Request。
- 431 Request Header Fields Too Large。
- 502。

不要把大量 session state 放 cookie。

## 缓冲

代理默认可能缓冲 upstream response。对普通 JSON 有利,对 SSE 有害。

如果缓冲开启,SSE token 会积累一段时间才发给客户端,用户看到的不是实时进度。

所以 streaming endpoint 要确认代理层关闭 buffering。

## 粘性会话

粘性会话让同一用户请求打到同一实例。

适合:

- 本地内存 session。
- WebSocket 特殊场景。

但会降低负载均衡灵活性。更好的设计是:

- 业务状态落共享存储。
- 实例无状态。
- 连接断开后可恢复。

M2.1 状态机应落库,不能依赖内存粘性。

## 蓝绿和灰度

代理/LB 可做:

- 蓝绿切换。
- 按比例灰度。
- 按 header/cookie 路由。
- 快速回滚。

LLM prompt 灰度也类似,但 prompt version 和 API 流量灰度不是同一层。prompt 灰度要记录 version,否则评测不可解释。

## 面试追问

问: 反向代理和正向代理区别是什么?

答: 正向代理代表客户端访问外部服务,客户端知道代理存在;反向代理代表服务端接收客户端请求,客户端通常不知道后面有多少后端实例。开发时访问 provider 走代理是正向代理,Nginx 转发到 API 是反向代理。

问: SSE 经过 Nginx 有什么注意点?

答: 要关闭响应缓冲,设置足够长的 read timeout,发送心跳,并确保连接断开后业务状态可恢复。否则代理可能缓冲 token 或因 idle timeout 断开连接。

问: 为什么不能信任任意 X-Forwarded-For?

答: 客户端可以伪造这个 header。应用只能在请求来自可信代理时使用 forwarded headers,否则会被伪造 IP 绕过限流或审计。

## Hard negatives

- 正向代理不是反向代理。
- TLS 终止不是业务鉴权。
- 粘性会话不是状态持久化。
- proxy buffering 对 JSON 可能没问题,对 SSE 可能破坏实时性。
- Prompt 灰度不是 LB 灰度,两者都要记录版本和路由依据。

