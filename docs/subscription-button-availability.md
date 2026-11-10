# 订阅按钮与结账链接开关独立（2026-11-10）

正式开启订阅、关闭结账链接时，公共接口正确返回 `payment_connected=true`、`checkout_connected=false`，但共用表单仍强制要求 `checkout_connected`，导致已填写 Session、卡片、授权的订阅按钮持续禁用。

表单现在按当前业务读取对应可用状态：订阅使用 `payment_connected`，仅提链使用 `checkout_connected`。提交处理器同步执行该检查，服务关闭时显示对应提示。保留 Session、地区、报价、卡片及授权校验，不更改后台功能开关或付款流程。

验证：TypeScript 检查和生产构建通过；浏览器回归覆盖订阅开/提链关、反向组合、同时开启、订阅服务关闭、提链服务关闭、表单直接提交拦截及 390px 布局。旧构建稳定复现按钮禁用，新构建通过全部五种组合。所有 API 均拦截为模拟响应，未创建真实订单或付款。

测试：`node --test frontend/tests/subscriptionAvailability.spec.mjs`；可使用 `STARBRIDGE_UI_ASSETS` 指定隔离构建目录，使用 `STARBRIDGE_UI_TEST_URL` 指定加载静态资源的站点。业务 API 始终由测试拦截。
