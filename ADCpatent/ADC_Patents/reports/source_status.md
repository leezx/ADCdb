# Patent Source Status

This file records source-access assumptions for the ADC patent index workflow.

- Google Patents: enabled as the primary no-key source via the public XHR search endpoint. Use slow paging to avoid 503 throttling.
- Lens: adapter-ready in principle, but API search requires an API token. Set `LENS_API_KEY` before enabling automated Lens harvest.
- Espacenet / EPO OPS: direct unauthenticated REST access is blocked by EPO fair-use policy. OPS credentials are needed for automated harvest.
- WIPO Patentscope: browser/search access returned 403 in this environment. Keep WIPO links in the index and use credentialed/manual access for cross-checking.
- USPTO / PatentsView: current PatentSearch API requires `X-Api-Key`. Set `PATENTSVIEW_API_KEY` before enabling automated USPTO enrichment.
- CNIPA: no stable unauthenticated public search API is configured. Keep CNIPA search links for manual/credentialed follow-up.

The current executable harvest therefore prioritizes a slow, exhaustive Google Patents index and stores cross-source links for follow-up/enrichment.
