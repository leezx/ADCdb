"""Resolve free-text asset names (from CT.gov/FDA records) against the
existing ADCdb_Obsidian corpus (ADCs/*.md, ~6100 entries imported from
adcdb.idrblab.net).

This is the entity-resolution layer for extending ADCdb_Obsidian with a
monthly delta feed. It never creates a new registry — it builds an in-memory
alias index over the existing cards so pipeline.py can tell whether an
incoming record is about a known asset (asset_id = card path, relative to
the ADCdb_Obsidian repo root) or a genuinely new one (asset_id = None).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

FRONTMATTER_LINE = re.compile(r"^([A-Za-z0-9_-]+):\s*\"?(.*?)\"?\s*$")
SYNONYMS_ROW = re.compile(r"\|\s*Synonyms\s*\|(.*?)\|", re.DOTALL)
NAME_FRONTMATTER = re.compile(r'^name:\s*"(.*)"\s*$', re.MULTILINE)


def parse_name(text: str) -> str:
    match = NAME_FRONTMATTER.search(text[:2000])
    return match.group(1).strip() if match else ""


def parse_synonyms(text: str) -> list[str]:
    """The scraped ADCdb table has a 'Synonyms' row whose cell often bleeds
    into an unlabeled 'Organization' continuation (a scrape artifact — the
    source table's next row lost its leading '|'). Truncate at that marker
    since everything after it is company names, not drug aliases."""
    match = SYNONYMS_ROW.search(text[:5000])
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


class EntityResolver:
    def __init__(self, adcdb_root: Path):
        self.adcdb_root = adcdb_root
        self.assets: list[AssetRecord] = []
        self._alias_index: dict[str, str] = {}
        self._load()

    def _load(self) -> None:
        adcs_dir = self.adcdb_root / "ADCs"
        for card in sorted(adcs_dir.glob("*.md")):
            # read only the head — name + Synonyms row both live in the
            # first ~5KB even on multi-MB cards, no need to load the whole file
            with card.open(encoding="utf-8", errors="replace") as handle:
                head = handle.read(6000)
            name = parse_name(head)
            aliases = parse_synonyms(head)
            if name and name.lower() not in {a.lower() for a in aliases}:
                aliases.append(name)
            if not aliases:
                aliases = [card.stem]
            asset_id = str(card.relative_to(self.adcdb_root))
            record = AssetRecord(asset_id=asset_id, name=name or card.stem, aliases=aliases)
            self.assets.append(record)
            for alias in aliases:
                key = alias.lower()
                self._alias_index.setdefault(key, asset_id)

    def resolve(self, name: str) -> str | None:
        """Exact (case-insensitive) alias match only — see rationale in
        DESIGN.md. No fuzzy matching; unmatched names surface as NEW_ASSET
        candidates for human review rather than risking a wrong auto-merge
        into a 6000+ entry corpus."""
        if not name:
            return None
        return self._alias_index.get(name.strip().lower())

    def resolve_any_with_name(self, names: list[str]) -> tuple[str | None, str | None]:
        for name in names:
            hit = self.resolve(name)
            if hit:
                return hit, name
        return None, (names[0] if names else None)

    def asset_by_id(self, asset_id: str) -> AssetRecord | None:
        for record in self.assets:
            if record.asset_id == asset_id:
                return record
        return None
