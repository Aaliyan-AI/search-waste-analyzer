# Google Ads: getting the data through a connector

Use only read queries (GAQL `search` / `searchStream`). Never call mutate or create tools.

These queries come from Google's published field reference. **They have not yet been run against a live account by the author of this skill.** On the first run, compare one campaign's totals with the Google Ads interface and tell the user the result.

## Before the queries

1. List the accessible accounts with the connector. If the user has a manager (MCC) account, let them choose which client accounts to include. Do not query accounts the user did not choose.
2. For each chosen account, read `customer.descriptive_name`, `customer.id`, `customer.currency_code`, `customer.time_zone`.
3. Run `python scripts/swa.py ranges --tz <customer.time_zone>` and use those four date ranges.

## Query A: every search term with totals (no keyword fields)

This query returns every term, so none is lost when keyword fields are unavailable.

```
SELECT customer.id, customer.descriptive_name, customer.currency_code, customer.time_zone,
       campaign.name, campaign.advertising_channel_type, ad_group.name,
       search_term_view.search_term, search_term_view.status,
       metrics.impressions, metrics.clicks, metrics.cost_micros, metrics.conversions
FROM search_term_view
WHERE segments.date BETWEEN '{start}' AND '{end}'
  AND metrics.clicks > 0
ORDER BY metrics.cost_micros DESC
```

Tag each returned row with `"source_query": "totals"` and `"period": 7` (or 15, 30, 90).

## Query B: the same terms with the triggering keyword

```
SELECT customer.id, customer.descriptive_name, customer.currency_code, customer.time_zone,
       campaign.name, campaign.advertising_channel_type, ad_group.name,
       search_term_view.search_term, search_term_view.status,
       segments.keyword.info.text, segments.keyword.info.match_type,
       metrics.impressions, metrics.clicks, metrics.cost_micros, metrics.conversions
FROM search_term_view
WHERE segments.date BETWEEN '{start}' AND '{end}'
  AND metrics.clicks > 0
ORDER BY metrics.cost_micros DESC
```

Tag rows with `"source_query": "keywords"`. Google's own forum reports that adding keyword segments can make some rows (for example Dynamic Search Ads) disappear. That is why Query A exists. The script uses the keyword rows where they exist and turns whatever Query A counted beyond them into one row with the keyword `Unavailable`.

## Query C: Performance Max search terms

`search_term_view` does not include Performance Max. Run this as well when the account has Performance Max campaigns:

```
SELECT customer.id, customer.descriptive_name, customer.currency_code, customer.time_zone,
       campaign.name, campaign.advertising_channel_type,
       campaign_search_term_view.search_term,
       metrics.impressions, metrics.clicks, metrics.cost_micros, metrics.conversions
FROM campaign_search_term_view
WHERE segments.date BETWEEN '{start}' AND '{end}'
  AND campaign.advertising_channel_type = 'PERFORMANCE_MAX'
  AND metrics.clicks > 0
```

Tag rows `"source_query": "totals"`. They have no keyword and will show `Unavailable` with the note that Performance Max has no keywords. If the connector reports that the view or filter is not supported, skip Query C and say Performance Max terms were not included.

## Saving the rows

- Write the connector's rows to a JSON file with code. Do not retype numbers.
- The script understands both snake_case (`metrics.cost_micros`) and camelCase (`costMicros`) names, nested or flat, and converts micros to money.
- Add `period` and `source_query` to each row, then save one file per period or one combined file.
- If the connector truncates results or limits rows, narrow the query (by campaign, or by minimum clicks) and tell the user the list may be incomplete. Never present a truncated result as complete.
- Pass `--platform google --account-name ... --account-id ...` only if rows lack account fields.

## Fields and what they mean

| Need | Field |
| --- | --- |
| Search term | `search_term_view.search_term` |
| Triggering keyword | `segments.keyword.info.text` |
| Keyword match type | `segments.keyword.info.match_type` |
| Is the term already a keyword or exclusion | `search_term_view.status` |
| Spend | `metrics.cost_micros` divided by 1,000,000 (the script does this) |
| Conversions | `metrics.conversions` (the platform's own Conversions value) |

`segments.search_term_match_type` is how the search term matched. It is a different thing from the keyword's own match type. Never put one in the other's column.

## Known limits

- Google hides search terms with too little volume for privacy reasons. Totals from this report can be lower than campaign totals.
- Recent days can receive conversions later. A 7-day zero-conversion list overstates.
- Each period is requested separately, not sliced from daily data, so that numbers match what the Google Ads interface shows for that range.
