#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
realtime_a_share_price.py
=========================

A 股盘中实时价格 / 准实时 5 日均价小工具(独立可运行,无需 akshare/tushare/vnpy)。

原理:
- 直接请求东方财富的公开行情接口(akshare 的 `stock_bid_ask_em` /
  `stock_zh_a_hist` 走的就是同一套):
    * https://push2.eastmoney.com/api/qt/stock/get          —— 单票实时盘口
    * https://push2his.eastmoney.com/api/qt/stock/kline/get —— 历史日 K
- 取最近 4 个交易日的收盘价,与当前最新成交价一起做 5 日 SMA,
  得到"截至此刻"的盘中 MA5(行情数据本身是分钟级聚合,可视为准实时)。

支持的标的格式(大小写均可):
    600519              自动判定为沪市 -> 1.600519
    000001              自动判定为深市 -> 0.000001
    sh600519 / SH600519 显式指定沪市
    sz000001            显式指定深市
    bj430047            显式指定北交所

依赖:
    pip install requests

用法:
    # 持续每 3 秒刷新一次 600519(贵州茅台)
    python realtime_a_share_price.py 600519

    # 自定义刷新间隔(秒)
    python realtime_a_share_price.py sz000001 --interval 5

    # 只查一次,不进入循环
    python realtime_a_share_price.py 600519 --once

    # 同时盯多只股票
    python realtime_a_share_price.py 600519 000001 300750 --interval 2

