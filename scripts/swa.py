#!/usr/bin/env python3
"""Search Waste Analyzer - find search terms with clicks but zero conversions.

Read-only helper for the `search-waste-analyzer` skill. Standard library only
(openpyxl is optional, for Excel output).

Commands
    ranges     print the 7/15/30/90 completed-day date ranges
    analyze    read exports/connector data, write results.json
    dashboard  turn results.json into one self-contained HTML dashboard
    xlsx       turn results.json into an Excel workbook (or CSV files)
    demo       write synthetic DEMO DATA as a CSV

The script never talks to an advertising platform. It only reads files.
"""
from __future__ import annotations

import argparse
import csv
import io
import json
import random
import re
import sys
from collections import OrderedDict, defaultdict
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path

PERIOD_DAYS = (7, 15, 30, 90)
UNAVAILABLE = "Unavailable"
GOOGLE = "Google Ads"
MICROSOFT = "Microsoft Advertising"
TWO = Decimal("0.01")

# ---------------------------------------------------------------------------
# Date ranges
# ---------------------------------------------------------------------------


def today_in_tz(tz_name: str | None, override: str | None = None) -> date:
    """Today's date in the given timezone (or the override, for tests)."""
    if override:
        return date.fromisoformat(override)
    try:
        from zoneinfo import ZoneInfo

        return datetime.now(ZoneInfo(tz_name or "UTC")).date()
    except Exception:
        return datetime.now(timezone.utc).date()


def period_range(today: date, days: int) -> tuple[date, date]:
    """Completed days only: ends yesterday, covers `days` days."""
    end = today - timedelta(days=1)
    return end - timedelta(days=days - 1), end


def all_ranges(today: date) -> dict[int, tuple[date, date]]:
    return {d: period_range(today, d) for d in PERIOD_DAYS}


# ---------------------------------------------------------------------------
# Reading data
# ---------------------------------------------------------------------------

SYNONYMS = {
    "search_term": ["search term", "search terms", "search query", "search term view search term",
                    "query", "suchbegriff", "suchbegriffe", "suchanfrage"],
    "keyword": ["keyword", "keyword text", "search keyword", "targeted keyword", "triggering keyword",
                "segments keyword info text", "keyword info text"],
    "keyword_match_type": ["keyword match type", "keyword type", "bid match type",
                           "segments keyword info match type", "keyword info match type"],
    "search_term_match_type": ["search term match type", "delivered match type", "match type",
                               "segments search term match type", "übereinstimmungstyp"],
    "clicks": ["clicks", "metrics clicks", "klicks"],
    "impressions": ["impressions", "impr", "metrics impressions", "impressionen"],
    "cost": ["cost", "spend", "metrics cost micros", "cost micros", "kosten"],
    "conversions": ["conversions", "metrics conversions", "conversions qualified"],
    "campaign": ["campaign", "campaign name", "kampagne"],
    "campaign_type": ["campaign type", "campaign advertising channel type", "advertising channel type",
                      "kampagnentyp"],
    "ad_group": ["ad group", "ad group name", "adgroup", "anzeigengruppe"],
    "account_name": ["account name", "account", "customer name", "customer descriptive name",
                     "kontoname", "konto"],
    "account_id": ["account id", "account number", "customer id", "customer account number",
                   "konto id", "kundennummer"],
    "currency": ["currency", "currency code", "customer currency code", "währung"],
    "timezone": ["timezone", "time zone", "customer time zone", "zeitzone"],
    "platform": ["platform"],
    "date": ["day", "date", "segments date", "tag", "datum"],
    "period": ["period", "period days"],
    "term_status": ["added excluded", "search term view status", "term status"],
    "is_demo": ["is demo"],
    "source_query": ["source query"],
}


def norm_header(h) -> str:
    s = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", " ", str(h))
    return re.sub(r"[^a-z0-9äöüß]+", " ", s.lower()).strip()


def flatten(d: dict, prefix: str = "") -> dict:
    out = {}
    for k, v in d.items():
        key = f"{prefix} {k}".strip()
        if isinstance(v, dict):
            out.update(flatten(v, key))
        else:
            out[key] = v
    return out


def parse_decimal(v, integer: bool = False):
    """Parse numbers from US or European exports. Returns None when missing."""
    if v is None:
        return None
    if isinstance(v, Decimal):
        return v
    if isinstance(v, bool):
        return Decimal(int(v))
    if isinstance(v, (int, float)):
        return Decimal(str(v))
    s = str(v).strip().replace("\xa0", " ")
    if s in ("", "--", "—", "-", "n/a", "N/A", "null", "None") or s[0] in "<>":
        return None
    s = re.sub(r"[^\d,.\-]", "", s)
    if not s or s == "-":
        return None
    if "," in s and "." in s:
        if s.rfind(",") > s.rfind("."):
            s = s.replace(".", "").replace(",", ".")
        else:
            s = s.replace(",", "")
    elif "," in s:
        if re.fullmatch(r"-?\d{1,3}(,\d{3})+", s):
            s = s.replace(",", "")
        else:
            s = s.replace(",", ".")
    elif "." in s and integer and re.fullmatch(r"-?\d{1,3}(\.\d{3})+", s):
        s = s.replace(".", "")
    try:
        return Decimal(s)
    except InvalidOperation:
        return None


