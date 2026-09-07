#!/usr/bin/env python3
"""Real Chromium checks against a local test HTTP server, using fictional fixtures.
No market requests and no real trading. Screenshots are clearly labelled demo.
"""
from __future__ import annotations

import argparse
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import tempfile
import threading

from playwright.sync_api import sync_playwright
from largo_auto_journal import publish


def sample_state(status="WAIT_CLOSE", close=None, opening=None):
    return {"schema": 1, "version": "largo-manual-1515-v1", "config_hash": "browser-test-only",
            "config": {"orders_enabled": False, "cost_assumption_pct": 0.3, "display_expiry": "15:25:00"},
            "updated_at": "2026-09-07T16:00:00+09:00", "health": {"message": "가상 검사 자료"},
            "days": [{"date": "2026-09-04", "status": "SIGNAL", "signal_at": "2026-09-04T15:15:00+09:00",
                      "selected": {"code": "123456", "name": "검사용 가상기업", "price": 10900,
                                   "change_pct": 5, "turnover_proxy": 1200e8, "theme_breadth": .8},
                      "reference": {"status": status, "close": close, "open": opening, "next_date": "2026-09-07"}}]}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--template", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)
    checks = []

    def check(name, condition):
        if not condition:
            raise AssertionError(name)
        checks.append(name)

    with tempfile.TemporaryDirectory() as folder:
        root = Path(folder)
        inp = root / "input.json"

        def write_state(value):
            inp.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")
            publish(inp, root / "app", args.template)

        write_state(sample_state())
        handler = partial(SimpleHTTPRequestHandler, directory=str(root))
        server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        url = f"http://127.0.0.1:{server.server_port}/app/"
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            context = browser.new_context(viewport={"width": 1440, "height": 1050}, accept_downloads=True)
            context.add_init_script("localStorage.setItem('largo-manual-1515-private-journal-v1','{\"sentinel\":true}')")
            page = context.new_page()
            errors = []
            page.on("pageerror", lambda error: errors.append(str(error)))
            page.goto(url, wait_until="networkidle")
            check("no manual fill form", page.locator("form,#tradeForm,#entry,#exit,#qty").count() == 0)
            check("automatically created planned row", "가상 매수 대기" in page.locator("#journalBody").inner_text())
            check("observed quote not used as buy", "10,900" not in page.locator("#journalBody").inner_text())
            check("pending not in statistics", page.locator("#n").inner_text() == "0건" and page.locator("#mean").inner_text() == "—")
            check("legacy journal preserved", page.evaluate("localStorage.getItem('largo-manual-1515-private-journal-v1')") == '{"sentinel":true}')
            check("legacy backup available without manual input", page.locator("#legacy").is_visible())

            write_state(sample_state("WAIT_OPEN", 10000))
            page.click("#refresh")
            page.wait_for_function("document.querySelector('#journalBody').textContent.includes('가상 보유')")
            check("close auto creates simulated buy", "10,000" in page.locator("#journalBody").inner_text())
            check("open position has no realized return", page.locator("#n").inner_text() == "0건")

            write_state(sample_state("COMPLETE", 10000, 10200))
            page.click("#refresh")
            page.wait_for_function("document.querySelector('#n').textContent === '1건'")
            check("next open auto closes and computes net", "+1.70%" in page.locator("#journalBody").inner_text())
            page.click("#refresh")
            page.wait_for_timeout(200)
            check("refresh cannot duplicate journal", page.locator("#journalBody tr").count() == 1)
            with page.expect_download() as download_info:
                page.click("#exportJournal")
            download = download_info.value
            csv_path = args.out / "browser-export.csv"
            download.save_as(csv_path)
            check("CSV exported automatically populated record", "검사용 가상기업" in csv_path.read_text(encoding="utf-8-sig"))

            page.fill("#search", "does-not-exist")
            check("search works", "검색 조건" in page.locator("#journalBody").inner_text())
            page.fill("#search", "")
            page.click("#demo")
            check("demo explicitly labelled", page.locator("#demoBanner").is_visible())
            check("demo isolated count", page.locator("#n").inner_text() == "3건")
            page.select_option("#statusFilter", "CLOSED")
            check("automatic status filter", page.locator("#journalBody tr").count() == 3)
            page.select_option("#statusFilter", "")
            page.screenshot(path=str(args.out / "desktop-demo.png"), full_page=True)
            check("desktop no page overflow", page.evaluate("document.documentElement.scrollWidth <= window.innerWidth + 1"))
            page.set_viewport_size({"width": 390, "height": 844})
            page.screenshot(path=str(args.out / "mobile-demo.png"), full_page=True)
            check("mobile no page overflow", page.evaluate("document.documentElement.scrollWidth <= window.innerWidth + 1"))
            page.click("#demo")
            page.wait_for_function("document.querySelector('#n').textContent === '1건'")
            check("demo not persisted into live", page.locator("#journalBody tr").count() == 1)

            s = sample_state("CORPORATE_ACTION_REVIEW", 10000, 15000)
            write_state(s)
            page.click("#refresh")
            page.wait_for_function("document.querySelector('#n').textContent === '0건'")
            check("review never reports fake profit", "가격 확인 필요" in page.locator("#journalBody").inner_text() and page.locator("#mean").inner_text() == "—")
            s = sample_state("COMPLETE", 10000, 15000)
            s["days"][0]["status"] = "DATA_BLOCK"
            write_state(s)
            page.click("#refresh")
            page.wait_for_function("document.querySelector('#journalBody').textContent.includes('자료 차단')")
            check("blocked journal does not name a pick", "검사용 가상기업" not in page.locator("#journalBody").inner_text())
            check("no JavaScript errors", not errors)
            context.close()

            empty = sample_state()
            empty["days"] = []
            write_state(empty)
            denied = browser.new_context(viewport={"width": 390, "height": 844})
            denied.add_init_script("Object.defineProperty(window,'localStorage',{get(){throw new Error('storage disabled')}})")
            second = denied.new_page()
            second.goto(url, wait_until="networkidle")
            check("works without localStorage", second.locator("#n").inner_text() == "0건")
            check("empty win rate stays blank", second.locator("#win").inner_text() == "—")
            second.screenshot(path=str(args.out / "empty-live-mobile.png"), full_page=True)
            denied.close()
            browser.close()
        server.shutdown()
    result = {"status": "PASS", "browser": "Chromium", "checks": len(checks), "passed": checks,
              "real_market_test": False, "note": "Synthetic fixture HTTP/browser tests; no live market or real execution tested."}
    (args.out / "browser-results.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
