"""
測試 TWSE 四支 API 是否能正確回傳指定日期的資料
用法：python3 scripts/test_apis.py
"""
import requests
import json

DATE = "20260410"  # 測試用 4/10
BASE = "https://www.twse.com.tw"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"
}

APIS = {
    "每日收盤 OHLCV (STOCK_DAY_ALL)": f"{BASE}/exchangeReport/STOCK_DAY_ALL?response=json&date={DATE}",
    "三大法人買賣超 (T86)":           f"{BASE}/fund/T86?response=json&date={DATE}&selectType=ALL",
    "融資融券 (MI_MARGN)":            f"{BASE}/exchangeReport/MI_MARGN?response=json&date={DATE}&selectType=ALL",
    "鉅額交易盤後 (BFIAUU)":          f"{BASE}/block/BFIAUU?response=json&date={DATE}",
}

for name, url in APIS.items():
    print(f"\n{'='*60}")
    print(f"[{name}]")
    print(f"URL: {url}")
    try:
        resp = requests.get(url, headers=HEADERS, timeout=15)
        resp.raise_for_status()
        data = resp.json()
        status = data.get("stat", "N/A")
        total = data.get("total", "N/A")
        fields = data.get("fields", [])
        rows = data.get("data", [])
        print(f"  stat   : {status}")
        print(f"  total  : {total}")
        print(f"  fields : {fields[:5]}{'...' if len(fields) > 5 else ''}")
        print(f"  rows   : {len(rows)} 筆")
        if rows:
            print(f"  第一筆 : {rows[0]}")
    except Exception as e:
        print(f"  ERROR: {e}")
