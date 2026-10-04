#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
MOPS 當日重大訊息 → Discord Embed
支援：純關鍵字 + 公司名單（名稱/代號）混合格式
單次掃描內不重複上傳
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
HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; MOPS-Discord-Bot/1.3-GHA)"}

TZ_TW = timezone(timedelta(hours=8))
# =======================================================


def log(msg: str):
    now = datetime.now(TZ_TW).strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{now}] {msg}", flush=True)


def load_targets(filepath: Path) -> tuple[list[str], set[str], set[str]]:
    """
    讀取關鍵字/公司名單檔，回傳：
    - keywords: 純關鍵字列表
    - company_codes: 公司代號集合
    - company_names: 公司名稱集合
    相容格式範例：
      減資
      庫藏股
      散熱： 奇鋐 (3017)、雙鴻 (3324)
      電源： 台達電 (2308)、光寶科 (2301)
    """
    if not filepath.exists():
        log(f"警告：找不到檔案 {filepath}")
        return [], set(), set()

    text = filepath.read_text(encoding="utf-8")

    keywords = []
    company_codes = set()
    company_names = set()

    # 先抓出所有「公司名 (代號)」或「公司名(代號)」
    # 例：奇鋐 (3017)、台達電(2308)、世芯-KY (3661)
    pattern = re.compile(r"([^\s、，,：:()（）]+)\s*[（(]\s*(\d{4})\s*[）)]")

    for match in pattern.finditer(text):
        name = match.group(1).strip()
        code = match.group(2).strip()
        if name:
            company_names.add(name)
        if code:
            company_codes.add(code)

    # 把已匹配到的「名稱 (代號)」整段移除，剩下的當純關鍵字處理
    cleaned = pattern.sub(" ", text)

    # 再依換行、逗號、頓號、斜線、空白切開
    raw = re.split(r"[\n,，、/／\s]+", cleaned)
    for token in raw:
        token = token.strip()
        # 過濾掉純類別標籤（後面通常有冒號）與空字
        if not token or token.endswith("：") or token.endswith(":"):
            continue
        # 過濾掉已經是純數字的（避免把代號再當關鍵字）
        if token.isdigit():
            continue
        keywords.append(token)

    # 去重並保持順序
    keywords = list(dict.fromkeys(keywords))

    log(f"載入純關鍵字 {len(keywords)} 個：{keywords}")
    log(f"載入公司代號 {len(company_codes)} 個：{sorted(company_codes)}")
    log(f"載入公司名稱 {len(company_names)} 個：{sorted(company_names)}")

    return keywords, company_codes, company_names


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


def is_match(item: dict, keywords: list[str], codes: set[str], names: set[str]) -> bool:
    """判斷這則公告是否符合任一條件"""
    title = item.get("主旨 ", "") or item.get("主旨", "")
    code = str(item.get("公司代號", "")).strip()
    name = str(item.get("公司名稱", "")).strip()

    # 1. 公司代號命中
    if code and code in codes:
        return True

    # 2. 公司名稱命中（完整或包含）
    if name:
        for n in names:
            if n in name or name in n:
                return True

    # 3. 標題含關鍵字
    for kw in keywords:
        if kw and kw in title:
            return True

    return False


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

    keywords, company_codes, company_names = load_targets(KEYWORDS_FILE)

    if not keywords and not company_codes and not company_names:
        log("沒有任何關鍵字或公司標的，結束")
        sys.exit(0)

    posted = load_posted()
    items = fetch_today_announcements()

    # 單次掃描內去重用
    seen_this_run = set()
    matched = []

    for item in items:
        if not is_match(item, keywords, company_codes, company_names):
            continue
        aid = make_id(item)
        if aid in posted or aid in seen_this_run:
            continue
        matched.append(item)
        seen_this_run.add(aid)

    log(f"符合條件且未推播過的公告：{len(matched)} 筆")

    new_count = 0
    for item in matched:
        aid = make_id(item)
        if send_embed_to_discord(item):
            posted.add(aid)
            new_count += 1
            time.sleep(1.2)

    if new_count > 0:
        save_posted(posted)
        log(f"本次新增推播 {new_count} 則")
    else:
        log("本次無新符合條件的訊息")

    log("執行完畢")
    sys.exit(0)


if __name__ == "__main__":
    main()
