<div align="center">

<img src="./logo.png" width="96" height="96" alt="SquadLens Logo" />

# SquadLens (`astrbot_plugin_pubg`)

**基于 AstrBot 的 QQ 群聊 PUBG 开黑小队战绩管家 · 可选移动优先 Web 战绩看板与 2D / 3D 战术回放引擎**

![Python](https://img.shields.io/badge/Python-3.10%2B-3776AB?logo=python&logoColor=white)
![AstrBot](https://img.shields.io/badge/AstrBot-%3E%3D4.10.4-F5C518)
![Web](https://img.shields.io/badge/Frontend-Zero--Build%20HTML5%20%2B%20Three.js-2EA8FF)
![Version](https://img.shields.io/badge/Version-v0.5.6-3CD070)

</div>

---

## 核心特性

### 🤖 QQ 群聊战绩管家 & AI 复盘
- **固定四人小队绑定**：在 AstrBot WebUI 可视化配置 4 位好友的游戏 ID 与平台，自动持久化缓存 `account_id`，减少重复查号损耗；对局内自动识别 1~4 号位与游戏内官方配色（黄 / 橙 / 蓝 / 绿）。
- **指令查战绩 & 自动播报**：支持 `/pubg last`、`/pubg recent`、`/pubg player <IGN>` 等指令；可选开启后台低频轮询，打完新对局后自动向已绑定群聊推送战报、高光称号（全场伤害王、成盒最快奖、友伤提醒）与回放直达链接。
- **大模型自然语言复盘（LLM Tool）**：内置 `pubg_get_recent_matches` 与 `pubg_get_match_detail` 工具，在群内直接 `@机器人` 提问（如“昨晚哪把吃鸡了？谁打的伤害最低？”）即可结合真实数据锐评复盘。

### 📊 Mobile-First Web 战绩看板（可选开关）
- **一键启停与访问保护**：通过 `enable_web` 开关控制内嵌轻量 `aiohttp` 服务，默认仅监听本机 `127.0.0.1:8001`（端口可自由修改），关闭或重载插件时自动释放端口；支持可选访问口令（`web_access_code`）。
- **小队战绩 KPI 与快捷筛选**：自动汇总近期吃鸡数/胜率、前五率、场均排名、场均击杀/伤害与近期伤害王；支持按吃鸡 / 前五 / 已缓存 / 地图快速筛选与最近 10 / 20 / 30 场切换。
- **离线下载与缓存管理**：
  - 支持一键导出当前战绩 **Excel 兼容 CSV 表格**、**战绩摘要 JSON**、**单局回放 JSON**，以及 **打包下载全部已缓存回放（`.zip`）**，防止官方 14 天后历史回放过期。
  - 支持在网页端实时查看缓存占用并分层清理服务端回放、对局缓存或浏览器本地缓存（清理对局缓存时自动保留小队绑定与机器人播报水位线）。

### 🗺️ 2D & 3D 战术回放引擎
- **2K 秒开 + 4K 渐进超清底图**：内置 9 张主流 PUBG 2K 超清 WebP 底图与艾伦格地形图（支持「默认 / 地形图」一键切换）；艾伦格默认地图支持 2K 首帧秒开 + 4K 后台渐进超清加载，服务端自动完成 **AVIF / WebP 内容协商**、**Gzip 压缩** 与 **ETag 304** 缓存优化。
- **艾伦格 3D 战术回放（控制台可关闭）**：
  - 通过控制台 `enable_3d_replay` 开关一键启停；内置离线 Three.js ES 模块（零外网 CDN）、`2048×2048` 高精度地形高程与立体建筑白模（支持顶栏一键隐藏/显示建筑白模，也支持外挂自定义 `buildings.glb`）。
  - 地图级自由漫游（鼠标左键 / 手机单指 / WASD 平移、右键 / 双指旋转俯仰、滚轮指向缩放）+ 2D/3D 三段式自适应图标缩放（LOD）+ 纤细暗描边贴地虚线轨迹。
- **电竞级淘汰日志 & 玩家详情**：
  - 固定等宽武器中轴对称对齐，点击任意记录自动跳转至交火前 3 秒并高亮交火连线；点 `▾` 可展开双方头甲包等级、武器中文配件、受击部位人形示意图与助攻明细。
  - 点击任意玩家名可查看其本局装备、武器战绩与个人交战记录；支持在「队伍追踪」按 **最终排名** 或 **队伍编号** 排序并自由追踪任意单人或整队路线。

---

## 快速安装

1. **导入插件**
   - 在 AstrBot 管理后台 -> **插件管理** -> **上传本地插件压缩包**，导入 `astrbot_plugin_pubg.zip`；
   - 或将本仓库克隆至 `AstrBot/data/plugins/astrbot_plugin_pubg` 并重启/重载 AstrBot。
2. **申请官方 API Key**
   - 前往 [PUBG Developer Portal](https://developer.pubg.com/) 免费申请官方 API Key（免费版限流 10 次/分钟，插件已内置令牌桶限流与 429 自动退避）。
3. **填写配置**
   - 在 AstrBot WebUI 打开 **SquadLens** 插件配置页，填写 `pubg_api_key` 与 `squad_members` 中的小队成员游戏 ID；如需开启网页看板，打开 `enable_web` 并按需设置 `web_port`、`web_public_url` 与 `web_access_code`，保存并重载插件即可。

---

## QQ 群指令一览

| 指令 | 说明 |
| --- | --- |
| `/pubg help` | 查看指令帮助 |
| `/pubg last` | 查看绑定四人小队最新一局详细战报、高光与回放链接 |
| `/pubg recent [数量]` | 查看小队近期对局记录列表（默认 5 场） |
| `/pubg player <游戏ID> [平台]` | 临时查询任意 PUBG 玩家的近期对局 |
| `/pubg squad` | 查看当前绑定的四人小队名单与 ID 缓存状态 |
| `/pubg bind` | 绑定当前群聊接收对局结束自动播报 |
| `/pubg unbind` | 取消当前群聊的自动播报绑定 |
| `/pubg web` | 查看内嵌 Web 战绩看板与回放服务状态 |

---

## 配置项说明 (`_conf_schema.json`)

| 配置项 | 类型 | 默认值 | 说明 |
| --- | --- | --- | --- |
| `pubg_api_key` | string | `""` | PUBG 官方 Developer API Key（10 RPM 免费额度） |
| `default_platform` | string | `"steam"` | 默认查询平台（`steam`/`kakao`/`psn`/`xbox`/`stadia`） |
| `squad_members` | template_list | 4 个成员槽位 | 固定小队成员（`ign`、`alias`、`platform`、`color`） |
| `enable_auto_broadcast` | bool | `false` | 是否开启对局结束自动群播报 |
| `broadcast_targets` | list[string] | `[]` | 自动播报会话列表（推荐使用 `/pubg bind` 自动绑定） |
| `poll_interval_seconds` | int | `180` | 后台轮询间隔（秒），建议 `>= 120` |
| `enable_web` | bool | `false` | 是否启动内嵌 Web 战绩看板与战术回放服务 |
| `web_host` | string | `"127.0.0.1"` | 内嵌 Web 监听地址（局域网直连可设为 `0.0.0.0`） |
| `web_port` | int | `8001` | 内嵌 Web 监听端口，支持自由修改 |
| `web_public_url` | string | `""` | 外部访问基址（如 `https://pubg.example.com`），用于群战报拼接回放链接 |
| `web_access_code` | string | `""` | 可选访问口令，公网开放时建议设置 |
| `enable_3d_replay` | bool | `true` | 是否开启艾伦格 3D 战术回放模式；关闭后隐藏 3D 入口且不加载 3D 资源 |
| `recent_matches_limit` | int | `10` | 默认展示近期对局数量 |
| `max_cached_replays` | int | `200` | 本地最多保留的 `.replay.json.gz` 场次，超出自动 LRU 淘汰 |
| `telemetry_sample_interval_sec` | float | `1.5` | Telemetry 轨迹抽稀间隔（秒） |

---

## 开发与测试

```bash
pip install -r requirements.txt pytest pytest-asyncio
pytest tests -v
```

---

## 致谢

- [AstrBot](https://github.com/AstrBotDevs/AstrBot) — 强大的跨平台聊天机器人与 Agent 框架
- [PUBG Official Developer API](https://documentation.pubg.com/) & [pubg/api-assets](https://github.com/pubg/api-assets) — 官方战绩接口、Telemetry 数据与武器字典资源
