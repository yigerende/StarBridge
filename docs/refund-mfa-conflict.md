# 代退 MFA 409 重试补充

2026-11-12，用户要求只增加本次失败位置的判断，沿用已有重试方式。

## 范围

子号登录时，`POST https://auth.openai.com/api/accounts/mfa/verify` 返回 409，识别为可重试的登录异常。诊断保留 `login_failed`、`http_status=409`、`stage=mfa_verify` 和有界原因 `mfa_state_conflict`，不记录验证码、凭据或原始响应内容。

本次仅修改 `CustomerLogin.request` 的分类和 Python/Go 两端诊断原因白名单。其他接口的 409、400/401/403、明确账号停用和挑战拦截沿用原处理。

## 复用现有执行规则

- 自动恢复开启：本轮退出并关闭登录会话，由现有调度按 `retry_seconds` 等待，重试上限仍为 `recovery.max_retries`。
- 自动恢复关闭：沿用原进程内的 `max_attempts` 和 `retry_seconds`，每次建立新登录会话，重新获取验证因子和当次动态验证码。
- 保持原订单、母号、代理和冻结积分；此次 409 本身不是网络故障，不触发代理切换、冷却或登录服务故障暂停。
- 只在登录失败处增加可重试判断；已提交的业务请求仍受原检查点限制，结果未知时不重新提交。
- 重试成功后按原成功条件结算；耗尽后按已有清理、核实和资金释放规则处理。

不新增配置项、重试循环或数据库字段，不修改 worker、调度器或钱包。

## 本地验证

运行时合成测试：

```text
python -m unittest test_mfa_conflict test_login_transient_http test_password_failure test_refund_runner test_diagnostics test_refund_transport -q
```

43 项通过。覆盖精确接口/方法/状态识别、其他响应不扩大重试、挑战和停用账户、新登录会话/验证因子/动态码、自动恢复单次返回、不提前清理或提交，以及已有网络和密码失败处理。

Go 使用本机隔离 PostgreSQL，每个测试单独创建并清理 schema，没有调用真实上游。以下测试通过：

- `TestMFAConflictUsesExistingRecoveryAndWallet`：原间隔生效、到期前不领取、原资源与冻结保留、无代理冷却切换、成功仅扣一次、耗尽后清理并仅释放一次、重复完成受原租约限制。
- `TestChildProxyFailureSwitchesRetryAndNewOrdersAvoidCooling`、`TestChildFailureClassifierExcludesAccountAndMotherFailures`：原网络故障切换及分类边界保留。
- `TestChildProxySwitchAfterUncertainSubmissionOnlyVerifies`、`TestUnknownSubmissionRetainsFreezeAndDoesNotReplay`：未知提交继续核实，不重复执行。

来源清单只更新本次三处源码的当前哈希和适配说明，保留来源哈希。`python scripts/refund_verify.py` 检查来源边界。

原改动在独立工作区完成。用户随后明确要求合并推送更新，现合入 f2da1be（含代理恢复检测和运营当前阶段），按在途空闲、备份和预构建流程发布；不修改正式重试配置，不重新执行历史订单。

合入当前代理恢复版本后复测：运行时43项通过（12.098秒），Go全套通过（46.339秒），来源清单通过。没有前端、数据库结构或默认重试配置变更。