def parse_date(v):
    if v is None:
        return None
    if isinstance(v, date):
        return v
    s = str(v).strip()
    for fmt in ("%Y-%m-%d", "%d.%m.%Y", "%Y/%m/%d", "%b %d, %Y", "%B %d, %Y", "%m/%d/%Y"):
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            continue
    return None


def parse_period(v):
    if v is None:
        return None
    m = re.search(r"\d+", str(v))
    return int(m.group()) if m else None


def norm_match_type(v) -> str:
    s = str(v or "").strip().lower()
    if not s or s in ("--", "-", "unavailable"):
        return UNAVAILABLE
    if "exact" in s:
        return "Exact"
    if "phrase" in s:
        return "Phrase"
    if "broad" in s:
        return "Broad"
    return str(v).strip()


def norm_platform(v, default=None) -> str:
    s = str(v or "").strip().lower()
    if "microsoft" in s or "bing" in s:
        return MICROSOFT
    if "google" in s:
        return GOOGLE
    return default or UNAVAILABLE


def read_text(path: Path) -> str:
    raw = path.read_bytes()
    if raw.startswith((b"\xff\xfe", b"\xfe\xff")):
        return raw.decode("utf-16")
    for enc in ("utf-8-sig", "cp1252"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode("latin-1")


def _is_header(cells) -> bool:
    n = {norm_header(c) for c in cells}
    return bool(n & set(SYNONYMS["search_term"])) and bool(n & set(SYNONYMS["clicks"]))


def load_csv_rows(path: Path) -> list[dict]:
    """Read a Google Ads / Microsoft export. Skips report titles and total rows."""
    lines = read_text(path).splitlines()
    for i, line in enumerate(lines):
        for delim in ("\t", ";", ","):
            cells = next(csv.reader([line], delimiter=delim), [])
            if len(cells) > 2 and _is_header(cells):
                reader = csv.reader(lines[i + 1:], delimiter=delim)
                headers = [norm_header(c) for c in cells]
                rows = []
                for r in reader:
                    if not any(c.strip() for c in r):
                        continue
                    first = r[0].strip().lower()
                    if first.startswith(("total", "gesamt")):
                        continue
                    row = {}
                    for h, v in zip(headers, r):
                        row.setdefault(h, v)
                    rows.append(row)
                return rows
    raise SystemExit(f"{path.name}: could not find a header row with a search term column and a clicks column.")


def load_json_rows(path: Path) -> list[dict]:
    data = json.loads(read_text(path))
    if isinstance(data, dict):
        for v in data.values():
            if isinstance(v, list):
                data = v
                break
    if not isinstance(data, list):
        raise SystemExit(f"{path.name}: expected a list of rows.")
    rows = []
    for item in data:
        if isinstance(item, dict):
            rows.append({norm_header(k): v for k, v in flatten(item).items()})
    return rows


def load_rows(path: Path) -> list[dict]:
    if path.suffix.lower() == ".json":
        return load_json_rows(path)
    return load_csv_rows(path)


def build_mapping(headers) -> dict:
    present = list(headers)
    mapping = {}
    for field, names in SYNONYMS.items():
        for name in names:
            if name in present:
                mapping[field] = name
                break
    return mapping


def build_records(rows: list[dict], defaults: dict, period=None):
    """Normalize raw rows into records. Returns (records, stats)."""
    headers = set()
    for r in rows:
        headers.update(r.keys())
    m = build_mapping(headers)
    stats = {"rows_read": len(rows), "skipped_no_search_term": 0, "skipped_no_clicks_value": 0,
             "columns_used": {k: v for k, v in m.items()}}
    if "search_term" not in m or "clicks" not in m:
        raise SystemExit("Need at least a search term column and a clicks column.")
    if "cost" not in m:
        raise SystemExit("Need a cost/spend column.")
    if "conversions" not in m:
        raise SystemExit("Need a conversions column. Without it we cannot know which terms had zero conversions.")
    micros = "micros" in m["cost"]
    records = []
    for r in rows:
        term = str(r.get(m["search_term"], "") or "").strip()
        if not term or term.lower().startswith("total"):
            stats["skipped_no_search_term"] += 1
            continue
        clicks = parse_decimal(r.get(m["clicks"]), integer=True)
        if clicks is None:
            stats["skipped_no_clicks_value"] += 1
            continue

        def g(field, default=""):
            return r.get(m[field], default) if field in m else default

        cost = parse_decimal(g("cost", None))
        if cost is not None and micros:
            cost = cost / Decimal(1000000)
        demo_flag = str(g("is_demo", "")).strip().lower() in ("1", "true", "yes")
        rec = {
            "platform": norm_platform(g("platform") or defaults.get("platform"), defaults.get("platform")),
            "account_id": str(g("account_id") or defaults.get("account_id") or "").strip(),
            "account_name": str(g("account_name") or defaults.get("account_name") or "").strip(),
            "currency": str(g("currency") or defaults.get("currency") or "").strip(),
            "timezone": str(g("timezone") or defaults.get("timezone") or "").strip(),
            "campaign": str(g("campaign")).strip(),
            "campaign_type": str(g("campaign_type")).strip(),
            "ad_group": str(g("ad_group")).strip(),
            "search_term": term,
            "search_term_match_type": norm_match_type(g("search_term_match_type")),
            "keyword": str(g("keyword")).strip(),
            "keyword_match_type": norm_match_type(g("keyword_match_type")),
            "term_status": str(g("term_status")).strip(),
            "impressions": parse_decimal(g("impressions", None), integer=True),
            "clicks": clicks,
            "cost": cost,
            "conversions": parse_decimal(g("conversions", None)),
            "date": parse_date(g("date", None)),
            "period": parse_period(g("period", None)) or period,
            "source_query": str(g("source_query")).strip().lower(),
            "is_demo": demo_flag or bool(defaults.get("demo")),
        }
        if rec["keyword"] in ("--", "-"):
            rec["keyword"] = ""
        records.append(rec)
    return records, stats


# ---------------------------------------------------------------------------
# Analysis
# ---------------------------------------------------------------------------


def _add(a, b):
    """Sum that stays None if either side is unknown."""
    if a is None or b is None:
        return None
    return a + b


def _is_pmax(campaign_type: str) -> bool:
    t = (campaign_type or "").lower().replace("_", " ")
    return "performance max" in t or "pmax" in t


def _keyword_info(keyword, kw_mt, campaign_type):
    if keyword:
        return keyword, kw_mt if kw_mt else UNAVAILABLE, ""
    if _is_pmax(campaign_type):
        return UNAVAILABLE, UNAVAILABLE, "Performance Max campaigns have no keywords."
    return UNAVAILABLE, UNAVAILABLE, "The platform did not return a triggering keyword for this row."


def _merge_two_queries(recs):
    """If rows are tagged source_query = totals / keywords, keep every term.

    Keyword rows are used where they exist. Whatever the totals query counted
    beyond them becomes one extra row with the keyword 'Unavailable'.
    """
    if not any(r["source_query"] in ("totals", "keywords") for r in recs):
        return recs, 0
    groups = defaultdict(lambda: {"totals": [], "keywords": [], "other": []})
    for r in recs:
        kind = r["source_query"] if r["source_query"] in ("totals", "keywords") else "other"
        key = (r["campaign"], r["ad_group"], r["search_term"].lower(), r.get("_label"))
        groups[key][kind].append(r)
    out, negatives = [], 0
    for g in groups.values():
        out.extend(g["other"])
        if g["keywords"]:
            out.extend(g["keywords"])
            if g["totals"]:
                t = g["totals"][0]
                res = dict(t)
                res["keyword"], res["keyword_match_type"] = "", UNAVAILABLE
                for f in ("clicks", "cost", "impressions", "conversions"):
                    tot = sum((x[f] for x in g["totals"]), Decimal(0)) if all(x[f] is not None for x in g["totals"]) else None
                    kw = sum((x[f] for x in g["keywords"]), Decimal(0)) if all(x[f] is not None for x in g["keywords"]) else None
                    if tot is None or kw is None:
                        res[f] = None
                    else:
                        diff = tot - kw
                        if diff < 0:
                            negatives += 1
                            diff = Decimal(0)
                        res[f] = diff
                if (res["clicks"] or 0) > 0 or (res["cost"] or 0) > 0:
                    out.append(res)
        else:
            out.extend(g["totals"])
    return out, negatives


def _aggregate(recs):
    agg = OrderedDict()
    for r in recs:
        k = (r["campaign"], r["ad_group"], r["search_term"].lower(), r["keyword"].lower(), r["keyword_match_type"])
        a = agg.get(k)
        if a is None:
            a = dict(r)
            a["_stm"] = defaultdict(Decimal)
            agg[k] = a
        else:
            for f in ("clicks", "cost", "impressions", "conversions"):
                a[f] = _add(a[f], r[f])
        a["_stm"][r["search_term_match_type"]] += r["clicks"] or 0
        if r["term_status"]:
            a["term_status"] = r["term_status"]
    for a in agg.values():
        stm = a.pop("_stm")
        top = max(stm.items(), key=lambda kv: kv[1])[0] if stm else UNAVAILABLE
        a["search_term_match_type"] = top
    return list(agg.values())


def _num(d, places=2):
    return None if d is None else float(d.quantize(Decimal(10) ** -places))


def analyze(records: list[dict], today: date | None, default_tz: str = "UTC", min_clicks: int = 1,
            period_ranges_override: dict | None = None, meta: dict | None = None) -> dict:
    """Apply Clicks > 0 AND Conversions = 0, separately per account and period."""
    accounts = OrderedDict()
    for r in records:
        key = (r["platform"], r["account_id"], r["account_name"])
        accounts.setdefault(key, []).append(r)

    warnings = []
    result_accounts = []
    for (platform, account_id, account_name), recs in accounts.items():
        tz = next((r["timezone"] for r in recs if r["timezone"]), "") or default_tz
        acct_today = today if today else today_in_tz(tz)
        ranges = all_ranges(acct_today)
        currencies = sorted({r["currency"] for r in recs if r["currency"]})
        is_demo = any(r["is_demo"] for r in recs)
        by_period = defaultdict(list)
        outside = 0
        for r in recs:
            if r["date"]:
                labels = [str(d) for d in PERIOD_DAYS if ranges[d][0] <= r["date"] <= ranges[d][1]]
                if not labels:
                    outside += 1
            elif r["period"]:
                labels = [str(r["period"])]
            else:
                labels = ["custom"]
            for lab in labels:
                rr = dict(r)
                rr["_label"] = lab
                by_period[lab].append(rr)

        labels_out = [str(d) for d in PERIOD_DAYS] + [l for l in by_period if l not in map(str, PERIOD_DAYS)]
        merged = {}
        term_conv = {}
        for lab in labels_out:
            recs_p = by_period.get(lab, [])
            recs_p, neg = _merge_two_queries(recs_p)
            if neg:
                warnings.append(f"{account_name}: keyword query counted more than the totals query for some terms ({neg}); extra clicks were ignored.")
            rows_p = _aggregate(recs_p)
            merged[lab] = rows_p
            tc = defaultdict(lambda: Decimal(0))
            unknown = set()
            for a in rows_p:
                t = a["search_term"].lower()
                if a["conversions"] is None:
                    unknown.add(t)
                else:
                    tc[t] += a["conversions"]
            term_conv[lab] = (tc, unknown)

        periods_out = OrderedDict()
        for lab in labels_out:
            rows_p = merged[lab]
            if lab == "custom":
                rng = (None, None)
            else:
                days = int(lab)
                rng = period_range(acct_today, days)
            waste, unknown_conv, kw_clicks, all_clicks = [], 0, Decimal(0), Decimal(0)
            tc, unk = term_conv[lab]
            for a in rows_p:
                if a["clicks"] is None or a["clicks"] < min_clicks:
                    continue
                if a["conversions"] is None:
                    unknown_conv += 1
                    continue
                if a["conversions"] != 0:
                    continue
                kw, kwmt, why = _keyword_info(a["keyword"], a["keyword_match_type"], a["campaign_type"])
                all_clicks += a["clicks"]
                if kw != UNAVAILABLE:
                    kw_clicks += a["clicks"]
                t = a["search_term"].lower()
                by = {}
                for l2 in labels_out:
                    tc2, unk2 = term_conv[l2]
                    has_term = any(x["search_term"].lower() == t for x in merged[l2])
                    by[l2] = None if (not has_term or t in unk2) else _num(tc2[t])
                others = [
                    {"keyword": _keyword_info(x["keyword"], x["keyword_match_type"], x["campaign_type"])[0],
                     "ad_group": x["ad_group"], "clicks": _num(x["clicks"], 0),
                     "conversions": _num(x["conversions"])}
                    for x in rows_p
                    if x["search_term"].lower() == t and x is not a
                ]
                waste.append({
                    "account_name": account_name or UNAVAILABLE,
                    "account_id": account_id or UNAVAILABLE,
                    "search_term": a["search_term"],
                    "keyword": kw,
                    "keyword_match_type": kwmt,
                    "clicks": int(a["clicks"]),
                    "spend": _num(a["cost"]),
                    "conversions": 0,
                    "platform": platform,
                    "details": {
                        "campaign": a["campaign"] or UNAVAILABLE,
                        "ad_group": a["ad_group"] or UNAVAILABLE,
                        "campaign_type": a["campaign_type"] or UNAVAILABLE,
                        "impressions": _num(a["impressions"], 0),
                        "avg_cpc": _num(a["cost"] / a["clicks"]) if a["cost"] is not None and a["clicks"] else None,
                        "search_term_match_type": a["search_term_match_type"],
                        "term_status": a["term_status"] or UNAVAILABLE,
                        "keyword_note": why,
                        "same_term_other_rows": others[:10],
                        "term_conversions_by_period": by,
                    },
                })
            waste.sort(key=lambda r: (r["spend"] is None, -(r["spend"] or 0)))
            spend_total = sum((Decimal(str(r["spend"])) for r in waste if r["spend"] is not None), Decimal(0))
            periods_out[lab] = {
                "label": f"{lab} Days" if lab != "custom" else "Custom period",
                "date_start": rng[0].isoformat() if rng[0] else None,
                "date_end": rng[1].isoformat() if rng[1] else None,
                "has_data": bool(rows_p),
                "rows": waste,
                "summary": {
                    "terms": len(waste),
                    "clicks": int(sum(r["clicks"] for r in waste)),
                    "spend": _num(spend_total),
                    "keyword_coverage_pct": round(float(kw_clicks / all_clicks * 100), 1) if all_clicks else None,
                    "rows_with_unknown_conversions": unknown_conv,
                },
            }
            if unknown_conv:
                warnings.append(f"{account_name} / {lab}: {unknown_conv} rows had clicks but no conversion value and were NOT counted as zero.")
        if len(currencies) > 1:
            warnings.append(f"{account_name}: more than one currency in the data ({', '.join(currencies)}). Spend is shown as returned.")
        if outside:
            warnings.append(f"{account_name}: {outside} rows were outside the four completed-day periods (for example today) and were ignored.")
        result_accounts.append({
            "platform": platform,
            "account_id": account_id or UNAVAILABLE,
            "account_name": account_name or UNAVAILABLE,
            "currency": currencies[0] if currencies else "",
            "timezone": tz,
            "today_used": acct_today.isoformat(),
            "is_demo": is_demo,
            "periods": periods_out,
        })
        if not any(r["timezone"] for r in recs):
            warnings.append(f"{account_name}: no timezone in the data, so '{default_tz}' was used to decide what yesterday is.")

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "today": (today or today_in_tz(default_tz)).isoformat(),
        "is_demo": any(a["is_demo"] for a in result_accounts),
        "rule": "Clicks > 0 AND Conversions = 0, applied per account, platform and period",
        "caveats": [
            "Zero conversions applies only to the selected period. A term can convert in a longer period.",
            "Recent days can still receive conversions later (conversion lag).",
            "Google Ads hides some low-volume search terms, so listed terms may not add up to full campaign spend.",
            "A keyword is shown only when the platform returned it. Otherwise it says Unavailable.",
        ],
        "warnings": sorted(set(warnings)),
        "meta": meta or {},
        "accounts": result_accounts,
    }


