# Search Waste Analyzer

An AI skill for Claude and ChatGPT that finds Google Ads and Microsoft Advertising **search terms with clicks but zero conversions**, shows which keyword triggered each one, and builds a dashboard and an Excel report.

- Rule: `Clicks > 0 AND Conversions = 0`, per account, platform and period
- Periods: last 7, 15, 30 and 90 **completed** days (today is never included)
- Shows the triggering keyword and its match type, or `Unavailable` when the platform does not provide it. It never guesses
- Keeps every advertising account separate
- Read-only: it never changes an advertising account

## Install

You need either Claude (a plan that includes custom skills with code execution turned on) or ChatGPT (a plan that includes skills). Download `search-waste-analyzer.zip` from the **Releases** page of this repository.

**Claude:** Settings, then Capabilities (or Features), find Skills, choose Upload skill, select the zip, and switch the skill on.

**ChatGPT:** open Skills, choose Create, then Upload from your computer, and select the zip. Skills in ChatGPT are not available on every plan, so check yours.

Custom skills are per user and do not sync between products, so upload it to each one you use. Read the files before you upload them, as you should with any skill that contains code. The only code is `scripts/swa.py`.

## Use

Try it first with the built-in demo: *"Use the Search Waste Analyzer with the demo data."* Everything in the demo is labelled DEMO DATA.

With real data, either:

1. **Connector:** if your Claude or ChatGPT has a Google Ads connector, say *"Run the Search Waste Analyzer for my Google Ads account Client A."* The skill queries the account read-only.
2. **Exports:** upload the Search terms report CSV (Google Ads) or Search query report CSV (Microsoft Advertising) and say which account and period it belongs to.

You get a dashboard file, an Excel report, and a short summary with the caveats.

## What it cannot do

- It cannot see search terms that Google hides for privacy reasons.
- Performance Max search terms have no keyword.
- Recent days can still receive conversions, so short periods overstate.
- Microsoft Advertising is supported through exports or a Microsoft connector. The Microsoft report column names are documented in `references/` but were not verified against a live account.
- The Google Ads queries in `references/google-ads-queries.md` come from Google's documentation and were not yet run against a live account. Compare one campaign's totals with Google Ads on your first run.

## Repository contents

| Path | Purpose |
| --- | --- |
| `SKILL.md` | The instructions the AI follows |
| `scripts/swa.py` | All calculations and file output (standard Python) |
| `scripts/dashboard_template.html` | The dashboard page |
| `references/` | Query examples, export guide, how to interpret results |
| `assets/sample-demo-data.csv` | Synthetic demo data |
| `tests/` | Automated tests |

Run the tests with `python3 -m unittest discover -s tests -v` (Python 3.9 or newer).

## Privacy

Search terms and account data are client data. When you use this skill, the data you give to Claude or ChatGPT is processed by that provider. Check your client agreements before using real accounts. No data is sent anywhere by the script itself.

## License

MIT
