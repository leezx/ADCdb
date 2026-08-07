"""Resolve free-text asset names (from EvidenceRecord.mentioned_assets, e.g.
CT.gov/FDA records today, PubMed/AACR/patent later) against the existing
ADCdb_Obsidian corpus (ADCs/*.md, ~6100 entries imported from
adcdb.idrblab.net).

This is the entity-resolution layer used to extend ADCdb_Obsidian with a
monthly delta feed. It never creates a new registry — it builds an in-memory
alias index over the existing cards. ADCdb_Obsidian is a frozen historical
baseline (see DESIGN.md); this module only reads it, never writes to it.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path

SYNONYMS_ROW = re.compile(r"\|\s*Synonyms\s*\|(.*?)\|", re.DOTALL)
NAME_FRONTMATTER = re.compile(r'^name:\s*"(.*)"\s*$', re.MULTILINE)


def parse_name(text: str) -> str:
    match = NAME_FRONTMATTER.search(text[:2000])
    return match.group(1).strip() if match else ""


def parse_synonyms(text: str) -> list[str]:
    """The scraped ADCdb table has a 'Synonyms' row whose cell often bleeds
    into an unlabeled 'Organization' continuation (a scrape artifact — the
    source table's next row lost its leading '|'). Truncate at that marker
    since everything after it is company names, not drug aliases.

    Searches the full text, not just the head: on heavily cross-referenced
    cards (e.g. approved drugs with hundreds of linked entities) the
    'Related'/'ADCdb Links' sections before the General Information table
    can run past 150KB, so a fixed small read window silently drops the
    Synonyms row entirely — verified this against the real corpus, e.g.
    'Trastuzumab deruxtecan.md' has it at byte offset ~163,600."""
    match = SYNONYMS_ROW.search(text)
    if not match:
        return []
    raw = match.group(1)
    raw = re.sub(r"\s*Organization\s.*$", "", raw, flags=re.DOTALL)
    return split_aliases(raw)


def split_aliases(value: str) -> list[str]:
    pieces = re.split(r"[,;；，/、]|\bor\b", value)
    aliases: list[str] = []
    seen: set[str] = set()
    for piece in pieces:
        cleaned = re.sub(r"\([^)]*\)", "", piece).strip()
        cleaned = re.sub(r"\s+", " ", cleaned)
        if len(cleaned) < 3:
            continue
        key = cleaned.lower()
        if key not in seen:
            aliases.append(cleaned)
            seen.add(key)
    return aliases


@dataclass
class AssetRecord:
    asset_id: str  # path relative to ADCdb_Obsidian root, e.g. "ADCs/Trastuzumab deruxtecan.md"
    name: str
    aliases: list[str] = field(default_factory=list)


class ResolutionStatus(str, Enum):
    EXACT_MATCH = "EXACT_MATCH"
    AMBIGUOUS_ALIAS = "AMBIGUOUS_ALIAS"
    UNRESOLVED = "UNRESOLVED"


@dataclass
class ResolutionResult:
    status: ResolutionStatus
    asset_ids: list[str] = field(default_factory=list)  # 0 for UNRESOLVED, 1 for EXACT_MATCH, 2+ for AMBIGUOUS_ALIAS
    matched_alias: str | None = None  # which input name produced the hit (or was tried last, if none matched)


class EntityResolver:
    def __init__(self, adcdb_root: Path):
        self.adcdb_root = adcdb_root
        self.assets: list[AssetRecord] = []
        # alias -> list of asset_ids. Deliberately a list, not
        # first-writer-wins: if ADCdb has two different cards sharing a
        # synonym, silently picking one would misattach evidence to the
        # wrong asset. That must surface as AMBIGUOUS_ALIAS for a human to
        # resolve, never get decided implicitly by file iteration order.
        self._alias_index: dict[str, list[str]] = {}
        self._load()

    def _load(self) -> None:
        adcs_dir = self.adcdb_root / "ADCs"
        for card in sorted(adcs_dir.glob("*.md")):
            # Full read, not a truncated head — see parse_synonyms()
            # docstring for why a fixed small window silently drops the
            # Synonyms row on heavily cross-referenced cards.
            with card.open(encoding="utf-8", errors="replace") as handle:
                text = handle.read()
            name = parse_name(text)
            aliases = parse_synonyms(text)
            if name and name.lower() not in {a.lower() for a in aliases}:
                aliases.append(name)
            if not aliases:
                aliases = [card.stem]
            asset_id = str(card.relative_to(self.adcdb_root))
            record = AssetRecord(asset_id=asset_id, name=name or card.stem, aliases=aliases)
            self.assets.append(record)
            for alias in aliases:
                key = alias.lower()
                bucket = self._alias_index.setdefault(key, [])
                if asset_id not in bucket:
                    bucket.append(asset_id)

    def resolve(self, name: str) -> ResolutionResult:
        """Exact (case-insensitive) alias match only — no fuzzy matching.
        A false merge into the wrong asset is worse than a false new-asset
        candidate for a 6000+ entry corpus, so ambiguity is surfaced rather
        than silently resolved (see DESIGN.md #2)."""
        if not name:
            return ResolutionResult(status=ResolutionStatus.UNRESOLVED)
        matches = self._alias_index.get(name.strip().lower(), [])
        if not matches:
            return ResolutionResult(status=ResolutionStatus.UNRESOLVED, matched_alias=name)
        if len(matches) == 1:
            return ResolutionResult(status=ResolutionStatus.EXACT_MATCH, asset_ids=matches, matched_alias=name)
        return ResolutionResult(status=ResolutionStatus.AMBIGUOUS_ALIAS, asset_ids=matches, matched_alias=name)

    def resolve_any(self, names: list[str]) -> ResolutionResult:
        """Try each candidate name in order; return the first non-UNRESOLVED
        result (an AMBIGUOUS_ALIAS still short-circuits — it is not
        'try the next name until something resolves cleanly', because that
        would silently prefer whichever name happens to be unambiguous and
        hide the collision)."""
        last_tried = names[0] if names else None
        for name in names:
            result = self.resolve(name)
            if result.status != ResolutionStatus.UNRESOLVED:
                return result
            last_tried = name
        return ResolutionResult(status=ResolutionStatus.UNRESOLVED, matched_alias=last_tried)

    def asset_by_id(self, asset_id: str) -> AssetRecord | None:
        for record in self.assets:
            if record.asset_id == asset_id:
                return record
        return None
