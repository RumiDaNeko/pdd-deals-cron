"""Crawl new deals from every store on smzdm.com, translate titles to Vietnamese, and hand
them to the pdd-deals Worker, which filters per subscriber and sends them on Telegram.

Runs on GitHub Actions every 10 minutes. The Worker keeps the cursor (publish time of the
last delivered deal); this script picks up everything newer than that.

  RUN_TOKEN=... python crawl.py              # crawl, translate, deliver
  python crawl.py --dry-run --no-translate   # just show what would be sent
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

WORKER = os.environ.get("WORKER_URL", "https://pdd-deals.harumi.workers.dev")
SMZDM = "https://api.smzdm.com/v1/list"
GTX = "https://translate.googleapis.com/translate_a/single"
TRANSLATE_BATCH = 40  # titles per request, joined by newlines

# smzdm mall_id -> Worker mall key (keep in sync with MALLS in the Worker).
MALL_IDS = {
    "8645": "pdd",
    "247": "tmall",  # 天猫精选
    "2537": "tmall",  # 天猫超市
    "183": "jd",
    "243": "taobao",
    "20155": "douyin",
    "257": "vip",
}
PAGE_SIZE = 50
MAX_PAGES = 10  # per store per run; JD alone posts ~500 deals/hour
FIRST_RUN_WINDOW = 15 * 60
MAX_BACKLOG = 60 * 60  # after an outage, skip deals older than this
BATCH = 10  # deals per /send call; the Worker sends one Telegram message per deal per subscriber
MAX_CALLS_PER_BATCH = 50  # each call covers ~24 messages; guards against a Worker that never finishes

SUBSIDY = re.compile(r"^百亿补贴[：:]\s*")
PRICE = re.compile(r"(\d+(?:\.\d+)?)\s*元")


def http_json(url: str, data: dict | None = None, token: str | None = None, timeout: int = 60):
    headers = {"User-Agent": "Mozilla/5.0", "Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    body = json.dumps(data, ensure_ascii=False).encode() if data is not None else None
    req = urllib.request.Request(url, data=body, headers=headers, method="POST" if body else "GET")
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode())


def to_deal(r: dict, mall: str) -> dict:
    price_text = str(r.get("article_price") or "")
    m = PRICE.search(price_text)
    cats = r.get("article_category") or []
    return {
        "id": str(r["article_id"]),
        "mall": mall,
        "cat": str(cats[0].get("title", "")) if cats else "",
        "title": str(r.get("article_title") or ""),
        "priceText": price_text,
        "price": float(m.group(1)) if m else None,
        "worthy": int(r.get("article_worthy") or 0),
        "unworthy": int(r.get("article_unworthy") or 0),
        "image": str(r.get("article_pic") or ""),
        "url": str(r.get("article_url") or f"https://www.smzdm.com/p/{r['article_id']}/"),
        "publishedAt": int(r.get("publish_date_lt") or 0),
    }


def crawl(since: int) -> list[dict]:
    deals: dict[str, dict] = {}
    for mall_id, mall in MALL_IDS.items():
        pages = 0
        for page in range(MAX_PAGES):
            q = urllib.parse.urlencode({"keyword": "", "mall_id": mall_id, "offset": page * PAGE_SIZE, "limit": PAGE_SIZE})
            try:
                body = http_json(f"{SMZDM}?{q}")
            except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as e:
                print(f"  {mall_id}: page {page} failed: {e}", file=sys.stderr)
                break
            rows = (body.get("data") or {}).get("rows") or [] if body.get("error_code") == "0" else []
            pages += 1
            for r in rows:
                if str(r.get("article_mall_id")) == mall_id and r.get("article_id"):
                    d = to_deal(r, mall)
                    if d["publishedAt"] > since:
                        deals.setdefault(d["id"], d)
            times = [int(r.get("publish_date_lt") or 0) for r in rows]
            if len(rows) < PAGE_SIZE or (times and min(times) <= since):
                break
        print(f"  {mall:7} {mall_id:>5}: {pages} page(s)")
    return sorted(deals.values(), key=lambda d: d["publishedAt"])


def sane(src: str, out: str) -> bool:
    if not out or len(out) > len(src) * 6 + 40:
        return False
    return not re.search(r"(\S+)(\s+\1){3,}", out)


def gtx(texts: list[str]) -> list[str] | None:
    """Google's public translate endpoint (no key). Returns None if blocked or misaligned."""
    data = urllib.parse.urlencode({"client": "gtx", "sl": "zh-CN", "tl": "vi", "dt": "t", "q": "\n".join(texts)}).encode()
    req = urllib.request.Request(GTX, data=data, headers={"User-Agent": "Mozilla/5.0"})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            body = json.loads(r.read().decode())
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as e:
        print(f"  translate failed: {e}", file=sys.stderr)
        return None
    lines = "".join(seg[0] for seg in body[0] if seg and seg[0]).split("\n")
    return [l.strip() for l in lines] if len(lines) == len(texts) else None


