# AGENTS.md — usstock 项目说明

> 本文件面向 AI 编码代理，假设读者对本项目一无所知。
> 最后更新：2026-08-05（基于对项目目录的实际探查）。

## 项目现状（重要）

项目已实现为**美股概念板块实时行情看板**（2026-08-05 完成首版）：

- 后端：Python 3.12 + FastAPI + uvicorn，行情经 yfinance 会话批量调用 Yahoo Finance v7 quote 端点，带 20 秒 TTL 缓存
- 前端：无框架单页（`static/` 下 HTML + 原生 JS + CSS），深色 Yahoo 风格，红涨绿跌，由 FastAPI 静态托管
- 种子数据：`data/boards.json`，来自目录下同花顺截图的 16 个自选股分组
- 目录结构：

```
requirements.txt / .env.example / README.md
app/main.py            FastAPI 入口（API + 静态托管）
app/models.py          Quote / Board 数据模型
app/providers/base.py  QuoteProvider 抽象接口
app/providers/yahoo.py Yahoo 批量报价实现
app/providers/tickstream.py Yahoo websocket 全时段 tick 流
app/services/events.py  SSE 广播注册表（tick 推送浏览器）
app/services/boards.py 板块定义加载
app/services/quotes.py TTL 缓存 + 板块聚合
data/boards.json       16 个板块种子数据
static/                index.html / app.js / style.css
```

- 17 张原始截图已归档到 `screenshots/`（`微信图片_20260804*.jpg`），是种子数据的原始来源，保留作参考。

## 项目意图（推断）

目录名 `usstock`（美股）加上截图内容表明：本项目大概率是一个**美股自选股跟踪/行情工具**，而目录中的截图是用户在同花顺 App 中手工维护的自选股分组，应作为项目的**种子数据 / 需求参考**。

## 截图中的自选股分组数据

截图来自同花顺 App「自选」页，每行包含：股票名称、市场标签（US / OTC / AH / HK / UK）、代码、最新价、涨幅、盘后价、涨速。分组及成分股如下（价格为截图当日快照，不应作为实时数据使用）：

| 分组 | 成分股（代码） |
| --- | --- |
| 美股M8 | NVDA、GOOGL、AAPL、MSFT、AMZN、META、TSLA、SPCX |
| US光（光通信/光模块） | AAOI、POET、LITE、GLW、COHR |
| US设备（半导体设备） | TER、AMAT、AMKR、KLAC、LRCX、ASML |
| US存储 | SKHY、SIMO、RMBS、SNDK、WDC、STX、MU |
| US功率（功率半导体） | IFNNY(OTC)、IPWR、VICR、AOSL、POWI、MPWR、ADI、TXN、STM、NVTS、ON、WOLF |
| US减肥药 | ARWR、WST、AMGN、NVO、LLY |
| US卫星通信 | 865176(板块指数)、GILT、VSAT、IRDM、ECHO、TSAT、SATL |
| US石油 | PBR、OXY、COP、BP、CVX |
| us油运 | FRO、DHT、INSW、BWET |
| US商业航天 | 865195(板块指数)、SIDU、SPCE、KTOS、GE、LMT、RKLB、HWM、ASTS、DXYZ |
| US太阳能 | 865057(板块指数)、ENPH、CSIQ、JKS、RUN、FSLR、SEDG |
| US锂电池 | HK2465、HK9696、HK1772(AH)、ALB、LAR、SQM、SLI、LAC、SGML |
| USAI应用 | FTNT、PLTR、TEM、CRM、WDAY、APP、NET、SNOW、MDB、DDOG |
| US核聚变 | ETR、GHM、BWXT、SMR、LTBR、OKLO |
| US稳定币 | CRCL、WULF、MSTR、MARA、COIN、HK2562(HK)、HK1788(HK) |
| US量子科技 | CYRX、IONQ、LUNR、QUBT、LASR、KEYS(UK)、RGT、ARQQ、QBTS、AMPG |

2026-08-05 之后用户手动调整的分组（不来自截图）：

> 注：上表为截图当时的原始分组与命名；后续已统一改名为「US + 概念名」格式并调整成分股，以 `data/boards.json` 为准。

