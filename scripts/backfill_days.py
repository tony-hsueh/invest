"""
逐日補抓缺漏的歷史資料（用於補散落的交易日缺口，而非整月缺口）
抓取：OHLCV（MI_INDEX）、三大法人買賣超、融資融券、鉅額交易（盤後）
輸出：data/YYYY-MM-DD.json

用法：
  python3 scripts/backfill_days.py 2026-06-16 2026-07-29   # 補指定範圍內所有缺漏交易日
  python3 scripts/backfill_days.py 2026-06-16              # 只補單一天
"""
import sys
import json
import time
import datetime
import os
import requests

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"
}


def fetch(url: str) -> dict:
    resp = requests.get(url, headers=HEADERS, timeout=20)
    resp.raise_for_status()
    return resp.json()


def parse_number(s):
    try:
        return float(str(s).replace(",", "").replace("+", ""))
    except (ValueError, TypeError):
        return None


def parse_int(s):
    try:
        return int(str(s).replace(",", ""))
    except (ValueError, TypeError):
        return None


def parse_signed(direction_html: str, magnitude_str: str):
    """MI_INDEX 用一段帶顏色的 HTML 表示漲跌方向：red=漲(+)，green=跌(-)"""
    magnitude = parse_number(magnitude_str)
    if magnitude is None:
        return None
    if "-" in direction_html:
        return -magnitude
    if "+" in direction_html:
        return magnitude
    return 0.0


# ── 1. 每日收盤價（全上市，支援任意歷史日期）───────────────────────────────
def fetch_prices(date_str: str) -> dict:
    """回傳 {stock_id: {name, open, high, low, close, volume, amount, change, transactions}}
    STOCK_DAY_ALL 只能抓當天資料，改用 MI_INDEX(type=ALLBUT0999) 抓歷史資料。
    """
    url = f"https://www.twse.com.tw/exchangeReport/MI_INDEX?response=json&date={date_str}&type=ALLBUT0999"
    data = fetch(url)
    if data.get("stat") != "OK":
        return {}
    tables = data.get("tables", [])
    price_table = None
    for t in tables:
        if (t.get("title") or "").find("每日收盤行情") != -1:
            price_table = t
            break
    if price_table is None:
        return {}
    # fields: 證券代號,證券名稱,成交股數,成交筆數,成交金額,開盤價,最高價,最低價,收盤價,漲跌(+/-),漲跌價差,...
    result = {}
    for row in price_table.get("data", []):
        if len(row) < 11:
            continue
        stock_id = row[0].strip()
        result[stock_id] = {
            "name":         row[1].strip(),
            "volume":       parse_int(row[2]),               # 成交股數
            "amount":       parse_int(row[4]),                # 成交金額
            "open":         parse_number(row[5]),              # 開盤價
            "high":         parse_number(row[6]),              # 最高價
            "low":          parse_number(row[7]),              # 最低價
            "close":        parse_number(row[8]),              # 收盤價
            "change":       parse_signed(row[9], row[10]),      # 漲跌價差（帶正負號）
            "transactions": parse_int(row[3]),                 # 成交筆數
        }
    return result


# ── 2. 三大法人買賣超 ────────────────────────────────────────────────────
def fetch_institutional(date_str: str) -> dict:
    url = f"https://www.twse.com.tw/fund/T86?response=json&date={date_str}&selectType=ALL"
    data = fetch(url)
    result = {}
    for row in data.get("data", []):
        if len(row) < 12:
            continue
        stock_id = row[0].strip()
        result[stock_id] = {
            "foreign_buy":  parse_int(row[2]),
            "foreign_sell": parse_int(row[3]),
            "foreign_net":  parse_int(row[4]),
            "trust_buy":    parse_int(row[8]),
            "trust_sell":   parse_int(row[9]),
            "trust_net":    parse_int(row[10]),
            "dealer_net":   parse_int(row[11]),
            "total_net":    parse_int(row[18]) if len(row) > 18 else None,
        }
    return result


