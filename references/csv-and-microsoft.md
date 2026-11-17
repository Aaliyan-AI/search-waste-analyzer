# Uploaded exports and Microsoft Advertising

## Google Ads CSV export

1. In Google Ads open Campaigns, then Insights and reports, then Search terms.
2. Set the date range to one of the periods from `python scripts/swa.py ranges`.
3. Add the columns Keyword (if offered), Clicks, Cost, Conversions, Currency code, Campaign and Ad group.
4. Download as CSV. Repeat for each period, or export with the Day segment and let the script cut the periods itself.

Run: `python scripts/swa.py analyze 30:file.csv --platform google --account-name "Client A" --account-id 123-456-7890 --tz-data Europe/Berlin`

Google Ads exports have no account column, so the account name and ID must come from the user.

## Microsoft Advertising

Use a Microsoft Advertising connector if one exists. Otherwise ask the user to export the **Search query report** (Reports, then Search terms / Search query) for each period as CSV, with these columns where available: Search term, Keyword, Delivered match type, Bid match type, Campaign, Ad group, Impressions, Clicks, Spend, Conversions, Currency, Account name, Account ID.

If the report is requested through the API, the report is `SearchQueryPerformanceReport` and the columns of interest are `SearchQuery`, `Keyword`, `BidMatchType`, `DeliveredMatchType`, `Clicks`, `Spend` and a conversion column (`Conversions` or `ConversionsQualified`). **Check the current column definitions before relying on them. They have not been verified against a live account.**

Run: `python scripts/swa.py analyze 30:file.csv --platform microsoft --account-name "Client C" --account-id F123456`

## How columns are mapped

The script recognizes these headers (case-insensitive, English and common German names):

| Meaning | Recognized headers (examples) |
| --- | --- |
| Search term | Search term, Search query, Suchbegriff |
| Triggering keyword | Keyword, Search keyword |
| Keyword match type | Keyword match type, Bid match type |
| Search term match type | Match type, Delivered match type |
| Clicks, Cost | Clicks / Klicks, Cost / Spend / Kosten |
| Conversions | Conversions (preferred), Conversions qualified |

**Important:** a plain `Match type` column in a search terms report describes how the *search term* matched, not the keyword. The script keeps it separate and shows the keyword match type as `Unavailable` unless a keyword match type column exists. If a user's export uses different names, ask which column holds what, then rename the header in a copy of the file.

## What the script does with problem data

- A row with clicks but no conversions value (`--`) is not counted as zero. It is reported in the warnings.
- Total rows and report title lines are skipped.
- Numbers like `1.234,56` and `1,234.56` both work.
- If a file has no date column and no period prefix, label it with `7:`, `15:`, `30:` or `90:` in the command, otherwise it is treated as a custom period.
