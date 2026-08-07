# ADC Intelligence Delta — Foundation (v0.1)

## Why this exists

`ADCdb_Obsidian/` is a frozen historical baseline crawled from
adcdb.idrblab.net (~6100 ADC / 1189 antibody / 285 antigen / 496 linker /
446 payload entries). It stopped updating the moment the crawl finished.
This tool keeps ADC intelligence current going forward — not by re-crawling
ADCdb, but by pulling in new evidence from external sources on a rolling
basis (ClinicalTrials.gov, FDA, and PubMed today; AACR, ASCO, ESMO, company
disclosures, and patents are planned for later PRs) and reconciling it
against the existing corpus.

## ADCdb_Obsidian is a frozen baseline, not a mutable store

**This pipeline never writes into `ADCdb_Obsidian/ADCs/*.md` or any other
card in that tree.** If it did, there would be no way to later answer:
which fields came from the original ADCdb crawl, which were added by this
pipeline, when, from what source, and why. `ADCdb_Obsidian/` is read-only
input to entity resolution. All delta output — evidence, resolved/ambiguous/
unresolved matches, and (in later PRs) seeds and events — belongs in its own
data directory, kept separate from the vault. Obsidian rendering of that
delta, if it happens at all, is a downstream *view*, not the source of
truth.

## The flow this PR establishes

```
External source (CT.gov, FDA, ...)
        │
        ▼
source adapter (sources/clinicaltrials.py, sources/fda.py, ...)
        │  to_evidence()
        ▼
EvidenceRecord            <-- the only shape adapters are allowed to produce
        │
        ▼
EntityResolver.resolve()  <-- reads ADCdb_Obsidian/ADCs/*.md, read-only
        │
        ▼
ResolutionResult: EXACT_MATCH | AMBIGUOUS_ALIAS | UNRESOLVED
```

Everything past `ResolutionResult` — turning evidence into `ADCSeed`s and
`ADCEvent`s, writing a reviewed delta, deciding what (if anything) ever gets
rendered back into Markdown — is explicitly **out of scope for this PR**.
`contracts.py` defines `ADCAsset`, `ADCSeed`, and `ADCEvent` now so the shape
is stable when that work lands, but nothing here populates them yet.

## Why a source-independent `EvidenceRecord`

The source universe this system eventually needs to cover is large:
ClinicalTrials.gov, FDA, PubMed, AACR, ASCO, ESMO, company pipelines/PR, and
patents. If the pipeline's core types were shaped around CT.gov/FDA fields
(as the first draft of this code was), every new source would force a
schema change and touch every downstream consumer. Instead, every adapter's
only public contract is:

```python
def to_evidence(raw_source_record) -> EvidenceRecord: ...
```

`EvidenceRecord` carries provenance (`source_type`, `source_url`,
`source_record_id`, `retrieved_at`), source-derived text
(`evidence_text`), and free-text mentions
(`mentioned_assets`/`mentioned_targets`/`mentioned_indications`) that
`entity_resolution.py` and future seed-extraction logic consume without
needing to know which API the record came from.

`evidence_text` is *not* guaranteed to be verbatim source text. For
sources with a real text body (PubMed abstracts, AACR abstracts) it will
be. For sources with no free-text body — FDA's drugsfda API only returns
structured fields like submission type/status/priority — the adapter
instead produces a deterministic serialization of those fields, because
`EvidenceRecord` still needs *some* text for downstream interpretation to
read. The field is named `evidence_text`, not `raw_text`, specifically so
nothing downstream (a human, a future Rule Engine) mistakes a synthesized
FDA description for a citable verbatim quote. The actual structured
fields are always preserved in `provenance` regardless of what
`evidence_text` contains — that's the field to read if you need the
real source data rather than its text rendering.

## Four entities, not one

An earlier version of this pipeline only distinguished "known asset" vs.
"new asset." That's wrong for the sources this system will add next: an
AACR abstract can report `Target X antibody-payload conjugate` with PDX
regression data and never assign a formal asset code at all. That's not a
`NEW_ASSET` — treating it as one would pollute the asset registry with
one-off academic constructs. It's a **seed**: an early hypothesis that a
target is ADC-tractable in a given indication, identified by
`Target × Indication × Modality` (e.g. `CDCP1 × colorectal cancer × ADC`),
not by any drug name — because the same seed should accumulate evidence
across a company's asset, an academic paper, and a bispecific variant that
all target the same biology, none of which share a name.

- **`ADCAsset`** — a named, organizationally-developed candidate/product.
  `asset_id` is owned by this intelligence system, not by ADCdb — an
  optional `baseline_ref` points at an `ADCdb_Obsidian/ADCs/*.md` card when
  one exists, but `asset_id` must stay assignable to assets ADCdb never
  crawled (e.g. a brand-new asset a future AACR abstract names for the
  first time), or the contract breaks the moment the system finds one.
  This PR does not implement `asset_id` generation or an asset registry —
  `EntityResolver` still returns ADCdb card paths, since every asset it
  can resolve against today does have one.
