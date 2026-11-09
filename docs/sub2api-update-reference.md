# Sub2API 更新实现参考

核对本地 sub2api/backend/internal/service/update_service.go、handler/admin/system_handler.go、pkg/sysutil/restart.go，以及 frontend/src/components/common/VersionBadge.vue。

Sub2API 从 GitHub Releases 查询版本并按操作系统、架构下载预编译归档。存在 checksums.txt 时验证校验和，解压后将当前可执行文件重命名为 .backup，再原子替换新程序。正在运行的进程继续服务；管理员单独点击重启后延迟退出，交给 Docker/systemd 重启策略重新拉起。页面倒数 8 秒，然后请求健康接口并重试；8 秒不是源码编译时间。

StarBridge 按用户明确选定的“拉取 GitHub 最新源码、重建并更新”要求保留源码发布流程。借鉴其准备/重启两步交互、版本徽标、管理员权限、回退和健康确认。更新准备由宿主维护服务完成 fetch、干净源码构建、Compose 校验；重启使用现成镜像，仅切换主应用和注册引擎，保留数据库、配置及密钥。复用构建缓存，没有宣称已配置 GitHub 镜像发布流水线。

长连接重启验收发现 SSE 会让 Uvicorn 等待退出，因此进程收到终止信号时主动关闭实时通知连接，并给剩余 HTTP 连接 5 秒结束窗口；业务队列仍须在重启前确认空闲。
