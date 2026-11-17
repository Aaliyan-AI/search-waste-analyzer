"""Tests for scripts/swa.py. Run with:  python3 -m unittest discover -s tests -v"""
import json
import sys
import tempfile
import unittest
from datetime import date
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import swa  # noqa: E402

TODAY = date(2026, 11, 17)


def rec(**kw):
    base = dict(platform=swa.GOOGLE, account_id="1", account_name="A", currency="EUR", timezone="Europe/Berlin",
                campaign="C", campaign_type="SEARCH", ad_group="G", search_term="term", search_term_match_type="Broad",
                keyword="kw", keyword_match_type="Phrase", term_status="", impressions=Decimal(10),
                clicks=Decimal(5), cost=Decimal("3.50"), conversions=Decimal(0), date=date(2026, 11, 15),
                period=None, source_query="", is_demo=False)
    for k in ("clicks", "cost", "conversions", "impressions"):
        if k in kw and kw[k] is not None and not isinstance(kw[k], Decimal):
            kw[k] = Decimal(str(kw[k]))
    base.update(kw)
    return base


def rows_for(res, acct_id, period):
    acc = next(a for a in res["accounts"] if a["account_id"] == acct_id)
    return acc["periods"][period]["rows"]


class DateRanges(unittest.TestCase):
    def test_brief_example(self):
        r = swa.all_ranges(TODAY)
        self.assertEqual(r[7], (date(2026, 11, 10), date(2026, 11, 16)))
        self.assertEqual(r[15], (date(2026, 11, 2), date(2026, 11, 16)))
        self.assertEqual(r[30], (date(2026, 10, 18), date(2026, 11, 16)))
        self.assertEqual(r[90], (date(2026, 8, 19), date(2026, 11, 16)))

    def test_year_boundary(self):
        s, e = swa.period_range(date(2027, 1, 1), 7)
        self.assertEqual(e, date(2026, 12, 31))
        self.assertEqual(s, date(2026, 12, 25))

    def test_today_never_included(self):
        for d, (s, e) in swa.all_ranges(TODAY).items():
            self.assertLess(e, TODAY)
            self.assertEqual((e - s).days + 1, d)


class Numbers(unittest.TestCase):
    def test_formats(self):
        self.assertEqual(swa.parse_decimal("1,234.56"), Decimal("1234.56"))
        self.assertEqual(swa.parse_decimal("1.234,56"), Decimal("1234.56"))
        self.assertEqual(swa.parse_decimal("12,5"), Decimal("12.5"))
        self.assertEqual(swa.parse_decimal("€ 3,20"), Decimal("3.20"))
        self.assertEqual(swa.parse_decimal("1.234", integer=True), Decimal(1234))
        self.assertEqual(swa.parse_decimal("1,234", integer=True), Decimal(1234))
        self.assertIsNone(swa.parse_decimal("--"))
        self.assertIsNone(swa.parse_decimal("< 10"))
        self.assertIsNone(swa.parse_decimal(""))


class Filter(unittest.TestCase):
    def test_only_clicks_and_zero_conversions(self):
        recs = [
            rec(search_term="waste", clicks=4, conversions=0),
            rec(search_term="converts", clicks=4, conversions=1),
            rec(search_term="no clicks", clicks=0, conversions=0),
            rec(search_term="fractional", clicks=4, conversions="0.3"),
        ]
        res = swa.analyze(recs, TODAY)
        terms = [r["search_term"] for r in rows_for(res, "1", "7")]
        self.assertEqual(terms, ["waste"])

    def test_unknown_conversions_not_counted_as_zero(self):
        res = swa.analyze([rec(search_term="x", conversions=None)], TODAY)
        self.assertEqual(rows_for(res, "1", "7"), [])
        self.assertEqual(res["accounts"][0]["periods"]["7"]["summary"]["rows_with_unknown_conversions"], 1)

    def test_periods_are_independent(self):
        recs = [
            rec(search_term="slow", clicks=3, conversions=0, date=date(2026, 11, 15)),
            rec(search_term="slow", clicks=3, conversions=2, date=date(2026, 9, 20)),
        ]
        res = swa.analyze(recs, TODAY)
        self.assertEqual([r["search_term"] for r in rows_for(res, "1", "7")], ["slow"])
        self.assertEqual(rows_for(res, "1", "90"), [])  # converted within 90 days
        d = rows_for(res, "1", "7")[0]["details"]["term_conversions_by_period"]
        self.assertEqual(d["7"], 0)
        self.assertEqual(d["90"], 2)

    def test_today_is_ignored(self):
        recs = [rec(search_term="t", date=TODAY, clicks=9)]
        res = swa.analyze(recs, TODAY)
        for p in ("7", "15", "30", "90"):
            self.assertEqual(rows_for(res, "1", p), [])

    def test_daily_rows_are_summed(self):
        recs = [rec(date=date(2026, 11, 14), clicks=2, cost="1.00"), rec(date=date(2026, 11, 15), clicks=3, cost="2.00")]
        r = rows_for(swa.analyze(recs, TODAY), "1", "7")[0]
        self.assertEqual((r["clicks"], r["spend"]), (5, 3.0))


