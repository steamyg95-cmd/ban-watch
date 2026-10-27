#!/usr/bin/env python3
"""
Powiadomienia o banach kont Margonem na podstawie https://czyjalega.pl/banlog

Instalacja:
    pip install requests plyer playwright
    playwright install chromium

Użycie:
    python margonem_ban_watch.py --discover   # (opcjonalnie) znajdź adres API strony
    python margonem_ban_watch.py              # uruchom monitor (pętla)
    python margonem_ban_watch.py --once       # jedno sprawdzenie (GitHub Actions)
"""
import argparse
import hashlib
import json
import os
import re
import time
from pathlib import Path

import requests

# ===================== KONFIGURACJA =====================
_default = "NickKonta1,NickKonta2"  # nicki lub ID kont po przecinku (wielkość liter bez znaczenia)
WATCHED = [w.strip() for w in os.environ.get("WATCHED", _default).split(",") if w.strip()]
PAGE_URL = "https://czyjalega.pl/banlog"
API_URL = os.environ.get("API_URL", "")  # adres JSON z --discover; puste = tryb przeglądarki
POLL_SECONDS = 60     # nie schodź poniżej ~30 s, żeby nie obciążać strony
DISCORD_WEBHOOK = os.environ.get("DISCORD_WEBHOOK", "")  # opcjonalnie
NTFY_TOPIC = os.environ.get("NTFY_TOPIC", "")  # temat ntfy.sh (powiadomienia na iPhone)
STATE_FILE = Path(__file__).with_name("seen_bans.json")
# ========================================================


def notify(title: str, msg: str) -> None:
    print(f"[{time.strftime('%H:%M:%S')}] {title}: {msg}")
    try:
        from plyer import notification
        notification.notify(title=title, message=msg[:250], timeout=15)
    except Exception:
        pass
    if DISCORD_WEBHOOK:
        try:
            requests.post(DISCORD_WEBHOOK, json={"content": f"**{title}**\n{msg}"}, timeout=10)
        except Exception as e:
            print("Błąd Discorda:", e)
    if NTFY_TOPIC:
        try:
            requests.post(
                f"https://ntfy.sh/{NTFY_TOPIC}",
                data=msg.encode("utf-8"),
                headers={"Title": "Margonem - ban"},
                timeout=10,
            )
        except Exception as e:
            print("Błąd ntfy:", e)


def find_records(data):
    """Zwraca największą listę słowników znalezioną w JSON-ie."""
    best = []

    def walk(x):
        nonlocal best
        if isinstance(x, list):
            if x and all(isinstance(i, dict) for i in x) and len(x) > len(best):
                best = x
            for i in x:
                walk(i)
        elif isinstance(x, dict):
            for v in x.values():
                walk(v)

    walk(data)
    return best


def fetch_api():
    r = requests.get(API_URL, timeout=20, headers={"User-Agent": "Mozilla/5.0"})
    r.raise_for_status()
    return [json.dumps(rec, ensure_ascii=False, sort_keys=True) for rec in find_records(r.json())]


def fetch_browser(page):
    page.goto(PAGE_URL, wait_until="networkidle")
    page.wait_for_timeout(2000)
    text = page.inner_text("body")
    return [l.strip() for l in text.splitlines() if l.strip()]


def hits(records):
    out = []
    for rec in records:
        for w in WATCHED:
            if re.search(rf"(?<!\w){re.escape(w)}(?!\w)", rec, re.I):
                out.append((rec, w))
                break
    return out


def rid(rec: str) -> str:
    return hashlib.sha1(rec.encode("utf-8")).hexdigest()


def discover():
    """Wypisuje adresy JSON ładowane przez stronę - to zwykle źródło danych banloga."""
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page()
        page.on(
            "response",
            lambda r: print("JSON:", r.url) if "json" in r.headers.get("content-type", "") else None,
        )
        page.goto(PAGE_URL, wait_until="networkidle")
        page.wait_for_timeout(3000)
        browser.close()
    print("Wklej wybrany adres do API_URL w konfiguracji.")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--discover", action="store_true")
    ap.add_argument("--once", action="store_true", help="jedno sprawdzenie i wyjście (GitHub Actions)")
    args = ap.parse_args()
    if args.discover:
        return discover()

    seen = set(json.loads(STATE_FILE.read_text())) if STATE_FILE.exists() else None

    pw = browser = page = None
    if not API_URL:
        from playwright.sync_api import sync_playwright
        pw = sync_playwright().start()
        browser = pw.chromium.launch()
        page = browser.new_page()

    notify("Margonem - monitor", f"Start. Obserwuję: {', '.join(WATCHED)}")
    try:
        while True:
            try:
                records = fetch_api() if API_URL else fetch_browser(page)
                found = hits(records)
                if seen is None:  # pierwsze uruchomienie: zapamiętaj stan, bez spamu
                    seen = {rid(r) for r, _ in found}
                    print(f"Zapisano stan początkowy ({len(seen)} wpisów dla obserwowanych kont).")
                else:
                    for rec, who in found:
                        if rid(rec) not in seen:
                            seen.add(rid(rec))
                            notify(f"BAN: {who}", rec[:300])
                STATE_FILE.write_text(json.dumps(sorted(seen)))
            except Exception as e:
                print("Błąd:", e)
            if args.once:
                break
            time.sleep(POLL_SECONDS)
    except KeyboardInterrupt:
        pass
    finally:
        if browser:
            browser.close()
        if pw:
            pw.stop()


if __name__ == "__main__":
    main()