# ---------------------------------------------------------------------------
# Output: dashboard, Excel / CSV
# ---------------------------------------------------------------------------

COLUMNS = ["Account Name", "Account ID", "Search Term", "Triggering Keyword", "Keyword Match Type",
           "Clicks", "Spend", "Conversions", "Platform"]


def write_dashboard(results: dict, out: Path) -> None:
    tpl = (Path(__file__).parent / "dashboard_template.html").read_text(encoding="utf-8")
    payload = json.dumps(results, ensure_ascii=False).replace("</", "<\\/").replace("\u2028", "\\u2028").replace("\u2029", "\\u2029")
    out.write_text(tpl.replace("/*__DATA__*/null", payload), encoding="utf-8")


def _row_values(r: dict):
    return [r["account_name"], r["account_id"], r["search_term"], r["keyword"], r["keyword_match_type"],
            r["clicks"], r["spend"], r["conversions"], r["platform"]]


def write_xlsx(results: dict, out: Path, account_id: str | None = None, fmt: str = "xlsx") -> list[Path]:
    accounts = [a for a in results["accounts"] if not account_id or a["account_id"] == account_id]
    if not accounts:
        raise SystemExit("No matching account in results.")
    labels = [str(d) for d in PERIOD_DAYS]
    demo = any(a["is_demo"] for a in accounts)
    if fmt == "csv":
        out.mkdir(parents=True, exist_ok=True)
        written = []
        for lab in labels:
            p = out / f"{lab}-days.csv"
            with p.open("w", newline="", encoding="utf-8-sig") as fh:
                w = csv.writer(fh)
                w.writerow(COLUMNS)
                for a in accounts:
                    for r in a["periods"].get(lab, {}).get("rows", []):
                        w.writerow(_row_values(r))
            written.append(p)
        return written
    try:
        from openpyxl import Workbook
        from openpyxl.styles import Alignment, Font, PatternFill
        from openpyxl.utils import get_column_letter
    except ImportError:
        raise SystemExit("openpyxl is not installed. Re-run the xlsx command with --format csv.")
    wb = Workbook()
    ov = wb.active
    ov.title = "Overview"
    bold = Font(bold=True)
    ov.append(["Search Waste Analyzer - zero-conversion search terms"])
    ov["A1"].font = Font(bold=True, size=14)
    if demo:
        ov.append(["DEMO DATA - not real advertising results"])
        ov["A2"].font = Font(bold=True, color="C00000")
    ov.append([f"Report date: {results['today']}. Each period covers completed days only and is analyzed on its own."])
    ov.append(["Zero conversions applies to the selected period only; a term can convert in a longer period."])
    ov.append([])
    ov.append(["Account Name", "Account ID", "Platform", "Period", "Date range", "Terms", "Clicks", "Spend", "Currency"])
    for c in ov[ov.max_row]:
        c.font = bold
    for a in accounts:
        for lab in labels:
            p = a["periods"].get(lab)
            if not p:
                continue
            ov.append([a["account_name"], a["account_id"], a["platform"], p["label"],
                       f"{p['date_start']} to {p['date_end']}", p["summary"]["terms"], p["summary"]["clicks"],
                       p["summary"]["spend"], a["currency"]])
    for i, wdt in enumerate([34, 18, 22, 10, 26, 8, 8, 12, 9], 1):
        ov.column_dimensions[get_column_letter(i)].width = wdt
    for lab in labels:
        ws = wb.create_sheet(f"{lab} Days")
        first = next((a["periods"][lab] for a in accounts if lab in a["periods"]), None)
        rng = f"{first['date_start']} to {first['date_end']}" if first else ""
        ws.append([f"{lab} Days ({rng}) - zero conversions in this period only"])
        ws["A1"].font = Font(bold=True, size=12)
        ws.append(["DEMO DATA - not real advertising results" if demo else ""])
        ws["A2"].font = Font(bold=True, color="C00000")
        ws.append([])
        ws.append(COLUMNS)
        for c in ws[4]:
            c.font = bold
            c.fill = PatternFill("solid", fgColor="E8EEF7")
        for a in accounts:
            for r in a["periods"].get(lab, {}).get("rows", []):
                ws.append(_row_values(r))
        ws.freeze_panes = "A5"
        ws.auto_filter.ref = f"A4:I{max(ws.max_row, 5)}"
        for i, wdt in enumerate([32, 16, 44, 30, 14, 8, 10, 12, 22], 1):
            ws.column_dimensions[get_column_letter(i)].width = wdt
        for row in ws.iter_rows(min_row=5):
            row[6].number_format = "#,##0.00"
    out.parent.mkdir(parents=True, exist_ok=True)
    wb.save(out)
    return [out]