class Accounts(unittest.TestCase):
    def test_accounts_never_mixed(self):
        recs = [rec(account_id="1", account_name="A", search_term="same"),
                rec(account_id="2", account_name="B", search_term="same", platform=swa.MICROSOFT, cost="9")]
        res = swa.analyze(recs, TODAY)
        self.assertEqual(len(res["accounts"]), 2)
        for acc in res["accounts"]:
            for row in acc["periods"]["7"]["rows"]:
                self.assertEqual(row["account_id"], acc["account_id"])
        self.assertEqual(rows_for(res, "1", "7")[0]["spend"], 3.5)
        self.assertEqual(rows_for(res, "2", "7")[0]["spend"], 9.0)

    def test_same_id_on_two_platforms_stays_separate(self):
        recs = [rec(platform=swa.GOOGLE), rec(platform=swa.MICROSOFT)]
        self.assertEqual(len(swa.analyze(recs, TODAY)["accounts"]), 2)

    def test_nine_column_fields(self):
        row = rows_for(swa.analyze([rec()], TODAY), "1", "7")[0]
        for k in ("account_name", "account_id", "search_term", "keyword", "keyword_match_type", "clicks",
                  "spend", "conversions", "platform"):
            self.assertIn(k, row)


class Keywords(unittest.TestCase):
    def test_missing_keyword_is_unavailable(self):
        row = rows_for(swa.analyze([rec(keyword="", keyword_match_type="Unavailable")], TODAY), "1", "7")[0]
        self.assertEqual(row["keyword"], "Unavailable")
        self.assertEqual(row["keyword_match_type"], "Unavailable")
        self.assertTrue(row["details"]["keyword_note"])

    def test_pmax_note(self):
        row = rows_for(swa.analyze([rec(keyword="", campaign_type="PERFORMANCE_MAX")], TODAY), "1", "7")[0]
        self.assertIn("Performance Max", row["details"]["keyword_note"])

    def test_keyword_never_invented_from_search_term(self):
        row = rows_for(swa.analyze([rec(search_term="accounting software free", keyword="")], TODAY), "1", "7")[0]
        self.assertEqual(row["keyword"], "Unavailable")

    def test_two_query_merge_keeps_unmatched_clicks(self):
        recs = [
            rec(source_query="totals", clicks=10, cost="10", keyword="", keyword_match_type="Unavailable"),
            rec(source_query="keywords", clicks=6, cost="6", keyword="kw", keyword_match_type="Phrase"),
        ]
        rows = rows_for(swa.analyze(recs, TODAY), "1", "7")
        by_kw = {r["keyword"]: r["clicks"] for r in rows}
        self.assertEqual(by_kw, {"kw": 6, "Unavailable": 4})

    def test_two_query_merge_term_only_in_totals(self):
        recs = [rec(source_query="totals", clicks=4, keyword="")]
        rows = rows_for(swa.analyze(recs, TODAY), "1", "7")
        self.assertEqual([(r["keyword"], r["clicks"]) for r in rows], [("Unavailable", 4)])


