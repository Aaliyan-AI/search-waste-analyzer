# Interpreting the results

## What a row means

A row says: in this account, in this period, this search term (triggered by this keyword) received clicks, cost money, and recorded zero conversions. It does not say the term can never convert.

## Reading across periods

| Pattern | Meaning | What to say |
| --- | --- | --- |
| Zero in 7, 15, 30 and 90 days | Consistent signal | Strongest candidate to review |
| Zero in 30 and 90, not in 7 | Recent small change | Likely still a candidate |
| Zero in 7 only, converted in 90 | Probably normal variation or conversion lag | Do not call it waste |
| Zero in 90 with very few clicks | Not enough data | Say there is too little evidence |

The expandable row shows the same term's conversions in each period (`term_conversions_by_period`) so this can be checked without extra columns.

## Things to check before anyone adds a negative keyword

- Is the term already a keyword or an exclusion (`term_status`)?
- Did the same term convert under another keyword or ad group (`same_term_other_rows`)?
- Are conversions tracked correctly for this account? Zero conversions across everything points to a tracking problem, not to bad terms.
- Is the volume high enough to judge? A term with 2 clicks proves little.
- Could a negative keyword block good traffic? Prefer the most specific match type.

## Wording

Use: "zero-conversion spend in the last 30 days", "candidates to review", "no conversions recorded yet".
Avoid: "wasted", "useless", "block these", "permanently unproductive".

## Always mention

- Recent days can receive conversions later.
- Google hides some low-volume terms, so the list is not complete.
- Keyword coverage: the share of clicks where the platform returned a keyword.
- Spend is in each account's own currency and is never added across accounts.

## Never

- Add negative keywords or change any account. Offer a list for the user to apply.
- Guess a keyword. `Unavailable` is the correct answer when the platform did not return one.
- Present demo data as real.