- **`ADCSeed`** — `Target × Indication × Modality`, not asset-name-keyed.
  Not populated by this PR (needs AACR/PubMed ingestion, later PRs).
- **`ADCEvent`** — a dated, *interpreted* change (`TRIAL_START`,
  `REGULATORY`, ...) derived from one or more `EvidenceRecord`s. Event
  interpretation is later-PR work; only the shape is fixed here.
- **`EvidenceRecord`** — the raw, source-attributed fact everything else is
  built from.

## Entity resolution: exact match only, ambiguity is surfaced, never hidden

`EntityResolver` builds an alias index from every card's `name` (frontmatter)
and `Synonyms` table row. Resolution is exact, case-insensitive string
matching — **no fuzzy matching, no embedding similarity, no LLM
"is this the same asset" calls decide a merge automatically.** For a
registry this size, a false merge (attaching new evidence to the wrong
asset) is worse than a false new-asset/seed candidate, because a false merge
silently corrupts history that's expensive to detect and undo later, while
an unresolved name just waits for a human to look at it once.

The real, meaningful failure mode this PR fixes: the original alias index
used `dict.setdefault(alias, first_asset_id_seen)` — if two different ADCdb
cards happened to share a synonym, the second was silently dropped in favor
of whichever file iteration order found first. This is not hypothetical:
checked against the live corpus, **202 of 13,474 alias keys map to more
than one card** (mostly closely-related antibody engineering variants, e.g.
`12G6 S298NT299AY300S-Mc-Val-Cit-PABC-MMAE` vs. `12G6 S298NY300S-...`
sharing a mutation-site alias). `resolve()` now returns one of three
statuses instead of a bare optional string:

- `EXACT_MATCH` — exactly one card matched.
- `AMBIGUOUS_ALIAS` — more than one card shares the alias; the caller gets
  every candidate `asset_id`, never an implicit pick.
- `UNRESOLVED` — no card matched.

`resolve_any()` tries a list of candidate names in order but **stops at the
first non-`UNRESOLVED` result** — including an ambiguous one. It does not
keep trying later names hoping one resolves cleanly, because that would
silently prefer an unambiguous alias over an ambiguous one and hide the
collision from the caller.

## A real bug this design caught before it shipped further

