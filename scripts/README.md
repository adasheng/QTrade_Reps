# scripts/

仓库自带的独立小工具集合,不依赖仓库内其它项目代码,可直接用 `python xxx.py` 跑。

## `realtime_a_share_price.py`

A 股盘中实时价格 + 准实时 5 日均价小工具。

数据源沿用 akshare 调用东方财富的同一套公开接口:

- `https://push2.eastmoney.com/api/qt/stock/get` —— 单票实时盘口快照
- `https://push2his.eastmoney.com/api/qt/stock/kline/get` —— 日线 K 线

脚本本身只依赖 `requests`,不需要安装 akshare/tushare/vnpy。

### 安装

```bash
pip install requests
```

### 使用

```bash
# 每 3 秒刷新一次 贵州茅台
python scripts/realtime_a_share_price.py 600519

# 同时盯多只,2 秒刷新一次
python scripts/realtime_a_share_price.py 600519 000001 300750 -i 2

# 只查一次
python scripts/realtime_a_share_price.py sz000001 --once

# 不算 MA5,纯看实时
python scripts/realtime_a_share_price.py 600519 --no-ma5
```

### 输出示例(交易时段)

```
[10:32:14] SH600519 贵州茅台  最新=1538.20  涨跌=+8.20 (+0.54%)  开=1530.00 高=1542.50 低=1528.10 昨收=1530.00  均价=1535.40  MA5=1521.36 ↑  量=12.43万  额=19.12亿  换手=0.10%
```

颜色规则(终端 TTY 下生效):
- 红 = 涨,绿 = 跌(沪深市场习惯,和欧美相反)。
- MA5 后的 `↑` 表示当前价上穿/位于 MA5 之上,`↓` 表示位于 MA5 之下。

### 标的代码支持的格式

| 写法 | 解析 |
|---|---|
| `600519` | 自动判定为沪市 → `1.600519` |
| `000001` / `300750` | 自动判定为深市 → `0.xxxxxx` |
| `sh600519` / `SH600519` | 显式沪市 |
| `sz000001` | 显式深市 |
| `bj430047` | 显式北交所(走深市 secid=0) |

### 注意

- 这是 HTTP 轮询级别的"准实时"(分钟内多次刷新),不是真正的逐笔 tick;
  毫秒级 tick 请用 `vnpy-master/` 里的 gateway 体系(XTP/QMT/TTS 等)。
- 行情快照只在 A 股交易时段(09:30–11:30、13:00–15:00 北京时间)持续变化,
  非交易时段调用拿到的是上一交易日收盘数据。
- 东方财富接口对高频请求有限速,建议 `--interval >= 1`。