注意:
- 该接口仅返回最近的盘口快照,真正"逐笔 tick 级"实时仍需走券商 L1/L2 行情(vnpy gateway)。
- 行情快照在交易时段(09:30-11:30, 13:00-15:00,北京时间)才会持续变化。
- 接口稳定性受东方财富侧限频影响,建议 interval >= 1 秒。
"""

from __future__ import annotations

import argparse
import datetime as dt
import signal
import sys
import time
from dataclasses import dataclass
from typing import Iterable

import requests


EM_QUOTE_URL = "https://push2.eastmoney.com/api/qt/stock/get"
EM_KLINE_URL = "https://push2his.eastmoney.com/api/qt/stock/kline/get"

QUOTE_FIELDS = (
    "f43,f44,f45,f46,f47,f48,f49,f50,f51,f52,f57,f58,f60,f71,"
    "f161,f168,f169,f170,f292"
)

KLINE_FIELDS1 = "f1,f2,f3,f4,f5,f6"
KLINE_FIELDS2 = "f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61,f116"

DEFAULT_TIMEOUT = 6
DEFAULT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    ),
    "Referer": "https://quote.eastmoney.com/",
}


# ---------------------------------------------------------------------------
# Symbol parsing
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Symbol:
    code: str   # 6位数字代码,例如 "600519"
    market: int  # 1=沪市/上证指数, 0=深市/北交所/创业板
    raw: str

    @property
    def secid(self) -> str:
        return f"{self.market}.{self.code}"

    @property
    def display(self) -> str:
        prefix = {1: "SH", 0: "SZ"}.get(self.market, "??")
        return f"{prefix}{self.code}"


def parse_symbol(text: str) -> Symbol:
    """把用户传入的字符串解析成 Eastmoney 的 secid。"""
    s = text.strip().lower()
    raw = text.strip()

    if s.startswith(("sh", "sz", "bj")):
        prefix, code = s[:2], s[2:]
        if not code.isdigit() or len(code) != 6:
            raise ValueError(f"非法股票代码: {text!r}")
        market = 1 if prefix == "sh" else 0
        return Symbol(code=code, market=market, raw=raw)

    if not s.isdigit() or len(s) != 6:
        raise ValueError(
            f"非法股票代码: {text!r}; 期望 6 位数字,可加 sh/sz/bj 前缀"
        )

    first = s[0]
    if first in ("5", "6", "9"):
        market = 1
    elif first in ("0", "2", "3"):
        market = 0
    elif first in ("4", "8"):
        market = 0
    else:
        market = 0

    return Symbol(code=s, market=market, raw=raw)


# ---------------------------------------------------------------------------
# Eastmoney API calls
# ---------------------------------------------------------------------------

def fetch_quote(session: requests.Session, sym: Symbol) -> dict:
    """拉取单票实时盘口快照。"""
    params = {
        "fltt": "2",
        "invt": "2",
        "fields": QUOTE_FIELDS,
        "secid": sym.secid,
    }
    r = session.get(
        EM_QUOTE_URL, params=params, headers=DEFAULT_HEADERS, timeout=DEFAULT_TIMEOUT
    )
    r.raise_for_status()
    payload = r.json()
    data = payload.get("data")
    if not data:
        raise RuntimeError(f"未取到 {sym.display} 的实时数据,接口返回: {payload}")
    return data


def fetch_recent_closes(
    session: requests.Session, sym: Symbol, n: int = 4
) -> list[float]:
    """取最近 n 个交易日的前复权收盘价,用于盘中 MA5 计算。"""
    today = dt.date.today()
    beg = (today - dt.timedelta(days=max(n * 4, 30))).strftime("%Y%m%d")
    end = today.strftime("%Y%m%d")
    params = {
        "fields1": KLINE_FIELDS1,
        "fields2": KLINE_FIELDS2,
        "ut": "7eea3edcaed734bea9cbfc24409ed989",
        "klt": "101",
        "fqt": "1",
        "secid": sym.secid,
        "beg": beg,
        "end": end,
    }
    r = session.get(
        EM_KLINE_URL, params=params, headers=DEFAULT_HEADERS, timeout=DEFAULT_TIMEOUT
    )
    r.raise_for_status()
    payload = r.json()
    data = payload.get("data") or {}
    klines = data.get("klines") or []
    if not klines:
        return []

    closes_with_dates: list[tuple[str, float]] = []
    for line in klines:
        parts = line.split(",")
        date_str = parts[0]
        close_str = parts[2]
        try:
            closes_with_dates.append((date_str, float(close_str)))
        except ValueError:
            continue

    today_str = today.strftime("%Y-%m-%d")
    filtered = [c for d, c in closes_with_dates if d != today_str]
    return filtered[-n:]


# ---------------------------------------------------------------------------
# Formatting
# ---------------------------------------------------------------------------

def _ansi(text: str, color: str) -> str:
    codes = {"red": "31", "green": "32", "yellow": "33", "dim": "2"}
    code = codes.get(color, "0")
    if not sys.stdout.isatty():
        return text
    return f"\x1b[{code}m{text}\x1b[0m"


def color_by_change(value: float, text: str) -> str:
    if value > 0:
        return _ansi(text, "red")
    if value < 0:
        return _ansi(text, "green")
    return text


def format_row(sym: Symbol, quote: dict, ma5: float | None) -> str:
    name = quote.get("f58", "?")
    last = quote.get("f43")
    open_ = quote.get("f46")
    high = quote.get("f44")
    low = quote.get("f45")
    pre_close = quote.get("f60")
    change = quote.get("f169")
    pct = quote.get("f170")
    volume_hand = quote.get("f47")
    amount = quote.get("f48")
    avg_price = quote.get("f71")
    turnover = quote.get("f168")

    now = dt.datetime.now().strftime("%H:%M:%S")

    def fmt_num(v, digits=2):
        if v in (None, "-", ""):
            return "  - "
        try:
            return f"{float(v):,.{digits}f}"
        except (TypeError, ValueError):
            return str(v)

    def fmt_amount(v):
        if v in (None, "-", ""):
            return "  - "
        try:
            v = float(v)
        except (TypeError, ValueError):
            return str(v)
        if v >= 1e8:
            return f"{v / 1e8:,.2f}亿"
        if v >= 1e4:
            return f"{v / 1e4:,.2f}万"
        return f"{v:,.0f}"

    try:
        pct_val = float(pct) if pct not in (None, "-", "") else 0.0
    except (TypeError, ValueError):
        pct_val = 0.0

    change_str = color_by_change(pct_val, f"{fmt_num(change)} ({fmt_num(pct)}%)")
    last_str = color_by_change(pct_val, fmt_num(last))

    ma5_str = "  - "
    if ma5 is not None:
        try:
            last_f = float(last)
            ma5_str = fmt_num(ma5)
            if last_f > ma5:
                ma5_str = _ansi(ma5_str, "yellow") + _ansi(" ↑", "red")
            elif last_f < ma5:
                ma5_str = _ansi(ma5_str, "yellow") + _ansi(" ↓", "green")
            else:
                ma5_str = _ansi(ma5_str, "yellow") + " ="
        except (TypeError, ValueError):
            ma5_str = fmt_num(ma5)

    return (
        f"[{now}] {sym.display} {name}"
        f"  最新={last_str}  涨跌={change_str}"
        f"  开={fmt_num(open_)} 高={fmt_num(high)} 低={fmt_num(low)} 昨收={fmt_num(pre_close)}"
        f"  均价={fmt_num(avg_price)}  MA5={ma5_str}"
        f"  量={fmt_amount((float(volume_hand) * 100) if volume_hand not in (None, '-', '') else None)}"
        f"  额={fmt_amount(amount)}  换手={fmt_num(turnover)}%"
    )


# ---------------------------------------------------------------------------
# Main loop
# ---------------------------------------------------------------------------

class GracefulExit(Exception):
    pass


def _install_signal_handlers() -> None:
    def handler(signum, frame):
        raise GracefulExit()

    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            signal.signal(sig, handler)
        except (ValueError, OSError):
            pass


def run(
    symbols: Iterable[Symbol],
    interval: float,
    once: bool,
    with_ma5: bool,
) -> int:
    session = requests.Session()

    closes_cache: dict[str, list[float]] = {}
    if with_ma5:
        for sym in symbols:
            try:
                closes_cache[sym.secid] = fetch_recent_closes(session, sym, n=4)
            except Exception as exc:
                print(
                    _ansi(
                        f"[warn] 获取 {sym.display} 历史收盘失败: {exc}", "dim"
                    ),
                    file=sys.stderr,
                )
                closes_cache[sym.secid] = []

    last_refresh_date = dt.date.today()

    while True:
        if with_ma5 and dt.date.today() != last_refresh_date:
            for sym in symbols:
                try:
                    closes_cache[sym.secid] = fetch_recent_closes(session, sym, n=4)
                except Exception:
                    pass
            last_refresh_date = dt.date.today()

        for sym in symbols:
            try:
                quote = fetch_quote(session, sym)
            except Exception as exc:
                print(
                    _ansi(f"[err]  {sym.display} 拉取失败: {exc}", "dim"),
                    file=sys.stderr,
                )
                continue

            ma5: float | None = None
            if with_ma5:
                prev_closes = closes_cache.get(sym.secid, [])
                last = quote.get("f43")
                if prev_closes and last not in (None, "-", ""):
                    try:
                        last_f = float(last)
                        ma5 = (sum(prev_closes) + last_f) / 5.0
                    except (TypeError, ValueError):
                        ma5 = None

            print(format_row(sym, quote, ma5), flush=True)

        if once:
            return 0
        try:
            time.sleep(interval)
        except GracefulExit:
            return 0


def build_arg_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="A 股盘中实时价 + 准实时 MA5(数据源: 东方财富 push2 接口)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    p.add_argument("symbols", nargs="+", help="一只或多只股票代码,例如 600519 sz000001")
    p.add_argument(
        "-i", "--interval", type=float, default=3.0, help="刷新间隔秒数,默认 3"
    )
    p.add_argument("--once", action="store_true", help="只查询一次后退出")
    p.add_argument(
        "--no-ma5", action="store_true", help="不计算盘中 MA5,只看实时价"
    )
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_arg_parser().parse_args(argv)

    try:
        syms = [parse_symbol(s) for s in args.symbols]
    except ValueError as exc:
        print(f"参数错误: {exc}", file=sys.stderr)
        return 2

    _install_signal_handlers()

    try:
        return run(
            symbols=syms,
            interval=max(0.5, args.interval),
            once=args.once,
            with_ma5=not args.no_ma5,
        )
    except GracefulExit:
        print("\n[exit] 已停止。", file=sys.stderr)
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