# ---------------------------------------------------------------------------
# Demo data (synthetic, clearly labelled)
# ---------------------------------------------------------------------------

GOOD_MODS = ["best", "pricing", "for small business", "demo", "buy", "quote", "compare plans", "near me"]
BAD_MODS = ["free", "jobs", "tutorial", "download", "open source", "template", "pdf", "course", "salary", "login"]

DEMO_ACCOUNTS = [
    {"platform": GOOGLE, "name": "[DEMO] Northwind Accounting Software", "id": "111-111-1111", "cur": "EUR",
     "tz": "Europe/Berlin", "terms": 70, "cpc": (0.8, 2.6), "fractional": False,
     "keywords": [("accounting software", "Phrase"), ("invoice software", "Exact"), ("bookkeeping app", "Broad"),
                  ("freelancer invoicing", "Phrase")], "dsa": True},
    {"platform": GOOGLE, "name": "[DEMO] Peak Fitness Gear Shop", "id": "222-222-2222", "cur": "USD",
     "tz": "America/New_York", "terms": 60, "cpc": (0.3, 1.4), "fractional": True,
     "keywords": [("adjustable dumbbells", "Exact"), ("home gym equipment", "Phrase"), ("kettlebell set", "Broad")]},
    {"platform": MICROSOFT, "name": "[DEMO] Harbor & Finch Law", "id": "F333333", "cur": "GBP",
     "tz": "Europe/London", "terms": 45, "cpc": (2.5, 7.5), "fractional": False,
     "keywords": [("employment solicitor", "Phrase"), ("tenancy dispute lawyer", "Exact"), ("will writing service", "Broad")]},
    {"platform": GOOGLE, "name": "[DEMO] Bluebird Travel Marketplace (large)", "id": "444-444-4444", "cur": "EUR",
     "tz": "Europe/Berlin", "terms": 150, "cpc": (0.2, 1.1), "fractional": True, "pmax": True,
     "keywords": [("city break deals", "Phrase"), ("cheap flights", "Broad"), ("weekend hotel offers", "Phrase"),
                  ("beach holiday packages", "Exact"), ("train tickets europe", "Phrase")]},
    {"platform": MICROSOFT, "name": "[DEMO] Orbit CRM for Teams", "id": "F555555", "cur": "USD",
     "tz": "America/Los_Angeles", "terms": 40, "cpc": (1.5, 5.0), "fractional": False,
     "keywords": [("crm software", "Phrase"), ("sales pipeline tool", "Broad"), ("customer database software", "Exact")]},
]


