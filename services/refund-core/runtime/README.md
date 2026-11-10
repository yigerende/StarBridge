# 私有服务端协议运行时

本目录不嵌入 Web 静态资源，不提供任何网站角色下载。

manager_oauth 从本地 chatgpt-space-merge 提取，来源提交 0bb1b53f895bca3501f16aaf28ee61699384e36b。登录、Sentinel、merge_v2 等复制文件保留原实现，不调用 Space 业务 API，不共享其配置或数据库。

refund_runner.py 提供本项目编排和持久化检查点通讯。密码/TOTP 以外的额外验证进入待跟进。凭据仅使用标准输入和内存，标准输出仅发送服务端检查点与通用结果码。Go 丢弃原始诊断。

离线测试：python -m unittest discover -s runtime -p 'test_*.py'。禁止替换成真实账号变更。