- **US 贵金属**（新增）：NEM、B、AEM、AU、WPM、PAAS、HL、CDE（2026-08-05 按近 3 个月日均成交额精简为前 8，剔除 AG、KGC、FNV、RGLD、EXK）。注意：MAG（MAG白银）2025 年被 PAAS 收购退市，用 EXK 替代后又在精简中被剔除；巴里克 2025 年更名 Barrick Mining，Yahoo 代码已从 GOLD 改为 **B**（GOLD 现在指向无关的 Gold.com）。
- **US 数据中心**（新增）：VRT、ETN、GEV、CEG、ANET、SMCI、DELL、PWR、FIX（AI 算力基建/电力配套）。
- **US 芯片**（新增）：NVDA、AVGO、MRVL、AMD、INTC、ARM、QCOM、TSM（芯片设计/制造龙头，NVDA 与「US M8」重叠属用户有意为之）。
- **US 光通讯**（原「US光」）：加入光芯片 Foundry TSEM（Tower 半导体）、GFS（格芯，硅光代工平台）；2026-08-07 加入 AXTI（AXT Inc，磷化铟/砷化镓衬底，光芯片上游材料）。
- **US 锂电池**：应用户要求移除了 3 只港股（2465.HK 龙蟠、9696.HK 天齐、1772.HK 赣锋），现仅保留美股标的：ALB、LAR、SQM、SLI、LAC、SGML。
- **US 稳定币**：2026-08-08 应用户要求移除 2 只港股（2562.HK、1788.HK 国泰君安国际），现仅保留美股标的：CRCL、WULF、MSTR、MARA、COIN。至此 boards.json 已无港股标的，.HK 映射/时段处理逻辑处于休眠状态。
- **US 商业航天**：新增 FLY（萤火虫航天，2025 年上市新股）；LUNR（Intuitive Machines，月球着陆器）从「US 量子科技」移入本板块。
- **US 新云厂商**（新增，Neocloud/GPU 云）：CRWV（CoreWeave）、NBIS（Nebius）、IREN、CORZ（Core Scientific）、APLD、WYFI（WhiteFiber）、CIFR（Cipher Digital）、HUT（Hut 8）。
- **US mRNA**（新增）：MRNA（莫德纳）、BNTX（BioNTech）、MRK（默沙东）、ALNY（Alnylam）。2026-08-19 Moderna与默沙东合作的个性化 mRNA 癌症疫苗 V940 获 III 期突破，开启肿瘤免疫新时代。精选 4 只高相关核心龙头（排除了纯大药企 PFE，CureVac 已于 2026-01 被 BNTX 收购退市）。

注意事项：

- 截图顶部标签栏还出现过「宽基」「US etf」「US无人驾驶」「昨日涨停」「首板」等分组，但没有对应的成分股截图；`US无人驾驶` 的截图（`screenshots/微信图片_20260804084907.jpg`）实际显示的是减肥药列表（切换瞬间截屏），该分组成分股**未知**，不要臆造。
- 个别代码并非标准美股代码（如 SPCX、SKHY、865xxx 板块指数、HK 前缀港股），是同花顺内部表示，建模时需留意。
- 截图文件命名含中文，处理文件路径时注意编码。

## 语言与沟通约定

- 项目素材和用户语境均为**中文**，面向用户的回复使用中文；代码标识符遵循最终选定技术栈的惯例。
- 分组命名已按用户要求统一为「US + 概念名」格式（大写 US、后空一格，如「US 光通讯」「US AI应用」；「美股M8」改为「US M8」）。新增分组沿用此格式。
- 「US 光通讯」（原名「US光」）已加入光芯片 Foundry：TSEM（Tower 半导体）、GFS（格芯，硅光平台）。

## 构建 / 测试 / 部署

