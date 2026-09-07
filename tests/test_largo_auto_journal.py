from __future__ import annotations

import copy
import json
from pathlib import Path
import tempfile
import unittest

from largo_auto_journal import build_journal, journal_csv, make_row, number, publish, summarize


def source(days=None):
    return {"schema": 1, "version": "largo-manual-1515-v1", "config_hash": "frozen-test-config",
            "config": {"orders_enabled": False, "cost_assumption_pct": 0.3},
            "updated_at": "2026-09-07T16:00:00+09:00", "days": [] if days is None else days}


def day(status="WAIT_CLOSE", close=None, opening=None, date="2026-09-04", next_date="2026-09-07"):
    return {"date": date, "status": "SIGNAL", "signal_at": date + "T15:15:00+09:00",
            "selected": {"code": "123456", "name": "가상기업", "price": 10900},
            "reference": {"status": status, "close": close, "open": opening, "next_date": next_date}}


class AutoJournalTests(unittest.TestCase):
    def row(self, value):
        return build_journal(source([value]))["rows"][0]

    def test_empty_metrics_are_null_not_zero(self):
        s = build_journal(source())["summary"]
        self.assertEqual(s["completed"], 0)
        for key in ("win_rate_pct", "mean_pct", "compound_pct", "drawdown_pct"):
            self.assertIsNone(s[key])

    def test_selected_signal_creates_planned_journal_without_input(self):
        r = self.row(day())
        self.assertEqual(r["status"], "PLANNED")
        self.assertEqual(r["name"], "가상기업")
        self.assertIsNone(r["buy_price"])

    def test_observed_price_is_never_assumed_buy_price(self):
        r = self.row(day("WAIT_OPEN", 10000))
        self.assertEqual(r["buy_price"], 10000)
        self.assertNotEqual(r["buy_price"], 10900)

    def test_wait_close_does_not_accept_unconfirmed_price(self):
        self.assertIsNone(self.row(day("WAIT_CLOSE", 10000))["buy_price"])

    def test_confirmed_close_automatically_buys(self):
        j = build_journal(source([day("WAIT_OPEN", 10000)]))
        self.assertEqual(j["rows"][0]["status"], "OPEN")
        self.assertEqual(j["summary"]["open"], 1)
        self.assertEqual(j["summary"]["completed"], 0)
        self.assertIsNone(j["rows"][0]["net_pct"])

    def test_next_open_automatically_sells(self):
        r = self.row(day("COMPLETE", 10000, 10200))
        self.assertEqual(r["status"], "CLOSED")
        self.assertEqual(r["gross_pct"], 2.0)
        self.assertEqual(r["net_pct"], 1.7)
        self.assertFalse(r["real_order"])
        self.assertTrue(r["simulation_only"])

    def test_weekend_uses_provided_next_market_session(self):
        r = self.row(day("COMPLETE", 10000, 10200))
        self.assertEqual(r["buy_date"], "2026-09-04")
        self.assertEqual(r["sell_date"], "2026-09-07")

    def test_no_trade_never_creates_fills_even_with_stray_prices(self):
        d = day("COMPLETE", 10000, 11000)
        d["status"] = "NO_TRADE"
        r = self.row(d)
        self.assertEqual(r["status"], "NO_TRADE")
        self.assertIsNone(r["name"])
        self.assertIsNone(r["net_pct"])

    def test_blocked_source_never_creates_trade_or_exposes_candidate(self):
        d = day("COMPLETE", 10000, 11000)
        d["status"] = "DATA_BLOCK"
        r = self.row(d)
        self.assertEqual(r["status"], "DATA_BLOCK")
        self.assertIsNone(r["buy_price"])
        self.assertIsNone(r["name"])

    def test_missing_open_cannot_complete(self):
        for status in ("MISSING_NEXT_OPEN", "MISSING_OPEN", "WAIT_OPEN"):
            with self.subTest(status=status):
                r = self.row(day(status, 10000))
                self.assertNotEqual(r["status"], "CLOSED")
                self.assertIsNone(r["sell_price"])
                self.assertIsNone(r["net_pct"])

    def test_unknown_reference_is_reviewed(self):
        self.assertEqual(self.row(day("UNKNOWN", 10000, 10500))["status"], "REVIEW")

    def test_revision_and_corporate_action_are_not_returns(self):
        for status in ("PRICE_REVISION_REVIEW", "CORPORATE_ACTION_REVIEW", "CALENDAR_MISSING"):
            with self.subTest(status=status):
                r = self.row(day(status, 10000, 15000))
                self.assertEqual(r["status"], "REVIEW")
                self.assertIsNone(r["net_pct"])

    def test_complete_requires_both_valid_prices(self):
        for close, opening in ((0, 10200), (10000, 0), (None, 10200), (10000, None), (True, 10200), (-1, 10200)):
            with self.subTest(close=close, opening=opening):
                self.assertEqual(self.row(day("COMPLETE", close, opening))["status"], "REVIEW")

    def test_same_day_and_earlier_exit_are_rejected(self):
        for when in ("2026-09-04", "2026-09-03", "not-a-date", None):
            self.assertEqual(self.row(day("COMPLETE", 10000, 10200, next_date=when))["status"], "REVIEW")

    def test_inconsistent_reference_return_is_quarantined(self):
        d = day("COMPLETE", 10000, 10200)
        d["reference"]["gross_pct"] = 99
        self.assertEqual(self.row(d)["status"], "REVIEW")

    def test_rounded_reference_return_is_accepted(self):
        d = day("COMPLETE", 9990, 10200)
        d["reference"]["gross_pct"] = round((10200 / 9990 - 1) * 100, 4)
        self.assertEqual(self.row(d)["status"], "CLOSED")

    def test_no_mutation_of_frozen_selection_or_config(self):
        s = source([day("COMPLETE", 10000, 10200)])
        before = copy.deepcopy(s)
        build_journal(s)
        self.assertEqual(s, before)

    def test_repeated_build_is_idempotent(self):
        s = source([day("COMPLETE", 10000, 10200)])
        self.assertEqual(build_journal(s), build_journal(s))

    def test_id_stable_across_lifecycle(self):
        ids = [self.row(d)["id"] for d in (day(), day("WAIT_OPEN", 10000), day("COMPLETE", 10000, 10200))]
        self.assertEqual(len(set(ids)), 1)

    def test_duplicate_dates_fail_instead_of_discarding(self):
        with self.assertRaises(ValueError):
            build_journal(source([day(), day()]))

    def test_invalid_dates_fail(self):
        with self.assertRaises(ValueError):
            build_journal(source([day(date="2026-99-40")]))

    def test_no_order_enabled_source(self):
        s = source()
        s["config"]["orders_enabled"] = True
        with self.assertRaises(ValueError):
            build_journal(s)

    def test_invalid_cost_and_missing_hash_fail(self):
        for value in (None, True, -1, 6, "bad"):
            s = source()
            s["config"]["cost_assumption_pct"] = value
            with self.assertRaises(ValueError):
                build_journal(s)
        s = source()
        s["config_hash"] = ""
        with self.assertRaises(ValueError):
            build_journal(s)

    def test_nonfinite_number_is_rejected(self):
        for value in (float("nan"), float("inf"), None, True, "invalid"):
            self.assertIsNone(number(value))

    def test_summary_excludes_pending_and_reports_zero_return_as_not_win(self):
        rows = [make_row(day("COMPLETE", 10000, 10030), 0.3, "test"),
                make_row(day("WAIT_OPEN", 10000, date="2026-09-07"), 0.3, "test")]
        s = summarize(rows)
        self.assertEqual(s["completed"], 1)
        self.assertEqual(s["win_rate_pct"], 0)
        self.assertAlmostEqual(s["mean_pct"], 0)

    def test_drawdown_includes_initial_capital(self):
        s = build_journal(source([day("COMPLETE", 10000, 9900)]))["summary"]
        self.assertAlmostEqual(s["drawdown_pct"], -1.3)
        self.assertAlmostEqual(s["compound_pct"], -1.3)

    def test_compounding_and_win_rate(self):
        rows = [make_row(day("COMPLETE", 10000, 10200), 0.3, "x"),
                make_row(day("COMPLETE", 10000, 9900, date="2026-09-07", next_date="2026-09-08"), 0.3, "x")]
        s = summarize(rows)
        self.assertEqual(s["win_rate_pct"], 50)
        self.assertAlmostEqual(s["mean_pct"], 0.2)
        self.assertAlmostEqual(s["compound_pct"], (1.017 * 0.987 - 1) * 100)
        self.assertAlmostEqual(s["drawdown_pct"], -1.3)

    def test_publish_writes_json_csv_and_html_without_touching_input(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            inp = root / "input.json"
            inp.write_text(json.dumps(source([day("COMPLETE", 10000, 10200)])), encoding="utf-8")
            before = inp.read_bytes()
            template = root / "template.html"
            template.write_text('<script type="application/json">__INITIAL_STATE__</script>', encoding="utf-8")
            j = publish(inp, root / "out", template)
            self.assertEqual(inp.read_bytes(), before)
            enriched = json.loads((root / "out/state.json").read_text())
            self.assertEqual(enriched["auto_journal"], j)
            self.assertEqual(enriched["days"], json.loads(before)["days"])
            self.assertTrue((root / "out/journal.csv").read_text().startswith("\ufeff"))
            self.assertIn("가상 매도 완료", (root / "out/index.html").read_text())
            self.assertNotIn("__INITIAL_STATE__", (root / "out/index.html").read_text())

    def test_csv_injection_is_escaped(self):
        d = day("COMPLETE", 10000, 10200)
        d["selected"]["name"] = "=HYPERLINK(1)"
        text = journal_csv(build_journal(source([d])))
        self.assertIn("'=HYPERLINK(1)", text)

    def test_html_embedding_cannot_terminate_json_script(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            d = day("COMPLETE", 10000, 10200)
            d["selected"]["name"] = '</script><script>alert(1)</script>'
            (root / "state.json").write_text(json.dumps(source([d])))
            (root / "template.html").write_text('<script type="application/json">__INITIAL_STATE__</script>')
            publish(root / "state.json", root / "out", root / "template.html")
            html = (root / "out/index.html").read_text()
            self.assertEqual(html.count("</script>"), 1)
            self.assertNotIn("<script>alert", html)


if __name__ == "__main__":
    unittest.main()
