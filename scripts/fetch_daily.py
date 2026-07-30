"""
每日台股資料抓取腳本
抓取：OHLCV、三大法人買賣超、融資融券、鉅額交易（盤後）
輸出：data/YYYY-MM-DD.json

用法：
  python3 scripts/fetch_daily.py              # 抓今天
  python3 scripts/fetch_daily.py 2026-04-10   # 抓指定日期
"""
import sys
import csv
import io
import json
import time
import datetime
import requests

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"
}


def fetch(url: str) -> dict:
    resp = requests.get(url, headers=HEADERS, timeout=20)
    if resp.status_code == 404:
        return {}
    resp.raise_for_status()
    return resp.json()


def fetch_csv(url: str) -> list:
    resp = requests.get(url, headers=HEADERS, timeout=20)
    if resp.status_code == 404:
        return []
    resp.raise_for_status()
    return list(csv.reader(io.StringIO(resp.text)))


def parse_number(s: str):
    """把 '1,234.56' 轉成 float，失敗回傳 None"""
    try:
        return float(str(s).replace(",", "").replace("+", ""))
    except (ValueError, TypeError):
        return None


def parse_int(s: str):
    """把 '1,234,567' 轉成 int，失敗回傳 None"""
    try:
        return int(str(s).replace(",", ""))
    except (ValueError, TypeError):
        return None


# ── 1. 每日收盤價（全上市）────────────────────────────────────────────────
def fetch_prices(date_str: str) -> dict:
    """回傳 {stock_id: {name, open, high, low, close, volume, amount, change, transactions}}"""
    # TWSE 已不再遵守 response=json，此端點固定回傳 CSV（Content-Type: text/csv）
    url = f"https://www.twse.com.tw/exchangeReport/STOCK_DAY_ALL?response=json&date={date_str}"
    rows = fetch_csv(url)
    if len(rows) < 2:
        return {}
    # 表頭：日期,證券代號,證券名稱,成交股數,成交金額,開盤價,最高價,最低價,收盤價,漲跌價差,成交筆數
    # TWSE 在休市日會回傳最近一個交易日的資料，用日期欄位比對確認（日期欄為民國年 YYYMMDD）
    expected_roc_date = f"{int(date_str[:4]) - 1911}{date_str[4:]}"
    if rows[1][0] != expected_roc_date:
        return {}
    result = {}
    for row in rows[1:]:
        if len(row) < 11:
            continue
        stock_id = row[1].strip()
        result[stock_id] = {
            "name":         row[2].strip(),
            "volume":       parse_int(row[3]),      # 成交股數
            "amount":       parse_int(row[4]),      # 成交金額
            "open":         parse_number(row[5]),   # 開盤價
            "high":         parse_number(row[6]),   # 最高價
            "low":          parse_number(row[7]),   # 最低價
            "close":        parse_number(row[8]),   # 收盤價
            "change":       parse_number(row[9]),   # 漲跌價差（帶正負號）
            "transactions": parse_int(row[10]),     # 成交筆數
        }
    return result


# ── 2. 三大法人買賣超 ────────────────────────────────────────────────────
def fetch_institutional(date_str: str) -> dict:
    """回傳 {stock_id: {foreign_buy, foreign_sell, foreign_net, trust_buy, trust_sell, trust_net, dealer_net, total_net}}"""
    url = f"https://www.twse.com.tw/fund/T86?response=json&date={date_str}&selectType=ALL"
    data = fetch(url)
    # fields[2..4]: 外陸資買進/賣出/買賣超（不含外資自營商）
    # fields[8..10]: 投信買進/賣出/買賣超
    # fields[11]: 自營商買賣超股數（合計）
    # fields[18]: 三大法人買賣超股數
    result = {}
    for row in data.get("data", []):
        if len(row) < 12:
            continue
        stock_id = row[0].strip()
        result[stock_id] = {
            "foreign_buy":  parse_int(row[2]),              # 外資買進
            "foreign_sell": parse_int(row[3]),              # 外資賣出
            "foreign_net":  parse_int(row[4]),              # 外資買賣超（正=買超）
            "trust_buy":    parse_int(row[8]),              # 投信買進
            "trust_sell":   parse_int(row[9]),              # 投信賣出
            "trust_net":    parse_int(row[10]),             # 投信買賣超
            "dealer_net":   parse_int(row[11]),             # 自營商買賣超
            "total_net":    parse_int(row[18]) if len(row) > 18 else None,  # 三大法人合計
        }
    return result


