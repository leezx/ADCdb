"""Minimal, source-independent data contracts for the ADC Intelligence Delta
pipeline.

Design rationale (see DESIGN.md for the full writeup): the pipeline must not
be built around any single source's record shape. Every source — today
ClinicalTrials.gov and FDA, later PubMed / AACR / ASCO / ESMO / company
disclosures / patents — normalizes into one EvidenceRecord shape. Everything
downstream (entity resolution, seed extraction, event classification) reads
EvidenceRecord and never needs to know which API it came from.

This module defines the contract shapes only. It does not implement seed
extraction or event classification yet — those are later-PR work, deferred
so the schema doesn't have to be redesigned once PubMed/AACR/patent sources
are added.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class EvidenceRecord:
    """One source-agnostic, evidence-attributed fact. This is the only
    shape source adapters (sources/clinicaltrials.py, sources/fda.py, and
    future sources/pubmed.py etc.) are allowed to produce."""

    evidence_id: str
    source_type: str  # "clinicaltrials" | "fda" | "pubmed" | "aacr" | "asco" | "esmo" | "company" | "patent"
    source_name: str  # human-readable source label, e.g. "ClinicalTrials.gov"
    source_url: str
    source_record_id: str  # source's own primary key, e.g. an NCT id or FDA application number
    publication_date: str | None  # ISO date the underlying record was published/last updated, if known
    retrieved_at: str  # ISO datetime this evidence was fetched
    title: str
    evidence_text: str  # source-derived text used for downstream interpretation: either
    # verbatim source text (e.g. a PubMed/AACR abstract) or a deterministic serialization
    # of structured source fields (e.g. an FDA submission's type/status/priority) when the
    # source has no free-text body. Never an LLM paraphrase or summary. Which case applies
    # for a given record is NOT distinguishable from this field alone — always keep the
    # actual structured fields in `provenance` too, and never treat evidence_text as a
    # citable verbatim quote without checking provenance first.
    mentioned_assets: list[str] = field(default_factory=list)  # free-text names as they appear in the source
    mentioned_targets: list[str] = field(default_factory=list)
    mentioned_indications: list[str] = field(default_factory=list)
    evidence_class: str = "UNCLASSIFIED"  # coarse bucket; fine-grained event typing is a later-PR concern
    confidence: str = "raw"  # "raw" until a validation step exists; do not invent higher values yet
    provenance: dict[str, Any] = field(default_factory=dict)  # raw source-specific fields, kept for traceability


@dataclass
class ADCAsset:
    """A named, organizationally-developed ADC drug candidate or approved
    product — Stelligen's own concept of an asset, not ADCdb's.

    asset_id is owned by this intelligence system, not by the ADCdb
    baseline. Every asset currently known to the system happens to also
    exist in ADCdb_Obsidian today, but the whole point of this pipeline is
    that future evidence (e.g. a 2027 AACR abstract naming a brand-new
    asset) will discover named ADCs that ADCdb never crawled. Those assets
    must still be representable, with baseline_ref=None — if asset_id were
    the ADCdb card path (as an earlier draft of this contract had it),
    there would be no legal identity to assign them.

    This PR does not implement asset_id generation or a mutable asset
    registry — EntityResolver still returns ADCdb card paths as the
    resolved identifier for this PR, since every asset it can currently
    resolve against does have one. Assigning Stelligen-owned asset_ids
    (e.g. "adc:trastuzumab_deruxtecan") and building the registry that
    mints new ones for baseline_ref=None assets is later-PR work; this
    dataclass only fixes the shape now so it doesn't change out from under
    that work."""

    asset_id: str  # Stelligen-owned stable identity — NOT necessarily an ADCdb_Obsidian path
    canonical_name: str
    aliases: list[str] = field(default_factory=list)
    baseline_ref: str | None = None  # optional: path into ADCdb_Obsidian/ADCs/*.md, if this asset has one


@dataclass
class ADCSeed:
    """An early therapeutic hypothesis that may not have a named asset yet:
    Target x Indication x Modality. Identity is deliberately NOT keyed on any
    drug/asset name (see DESIGN.md #5) so that an academic paper, a company
    poster, and a later-named asset can all accumulate evidence against the
    same seed.

    Not populated by this PR — AACR/PubMed ingestion (later PRs) is what
    actually produces seeds. Defined now so the schema is stable when that
    lands."""

    seed_id: str  # stable slug derived from (target, indication, modality), e.g. "CDCP1|colorectal_cancer|ADC"
    target: str
    indication: str
    modality: str = "ADC"
    supporting_evidence_ids: list[str] = field(default_factory=list)


@dataclass
class ADCEvent:
    """A dated, interpreted change — e.g. TRIAL_START, REGULATORY,
    CLINICAL_READOUT — derived from one or more EvidenceRecords. Event
    *interpretation* (turning raw evidence into a typed event) is later-PR
    work; this dataclass only fixes the shape so EvidenceRecord doesn't need
    to change again when that lands.

    An event attaches to an asset_id, a seed_id, or both — a single
    EvidenceRecord can simultaneously resolve to a known asset AND
    contribute new evidence to a target x indication seed."""

    event_id: str
    asset_id: str | None
    seed_id: str | None
    event_type: str
    event_date: str
    evidence_ids: list[str] = field(default_factory=list)
