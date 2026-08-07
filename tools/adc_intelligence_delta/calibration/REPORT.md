# PubMed Radar Calibration v0.1 — Report

Two independent experiments measuring the production `ADC_QUERY_TERM`
(`sources/pubmed.py`) without modifying it. Corpus: the same 45-day window
(2026-06-23 to 2026-08-07, 515 articles) used for the earlier live checks in
PR #1/#2. Raw data and tooling live in this directory; **the production
query in `src/sources/pubmed.py` was not changed by this PR.**

## Experiment A — Precision (LLM classify all 515, human spot-check)

All 515 articles were classified by LLM (5 parallel batches of ~103, no
sampling) into 5 mutually-exclusive categories, plus `is_true_adc`,
`confidence`, and a one-sentence reason each. Full labeled corpus:
`labeled_corpus_full.jsonl`.

| Category | Count | % |
|---|---|---|
| ADC_RELATED_BUT_NOT_ASSET_SEED | 159 | 30.9% |
| CLINICAL_ADC | 148 | 28.7% |
| ADC_REVIEW_OR_METHOD | 138 | 26.8% |
| **PRECLINICAL_ADC_SEED** | **62** | **12.0%** |
| IRRELEVANT | 8 | 1.6% |

- **`is_true_adc` = true: 507/515 (98.4%)** — the query's raw topical
  precision is very high. Confidence: 331 HIGH / 161 MEDIUM / 23 LOW.
- **The 8 IRRELEVANT (false positive) examples are not the noise type we
  originally guessed** (the Foundation/PR#1 write-ups worried about
  "conjugate vaccine"-style homonyms). The actual false positives are
  adjacent-but-different *drug modalities* that legitimately use ADC-like
  vocabulary: a small-molecule drug conjugate (DUPA-SS-exatecan, not an
  antibody), a photoimmunoconjugate (Cetuximab-I21, near-infrared
  photoimmunotherapy, not a cytotoxic payload), an antibacterial ADC (real
  ADC chemistry, wrong disease area — *Neisseria gonorrhoeae*, not cancer),
  an intracellular-enzyme electrophile conjugate (PFKL activator), and an
  editorial on an *unconjugated* antibody (avelumab) that merely used
  "conjugate"-adjacent language. Two more (EGFR-NSCLC review, prostate
  cancer review) appear to be abstract-truncation artifacts — no ADC term
  visible in the given abstract text despite topical proximity, worth a
  second look at full text before concluding they're true false positives.
- **The real finding is not precision, it's yield.** Only 12% of the
  corpus is the actionable `PRECLINICAL_ADC_SEED` tier. The two largest
  buckets — `ADC_RELATED_BUT_NOT_ASSET_SEED` (31%, ADCs mentioned only as
  background/context in a paper about something else) and
  `ADC_REVIEW_OR_METHOD` (27%, syntheses and chemistry-methods papers with
  no new target/candidate data) — are topically on-target but low
  actionable value. If seed density (not raw precision) becomes the
  optimization target, narrowing toward primary-research article types
  would likely help more than tightening the ADC keyword match itself.

**Human audit sample**: `human_audit_sample.md` (also `.jsonl`), 67
stratified articles (25 PRECLINICAL_ADC_SEED, 13 CLINICAL_ADC, 14
REVIEW/METHOD, 8 IRRELEVANT — all of them, since only 8 exist total, 15
LOW-confidence with overlap) for you to spot-check the LLM's category
calls. Not self-audited — that's the point of this file.

## Experiment B — Recall (independent gold set, production query tested against it)

To avoid circularity, the gold set was built via a **different discovery
method** than the production free-text query: PubMed's controlled-vocabulary
MeSH heading `Immunoconjugates[Mesh]` combined with preclinical-signal
keywords (internalization, PDX, patient-derived xenograft, target
expression, novel target), over Jan 2025–Aug 2026 (190 candidates,
`gold_candidates_raw.jsonl`). An LLM screened these down to 75 that
strictly qualify as reporting new, specific preclinical ADC-tractability
evidence (`gold_set.jsonl`) — conservatively: reviews, methods-only papers,
already-approved-ADC studies, and off-modality conjugates (radioimmuno-,
photoimmuno-, aptamer/DARPin-drug conjugates) were excluded even when
preclinically rigorous, since the radar's ADC-phrase/suffix query
structurally cannot catch them and including them would unfairly penalize
recall.

**Result: the production query retrieved 75/75 (100%) of the gold set**
(`check_recall.py`, verified via `<ADC_QUERY_TERM> AND <pmid>[uid]` against
the live API — sanity-checked against unrelated negative-control PMIDs,
which correctly returned 0, so this isn't a broken test). Zero misses to
build a miss-taxonomy from.

**This is a real result, but it does not settle the recall question the
review raised, and shouldn't be read as "recall is fine."** The gold-set
construction has a structural selection bias: requiring
`Immunoconjugates[Mesh]` means every candidate was *already* judged by a
human NLM indexer to be unambiguously about immunoconjugates — and papers
clear enough to earn that MeSH tag are also, apparently, almost always
clear enough to use standard ADC vocabulary somewhere in title/abstract.
The specific recall-gap scenarios the review named — company-code-only
ADCs, novel payload chemistries outside the current suffix list, papers
describing "antibody conjugated to X" without ever saying "ADC" — are
exactly the papers *least* likely to get a clean `Immunoconjugates` MeSH
tag quickly, so this gold set is structurally unlikely to contain them
regardless of how the production query performs. (MeSH-indexing lag is a
real but partial confound too — some gold-set entries are as recent as
2026-07-21/epub-ahead 2026-09, so fast indexing does happen, but that
doesn't remove the vocabulary-correlation bias.)

**Honest conclusion: recall is unmeasured for the failure modes that
matter most, not confirmed good.** A gold set immune to this bias would
need to be built without any MeSH/indexer involvement — e.g. from AACR/ASCO
conference-abstract-to-later-PubMed-paper linkage, or from company-code
searches, both left for a follow-up rather than attempted in this PR.

## What this PR does and does not do

- Adds calibration tooling (`fetch_corpus.py`, `fetch_gold_candidates.py`,
  `check_recall.py`) and its output data under `calibration/`.
- **Does not modify `ADC_QUERY_TERM` or any production code in
  `src/`.** Query-expansion candidates from the design discussion
  (`"antibody conjugated"[tiab]`, `antibody AND MMAE`-style combinations)
  remain unapplied pending better recall evidence, per the review's
  explicit instruction.
- Leaves two follow-ups open, not started here: (1) a company-code/AACR-linkage-based
  gold set that avoids the MeSH selection bias above, to actually test the
  recall-gap hypothesis; (2) resolving the two ambiguous IRRELEVANT
  classifications by checking full text instead of abstract-only.
