# 注册账号完整 Session

已合入 `8490ad8` 之后的主分支。用户于 2026-11-11 明确要求提交推送 GitHub 并更新正式；发布采用预构建、隔离验证、等待在途完成、备份后切换的既有流程。实际上线版本与验收结果记录于正式运维 runbook。

## 用户确认的范围

- 原注册平台所有源码及管理页面完全不改，不增加原管理台的下载按钮。
- 星桥用户端与运营端的注册账号凭据将 Session Token 展示换为完整 Session JSON，已有“邮箱+Session”按钮下载真正的 JSON。
- 标准注册成功，或者注册加 OAuth 成功后，通过已有登录 Cookie 调用 `GET https://chatgpt.com/api/auth/session`。
- 原注册流程、代理配置、交付条件、扣费条件不改。查询与下载不触发上游获取，不重新注册或 OAuth。

## 实现位置与数据流

`backend/registration_sessions.py` 是星桥侧的独立成功结果观察器。星桥客户订单以原交付事务写入的 `platform_accounts` 为成功依据；原管理台直接执行的任务只读共享目录中的完成检查点，逐账号确认 `status=complete`，需要 OAuth 时还要求有效 CPA 产物标记。客户任务检查点不会重复进入后一条路径。

首次安装记录启用时间，只处理之后新交付或新完成的账号，不批量刷新历史账号。5 秒发现一次新完成任务；有任务时两个获取槽位连续处理。任务以完成事件唯一键去重，领取使用 PostgreSQL `FOR UPDATE SKIP LOCKED` 和 90 秒租约，保存校验租约，旧执行者不能覆盖新结果。进程退出后的未完成任务可在租约过期后恢复。

状态为：成功结果 → queued → running → ready；网络错误和 408/429/500/502/503/504 进入有界重试，最多 3 次、等待 5/20 秒；身份不匹配、失效 Cookie、无效 JSON 等进入 failed。失败只更新获取任务，不改账号、订单、钱包或交付结果。

从内部注册 worker 的现有只读账号详情接口读取 token 与该账号注册代理。复用已有代理地址，不重新生成代理、不探测出口；有代理记录但地址不可用时不擅自改直连。请求关闭环境代理继承、重定向，设置连接/读超时及 20 秒总时限；Session 正文最多 1 MiB。保存前验证 AT、邮箱归属、有效期和错误标记，保留 JSON 全部字段，使用星桥 `MASTER_KEY` AES-GCM 加密保存于独立表。

默认共享目录为容器内 `/app/registration/.runtime`。本地非 Compose 启动应设置 `REGISTRATION_RUNTIME_DIR` 为现有注册运行目录；通过已有 `WORKER_URL` 访问内部 worker。无需修改原注册平台配置文件或源码。

`backend/registration_gateway.py` 仍先调用原账号接口完成身份、所有权、领取期、功能开关和审计检查。仅成功响应附加 Session；`email_session` 内部通过原 JSON 导出处理器验证全部所选账号，再生成 `邮箱----单行JSON` TXT。任一账号未保存完整 Session，整批 409，不回退到 token，不跳过账号。用户仍受 72 小时领取期约束，管理员沿用原权限。

`frontend/src/registration/SessionCredentials.tsx` 只在星桥 Vite 构建中替换共享凭据组件。原注册平台前端文件及独立构建不受影响。保留邮箱、密码、2FA、AT、RT、OAuth AT；Session 支持完整复制与展开。获取中、失败、未保存分别提示，缺少内容时禁用复制。

## 用户 API 变化

创建注册订单仍返回 HTTP 202 与订单信息，订单查询仍用于获取进度。账号凭据查询与 JSON 导出返回 `session`（完整对象或 null）、`session_status`、`session_fetched_at`，不再使用 `session_token` 代表交付的 Session。`email_session` 的每行第二项是单行 JSON。所有权、鉴权、领取期限和其他导出格式不变。统一用户文档与示例同步更新；原注册引擎的原始契约文件不改。

同订阅付款后的刷新使用同一个 `/api/auth/session` 路径。订阅已有流程带 `refresh=true&reason=token_expired` 并更新后续调用使用的 AT；本轮按用户提供的无查询参数地址获取并完整保存返回值，没有改订阅流程。

## 本地验证（2026-11-11）

- `python -m unittest discover -s tests -p test_registration_sessions.py -v`：14 项，使用独立临时 PostgreSQL 数据库及原注册 API 服务；覆盖 64 次并发发现/领取、加密完整性、OAuth 成功边界、租约恢复/防覆盖、两并发槽位、检查点只读、网络失败重试、不改变交付/资金、其他用户/管理员/领取过期/功能关闭等。
- `python -m unittest discover -s tests -p test_registration_gateway.py -v`：原网关 4 项回归通过。
- `npm run build`：星桥类型检查与生产构建通过。
- `node --test frontend/tests/registrationSessions.spec.mjs`：真实浏览器加载用户端与运营端组件，验证 12,000 字符 AT 的完整 Session 展开/复制/下载、获取中提示，以及 390px 窄屏无横向溢出。使用合成账号，下载与接口全部为测试数据。
- `python scripts/registration_verify.py`：411 文件来源检查通过，原执行引擎未改。与本轮基点比较，整个 `services/gpt-register` 目录无源码差异。

未使用真实账号调用 ChatGPT；用户提供的是 token 占位符。因此本轮验证覆盖本地实现及协议模拟，上游实际连通性需在合入运行后由新的成功注册验证。验证没有发起真实注册、OAuth 或付款；正式发布只进行部署及只读验收。

合入主分支后重新验证：18 项后端测试、8 项浏览器及文档检查、前端生产构建通过。用户 API 文档版本更新至 2.6.2。