# ── 3. 融資融券 ──────────────────────────────────────────────────────────
def fetch_margin(date_str: str) -> dict:
    url = f"https://www.twse.com.tw/exchangeReport/MI_MARGN?response=json&date={date_str}&selectType=ALL"
    data = fetch(url)
    tables = data.get("tables", [])
    if len(tables) < 2:
        return {}
    result = {}
    for row in tables[1].get("data", []):
        stock_id = row[0].strip()
        result[stock_id] = {
            "margin_buy":     parse_int(row[2]),
            "margin_sell":    parse_int(row[3]),
            "margin_balance": parse_int(row[6]),
            "short_buy":      parse_int(row[8]),
            "short_sell":     parse_int(row[9]),
            "short_balance":  parse_int(row[12]),
        }
    return result


# ── 4. 鉅額交易（盤後）──────────────────────────────────────────────────
def fetch_block_trades(date_str: str) -> list:
    url = f"https://www.twse.com.tw/block/BFIAUU?response=json&date={date_str}"
    data = fetch(url)
    result = []
    for row in data.get("data", []):
        result.append({
            "stock_id":   row[0].strip(),
            "name":       row[1].strip(),
            "trade_type": row[2].strip(),
            "price":      parse_number(row[3]),
            "volume":     parse_int(row[4]),
            "amount":     parse_int(row[5]),
        })
    return result


# ── 主程式 ───────────────────────────────────────────────────────────────
def iter_dates(start: datetime.date, end: datetime.date):
    d = start
    while d <= end:
        if d.weekday() < 5:  # 只考慮週一到週五
            yield d
        d += datetime.timedelta(days=1)


def backfill_day(date: datetime.date) -> bool:
    """補抓單一天，回傳是否成功寫入（False 表示當天休市或已存在）"""
    date_iso = date.isoformat()
    out_path = f"data/{date_iso}.json"
    if os.path.exists(out_path):
        print(f"  {date_iso} 已存在，略過")
        return False

    date_str = date.strftime("%Y%m%d")
    print(f"抓取 {date_iso}...")

    prices = fetch_prices(date_str)
    if not prices:
        print(f"  {date_iso} 無收盤資料（休市），略過")
        return False
    time.sleep(0.5)

    institutional = fetch_institutional(date_str)
    time.sleep(0.5)
    margin = fetch_margin(date_str)
    time.sleep(0.5)
    block_trades = fetch_block_trades(date_str)

    all_ids = set(prices)
    stocks = {}
    for stock_id in sorted(all_ids):
        stocks[stock_id] = {
            "name":          prices.get(stock_id, {}).get("name", ""),
            "price":         prices.get(stock_id),
            "institutional": institutional.get(stock_id),
            "margin":        margin.get(stock_id),
        }

    output = {"date": date_iso, "stocks": stocks, "block_trades": block_trades}
    os.makedirs("data", exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, separators=(",", ":"))
    print(f"  ✓ {date_iso}（{len(stocks)} 支，法人 {len(institutional)}，融資 {len(margin)}，鉅額 {len(block_trades)} 筆）")
    return True


def rebuild_manifest():
    import glob
    files = glob.glob("data/[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9].json")
    dates = sorted(
        [os.path.basename(f).replace(".json", "") for f in files],
        reverse=True,
    )
    manifest = {
        "dates": dates,
        "latest": dates[0] if dates else None,
        "updated": datetime.datetime.now().isoformat(timespec="seconds"),
    }
    with open("data/manifest.json", "w", encoding="utf-8") as f:
        json.dump(manifest, f, ensure_ascii=False, indent=2)
    print(f"\nmanifest.json 已重建（共 {len(dates)} 個交易日）")


def main():
    if len(sys.argv) < 2:
        print("用法：")
        print("  python3 scripts/backfill_days.py 2026-06-16 2026-07-29")
        print("  python3 scripts/backfill_days.py 2026-06-16")
        sys.exit(1)

    start = datetime.date.fromisoformat(sys.argv[1])
    end = datetime.date.fromisoformat(sys.argv[2]) if len(sys.argv) > 2 else start

    added = 0
    for d in iter_dates(start, end):
        if backfill_day(d):
            added += 1

    if added:
        rebuild_manifest()
    print(f"\n完成！新增 {added} 個交易日")


if __name__ == "__main__":
    main()