class Files(unittest.TestCase):
    def test_google_style_csv_with_preamble_and_totals(self):
        text = ("Search terms report\nNovember 10, 2026 - November 16, 2026\n"
                "Search term,Match type,Campaign,Ad group,Clicks,Cost,Conversions\n"
                "free thing,Broad match,C1,G1,\"1,234\",\"1,500.25\",0.00\n"
                "good thing,Exact match,C1,G1,10,20.00,2.00\n"
                "Total: Search terms,,,,1244,1520.25,2.00\n")
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "x.csv"
            p.write_text(text, encoding="utf-8")
            recs, stats = swa.build_records(swa.load_rows(p), {"platform": swa.GOOGLE, "account_id": "9", "account_name": "Z"}, 7)
        self.assertEqual(len(recs), 2)
        self.assertEqual(recs[0]["clicks"], Decimal(1234))
        self.assertEqual(recs[0]["cost"], Decimal("1500.25"))
        self.assertEqual(recs[0]["period"], 7)
        self.assertEqual(recs[0]["keyword"], "")
        # the UI 'Match type' column is the search-term match type, never the keyword's
        self.assertEqual(recs[0]["keyword_match_type"], swa.UNAVAILABLE)
        self.assertEqual(recs[0]["search_term_match_type"], "Broad")

    def test_gaql_json_with_micros(self):
        data = [{"searchTermView": {"searchTerm": "abc"}, "campaign": {"name": "C"}, "adGroup": {"name": "G"},
                 "segments": {"keyword": {"info": {"text": "kw", "matchType": "PHRASE"}}},
                 "metrics": {"clicks": "7", "costMicros": "2500000", "conversions": 0}}]
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "x.json"
            p.write_text(json.dumps(data))
            recs, _ = swa.build_records(swa.load_rows(p), {"platform": swa.GOOGLE, "account_id": "9", "account_name": "Z"}, 30)
        self.assertEqual(recs[0]["cost"], Decimal("2.5"))
        self.assertEqual(recs[0]["keyword"], "kw")
        self.assertEqual(recs[0]["keyword_match_type"], "Phrase")

    def test_dashboard_data_cannot_break_out_of_script(self):
        res = swa.analyze([rec(search_term="</script><img src=x onerror=alert(1)>")], TODAY)
        with tempfile.TemporaryDirectory() as d:
            out = Path(d) / "d.html"
            swa.write_dashboard(res, out)
            html = out.read_text(encoding="utf-8")
        self.assertNotIn("</script><img", html)


class Demo(unittest.TestCase):
    def test_demo_data_properties(self):
        rows = swa.generate_demo(TODAY, 7)
        self.assertTrue(all(r["is_demo"] == "true" for r in rows))
        self.assertTrue(all(r["account_name"].startswith("[DEMO]") for r in rows))
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "demo.csv"
            import csv
            with p.open("w", newline="", encoding="utf-8") as fh:
                w = csv.DictWriter(fh, fieldnames=swa.DEMO_FIELDS)
                w.writeheader()
                w.writerows(rows)
            recs, _ = swa.build_records(swa.load_rows(p), {}, None)
        res = swa.analyze(recs, TODAY)
        self.assertTrue(res["is_demo"])
        self.assertEqual(len(res["accounts"]), 5)
        plats = {a["platform"] for a in res["accounts"]}
        self.assertEqual(plats, {swa.GOOGLE, swa.MICROSOFT})
        for acc in res["accounts"]:
            for p in acc["periods"].values():
                for r in p["rows"]:
                    self.assertGreater(r["clicks"], 0)
                    self.assertEqual(r["conversions"], 0)
                    self.assertEqual(r["account_id"], acc["account_id"])
        big = next(a for a in res["accounts"] if "large" in a["account_name"])
        self.assertGreaterEqual(big["periods"]["30"]["summary"]["terms"], 100)
        # demo contains keyword-less rows (Performance Max / DSA) and a term that converts only in a longer period
        kinds = [r["keyword"] for a in res["accounts"] for r in a["periods"]["90"]["rows"]]
        self.assertIn("Unavailable", kinds)

    def test_demo_is_deterministic(self):
        self.assertEqual(swa.generate_demo(TODAY, 7)[:50], swa.generate_demo(TODAY, 7)[:50])


if __name__ == "__main__":
    unittest.main()