def _poisson(rng: random.Random, lam: float) -> int:
    import math
    limit, k, p = math.exp(-lam), 0, 1.0
    while True:
        p *= rng.random()
        if p <= limit:
            return k
        k += 1


def generate_demo(today: date, seed: int = 7) -> list[dict]:
    rng = random.Random(seed)
    rows = []
    days = [today - timedelta(days=i) for i in range(0, 91)]  # includes today (incomplete) on purpose
    for acc in DEMO_ACCOUNTS:
        google = acc["platform"] == GOOGLE
        terms = []
        kws = acc["keywords"]
        i = 0
        while len(terms) < acc["terms"]:
            kw, kwmt = kws[i % len(kws)]
            i += 1
            bad = rng.random() < 0.45
            mod = rng.choice(BAD_MODS if bad else GOOD_MODS)
            extra = rng.choice(["", "", " uk", " 2026", " reviews", " small team", " for beginners"])
            text = f"{mod} {kw}{extra}".strip()
            if any(t["text"] == text and t["kw"] == kw for t in terms):
                continue
            if bad:
                cvr = 0.0 if rng.random() < 0.85 else 0.02
            else:
                cvr = rng.choice([0.0, 0.0, 0.03, 0.06, 0.1])
            lam = rng.choice([0.02, 0.04, 0.08, 0.15, 0.3, 0.6, 1.0])
            terms.append({"text": text, "kw": kw, "kwmt": kwmt, "campaign": f"Search - {kw.title()}",
                          "adgroup": kw.title(), "cvr": cvr, "lam": lam, "ctype": "SEARCH"})
        # same term under a second keyword, converts in only one of them
        for t in rng.sample(terms[:30], 3):
            kw2, mt2 = rng.choice([k for k in kws if k[0] != t["kw"]])
            terms.append({**t, "kw": kw2, "kwmt": mt2, "campaign": f"Search - {kw2.title()}",
                          "adgroup": kw2.title(), "cvr": 0.0 if t["cvr"] > 0 else 0.08, "lam": max(t["lam"], 0.4)})
        if acc.get("dsa"):
            for n in range(6):
                terms.append({"text": f"accounting software {rng.choice(['guide','compare','small firm','vat','payroll','uk'])} {n}",
                              "kw": "", "kwmt": "", "campaign": "DSA - Website", "adgroup": "All pages",
                              "cvr": 0.0, "lam": 0.5, "ctype": "DYNAMIC_SEARCH_ADS"})
        if acc.get("pmax"):
            for n in range(16):
                terms.append({"text": f"{rng.choice(['last minute','cheap','family','luxury','ski','city'])} trip {rng.choice(['lisbon','rome','vienna','prague','crete'])} {n}",
                              "kw": "", "kwmt": "", "campaign": "PMax - All destinations", "adgroup": "",
                              "cvr": rng.choice([0.0, 0.0, 0.05]), "lam": 0.7, "ctype": "PERFORMANCE_MAX"})
        for t in terms:
            cpc = rng.uniform(*acc["cpc"])
            term_status = rng.choice(["None", "None", "None", "Added", "Excluded"]) if google else ""
            stm = rng.choice(["Exact (close variant)", "Phrase (close variant)", "Broad"]) if google else rng.choice(["Exact", "Phrase", "Broad"])
            for d in days:
                clicks = _poisson(rng, t["lam"])
                if d == today and rng.random() < 0.5:
                    clicks += 1  # incomplete day: must be ignored by the analysis
                impressions = clicks * rng.randint(12, 40) + (rng.randint(0, 6) if rng.random() < 0.15 else 0)
                if impressions == 0 or (clicks == 0 and rng.random() > 0.02):
                    continue
                cost = round(sum(cpc * rng.uniform(0.7, 1.3) for _ in range(clicks)), 2)
                conv = 0.0
                for _ in range(clicks):
                    if rng.random() < t["cvr"]:
                        conv += round(rng.uniform(0.2, 1.0), 2) if acc["fractional"] else 1
                rows.append({
                    "date": d.isoformat(), "platform": acc["platform"], "account_name": acc["name"],
                    "account_id": acc["id"], "currency": acc["cur"], "timezone": acc["tz"],
                    "campaign": t["campaign"], "campaign_type": t["ctype"], "ad_group": t["adgroup"],
                    "search_term": t["text"], "search_term_match_type": stm, "keyword": t["kw"],
                    "keyword_match_type": t["kwmt"], "term_status": term_status, "impressions": impressions,
                    "clicks": clicks, "cost": f"{cost:.2f}", "conversions": f"{round(conv, 2):g}", "is_demo": "true",
                })
    return rows