- 安装依赖：`pip install -r requirements.txt`（fastapi、uvicorn、yfinance、pillow）
- 运行：`uvicorn app.main:app --port 8000` 或 `python -m app.main`，浏览器访问 `http://127.0.0.1:8000`；开发改后端代码用 `uvicorn app.main:app --port 8000 --reload`（保存 .py 自动重载，无需手动重启；`run.command` 启动脚本已默认带 `--reload`；前端 static/ 改动只需刷新浏览器，本就无需重启）
- 配置：环境变量见 `.env.example`（PORT / HOST / CACHE_TTL / FRONTEND_REFRESH_OPEN / FRONTEND_REFRESH_CLOSED）
- 测试：暂无自动化测试；验证方式为启动服务后 curl `/api/summary`、`/api/boards/{id}`、`/api/all` 检查字段与数值
- 涨跌幅口径：`/api/summary`、`/api/all`、`/api/export.png` 支持 `?metric=regular|current`，默认 `regular`（盘中涨跌幅；current 为当前时段口径：PRE 用盘前/POST·CLOSED 用盘后/PREPRE 用夜盘，缺数据回落盘中）。注意 PREPRE（20:00–04:00 ET 隔夜时段，UI 显示「夜盘」）的 post 通道此时段优先承载 **Yahoo websocket 实时推送**（见下条）；2026-08-11 起按用户要求改了兜底规则：本时段无 tick 的票回落 v7 快照里的盘后终值（`Quote.post_session` 标记 `"night"`/`"post"`，前端明细表加 `*`+脚注、大字版/导出图标注「盘后」而非「夜盘」，不再置空显示 `--`），最新口径即实时夜盘涨跌幅（基准=盘中收盘价，与同花顺/Yahoo 网页版一致）；`app/models.py` 的 `current_change_percent`、导出图 `_session_note`、前端 `sessionNote`/`currentPeriod`/`postLabel` 保持一致，PREPRE 时标签按 `post_session` 显示「夜盘」或「盘后」。口径影响板块均值、涨/跌/平家数、领涨领跌、板块排序与渐进着色、大字版/导出图股票行排序（缺该口径数据排末尾）；个股行展示字段不变。响应带 `"metric"` 回显；前端「⚙ 设置」浮层切换（localStorage `metric` 记忆；浮层在 ≤720px 窄屏下左对齐防顶出屏外），metric=current 时标准看板卡片与大字版板块头的涨跌幅前有时段小标签（`metricTag()`：盘前/盘中/盘后/夜盘；CLOSED 标「盘后」，因此时 current 口径实际取盘后值），导出图头部有「口径：盘中涨跌幅/当前时段」小字标注（与 stale 提示同行错开），metric=current 时导出图板块头均幅前同样有时段小标签（`METRIC_SESSION_LABEL`，与前端 `metricTag()` 一致）
- 导出长图：`GET /api/export.png?metric=…&theme=dark|light` 用 Pillow（`app/services/export.py`）服务端渲染全板块行情长图（900px 宽竖图——2026-08-08 由 1200 改 900，手机微信内等效放大 1.33 倍，长公司名更易触发省略号截断；红涨绿跌，attachment 下载，复用 quotes 缓存）。2026-08-08 起采用用户选定的 **K 版式（Bento 摘要风）**：标题区（48px 标题+状态 pill+生成/数据时间+右侧「口径：」标注+stale 提示）→ 2×2 Bento 磁贴（上涨/下跌板块数、领涨/领跌板块名+均幅，100px 高）→ 板块卡片（圆角 14，板块头=**左侧 5px 彩色状态条**（涨红跌绿无数据灰）+名称 34 bold+涨跌平 20 dim+均幅 38 bold 右对齐，不再有 tint 底色带）→ 成分股行 88px 两行（第一行名称 26 bold+代码 20 dim+最新价 26+**盘中涨跌幅** 30 bold，任何时段固定显示盘中涨幅；第二行入选理由 20 dim+盘前/盘后/夜盘标注 20 右对齐于盘中涨幅正下方——PRE 显示「盘前 +x%」、POST/POSTPOST/CLOSED 显示「盘后 +x%」、PREPRE 显示「夜盘 +x%」、REGULAR 不显示）。主标题 52px（字号 2026-08-08 整体放大回调，与旧大字版观感一致）。`PALETTES` 双调色板：dark（BG #12161d/PANEL #191e27/UP #f23645/DOWN #0ecb81）/ light（BG #eef1f5/PANEL #ffffff/UP #e0263a/DOWN #0a9e66/ACCENT #2f6fe4），theme 参数白名单校验。中文字体按 msyh → simhei → Noto Sans CJK → 文泉驿回退查找，全缺失时日志告警；前端顶部状态栏有「导出长图」按钮（加载期间置灰），导出自动带当前页面的 metric+theme
- 主题切换：前端「⚙ 设置」浮层新增主题 radio（深色默认/浅色，localStorage `theme` 记忆，`index.html` head 内联脚本先置 `data-theme` 防闪烁）；`style.css` 全部颜色走 CSS 变量，`[data-theme="light"]` 覆盖一套浅色变量；切换后触发 refresh 重渲染（网格卡片 tint 颜色随主题变）；导出长图跟随当前主题
- 大字版模式：前端顶部「标准看板 / 大字版」tab 切换（localStorage 记忆，`?view=big|std` 可覆盖）；大字版与导出长图同为 K 版式：顶部 2×2 Bento 摘要磁贴（`.bento`：涨/跌板块数、领涨/领跌板块名+均幅）+ 各板块头左侧 5px 彩色状态条（`--head-accent` CSS 变量），股票两行卡片排版不变；数据走 `GET /api/all`（`quotes_service.all_details()`：一次返回各板块明细 + current_avg/涨/跌/平，按 current_avg 降序，复用 TTL 缓存），与标准看板共用同一套自动刷新
- 板块渐进底色：仅标准看板网格卡片沿用（CSS 变量 `--tint` 渐变，`tintFor()` 分档：涨红跌绿、无数据或 0 为中性灰，透明度按 |current_avg| 分档 <0.5%→0.07、<1.5%→0.14、<3%→0.22、<5%→0.32、≥5%→0.44，颜色随主题切换）；大字版板块头与导出长图板块头自 2026-08-08 K 版式起改为左侧彩色状态条（涨红/跌绿/无数据灰，不分档）
- 数据源：Yahoo Finance v7 quote（经 yfinance 的 `YfData` 会话，自动处理 cookie/crumb），一次批量请求全部代码；在中国大陆网络下实测可直连（2026-08-05 验证）
- 数据源与时段：Yahoo v7 批量为基础。2026-08-06 Yahoo v7 出现全天卡顿（开盘 40 分钟仍显示昨收、marketState 卡在 PRE）；2026-08-07 实测确认**新浪/腾讯的最新价只跟随盘中时段**（盘后显示的是盘中收盘价，不能用于盘前/盘后）。因此：**① 市场状态用美东时钟计算**（`us_market_state()`，zoneinfo America/New_York），不信任 Yahoo 的 marketState；**② 仅 REGULAR 时段用新浪覆盖盘中数据**（国内直连快、防 Yahoo 卡顿）；**③ 盘前/盘后数据一律走 Yahoo v7**，缺字段时（v7 故障期）用 quoteSummary price 模块切片慢补（每周期 ≤`FALLBACK_SLICE`(15) 只、缓存 300s，防 burst 限流墙；其涨跌幅是小数需 ×100）；v7 整批请求瞬时失败（如 curl 16 HTTP/2 抖动）时 2s 后重建 YfData 会话重试一次，仍败则上层回落上次缓存 + stale 标记；**④ 新浪未覆盖的（OTC 如 IFNNY）**各时段都走 quoteSummary；**⑤ 全时段 websocket tick 覆盖**（`app/providers/tickstream.py`，yfinance.WebSocket 后台常驻线程、断线自动重连）：Yahoo streamer 是成交流水线，盘前/盘中/盘后/夜盘有成交即推送（2026-08-07 实测），`_fill_from_stream()` 在每轮 HTTP 快照后按时段叠加最新 tick（PRE→pre_*、REGULAR→regular_*、POST→post_*、PREPRE→post_*；时间戳必须落在 `session_window()` 的当前时段窗口内；REGULAR 时段还要求 tick 比 v7 快照的 regularMarketTime 新，防旧 tick 回退价格）。**单连接订太多 symbol 会被 Yahoo 静默丢弃部分推送**（2026-08-09 两次实测：订 146 只时仅 ~90 只有 tick，NVDA/AXTI/GFS/COHR 等活跃票也丢，丢失集合随连接随机变化）——因此按 `WS_CHUNK`(30) 只分片到多条连接并行订阅（146 只 → 5 条），任一连接断开整体重建；且每次换交易时段（`_fill_from_stream` 检测到 state 变化）调用 `YahooTickStream.reconnect()` 强制重订，防长连接老化。诊断接口 `GET /api/stream-debug` 可看连接数/已订阅数/各 symbol tick 年龄。**夜盘（PREPRE）特殊：无任何 HTTP 快照源**（v7/v8/v10 接口均无夜盘字段，20:00 后冻结；Yahoo 网页版的 BOATS 夜盘价即来自此流）——夜盘 100% 靠 tick，2026-08-11 起无 tick 的票回落 v7 快照盘后终值（20:00 后冻结）并标注「盘后」（`post_session="post"`；曾按用户要求置空显示 `--`，后改为此兜底规则。曾测雪球接口做快照兜底，因其推送会中途冻结已移除；2026-08-10 复测仍未恢复——`quote.json` 有专属夜盘字段（`current_night_session`/`percent_night_session`，需先取 `xq_a_token` cookie 否则 400016），但夜盘时间戳冻结在上周五、盘后字段冻结 53h，市场状态显示「已收盘」，不可用作夜盘源。全网调研结论：**免费渠道中只有 Yahoo websocket 有实时夜盘**（Polygon/Finnhub/Twelve Data/Alpha Vantage/新浪/腾讯/东财的扩展时段全部止于 20:00 ET）；付费可选 Tiingo BOATS（~$39/月，REST+WS 自助开通）、长桥/富途/老虎 OpenAPI（需开户，长桥 `overnight_quote` 有 GitHub issue 报 None 需实测）、TickDB（国内直连付费商，trade_session=3 为夜盘）**周末规则**（Blue Ocean 周日晚～周五凌晨交易）：周五 20:00 起至周日 20:00 判为 CLOSED——周五晚/周六显示盘后终值（不再误判夜盘显示 `--`）；周日 20:00 起为当周第一个夜盘（PREPRE，tick 生效）。推送为成交驱动、无初始快照：冷门股显示的是本时段最后一笔真实成交价。涨幅基准=盘中收盘价，与同花顺 App 一致。港股（.HK）时段不同，不参与新浪/tick 覆盖，沿用 Yahoo；**⑥ tick 实时推送到浏览器（SSE）**：tick 到达 ws 线程后回调 `quotes._on_tick` 就地修补 TTL 缓存里的 Quote 对象（聚合基于同一批对象，前端重拉即得新值，不触发额外数据源请求），并经 `GET /api/events`（`app/services/events.py`，SSE，1.5s 节流 + 25s 心跳）推送「tick」信号；前端 `startEventStream()`（EventSource）收到后节流 1.5s 调用 `refresh()` 整页刷新；30s 轮询保留作快照校正与兜底（夜盘时段轮询只刷新盘中收盘价等静态字段，无法校正夜盘价）。周末 CLOSED 无 tick 窗口，不推不补
- 代码映射：港股 `HKxxxx` → `xxxx.HK`；同花顺板块指数 865176/865195/865057 已于 2026-08-05 从 boards.json 删除（不再有 unsupported 指数行）；SPCX（Space Exploration Technologies）、BWET（Breakwave Tanker Shipping ETF）在 Yahoo 有数据，未标记 unsupported
- 成分股字段：`data/boards.json` 每只股票除 symbol/name/market/unsupported 外还有 `reason`（一句精炼中文的入选理由），经 `Stock.reason` 透传到 `/api/boards/{id}`，前端明细表与导出长图均在名称/代码后展示「入选理由」列

## 安全注意事项

- 截图中不含敏感凭证，但属于用户个人投资偏好信息，不要对外上传。
- 未来若接入行情数据 API（如需要 API Key），密钥必须通过环境变量或本地配置文件注入，**不要硬编码进仓库**。免费 Key 的注册流程见 `docs/行情API-Key注册指南.md`（Finnhub 推荐；Twelve Data 免费档不含实时盘前盘后）。
- 行情数据涉及第三方服务条款，抓取或调用前确认许可。
