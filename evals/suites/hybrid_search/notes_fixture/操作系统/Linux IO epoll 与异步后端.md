# Linux IO epoll 与异步后端

## 为什么重要

后端面试经常从"进程线程"追到"网络 IO 模型"。LLM 应用也绕不开:

- SSE 长连接。
- provider streaming。
- FastAPI async endpoint。
- 数据库连接池。
- 文件上传和笔记导入。
- 多用户并发等待。

理解 IO 模型,才能解释为什么异步不是让 CPU 计算变快,而是提高等待型任务的并发承载。

## 阻塞 IO

阻塞 IO 的流程:

```text
用户线程调用 read
内核等待数据到达
数据从内核缓冲区拷贝到用户缓冲区
read 返回
```

等待期间线程被挂起。简单但并发多时线程数量会膨胀。

如果每个 SSE 连接都占一个阻塞线程,连接数上来后内存和调度成本会很高。

## 非阻塞 IO

非阻塞 IO 调用 read 时:

- 有数据就返回数据。
- 没数据就返回 EAGAIN/EWOULDBLOCK。

应用需要不断轮询。直接 busy loop 会浪费 CPU,所以通常配合 IO 多路复用。

## IO 多路复用

多路复用让一个线程监听多个 fd:

- select。
- poll。
- epoll。

select 有 fd 数量限制且每次要扫描集合。poll 没固定 fd 限制,但仍要线性扫描。epoll 更适合大量连接。

## epoll

epoll 三个核心调用:

- `epoll_create` 创建实例。
- `epoll_ctl` 注册或修改 fd。
- `epoll_wait` 等待事件。

epoll 适合大量连接中少量活跃的场景。例如很多浏览器 SSE 连接大多数时间没有事件。

但 epoll 不是魔法:

- 应用层处理慢仍会阻塞事件循环。
- 数据库连接池耗尽仍会卡。
- LLM provider 慢仍要等待。
- Python CPU 密集计算仍受 GIL 影响。

## LT 与 ET

Level Triggered:

- 只要 fd 还有数据,epoll_wait 会持续通知。
- 编程更安全。

Edge Triggered:

- 状态变化时通知一次。
- 必须一次读到 EAGAIN。
- 性能更好但容易写错。

面试回答中可以说:多数业务框架已经封装这些细节,应用开发者更应关注不要阻塞 event loop。

## Reactor 模式

Reactor 模式:

```text
事件循环等待 IO 事件
事件到达后分发 handler
handler 处理读写
```

Node.js、Nginx、Python asyncio 都可从 Reactor 角度理解。

FastAPI async endpoint 运行在 ASGI 服务器上,底层事件循环负责调度协程。协程遇到 await 时让出控制权。

## Proactor 模式

Proactor 更强调异步操作完成通知:

```text
提交异步 read
内核完成后通知应用
应用处理完成结果
```

Windows IOCP 是典型 Proactor。Linux 上常见是 Reactor + 非阻塞 IO,新一些的 io_uring 更接近异步提交/完成队列模型。

普通后端面试不必展开太深,但要知道不同 OS 实现不同。

## asyncio 与 await

`await` 的含义不是开新线程,而是在等待 IO 时让出事件循环。

适合:

- HTTP provider 调用。
- DB 异步驱动。
- Redis 调用。
- 文件小块异步处理。
- SSE 推送。

不适合直接包 CPU 密集任务:

- 大量 JSON 解析。
- 大规模 embedding 后处理。
- 压缩解压。
- 加密计算。

CPU 密集任务要考虑进程池、线程池、队列或独立 worker。

## FastAPI SSE

SSE 典型形式:

```text
HTTP response 保持打开
Content-Type: text/event-stream
后端持续 yield event
```

风险:

- 客户端断开。
- 代理 idle timeout。
- 心跳缺失。
- 后端任务完成但事件发送失败。
- 多标签页重复 submit。

JobCopilot 的 `submit_session_sse` 应把最终状态写数据库。SSE 只是通知通道,不是唯一事实源。

## 背压

背压表示下游处理不过来时,上游要减速或停止。

在 streaming 中:

- provider token 来得快。
- 后端解析和写 SSE 慢。
- 浏览器接收慢。
- 网络缓冲区积压。

如果没有背压,内存队列可能无限增长。

常见处理:

- bounded queue。
- 超时断开。
- 丢弃中间进度事件,保留最终状态。
- 限制并发 streaming 数。

## 文件描述符

每个 socket、文件、pipe 都占 fd。

高并发服务要关注:

- `ulimit -n`。
- fd 泄漏。
- 连接未关闭。
- 日志文件轮转。

症状:

```text
Too many open files
```

排查:

- `lsof -p <pid>`。
- 连接池配置。
- HTTP client session 是否复用并关闭。

## 连接池

连接池不是越大越好。

需要同时考虑:

- 应用并发。
- DB max connections。
- provider rate limit。
- 每个请求占用连接时间。
- 超时。

如果 DB pool 太小,请求排队;太大,数据库被打爆。

对于 LLM 应用,provider latency 往往长,不要在等待 provider 时长期占用数据库事务。

## 超时

常见超时:

- connect timeout。
- read timeout。
- write timeout。
- pool acquire timeout。
- overall deadline。

面试中要区分:

- HTTP 连接建立慢。
- provider 已连接但长时间没 token。
- SSE 客户端断开。
- DB 连接拿不到。

只设置一个很大的 timeout 通常不够。

## 常见错误

1. **把 async 当成多线程**:async 是协作式调度,不是自动并行 CPU。
2. **在 async endpoint 里调用阻塞 SDK**:会堵住 event loop。
3. **SSE 不发心跳**:代理可能断开 idle 连接。
4. **等待 provider 时持有 DB 事务**:容易拖长锁和连接占用。
5. **无界队列**:慢客户端导致内存堆积。
6. **只看 QPS 不看连接数**:SSE 场景连接数本身就是资源。

## 面试追问

问: FastAPI async 为什么适合 LLM streaming?

答: 因为 provider streaming 和 SSE 都是 IO 等待型任务。协程在等待 provider token 或网络写入时可以让出事件循环,同一线程处理更多连接。但如果在 async endpoint 里做 CPU 密集计算或调用阻塞 SDK,事件循环仍会被堵住。

问: SSE 断开后后端任务怎么办?

答: 断开只说明通知通道断了,不等于业务任务一定取消。后端需要检查客户端断开,可选择取消或继续完成。最终状态必须落库,用户刷新后从 session 状态恢复,不能依赖 SSE 缓冲。

问: epoll 解决了什么,没解决什么?

答: 它解决大量 fd 事件等待的效率问题,避免每个连接一个线程或线性扫描所有 fd。但业务 handler 慢、数据库连接池耗尽、provider 延迟、CPU 密集任务,都不是 epoll 自身能解决的。

## Hard negatives

- `async` 不是多进程,不能自动绕开 CPU 瓶颈。
- `epoll` 不是消息队列,不能保证任务可靠执行。
- `SSE` 不是 WebSocket。SSE 是服务端单向推送,基于 HTTP。
- `心跳事件` 不是业务完成事件,只用于保活和断线检测。
- `连接池变大` 不等于吞吐一定变高,可能把下游打爆。