DEMO_FIELDS = ["date", "platform", "account_name", "account_id", "currency", "timezone", "campaign", "campaign_type",
               "ad_group", "search_term", "search_term_match_type", "keyword", "keyword_match_type", "term_status",
               "impressions", "clicks", "cost", "conversions", "is_demo"]


# ---------------------------------------------------------------------------
# Command line
# ---------------------------------------------------------------------------


def cmd_ranges(a):
    today = today_in_tz(a.tz, a.today)
    print(f"Reporting date (today in {a.tz}): {today}")
    for d, (s, e) in all_ranges(today).items():
        print(f"{d:>2} days: {s} to {e}")


def cmd_analyze(a):
    defaults = {"platform": norm_platform(a.platform) if a.platform else None, "account_name": a.account_name,
                "account_id": a.account_id, "currency": a.currency, "timezone": a.tz_data, "demo": a.demo}
    records, all_stats = [], []
    for item in a.inputs:
        m = re.match(r"^(\d+):(.+)$", item)
        period, path = (int(m.group(1)), Path(m.group(2))) if m else (None, Path(item))
        if not path.exists():
            raise SystemExit(f"File not found: {path}")
        recs, stats = build_records(load_rows(path), defaults, period)
        stats["file"] = path.name
        all_stats.append(stats)
        records.extend(recs)
    if not records:
        raise SystemExit("No usable rows found.")
    for r in records:
        if not r["account_name"] and not r["account_id"]:
            raise SystemExit("Rows have no account. Pass --account-name and --account-id (and --platform).")
        if r["platform"] == UNAVAILABLE:
            raise SystemExit("Platform unknown. Pass --platform google or --platform microsoft.")
    today = date.fromisoformat(a.today) if a.today else None
    res = analyze(records, today, default_tz=a.tz, min_clicks=a.min_clicks, meta={"inputs": all_stats})
    Path(a.out).write_text(json.dumps(res, ensure_ascii=False, indent=1), encoding="utf-8")
    for acc in res["accounts"]:
        print(f"{acc['account_name']} ({acc['platform']}, {acc['account_id']})" + ("  [DEMO DATA]" if acc["is_demo"] else ""))
        for lab, p in acc["periods"].items():
            s = p["summary"]
            print(f"  {p['label']:>12} {p['date_start']}..{p['date_end']}: {s['terms']} terms, {s['clicks']} clicks, "
                  f"{s['spend']} {acc['currency']} spend" + ("" if p["has_data"] else "  (no data supplied)"))
    for w in res["warnings"]:
        print("WARNING:", w)
    print(f"Wrote {a.out}")


