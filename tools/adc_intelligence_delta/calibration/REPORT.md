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

- **LLM-estimated topical precision (`is_true_adc` = true): 507/515
  (98.4%)** — not yet human-validated. The 67-article stratified sample in
  `human_audit_sample.md` exists specifically to check whether this number
  holds up; until that audit happens, treat 98.4% as a model-labeled
  estimate, not a confirmed precision figure. Confidence: 331 HIGH / 161
  MEDIUM / 23 LOW.
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
- **The real finding is not precision, it's yield** — specifically,
  LLM-estimated yield: only 12% of the corpus is labeled the actionable
  `PRECLINICAL_ADC_SEED` tier, pending the same human audit. The two largest
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
`gold_candidates_raw.jsonl`). An LLM screened these down to a gold set
(`gold_set.jsonl`) that strictly qualifies as reporting new, specific
preclinical ADC-tractability evidence for the target domain: an
antibody-based construct covalently linked to a cytotoxic small-molecule
payload, intended for target-mediated delivery, with new experimental
evidence (not a review/methods-only paper, not an already-approved asset
studied without new tractability evidence).

**Gold-set membership was decided purely by this domain/modality
definition — never by whether the production query's wording would or
would not match a candidate's text.** The first version of this
experiment stated one exclusion rationale in a way that conflated the two
("the radar's ADC-phrase/suffix query structurally cannot catch them and
including them would unfairly penalize recall"); that framing was wrong —
if a paper is genuinely in-domain but uses wording the production query
misses, that is exactly the failure the benchmark exists to catch, not a
valid reason to drop it from the gold set. Radioimmunoconjugates,
photoimmunoconjugates, and aptamer/DARPin-drug conjugates are correctly
excluded, but only because they are a different modality entirely (no
covalently-linked cytotoxic small-molecule payload / not an antibody) —
never because of how they'd score against `ADC_QUERY_TERM`. All 115
excluded-vs-190 candidates were re-checked against this domain-only
rubric specifically to catch any case where the original screening's
reasoning had, even implicitly, let query capability leak into a
membership decision; see the "gold-set eligibility re-check" subsection
below for what that re-check found.

**Observed recall on this MeSH-derived benchmark: 81/81 (100%)**
(`check_recall.py`, verified via `<ADC_QUERY_TERM> AND <pmid>[uid]` against
the live API — sanity-checked against unrelated negative-control PMIDs,
which correctly returned 0, so this isn't a broken test). **This is a
benchmark-specific number, not a general recall figure — see below for
why**, and it already reflects the eligibility correction described next
(the 81 includes 6 papers restored after the re-check found they'd been
wrongly excluded from the original 75-entry set; a 7th restored candidate
was moved to a separate adjacent-modality watchlist rather than counted
here — see "gold-set eligibility re-check" below for both).

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

**Precise statement of what was and wasn't measured:** benchmark recall on
this MeSH-derived gold set is the number above. **General preclinical-seed
recall remains unknown**, because this benchmark has a MeSH/vocabulary
selection bias — it is not evidence that recall is fine, and should not be
cited as "the radar has good recall" without that qualifier. A gold set
immune to this bias would need to be built without any MeSH/indexer
involvement — e.g. from AACR/ASCO conference-abstract-to-later-PubMed-paper
linkage, or from company-code searches, both left for a follow-up rather
than attempted in this PR.

### Gold-set eligibility re-check (target-leakage audit)

All 115 of the 190 MeSH-derived candidates that were NOT included in the
original gold set were re-screened against the domain-only rubric above,
specifically to check whether any exclusion had been influenced by
production-query capability rather than genuine domain mismatch. Full
per-paper verdicts: `recheck_excluded.jsonl`.

**Result: 108 correctly excluded (reviews, off-modality conjugates,
already-approved-asset studies with no new tractability evidence, wrong
indication), 6 wrongly excluded strict-ADC positives, 1 ontology-boundary
candidate.** The 6 wrongly-excluded papers are genuine in-domain
preclinical constructs with new in vitro/in vivo evidence that had been
dropped from the original screening: a receptor-ubiquitination "ubitaADC"
platform (PMID 41887220), a novel peptidomimetic-linker trastuzumab
construct (PMID 41549487), a BB-1701 HER2-ADC study in T-DXd-resistant
disease (PMID 41548044), a ProTide-payload gemcitabine ADC (PMID
41273992), an albumin-binding scFv-MMAE "Albubody" platform (PMID
40850443), and a high-DAR antibody-fragment ADC (PMID 40495111). The 1
ontology-boundary candidate, a ROR1-targeting antibody-PROTAC degrader
conjugate (PMID 39816690), is discussed on its own below — an earlier
pass on this re-check had also marked it `SHOULD_BE_INCLUDED` alongside
the other 6, but that verdict was itself an instance of the same
leakage-adjacent error one level up (see below), corrected in
`recheck_excluded.jsonl` to `ONTOLOGY_BOUNDARY`.

**This is a genuine, confirmed finding about the original gold-set
screening: it had target leakage.** But it is a screening-quality finding,
not — on its own — a production-query recall finding, and an earlier
version of this report conflated the two by claiming "all 7 use
non-standard wording" and presenting their restoration as evidence of a
recall gap. That claim does not survive checking `recall_hits.jsonl`: **all
6 of the wrongly-excluded strict-ADC papers are recall hits** — the
production query does match them (e.g. PMID 41549487's title is literally
"...for Antibody-Drug Conjugates," and its abstract opens with that exact
phrase; the other 5 are caught via a listed payload suffix or the ADC
phrase elsewhere in title/abstract, despite an *unrelated* platform/code
name like "ubitaADC" or "Albubody" appearing alongside it). For those 6,
the original screening's exclusion was a pure judgment error — unrelated
to whether `ADC_QUERY_TERM`'s wording could catch the paper — so their
restoration demonstrates gold-set leakage, not a recall gap. All 6 were
restored to `gold_set.jsonl` (75 → 81) and `check_recall.py` was rerun
against the corrected set — that's the 81/81 (100%) figure reported
above.

**The remaining candidate, PMID 39816690 (ROR1-targeting antibody-PROTAC
degrader conjugate), is different and is deliberately *not* in
`gold_set.jsonl`.**
It pairs an antibody with a PROTAC payload — targeted protein degradation,
not a classical cytotoxic small molecule — so whether it counts as
in-domain under this report's own rubric ("antibody-based construct
covalently linked to a **cytotoxic small-molecule payload**") is itself an
ontology judgment call, not a settled fact. Counting it in the gold set
while also treating it as a confirmed miss would silently redefine the
domain specifically to manufacture a recall-gap finding — the same kind of
circularity the original review flagged, just moved one level up. Instead
it's tracked separately in `adjacent_modality_watchlist.jsonl`: excluded
from the strict-ADC recall figure (81/81, 100%), but kept on record because
`ADC_QUERY_TERM` demonstrably cannot match its wording ("degrader-antibody
conjugate," no ADC phrase, no listed payload suffix), which is directly
relevant if Stelligen's ADC intelligence scope is later broadened to
include degrader-antibody conjugates as a payload class. **On the current,
strict domain definition, this benchmark found zero confirmed production-query
misses; the PROTAC case is evidence for a possible future scope-expansion
decision, not evidence of an existing recall gap under the current
scope.**

## What this PR does and does not do

- Adds calibration tooling (`fetch_corpus.py`, `fetch_gold_candidates.py`,
  `check_recall.py`) and its output data under `calibration/`.
- **Does not modify `ADC_QUERY_TERM` or any production code in
  `src/`.** On the strict-ADC gold set, this benchmark found no confirmed
  production-query miss (81/81); the one adjacent-modality candidate found
  during the leakage audit (PMID 39816690, PROTAC payload) is tracked as a
  watchlist item, not treated as a miss requiring a query fix.
- Leaves three follow-ups open, not started here: (1) a company-code/AACR-linkage-based
  gold set that avoids the MeSH selection bias above, to actually test for
  recall gaps this MeSH-biased benchmark structurally cannot surface; (2) resolving
  the two ambiguous IRRELEVANT classifications from Experiment A by
  checking full text instead of abstract-only; (3) the human audit of
  `human_audit_sample.md` to convert the LLM-estimated precision/yield
  numbers into validated ones.
