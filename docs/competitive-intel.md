---
purpose: "Dated log of competitor pricing / feature changes that affect scrapefold's positioning or engine roadmap."
updated: "2026-06-04"
related:
  - post-1.0/backlog.md
  - README.md
---

# Competitive intel

Append-only log of notable pricing / feature shifts among the SaaS scraping
vendors scrapefold wraps or competes with. Newest first. Each row should
link a source and state the concrete impact on scrapefold (engine work,
docs, positioning).

| Date | Vendor | Change | Source | Impact on scrapefold |
|---|---|---|---|---|
| 2026-06-04 | ScraperAPI | **AI Parser GA.** LLM-based structured extraction leaves beta. New pricing: 30,000 credits per parser **create** and per **edit**; first parser free; existing beta parsers grandfathered (no retroactive charges). | Product email "AI Parser is Now Live \| Your Beta Parsers Are Safe" (noreply@scraperapi.com, 2026-06-04) | Added `scraperapi` engine (uses parsers by reference). Parser **lifecycle** management deferred — see [backlog](post-1.0/backlog.md) "ScraperAPI AI Parser lifecycle". |