# ── 3. 融資融券 ──────────────────────────────────────────────────────────
def fetch_margin(date_str: str) -> dict:
    """回傳 {stock_id: {margin_buy, margin_sell, margin_balance, short_buy, short_sell, short_balance}}
    融資欄: 買進, 賣出, 現金償還, 前日餘額, 今日餘額
    融券欄: 買進, 賣出, 現券償還, 前日餘額, 今日餘額
    """
    url = f"https://www.twse.com.tw/exchangeReport/MI_MARGN?response=json&date={date_str}&selectType=ALL"
    data = fetch(url)
    # 資料在 tables[1]（彙總明細表），不是直接在 data
    tables = data.get("tables", [])
    if len(tables) < 2:
        return {}
    # fields: 代號, 名稱, 買進(融資), 賣出(融資), 現金償還, 前日餘額(融資), 今日餘額(融資),
    #         次一營業日限額, 買進(融券), 賣出(融券), 現券償還, 前日餘額(融券), 今日餘額(融券),
    #         次一營業日限額, 資券互抵, 註記
    result = {}
    for row in tables[1].get("data", []):
        stock_id = row[0].strip()
        result[stock_id] = {
            "margin_buy":      parse_int(row[2]),   # 融資買進
            "margin_sell":     parse_int(row[3]),   # 融資賣出
            "margin_balance":  parse_int(row[6]),   # 融資今日餘額
            "short_buy":       parse_int(row[8]),   # 融券買進（回補）
            "short_sell":      parse_int(row[9]),   # 融券賣出
            "short_balance":   parse_int(row[12]),  # 融券今日餘額
        }
    return result


# ── 4. 鉅額交易（盤後）──────────────────────────────────────────────────
def fetch_block_trades(date_str: str) -> list:
    """回傳 [{stock_id, name, trade_type, price, volume, amount}]"""
    url = f"https://www.twse.com.tw/block/BFIAUU?response=json&date={date_str}"
    data = fetch(url)
    # fields: 證券代號, 證券名稱, 交易別, 成交價, 成交股數, 成交金額
    result = []
    for row in data.get("data", []):
        result.append({
            "stock_id":   row[0].strip(),
            "name":       row[1].strip(),
            "trade_type": row[2].strip(),           # 配對交易 / 單向交易
            "price":      parse_number(row[3]),
            "volume":     parse_int(row[4]),
            "amount":     parse_int(row[5]),
        })
    return result


# ── 主程式 ───────────────────────────────────────────────────────────────
def main():
    if len(sys.argv) > 1:
        date = datetime.date.fromisoformat(sys.argv[1])
    else:
        date = datetime.date.today()

    date_str = date.strftime("%Y%m%d")
    date_iso = date.isoformat()
    print(f"抓取日期：{date_iso}")

    print("  [1/4] 每日收盤價...")
    prices = fetch_prices(date_str)
    print(f"        {len(prices)} 支")

    # 收盤價為空 → 休市，或傳入的日期不是今天（STOCK_DAY_ALL 不支援歷史補抓）
    if not prices:
        if date != datetime.date.today():
            print(f"錯誤：STOCK_DAY_ALL 只支援當天資料，無法補抓 {date_iso}。")
        else:
            print("今日休市，無資料，略過。")
        sys.exit(0)

    time.sleep(1)

    print("  [2/4] 三大法人買賣超...")
    institutional = fetch_institutional(date_str)
    print(f"        {len(institutional)} 支")
    time.sleep(1)

    print("  [3/4] 融資融券...")
    margin = fetch_margin(date_str)
    print(f"        {len(margin)} 支")
    time.sleep(1)

    print("  [4/4] 鉅額交易（盤後）...")
    block_trades = fetch_block_trades(date_str)
    print(f"        {len(block_trades)} 筆")

    # 合併成以股票代號為主鍵的結構
    all_ids = set(prices)
    stocks = {}
    for stock_id in sorted(all_ids):
        stocks[stock_id] = {
            "name":          (prices.get(stock_id, {}) or institutional.get(stock_id, {})).get("name", ""),
            "price":         prices.get(stock_id),
            "institutional": institutional.get(stock_id),
            "margin":        margin.get(stock_id),
        }

    output = {
        "date":         date_iso,
        "stocks":       stocks,
        "block_trades": block_trades,
    }

    import os
    os.makedirs("data", exist_ok=True)
    out_path = f"data/{date_iso}.json"
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, separators=(",", ":"))

    size_kb = os.path.getsize(out_path) / 1024
    print(f"\n完成！輸出：{out_path}（{size_kb:.1f} KB）")
    print(f"  股票數：{len(stocks)}")
    print(f"  鉅額交易：{len(block_trades)} 筆")

    # 更新 data/manifest.json（前端靠此知道有哪些日期可用）
    _update_manifest(date_iso)

    # 印出 2330 當天資料預覽
    if "2330" in stocks:
        print("\n--- 台積電 (2330) 資料預覽 ---")
        print(json.dumps(stocks["2330"], ensure_ascii=False, indent=2))


def _update_manifest(new_date_iso: str):
    """將新日期加入 data/manifest.json，並依日期降冪排序。"""
    import os
    import datetime as dt
    manifest_path = "data/manifest.json"
    try:
        with open(manifest_path, encoding="utf-8") as f:
            manifest = json.load(f)
    except FileNotFoundError:
        manifest = {"dates": []}

    dates = manifest.get("dates", [])
    if new_date_iso not in dates:
        dates.append(new_date_iso)
    dates.sort(reverse=True)

    manifest["dates"] = dates
    manifest["latest"] = dates[0]
    manifest["updated"] = dt.datetime.now().isoformat(timespec="seconds")

    with open(manifest_path, "w", encoding="utf-8") as f:
        json.dump(manifest, f, ensure_ascii=False, indent=2)
    print(f"  manifest.json 已更新（共 {len(dates)} 個交易日）")


if __name__ == "__main__":
    main()
