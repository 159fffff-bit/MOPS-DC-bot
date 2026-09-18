#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
MOPS 當日重大訊息 → Discord Embed
適用於 GitHub Actions（執行一次後結束）
"""

import os
import re
import json
import time
import hashlib
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path

import requests

# ======================== 設定區 ========================
DISCORD_WEBHOOK = os.getenv("DISCORD_WEBHOOK", "").strip()

BASE_DIR = Path(__file__).parent
KEYWORDS_FILE = BASE_DIR / "keywords.txt"
POSTED_FILE = BASE_DIR / "posted.json"

MOPS_API = "https://openapi.twse.com.tw/v1/opendata/t187ap04_L"
HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; MOPS-Discord-Bot/1.2-GHA)"}

TZ_TW = timezone(timedelta(hours=8))
# =======================================================


def log(msg: str):
    now = datetime.now(TZ_TW).strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{now}] {msg}", flush=True)


def load_keywords() -> list[str]:
    if not KEYWORDS_FILE.exists():
        log(f"警告：找不到關鍵字檔 {KEYWORDS_FILE}")
        return []
    text = KEYWORDS_FILE.read_text(encoding="utf-8")
    raw = re.split(r"[\n,，/／\s]+", text)
    keywords = [k.strip() for k in raw if k.strip()]
    log(f"載入關鍵字 {len(keywords)} 個：{keywords}")
    return keywords


def load_posted() -> set[str]:
    if not POSTED_FILE.exists():
        return set()
    try:
        data = json.loads(POSTED_FILE.read_text(encoding="utf-8"))
        return set(data)
    except Exception as e:
        log(f"讀取 posted.json 失敗：{e}")
        return set()


def save_posted(posted: set[str]):
    # 只保留最近 800 筆，避免檔案無限成長
    lst = list(posted)[-800:]
    POSTED_FILE.write_text(
        json.dumps(lst, ensure_ascii=False, indent=2),
        encoding="utf-8"
    )
    log(f"已更新 posted.json（目前紀錄 {len(lst)} 筆）")


def make_id(item: dict) -> str:
    key = f"{item.get('公司代號')}_{item.get('發言日期')}_{item.get('發言時間')}_{item.get('主旨 ', '')}"
    return hashlib.md5(key.encode("utf-8")).hexdigest()


def roc_to_western(roc_date: str) -> str:
    """民國年 → 西元 YYYYMMDD"""
    if not roc_date or len(roc_date) < 6:
        return ""
    try:
        year = int(roc_date[:3]) + 1911
        return f"{year}{roc_date[3:]}"
    except Exception:
        return ""


def format_time(time_str: str) -> str:
    if not time_str:
        return ""
    t = str(time_str).zfill(6)
    return f"{t[0:2]}:{t[2:4]}:{t[4:6]}"


def build_detail_link(item: dict) -> str:
    code = item.get("公司代號", "")
    spoke_date_roc = item.get("發言日期", "")
    spoke_time = str(item.get("發言時間", "")).zfill(6)
    year_roc = spoke_date_roc[:3] if len(spoke_date_roc) >= 3 else ""
    western_date = roc_to_western(spoke_date_roc)

    return (
        "https://mops.twse.com.tw/mops/web/t05st01"
        f"?encodeURIComponent=1&firstin=true&TYPEK=all"
        f"&step=2&off=1"
        f"&co_id={code}"
        f"&spoke_date={western_date}"
        f"&spoke_time={spoke_time}"
        f"&seq_no=1"
        f"&year={year_roc}"
        f"&month=all&e_month=all"
    )


def fetch_today_announcements() -> list[dict]:
    try:
        r = requests.get(MOPS_API, headers=HEADERS, timeout=25)
        r.raise_for_status()
        data = r.json()
        log(f"成功取得 {len(data)} 筆當日重大訊息")
        return data
    except Exception as e:
        log(f"抓取 API 失敗：{e}")
        return []


def filter_by_keywords(items: list[dict], keywords: list[str]) -> list[dict]:
    if not keywords:
        return []
    result = []
    for item in items:
        title = item.get("主旨 ", "") or item.get("主旨", "")
        for kw in keywords:
            if kw in title:
                result.append(item)
                break
    return result


def send_embed_to_discord(item: dict) -> bool:
    code = item.get("公司代號", "")
    name = item.get("公司名稱", "")
    title = (item.get("主旨 ", "") or item.get("主旨", "")).replace("\r\n", " ").strip()
    date_roc = item.get("發言日期", "")
    time_raw = item.get("發言時間", "")
    clause = item.get("符合條款", "")
    content = (item.get("說明", "") or "").replace("\r\n", "\n").strip()

    if len(content) > 1800:
        content = content[:1800] + "…\n\n（內容過長，已截斷）"

    detail_url = build_detail_link(item)
    time_fmt = format_time(time_raw)

    embed = {
        "title": f"[{code}] {name}｜{title[:200]}",
        "url": detail_url,
        "description": content or "（無詳細說明）",
        "color": 0x1E90FF,
        "fields": [
            {"name": "公司", "value": f"{code} {name}", "inline": True},
            {"name": "發言時間", "value": f"{date_roc} {time_fmt}", "inline": True},
            {"name": "符合條款", "value": clause or "—", "inline": True},
            {"name": "詳細頁面", "value": f"[點此開啟原始公告]({detail_url})", "inline": False},
        ],
        "footer": {"text": "公開資訊觀測站 · GitHub Actions"},
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }

    payload = {
        "username": "MOPS 重大訊息",
        "embeds": [embed],
    }

    try:
        r = requests.post(DISCORD_WEBHOOK, json=payload, timeout=15)
        if r.status_code in (200, 204):
            log(f"已推播：[{code}] {title[:50]}...")
            return True
        else:
            log(f"Discord 回傳錯誤 {r.status_code}：{r.text[:200]}")
            return False
    except Exception as e:
        log(f"推播失敗：{e}")
        return False


def main():
    log("=" * 50)
    log("MOPS → Discord 監控開始（GitHub Actions 模式）")
    log("=" * 50)

    if not DISCORD_WEBHOOK:
        log("錯誤：未設定環境變數 DISCORD_WEBHOOK")
        sys.exit(1)

    keywords = load_keywords()
    if not keywords:
        log("沒有關鍵字，結束")
        sys.exit(0)

    posted = load_posted()
    items = fetch_today_announcements()
    matched = filter_by_keywords(items, keywords)

    log(f"符合關鍵字的公告：{len(matched)} 筆")

    new_count = 0
    for item in matched:
        aid = make_id(item)
        if aid in posted:
            continue
        if send_embed_to_discord(item):
            posted.add(aid)
            new_count += 1
            time.sleep(1.2)  # 避免 Discord rate limit

    if new_count > 0:
        save_posted(posted)
        log(f"本次新增推播 {new_count} 則")
    else:
        log("本次無新符合關鍵字的訊息")

    log("執行完畢")
    sys.exit(0)


if __name__ == "__main__":
    main()
