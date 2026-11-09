# 星桥 · StarBridge

订阅与注册统一服务工作台。Python / FastAPI、PostgreSQL、Vue，以及保留完整原引擎和原管理台的 GPT Register 独立服务。统一账号、登录、会员、积分钱包、开发接入、接口文档与伙伴计划；两类订单、配置、代理和队列分别运行。

订阅办理下级为结账链接、续费管理；注册办理下级为我的账号。订单中心、资金中心按订阅／注册切换。注册订单先冻结积分，成功交付才扣费，失败释放，支持统一会员减免及两位小数结算。

## Docker 部署

全新安装使用 Compose project `starbridge`，容器为 `starbridge-app`、`starbridge-postgres`、`starbridge-registration-app`、`starbridge-registration-worker`。对外端口 **5876**，数据库与注册服务仅内部访问。

正式仓库为 `yigerende/StarBridge`，部署目录为 `/opt/1panel/apps/starbridge`：

```sh
git clone https://github.com/yigerende/StarBridge.git starbridge
cd starbridge
cp .env.example .env
# 设置数据库密码、32 字节 Base64 主密钥、初始管理员密码及注册网关密钥。
docker compose -f compose.yml -f compose.registration.yml build
sudo install -d -m 0700 -o 1000 -g 1000 .registration-runtime
docker compose -f compose.yml -f compose.registration.yml up -d postgres
# 初始化统一表；此进程不启动应用或业务调度。
docker compose -f compose.yml -f compose.registration.yml run --rm --no-deps app python -c "from backend import db;from backend.seed import seed;db.initialize();seed();db.close()"
docker compose -f compose.yml -f compose.registration.yml run --rm --no-deps registration-app node integration/initialize.js
docker compose -f compose.yml -f compose.registration.yml run --rm --no-deps registration-app node -e "require('./docker/runtime').initializeRuntime(process.cwd());require('./src/platform/security').loadSecrets(process.cwd())"
docker compose -f compose.yml -f compose.registration.yml up -d
```

访问 `http://服务器地址:5876`。统一接口文档 `/docs`，OpenAPI `/api/openapi.json`，健康检查 `/health/live`、`/health/ready`。

本次正式安装使用全新空库，只创建指定管理员；原 xufupython 和 GPT Register 的正式服务继续独立运行，不迁入其数据、配置或密钥。后续更新必须保留 StarBridge 自己的数据库卷、`.env`、`.registration-runtime` 及加密密钥，禁止 `down -v`。详见 [品牌与部署说明](docs/starbridge-rebrand.md)。

## 本地开发

本地项目目录为 `starbridge`；Windows、Python 3.11+、PostgreSQL 18、Node.js 22+。页面端口为 **18970**。当前查看环境使用独立预览数据库和停用执行器，不使用已迁往正式的本机历史业务数据。

```powershell
npm --prefix frontend ci
npm --prefix frontend run build
npm --prefix services/gpt-register ci
npm --prefix services/gpt-register/web ci
npm --prefix services/gpt-register/web run build
```

通用本地脚本 `start-local.ps1` / `restart-local.ps1` 会使用 `.env` 指定的数据库；禁止用它们重新启动本机旧业务队列。当前已配置的隔离预览用 `.local/registration-view/start.py` 启动，不启动真实注册或订阅执行。

## 验证与变更

```powershell
python scripts/registration_verify.py
node scripts/registration_openapi.cjs
npm --prefix frontend run build
npm --prefix services/gpt-register/web run build
```

后台测试使用独立临时数据库；网络请求用模拟响应，不执行真实注册、付款或取消续费。源码完整性、财务结算和生产迁移说明见 [整合文档](docs/registration-integration.md)，会员报价见 [会员减免](docs/registration-membership.md)。
