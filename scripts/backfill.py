"""
歷史資料補抓腳本
用法：
  python3 scripts/backfill.py 2026-03          # 補單一月份
  python3 scripts/backfill.py 2026-01 2026-03  # 補指定範圍（含頭尾）
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


def roc_to_iso(roc_date: str) -> str:
    """'115/03/02' → '2026-03-02'"""
    y, m, d = roc_date.strip().split("/")
    return f"{int(y) + 1911}-{m}-{d}"


# ── 資料抓取 ──────────────────────────────────────────────────────────────

def fetch_stock_list() -> dict:
    """用 STOCK_DAY_ALL 取得今日股票清單，回傳 {stock_id: name}"""
    data = fetch("https://www.twse.com.tw/exchangeReport/STOCK_DAY_ALL?response=json")
    return {row[0].strip(): row[1].strip() for row in data.get("data", [])}


def fetch_stock_month(stock_id: str, date_str: str) -> dict:
    """抓單一個股指定月份的日K，回傳 {date_iso: price_dict}"""
    url = f"https://www.twse.com.tw/exchangeReport/STOCK_DAY?response=json&date={date_str}&stockNo={stock_id}"
    data = fetch(url)
    if data.get("stat") != "OK":
        return {}
    result = {}
    for row in data.get("data", []):
        date_iso = roc_to_iso(row[0])
        result[date_iso] = {
            "volume":       parse_int(row[1]),
            "amount":       parse_int(row[2]),
            "open":         parse_number(row[3]),
            "high":         parse_number(row[4]),
            "low":          parse_number(row[5]),
            "close":        parse_number(row[6]),
            "change":       parse_number(row[7]),
            "transactions": parse_int(row[8]),
        }
    return result


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


# ── 主程式 ────────────────────────────────────────────────────────────────

def iter_months(start_month: str, end_month: str):
    """產生 start_month 到 end_month 之間的所有月份（'YYYY-MM' 格式）"""
    y, m = map(int, start_month.split("-"))
    ey, em = map(int, end_month.split("-"))
    while (y, m) <= (ey, em):
        yield f"{y}-{m:02d}"
        m += 1
        if m > 12:
            m = 1
            y += 1


def main():
    if len(sys.argv) < 2:
        print("用法：")
        print("  python3 scripts/backfill.py 2026-03")
        print("  python3 scripts/backfill.py 2026-01 2026-03")
        sys.exit(1)

    start_month = sys.argv[1][:7]
    end_month = sys.argv[2][:7] if len(sys.argv) > 2 else start_month
    months = list(iter_months(start_month, end_month))
    print(f"補抓月份：{months}")

    print("\n取得股票清單...")
    stock_names = fetch_stock_list()
    stock_ids = sorted(stock_names.keys())
    print(f"共 {len(stock_ids)} 支")
    time.sleep(1)

    os.makedirs("data", exist_ok=True)

    for month in months:
        print(f"\n{'='*50}")
        print(f"月份：{month}")
        year, mon = map(int, month.split("-"))
        date_str = f"{year}{mon:02d}01"

        # ── Step 1：逐支股票抓 OHLCV ──────────────────────────────────────
        print(f"[1/2] 抓 OHLCV（{len(stock_ids)} 支）...")
        daily_prices = {}  # {date_iso: {stock_id: price_dict}}
        errors = 0

        for i, stock_id in enumerate(stock_ids):
            try:
                month_data = fetch_stock_month(stock_id, date_str)
                for date_iso, price in month_data.items():
                    daily_prices.setdefault(date_iso, {})[stock_id] = price
            except Exception as e:
                errors += 1
            time.sleep(0.5)

            if (i + 1) % 100 == 0:
                print(f"  進度：{i + 1}/{len(stock_ids)}，發現 {len(daily_prices)} 個交易日")

        trading_days = sorted(daily_prices.keys())
        print(f"  完成，發現 {len(trading_days)} 個交易日，{errors} 支抓取失敗")

        # ── Step 2：對每個交易日抓法人/融資/鉅額 ──────────────────────────
        print(f"[2/2] 抓法人/融資/鉅額...")
        for date_iso in trading_days:
            out_path = f"data/{date_iso}.json"
            if os.path.exists(out_path):
                print(f"  {date_iso} 已存在，略過")
                continue

            d_str = date_iso.replace("-", "")
            try:
                institutional = fetch_institutional(d_str)
                time.sleep(0.5)
                margin = fetch_margin(d_str)
                time.sleep(0.5)
                block_trades = fetch_block_trades(d_str)
                time.sleep(0.5)
            except Exception as e:
                print(f"  警告：{date_iso} 法人/融資抓取失敗 ({e})")
                institutional, margin, block_trades = {}, {}, []

            prices = daily_prices[date_iso]
            all_ids = set(prices) | set(institutional) | set(margin)
            stocks = {}
            for sid in sorted(all_ids):
                p = prices.get(sid)
                name = stock_names.get(sid, "")
                stocks[sid] = {
                    "name": name,
                    "price": {**p, "name": name} if p else None,
                    "institutional": institutional.get(sid),
                    "margin": margin.get(sid),
                }

            output = {"date": date_iso, "stocks": stocks, "block_trades": block_trades}
            with open(out_path, "w", encoding="utf-8") as f:
                json.dump(output, f, ensure_ascii=False, separators=(",", ":"))
            print(f"  ✓ {date_iso}（{len(stocks)} 支）")

    print("\n完成！")


if __name__ == "__main__":
    main()
