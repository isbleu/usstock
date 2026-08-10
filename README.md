# 美股概念板块实时行情看板

以同花顺自选股分组为种子数据的美股概念板块行情看板：Yahoo 风格深色单页界面，红涨绿跌，支持盘前 / 盘中 / 盘后三时段数据与板块聚合。

## 功能

- 一屏总览 16 个概念板块：按当前时段等权平均涨跌幅降序排列的卡片，含涨/跌/平家数、领涨股、三时段迷你均值
- 点击卡片展开个股明细：最新价、涨跌幅、盘前价/%、盘后价/%，当前时段列高亮，按涨跌幅排序
- 顶部状态栏：市场状态（盘前/盘中/盘后/休市）、美东时间、数据时间、刷新倒计时
- 服务端批量拉取 Yahoo Finance 报价 + 20 秒 TTL 缓存；整批失败时返回上次缓存并标记 stale
- 自动刷新：开盘相关时段 30 秒，休市 5 分钟
- 导出分享长图：顶部「导出长图」按钮下载全板块行情长图（服务端 Pillow 渲染，960px 宽深色竖图，红涨绿跌，含市场状态、生成/数据时间、各板块成分股表格，当前时段列高亮），适合微信分享

## 安装

```bash
pip install -r requirements.txt
```

## 运行

```bash
# 方式一
uvicorn app.main:app --port 8000

# 方式二
python -m app.main
```

然后浏览器打开 <http://127.0.0.1:8000>。

## 配置

复制 `.env.example` 并按需设置环境变量：

| 变量 | 默认 | 说明 |
| --- | --- | --- |
| `PORT` | 8000 | 服务端口 |
| `HOST` | 127.0.0.1 | 监听地址（服务器部署用 0.0.0.0） |
| `CACHE_TTL` | 20 | 服务端行情缓存秒数 |
| `FRONTEND_REFRESH_OPEN` | 30000 | 前端开盘时段轮询毫秒数 |
| `FRONTEND_REFRESH_CLOSED` | 300000 | 前端休市时段轮询毫秒数 |

## 目录结构

```
app/            FastAPI 后端（main.py 入口，providers/ 数据源，services/ 缓存与聚合）
data/boards.json 板块与成分股种子数据（含 unsupported 标注）
static/         前端单页（index.html / app.js / style.css）
```

## API

- `GET /api/summary` — 全部板块聚合（当前时段均值、涨跌家数、领涨领跌、三时段均值、marketState、时间戳、stale）
- `GET /api/boards/{board_id}` — 单板块个股明细（三时段价格与涨跌幅）
- `GET /api/config` — 前端轮询间隔配置
- `GET /api/export.png` — 导出全板块行情长图（attachment 下载，文件名 `usstock_YYYYMMDD_HHMM.png`）

## 导出长图说明

- 服务端用 Pillow 渲染（`app/services/export.py`），复用行情缓存，不额外拉取数据
- 中文字体按列表回退查找：微软雅黑 → 黑体 → Noto Sans CJK → 文泉驿（Windows/Linux/macOS 均可用）；全部缺失时日志告警且中文可能显示为方块
- 板块按当前时段平均涨跌幅降序，成分股按当前涨跌幅降序；unsupported / 无数据的行显示「数据不可用」，不计入统计

## 数据说明

- 数据源：Yahoo Finance（免费接口，无需 API Key），通过 `yfinance` 会话批量调用 v7 quote 端点
- 同花顺板块指数（865176/865195/865057）无对应 Yahoo 代码，标记 `unsupported`，不参与聚合
- 港股成分股使用 `xxxx.HK` 代码，其涨跌幅跟随港股时段，与美股盘前/盘后不同步属正常现象