The first version of `EntityResolver._load()` only read the first 6000
bytes of each card as a performance shortcut, on the assumption that a
card's `name` and `Synonyms` fields both live near the top. That assumption
holds for small/obscure entries but is **false for exactly the
highest-value cards** — heavily cross-referenced approved drugs. E.g.
`Trastuzumab deruxtecan.md` is 599KB and its `Synonyms` row (containing
`DS-8201`, `T-DXd`, and other widely-used codes) starts at byte offset
~163,600, because the `Related`/`ADCdb Links` sections before the General
Information table scale with how many other ADCdb entities cross-reference
that card. Under the head-only read, `DS-8201`, `T-DXd`, `MK-2870`, and
`SKB264` all silently resolved as `UNRESOLVED` — the exact opposite of what
you'd want for the most important, most-studied drugs in the corpus. Fixed
by reading the full file; loading all 6098 cards now takes ~14s (was ~1s
with the broken truncation), which is entirely acceptable for a monthly
job. Caught by running entity resolution against the real corpus and
spot-checking known aliases, not by the unit tests alone (which use small
synthetic fixtures and wouldn't have exercised this size-dependent bug) —
worth remembering before trusting fixture-only test coverage on this
codebase again. Locked in as a regression test
(`test_synonyms_resolve_when_row_occurs_past_first_6000_bytes`) so a future
reintroduction of a fixed-size read window fails in CI, not just against
the live corpus.

## What the Foundation PR deliberately did not do

- No PubMed / AACR / ASCO / ESMO / company / patent ingestion yet.
- No fuzzy/LLM-assisted entity resolution.
- No writes to `ADCdb_Obsidian/` — reads only.
- No `ADCSeed`/`ADCEvent` population — contracts only.
- No integration with `ADCpatent/` (its own acquisition subsystem with
  existing cron/download/index/log workflow; future integration is a
  dedicated adapter emitting `EvidenceRecord`, not a refactor of that
  system).
- No Rule Engine / StelligenOS integration.

## PR #2: PubMed rolling radar — validating the Foundation held

The Foundation PR's whole premise was that adding a new source should only
require `source_raw_record -> EvidenceRecord`, with zero changes to
`contracts.py`. PubMed was the test case, being the most different source
from CT.gov/FDA available next (unstructured free text instead of
structured trial/regulatory fields). **Result: `contracts.py` needed zero
changes.** `sources/pubmed.py` adds `to_evidence()` following the exact
same pattern as the other two adapters, and nothing downstream had to
change. This is also the first adapter where `evidence_text` is faithful,
source-derived abstract text (the PubMed abstract, reconstructed from its
XML — see `to_evidence()`'s docstring for why that's not strictly
byte-for-byte verbatim) rather than FDA's synthesized description —
validating that `EvidenceRecord.evidence_text`'s "may be either" contract
(see above) was the right call, not overcautious hedging.

Two real, non-hypothetical precision problems were found and fixed by
running the adapter against live NCBI data over the same 45-day window
used for the CT.gov/FDA checks, not by unit tests alone (same lesson as
the Foundation PR's byte-6000 bug — fixture tests validate shape, only a
real corpus/API run validates recall and precision):

1. **PubMed's automatic term mapping silently broadens queries.** The
   unqualified query term `emtansine` was expanded by PubMed to
   `"maytansine"[Supplementary Concept] OR "maytansine"[MeSH Terms]`,
   pulling in the entire maytansinoid payload class instead of just
   emtansine — invisible unless you inspect the API's
   `querytranslation` field. Fixed by qualifying every OR'd term with
   `[tiab]` (title/abstract, literal match), which suppresses MeSH
   auto-expansion. Verified: this both eliminated the silent expansion
   (`translationset` went from non-empty to empty) and reduced the 45-day
   window's result count from 529 to 515.
2. **The free-text asset-mention heuristic captured English connector
   words as part of drug names** — e.g. "trastuzumab **and** deruxtecan"
   extracted `"and deruxtecan"`. Fixed with a small stopword exclusion list
   (`and`/`or`/`with`/`plus`/...) checked against the token immediately
   before a matched payload suffix. Locked in as
   `test_extract_asset_mentions_excludes_english_connector_words`.

`mentioned_assets` extraction here is explicitly a coarse heuristic, not
NER — see `_extract_asset_mentions()`'s docstring. PubMed's API has no
structured drug-name field the way CT.gov's interventions or FDA's
generic_name/brand_name do, so recall is intentionally traded for
precision (a missed mention just means fewer resolvable candidates per
article; a wrong mention risks a bad entity resolution downstream).

### The search query itself is precision-first, not just mention extraction

The paragraph above documents low recall in *mention extraction* (finding
drug names inside an article this radar already fetched). That is a
separate concern from recall in the *search query* (which articles the
radar fetches in the first place) — and the query has the same bias.
`ADC_QUERY_TERM` matches "antibody-drug conjugate"-style phrases plus a
short list of known INN payload suffixes (vedotin, deruxtecan, ...). That
combination is reasonable for tracking assets that already have a formal
name, but this module is named a *seed* radar — its stated purpose (see
"Why this exists" above) is catching left-edge, pre-asset signals, and the
current query has a real gap there. It will miss:

- academic ADC constructs that never got a formal INN,
- company-code-only ADCs whose code isn't in the query,
- payload chemistries outside the current suffix list,
- articles that describe an "antibody conjugated to MMAE/SN-38/exatecan/..."
  without ever using the phrase "antibody-drug conjugate."

**v0.1's PubMed radar prioritizes precision over recall and does not claim
complete preclinical seed coverage.** This is a deliberate, not accidental,
scope limit for this PR — expanding the query with modality-construction
terms (e.g. `"antibody conjugated"[tiab]`, `"antibody-payload"[tiab]`, or
`antibody AND MMAE`-style combinations) is a real option, but calibrating
it without also making it noisy needs to be measured against actual recall
data, not guessed at. That measurement — auditing a sample of the ~515
articles/month for true-positive rate, and checking recall against a known
set of recent preclinical ADC papers — is deliberately left as a follow-up
task rather than folded into this PR.

## PR #3: Calibration v0.1 — measuring precision and recall, not guessing

That follow-up measurement task landed as its own PR. Full results and
methodology: `calibration/REPORT.md`. Headline findings, without repeating
the report: topical precision is high (98.4% of 515 articles genuinely
about ADCs) but seed yield is low (12% are the actionable
`PRECLINICAL_ADC_SEED` tier); a recall check against an independently-built
75-paper gold set found 100% recall, but with a structural selection bias
(the gold set required a PubMed MeSH `Immunoconjugates` tag, which
correlates with using standard ADC vocabulary) that means the specific
recall-gap scenarios hypothesized above (company-code-only ADCs, novel
payload chemistries, "antibody conjugated to X" wording) remain untested,
not confirmed absent. **`ADC_QUERY_TERM` was not changed by this PR** —
calibration data informs a future query-expansion decision, it doesn't
make one.