def cmd_dashboard(a):
    res = json.loads(Path(a.results).read_text(encoding="utf-8"))
    write_dashboard(res, Path(a.out))
    print(f"Wrote {a.out}")


def cmd_xlsx(a):
    res = json.loads(Path(a.results).read_text(encoding="utf-8"))
    for p in write_xlsx(res, Path(a.out), a.account_id, a.format):
        print(f"Wrote {p}")


def cmd_demo(a):
    today = today_in_tz("Europe/Berlin", a.today)
    rows = generate_demo(today, a.seed)
    with open(a.out, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=DEMO_FIELDS)
        w.writeheader()
        w.writerows(rows)
    print(f"Wrote {len(rows)} DEMO DATA rows to {a.out}")


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("ranges")
    s.add_argument("--today")
    s.add_argument("--tz", default="UTC")
    s.set_defaults(fn=cmd_ranges)
    s = sub.add_parser("analyze")
    s.add_argument("inputs", nargs="+", help="CSV/JSON files. Prefix with 7: 15: 30: or 90: if a file holds one period.")
    s.add_argument("--out", default="results.json")
    s.add_argument("--today")
    s.add_argument("--tz", default="UTC", help="Timezone used to decide 'yesterday' when the data has none.")
    s.add_argument("--tz-data", default=None, help="Timezone of the account when the file has no timezone column.")
    s.add_argument("--platform")
    s.add_argument("--account-name")
    s.add_argument("--account-id")
    s.add_argument("--currency")
    s.add_argument("--min-clicks", type=int, default=1)
    s.add_argument("--demo", action="store_true", help="Label the data as DEMO DATA.")
    s.set_defaults(fn=cmd_analyze)
    s = sub.add_parser("dashboard")
    s.add_argument("results")
    s.add_argument("--out", default="dashboard.html")
    s.set_defaults(fn=cmd_dashboard)
    s = sub.add_parser("xlsx")
    s.add_argument("results")
    s.add_argument("--out", default="search-waste-report.xlsx")
    s.add_argument("--account-id")
    s.add_argument("--format", choices=["xlsx", "csv"], default="xlsx")
    s.set_defaults(fn=cmd_xlsx)
    s = sub.add_parser("demo")
    s.add_argument("--out", default="demo-data.csv")
    s.add_argument("--today")
    s.add_argument("--seed", type=int, default=7)
    s.set_defaults(fn=cmd_demo)
    a = p.parse_args(argv)
    a.fn(a)


if __name__ == "__main__":
    main()
