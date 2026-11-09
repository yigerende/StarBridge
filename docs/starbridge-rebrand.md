# 星桥 StarBridge 更名说明

## 名称与入口

| 项目 | 新名称 |
| --- | --- |
| 中文品牌 / 英文品牌 | 星桥 / StarBridge |
| 本地目录 | `D:/aixiangmu/反代/starbridge` |
| Compose project | `starbridge` |
| 主应用 / PostgreSQL | `starbridge-app` / `starbridge-postgres` |
| 注册应用 / 执行器 | `starbridge-registration-app` / `starbridge-registration-worker` |
| 应用 / 注册镜像 | `starbridge-app` / `starbridge-registration` |
| 新安装默认网络 / 数据卷 | `starbridge_private` / `starbridge_postgres_data` |
| 新安装默认数据库 / 角色 | `starbridge` / `starbridge` |

标识采用深青底色、桥拱与四角星图形；浏览器图标、登录页、用户端、运营端、默认图标预览与注册接入使用同一 SVG。自定义品牌仍支持保存。旧默认品牌（序付 / XUPAY）首次启动迁为新默认；其他业务设置与自定义站名保持。

历史数据库字段、会话 Cookie、API key 与兑换码前缀保留兼容，不因品牌更名强制所有用户重新授权。原注册引擎及原管理台源码不做品牌替换，接入层显示统一站名和图标。

## 独立正式部署

用户于 2026-11-09 明确指定新仓库 `https://github.com/yigerende/StarBridge.git`、正式目录 `/opt/1panel/apps/starbridge` 和对外端口 **5876**。新部署使用全新空库，仅创建指定管理员；原 `/opt/1panel/apps/xufupython` 和 `/opt/1panel/apps/gptreg` 继续运行。新项目不共享旧数据卷、运行目录、密钥或业务配置。

发布顺序为本地验证和提交推送 → 正式从 GitHub 克隆/拉取 → 构建两个镜像 → 初始化独立数据库及注册运行目录 → 启动四个容器 → 验证健康、登录、原注册管理台及其他容器保持。数据库和两个注册服务不映射公网端口。

首次启动先执行统一数据库初始化，再初始化注册表及 runtime；运行目录所有者为注册镜像 UID/GID 1000:1000。业务资源由运营管理员在两类业务各自的配置入口设置，不能把预览数据、演示余额或旧正式供应商密钥带入。

后续更新保留本项目的 `.env`、`.registration-runtime`、`starbridge_postgres_data` 和主密钥；只更新经过验证的目标应用镜像。备份集中在 `/opt/1panel/apps/codex-release-backups/starbridge`，验证成功后保留最近三份完整备份。不得执行 `down -v` 或全局 prune。

历史业务迁移能力保留于整合文档，仅在后续明确要求迁移时使用；本次不执行迁移和停用旧队列。
