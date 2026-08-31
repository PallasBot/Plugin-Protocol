# 更新日志

本文件依据 git tag 历史整理，版本号遵循[语义化版本](https://semver.org/lang/zh-CN/)。
新提交合入后请在 `## [Unreleased]` 下记录，发布时随版本 tag 归档。

## [4.0.64] - 2026-08-31

### 更新公告

- 重新上号的多轮会话按牛牛账号和用户隔离，避免多个 Bot 之间串用状态。
- 分片模式下的重新上号转发继续按 Bot 账号和用户隔离。

## [4.0.63] - 2026-08-25

### 更新公告

- Docker Runtime 已回收过的容器不再被每次启动重复回收，并避免因此反复打印告警日志。

## [4.0.62] - 2026-08-14

- refactor(log): 日志占位符与运维约定整改，就绪事件收进摘要

## [4.0.61] - 2026-08-11

- feat(logging): 统一批量账号操作事件文案

## [4.0.60] - 2026-08-11

- feat(logging): 补充账号创建、重新上号与批量操作业务事件日志

## [4.0.59] - 2026-08-10

### 更新公告

- 协议账号实例名支持独立快速保存，不再检查、同步或热更新 WS、端口与 Runtime 配置。
- Docker 镜像操作会预先检测 CLI、socket 权限与 daemon 连通性，并在控制台任务中给出准确的失败原因。

## [4.0.58] - 2026-08-10

### 更新公告

- SnowLuma Runtime 保持用户选择的上游 Docker 镜像；已有 Runtime 可保存为下次启动采用指定镜像，当前容器不会因此重启。
- Docker Runtime 连续 3 天没有任一账号的 Bot WS 连接时，会仅回收容器，保留账号、Runtime 注册和数据目录。

## [4.0.57] - 2026-08-08

### 更新公告

- 适配 SnowLuma Docker 新版持久化布局，应用数据改挂载到 `/app/data`；QQ 配置与本地数据仍使用原有挂载。
- 重建本地 SnowLuma 派生镜像时会强制拉取上游基镜像，避免沿用旧的存储布局。

## [4.0.56] - 2026-08-05

### 更新公告

- 可在控制台设置新建账号默认使用 NapCat 或 SnowLuma；私聊“创建牛牛”未指定类型时也会沿用该选择。
- 切换协议端时不再把 SnowLuma Runtime 目录误存为 NapCat 数据目录，后续切回 NapCat 更可靠。

## [4.0.55] - 2026-08-04

### 更新公告

- SnowLuma Runtime 配置支持指定 Docker 镜像，并可恢复使用默认镜像。
- 控制台可将镜像批量保存到全部 Runtime，选择下次启动生效或逐个重建当前运行容器。
- 批量重建只使用本机已有镜像，不会自动拉取或构建；操作进度会实时反馈失败阶段。

## [4.0.54] - 2026-08-04

### 更新公告

- SnowLuma 同一 QQ 出现旧 `--relaunch` 孤儿进程时，恢复登录会清理旧窗口，仅保留当前 QQ 进程。
- QQ 提示账号存在安全风险时，控制台会明确提示先在最新版手机 QQ 按指引恢复，不再误以为二维码未生成。

## [4.0.53] - 2026-08-04

### 更新公告

- SnowLuma WebUI 的 OneBot 配置响应带 `config` 包装时，保存 WS 配置可正确热应用。

## [4.0.52] - 2026-08-04

### 更新公告

- 修改 SnowLuma OneBot WS 地址、名称或 token 时，读取在线完整配置后热应用，不再重启容器。
- Docker OneBot 配置文件改为容器可读，避免保存后协议端无法加载。
- 共享 SnowLuma Runtime 的「重启」恢复为重启整个 Runtime；「启 QQ / 停 QQ」仍只影响当前账号。

## [4.0.51] - 2026-08-04

### 更新公告

- SnowLuma 账号修改 OneBot WS 地址（含端口）、名称或 token 后会直接热应用，不再重启容器

### Added

- feat(snowluma): 通过 SnowLuma WebUI 热应用已保存的 OneBot 配置，并向控制台返回应用结果

## [4.0.50] - 2026-07-28

### 更新公告

- SnowLuma Docker 默认内存上限改为 2g、memory+swap 改为 3g，减轻 QQ 顶满 1g 后换页狂读盘

### Changed

- chore(snowluma): 默认 `pallas_protocol_snowluma_docker_memory_limit/swap` 由 1g/1536m 调整为 2g/3g

## [4.0.49] - 2026-07-28

### 更新公告

- 停止账号时，若 supervisor 已停但 QQ `--relaunch` 孤儿还在，会继续强制结束，不再误报「进程仍在」

### Fixed

- fix(snowluma): `supervisorctl stop` 后补杀 `--relaunch` 孤儿 QQ，避免停止失败

## [4.0.48] - 2026-07-28

### 更新公告

- 协议账号「停止」只停该号 QQ，不拆共享 Runtime 容器
- 主号 / 挂载副号均经 supervisord 真正停住，避免 toast 已停但进程立刻被拉起
- 卡片「运行中」按该号 QQ 进程判断，不再把容器还在当成账号还在跑

### Added

- feat(snowluma): 停账号会停止该号 QQ 主进程（不拆共享 Runtime 容器）

### Fixed

- fix(snowluma): 停 QQ 时 SIGTERM 被 Electron 吞掉仍返回成功，改为确认后补 SIGKILL；主进程匹配排除 crashpad
- fix(snowluma): 主号 QQ 经 `supervisorctl stop/start qq` 启停，避免 supervisord `autorestart` 把刚停的进程立刻拉起
- fix(snowluma): 共享 Runtime 副号（`qq-extra-N`）同样走 supervisorctl，修复挂载号停不掉/立刻被拉起
- fix(snowluma): 账号 `process_running` 按该号 QQ 进程判断，不再把共享 Runtime 容器在跑当成仍运行中

## [4.0.47] - 2026-07-28

- feat(snowluma): Docker 注入 `SNOWLUMA_WEBUI_BOOTSTRAP_PASSWORD` + `SNOWLUMA_HOOK_AUTOLOAD=1`，固定 WebUI 口令并默认交给上游自动注入 Hook
- fix(snowluma): WebUI 登录有托管口令时不再连环试日志初始密码，避免触发 429；429 给出明确提示
- fix(snowluma): 启停/一键登录/共享 Runtime 副号不再自动经 WebUI 注入 Hook（保留手动「注入」）
- feat(snowluma): WebUI 换挂 Runtime 保存后，独占旧 Runtime 会停并删除旧容器，且清理已空的 Runtime 注册；共享且仍有其它账号时不删

## [4.0.46] - 2026-07-26

- fix(snowluma): 切换到新建 Runtime 时同步 OneBot 宿主机端口到 Runtime，避免 Docker 启动报缺端口
- fix(snowluma): 挂载已有 Runtime 时忽略表单镜像，沿用 Runtime 已有镜像

## [4.0.45] - 2026-07-26

- feat(config): WebUI 配置字段增加 ui_group 分组与 ui_order 排序


## [4.0.44] - 2026-07-26

- feat(snowluma): 单容器多 QQ：挂载 `qq-homes` + `SNOWLUMA_EXTRA_QQ_HOMES`；按 UIN/PID 选登录窗与二维码缓存，避免串扰；共享 Runtime 二次启动时按需拉起对应 QQ 进程
- fix(snowluma): 重新上号刷新二维码前确保多 QQ 进程与选窗上下文
- feat(snowluma): 派生镜像打与上游相同的版本 tag；运行时按配置上游 tag 选用，可像 NapCat 一样固定版本
- feat(runtime): `runtime_profile` 持久化 `snowluma_docker_image` 并同步到配置
- feat(snowluma): 账号运行时可按账号/Runtime 选择 SnowLuma Docker 镜像（与 NapCat 账号级镜像选择对齐）

## [4.0.43] - 2026-07-25

- fix(runtime): 同镜像 Docker 拉取任务单飞，避免并发 `docker pull` 互相卡住
- fix(runtime): 单层 `Download complete` 不再误标为总进度 70%；无输出时心跳提示「仍在拉取」
- fix(runtime): pull 子进程独立进程组，任务取消时可终止残留 docker 进程

## [4.0.42] - 2026-07-25

- feat(runtime): Docker 镜像拉取改为异步任务（`job_id` + 状态查询 + SSE 进度），并支持按 `protocol=napcat|snowluma` 选择目标镜像
- feat(snowluma): 拉取上游镜像后强制重建 `pallas/snowluma-auto-login` 派生镜像
- fix(runtime): SnowLuma 账号日志按 runtime track id 归集，避免容器日志空白
- fix(runtime): 解析 docker `\r` 刷新行与层内百分比，拉取进度可真实推进

## [4.0.41] - 2026-07-25

- feat: PluginMetadata.extra 增加 `help_tag`（帮助图分组）

## [4.0.39] - 2026-07-24

- fix(runtime): 保存协议资产时仅在 Docker↔非 Docker 模式切换时停进程/删容器；仅改镜像等配置不再连带停止 SnowLuma 等其它协议端。

## [4.0.38] - 2026-07-22

- feat(runtime): 支持按账号切换 NapCat / SnowLuma 运行时、NapCat Docker 镜像，以及新建或挂载已有 SnowLuma Runtime；切换失败自动回滚。
- fix(web): Bot WebUI 的协议接口请求改用真实控制台 API 基址，账号保存不再落入 SPA 回退页。
- fix(runtime): NapCat 缺少 bypass 配置时默认六项全开；账号运行时展示优先使用实际 Docker 镜像。

## [4.0.37] - 2026-07-22

- feat(snowluma): 显式 SnowLuma Runtime，支持一个进程/容器挂多个 QQ（Shell + Docker）；启停 QQ 与启停 Runtime 分层；旧账号自动迁移为单 QQ Runtime。

## [4.0.36] - 2026-07-18

- fix(relogin): 重新上号前通过 Pallas-Bot 集群在线态判断账号是否已连接；已在线时直接提示无需操作，不再重启协议端。

## [4.0.35] - 2026-07-16

- feat(protocol): 控制台按账号反代 SnowLuma noVNC，统一复用控制台会话鉴权，不再要求为每个实例单独暴露公网端口。
- fix(web): 撤回 NapCat/SnowLuma 原生 WebUI 的控制台反代入口；上游 SPA 不支持子路径部署，避免提供登录后失效的半可用链接。

## [4.0.34] - 2026-07-16

- fix(snowluma): 协议插件新建或同步 OneBot 配置时默认关闭内置 `#sl` 状态命令；保留账号已有的显式设置。

## [4.0.33] - 2026-07-16

- fix(relogin): SnowLuma 重新上号增加「是否提示风险/外挂设备」问答；回复「是」仅清 QQ 本地登录态并重建容器，回复「否」沿用普通恢复流程。
- fix(relogin): 分片 worker 在等待 hub 长任务前立即回复「正在恢复登录」或「正在重置登录态」，最终统一返回账号上线或二维码。

## [4.0.32] - 2026-07-16

- fix(snowluma): 补齐 QQ 恢复登录状态机：识别并关闭 `fbsetbg` 桌面报错，处理下线通知、身份失效与连续确认弹窗；结果收敛为成功登录或可发送二维码。
- fix(snowluma): 已过期二维码自动刷新；二维码页检测「自动登录」蓝色勾选，未勾选时点击并复检，避免一键登录切页后状态丢失。
- fix(snowluma): 自动登录镜像预装 ImageMagick、Tesseract 与中文 OCR 数据，避免容器运行时缺少弹窗识别依赖。
- fix(relogin): SnowLuma 容器已运行时直接执行恢复流程，不再无谓重启容器。

## [4.0.31] - 2026-07-15

- fix(snowluma): Linux Docker 改用本地构建的 `pallas/snowluma-auto-login:latest`（固定 `motricseven7/snowluma:latest` 基础镜像并预装 xdotool）；运行时配置不再接受 SnowLuma 镜像覆盖。
- fix(snowluma): 旧会话失效时先关闭 xmessage 提示并重新定位 QQ 窗；识别到二维码即停止点击，移除运行中安装 xdotool。
- fix(relogin): SnowLuma 一键登录、二维码、超时回复改为简洁文案，避免泄漏 inject hook 原始错误。

## [4.0.30] - 2026-07-14

- fix(web): 旧 `/protocol/console` 页面及登录壳仅保留无鉴权 307 兼容跳转，正式入口统一为 `/pallas/protocol`

## [4.0.29] - 2026-07-14

- feat(snowluma): 容器启动后自动轮询并点击 QQ 一键登录（`pallas_protocol_snowluma_auto_quick_login`，默认开）；已在跑但未连上的账号也会补点
- feat(snowluma): 恢复登录时顺手勾选扫码页「自动登录」，便于容器重启后 QQ 自行保持会话

## [4.0.28] - 2026-07-14

- fix(snowluma): WebUI 自动改密前先完成 EULA consent；否则 `change-password` 403，托管口令写不进 `accounts.json` / WebUI 不展示
- fix(snowluma): 一键登录点击 Y 比例 0.78→0.68，避免点到页脚链导致「已点击」但未真正登录
- feat(snowluma): Docker 注入 `SNOWLUMA_ACCEPT_EULA=1` / `SNOWLUMA_ACCEPT_PRIVACY=1`（上游 1.12.2 无人值守同意；旧镜像忽略）

## [4.0.27] - 2026-07-14

- fix(relogin): 临时会话发二维码失败时兜底提示加好友，避免 matcher 整次崩溃

## [4.0.26] - 2026-07-14

- refactor(web): 移除已迁入 Bot WebUI 的独立 HTML 页（`pages_pkg/`）与壳层静态 CSS，仅保留登录页字体片段与 favicon

## [4.0.25] - 2026-07-14

- fix(relogin): 群临时会话回复走 `send_private_msg` + `group_id`，SnowLuma 非好友可收文字/二维码
- feat(snowluma): WebUI API 客户端（改密、inject consent、进程轮询）与配置 Docker 同步
- fix(web): 协议独立 HTML 页重定向至 Bot WebUI `/pallas/protocol` 系列路由；偏好设置跳转 `/pallas/preferences`

## [4.0.24] - 2026-07-14

- chore(release): 版本号对齐（与 4.0.23 变更一并合入 4.0.25 发布）

## [4.0.23] - 2026-07-14

- fix(relogin): 容器重启后 QR 窗未就绪时继续轮询截屏/一键登录，不再立即报「恢复登录失败」
- fix(snowluma): refresh 失败时走 `wait_and_capture_snowluma_qrcode` 而非直接抛错
- fix(snowluma): bootstrap 口令按日志文件日期从新到旧解析，避免容器重建后误用旧口令

## [4.0.22] - 2026-07-13

- fix(snowluma): inject / 重新上号前自动完成 WebUI 首次改密（`mustChangePassword`），避免 `/api/processes` 403
- fix(snowluma): 托管改密后写入 `snowluma_managed_webui_password`，后续优先复用

## [4.0.21] - 2026-07-13

- feat(relogin): SnowLuma 一键登录分支——重新上号时自动点「登录」并等待牛牛连上，成功回复「牛牛已重新上线」
- feat(protocol): `wait_account_bot_connected` / `wait_account_process_running` / `is_bot_connected` 供上号流程轮询
- feat(snowluma): 一键登录与 inject consent、Docker wsClients 同步（4.0.20 起累积，本版一并发布）

## [4.0.20] - 2026-07-13

- fix(snowluma): libzbar 检测改为实测 decode（`zbar_version` API 已不可用导致误报）
- fix(snowluma): 截取 QQ 登录窗而非整屏；关闭 xmessage；xdotool 点击刷新后再识别
- fix(snowluma): 仅保存可解码的 txz.qq.com 二维码，废弃整屏「已过期」回退
- fix(web): 协议页主题与主 WebUI `consolePrefs` 同步；首屏防闪
- fix(web): `account_qrcode_meta` 返回 `host_deps` 状态
- fix(web): 二维码「刷新」调用 `POST /qrcode/refresh`（NapCat RefreshQRcode / SnowLuma 重截屏）
- deps: 显式声明 `httpx`、`pydantic`；`nonebot2[fastapi]` 替代裸 `nonebot2`

## [4.0.19] - 2026-07-13

- feat(snowluma): Docker 容器支持 `memory` / `memory-swap` 限额（默认 1g / 1536m），降低多开 OOM 风险
- feat(snowluma): 账号健康态 `login_required` / `health_status` / `operational_warnings`（容器运行但未连牛牛 → 待扫码）
- feat(snowluma): Hub 启动时审计 QR 截屏宿主机依赖（ImageMagick、libzbar、pillow、pyzbar）；`GET /api/snowluma/host-deps`
- feat(migrate): `POST /api/accounts/migrate-to-snowluma` 与 CLI `tools/migrate_napcat_to_snowluma.py`（默认不保留 NapCat 数据，可选 rolling 启动）
- feat(web): 协议仪表盘 KPI/状态胶囊展示「待扫码」
- fix(migrate): `preserve_napcat_data=True` 时保留既有 `account_data_dir`

## [4.0.18] - 2026-07-13

- feat(snowluma): SnowLuma Docker 无头截屏识别 QQ 登录二维码，写入 `cache/qrcode.png`，供「牛牛重新上号」与协议页 `/qrcode` 复用
- feat(snowluma): 截屏回退 `xwd` + 宿主机 ImageMagick `convert`（SnowLuma 镜像无 import/scrot）
- fix(snowluma): 容器文件读取改用 `docker exec cat`（`docker cp -` 输出 tar 导致 XWD/PNG 损坏）
- deps: 新增 `pillow`、`pyzbar`（宿主机需安装 `libzbar0`；XWD 回退需 `imagemagick`）

## [4.0.17] - 2026-06-30
- refactor(metadata): `help_audience` 由 `maintainer` 改为 `superuser`（项目无独立维护者权限等级，统一到超管）

## [4.0.16] - 2026-06-27
- docs(readme): 命令权限默认等级改用中文展示

## [4.0.15] - 2026-06-27

- style(web): 从 WebUI 同步 `console-shared.css`（窄屏与 AI 观测 Hub 样式）

## [4.0.14] - 2026-06-27
- docs(readme): 「怎么使用」口令统一加行内代码标记

## [4.0.13] - 2026-06-27

- fix(web): 补 `web/contract.py`，修复 pages_pkg 引用 `pallas_plugin_protocol.web.contract` 导致插件 import 失败

## [4.0.12] - 2026-06-27

- style(web): 内置 WebUI 与主控制台全面对齐——CSS 三层同步（console-shared + shell-protocol）、Poppins、data-summary-card / data-conn-capsule、StatCard KPI、SVG 侧栏图标
- feat(web): WebUI 引导横幅支持「不再显示」并 localStorage 记忆
- refactor(web): `pages.py` 拆分为 `pages_pkg/`（shell / dashboard / forms / account）

## [4.0.11] - 2026-06-27

- feat(batch): 新增 `POST /api/accounts/batch` rolling/限流批量启停重启，SSE 进度流；配置项 `pallas_protocol_restart_max_concurrency` / `pallas_protocol_restart_stagger_s`
- fix(web): 内置协议页批量操作改为 batch API，默认 rolling 降低峰值负载
- refactor(batch): 批量编排逻辑抽到 `account_batch_ops`；Bot 启动/停止全部启用账号改走 rolling batch
- feat(web): 内置仪表盘增加「推荐使用 Bot WebUI」引导横幅

## [4.0.10] - 2026-06-27

- fix(config): 改为写入 `config/plugins.json` 禁用 NapCat 内置插件 `napcat-plugin-builtin`，真正阻止 `#napcat` 本地命令响应（4.0.9 写入的 `enableLocalCommand` 字段 NapCat 不识别，无效）

## [4.0.9] - 2026-06-27

- fix(config): 同步 NapCat onebot11.json 时默认写入 `enableLocalCommand: false`，避免触发 `#napcat` 等本地命令（注：该字段 NapCat 不识别，已由 4.0.10 修正）

## [4.0.8] - 2026-06-25
- feat(metadata): 补充重新上号命令冷却声明

## [4.0.7] - 2026-06-24
- feat(knowledge): 声明 knowledge_sources FAQ 供 LLM 注入

## [4.0.6] - 2026-06-19
- docs: 改用 PyPI 版本徽章
- chore(assets): 替换品牌头像为透明背景版本

## [4.0.5] - 2026-06-18
- docs(readme): 统一官方插件卡片模板

## [4.0.4] - 2026-06-18
- docs(readme): 更新官方扩展安装命令

## [4.0.3] - 2026-06-18
- migrate: src.* → pallas.api.* / pallas.product.* / pallas.core.*
- release: bump to 4.0.3 for pallas import migration

## [4.0.2] - 2026-06-18
- docs(readme): 添加 Pallas-Bot hero 图
- chore(release): 4.0.2 同步 README 进 PyPI 包

## [4.0.1] - 2026-06-17
- feat: Pallas-Bot 4.0 官方扩展首包
- fix(build): 修正 hatch wheel 的 src 包路径
- feat(release): PyPI 发版 workflow 与 4.0.1