def translate(deals: list[dict]) -> None:
    texts = [SUBSIDY.sub("", d["title"]).replace("\n", " ") for d in deals]
    t0, done = time.time(), 0
    for i in range(0, len(texts), TRANSLATE_BATCH):
        chunk = texts[i : i + TRANSLATE_BATCH]
        out = gtx(chunk)
        if out is None:
            break  # blocked or rate limited: send the rest with Chinese titles
        for j, vi in enumerate(out):
            d = deals[i + j]
            if sane(chunk[j], vi):
                d["titleVi"] = ("[Trợ giá 10 tỷ tệ] " if SUBSIDY.match(d["title"]) else "") + vi
                done += 1
        time.sleep(0.5)
    print(f"translated {done}/{len(texts)} titles in {time.time() - t0:.0f}s, e.g.")
    for d in deals[:5]:
        print(f"  {d['title'][:40]}  ->  {d.get('titleVi', '(untranslated)')}")


def deliver(deals: list[dict], token: str) -> int:
    sent = 0
    for i in range(0, len(deals), BATCH):
        batch = deals[i : i + BATCH]
        position: dict = {}  # where the Worker stopped: {"from": subscriber index, "skip": deals already sent to it}
        for _ in range(MAX_CALLS_PER_BATCH):
            try:
                r = http_json(f"{WORKER}/send", {"deals": batch, **position}, token)
            except urllib.error.HTTPError as e:
                # Stop here; the cursor stays before this batch, so the next run retries it.
                print(f"/send failed at batch {i // BATCH}: {e.code} {e.read().decode()[:200]}", file=sys.stderr)
                return sent
            sent += r.get("sent", 0)
            if r.get("done"):
                break
            nxt = r["next"]
            position = nxt if isinstance(nxt, dict) else {"from": nxt}
        else:
            print(f"/send did not finish batch {i // BATCH} in {MAX_CALLS_PER_BATCH} calls; stopping", file=sys.stderr)
            return sent
    return sent


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--no-translate", action="store_true")
    args = p.parse_args()
    token = os.environ.get("RUN_TOKEN", "")

    now = int(time.time())
    cursor = None
    if token:
        cursor = http_json(f"{WORKER}/cursor", token=token).get("cursor")
    since = max(int(cursor) if cursor else now - FIRST_RUN_WINDOW, now - MAX_BACKLOG)
    print(f"crawling deals newer than {time.strftime('%Y-%m-%d %H:%M:%S', time.gmtime(since))} UTC")

    deals = crawl(since)
    print(f"{len(deals)} new deals")
    if not deals:
        return
    if not args.no_translate:
        translate(deals)
    if args.dry_run or not token:
        for d in deals[:20]:
            print(f"  {d['mall']:7} {d['priceText'][:16]:16} {d['cat']:6} {d.get('titleVi') or d['title']}")
        return
    print(f"delivered {deliver(deals, token)} deal messages")


if __name__ == "__main__":
    main()
