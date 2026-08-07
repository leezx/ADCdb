from pathlib import Path

import entity_resolution as er


def _write_card(root: Path, filename: str, name: str, synonyms: str, filler_bytes: int = 0) -> None:
    adcs_dir = root / "ADCs"
    adcs_dir.mkdir(parents=True, exist_ok=True)
    filler = ("x" * filler_bytes + "\n") if filler_bytes else ""
    content = (
        "---\n"
        f'id: "TEST{filename}"\n'
        f'name: "{name}"\n'
        'entity_type: "ADC"\n'
        'source_url: "https://example.invalid"\n'
        "---\n\n"
        f"# {name}\n\n"
        f"{filler}"
        "## General Information\n\n"
        "| Field              | Value |\n"
        "| ------------------ | ----- |\n"
        f"| Synonyms           | {synonyms} |\n"
    )
    (adcs_dir / filename).write_text(content, encoding="utf-8")


def test_parse_synonyms_truncates_at_organization_bleed():
    text = (
        "| Synonyms           | DS-8201; DS-8201a; T-DXd Organization Daiichi Sankyo Inc.; AstraZeneca PLC |\n"
    )
    aliases = er.parse_synonyms(text)
    assert aliases == ["DS-8201", "DS-8201a", "T-DXd"]
    assert not any("Daiichi" in a or "Organization" in a for a in aliases)


def test_parse_name_from_frontmatter():
    text = '---\nid: "X"\nname: "Trastuzumab deruxtecan"\n---\n'
    assert er.parse_name(text) == "Trastuzumab deruxtecan"


def test_exact_canonical_name_resolution(tmp_path):
    _write_card(tmp_path, "drug_a.md", "Drug Alpha", "AlphaCode-1; AC1")
    resolver = er.EntityResolver(tmp_path)

    result = resolver.resolve("Drug Alpha")

    assert result.status == er.ResolutionStatus.EXACT_MATCH
    assert result.asset_ids == ["ADCs/drug_a.md"]


def test_exact_synonym_resolution_is_case_insensitive(tmp_path):
    _write_card(tmp_path, "drug_a.md", "Drug Alpha", "AlphaCode-1; AC1")
    resolver = er.EntityResolver(tmp_path)

    result = resolver.resolve("alphacode-1")

    assert result.status == er.ResolutionStatus.EXACT_MATCH
    assert result.asset_ids == ["ADCs/drug_a.md"]


def test_ambiguous_alias_when_two_cards_share_a_synonym(tmp_path):
    _write_card(tmp_path, "drug_a.md", "Drug Alpha", "SharedCode-9; AC1")
    _write_card(tmp_path, "drug_b.md", "Drug Beta", "SharedCode-9; BC1")
    resolver = er.EntityResolver(tmp_path)

    result = resolver.resolve("SharedCode-9")

    assert result.status == er.ResolutionStatus.AMBIGUOUS_ALIAS
    assert set(result.asset_ids) == {"ADCs/drug_a.md", "ADCs/drug_b.md"}


def test_unresolved_when_no_card_matches(tmp_path):
    _write_card(tmp_path, "drug_a.md", "Drug Alpha", "AlphaCode-1")
    resolver = er.EntityResolver(tmp_path)

    result = resolver.resolve("Totally Unknown Compound")

    assert result.status == er.ResolutionStatus.UNRESOLVED
    assert result.asset_ids == []


def test_resolve_any_short_circuits_on_ambiguous_without_trying_later_names(tmp_path):
    _write_card(tmp_path, "drug_a.md", "Drug Alpha", "SharedCode-9")
    _write_card(tmp_path, "drug_b.md", "Drug Beta", "SharedCode-9")
    resolver = er.EntityResolver(tmp_path)

    # "SharedCode-9" is ambiguous; "AlphaCode-1" would resolve cleanly, but
    # must not be silently preferred just because it comes second and is
    # unambiguous — that would hide the collision from the caller.
    result = resolver.resolve_any(["SharedCode-9", "Drug Alpha"])

    assert result.status == er.ResolutionStatus.AMBIGUOUS_ALIAS


def test_resolve_any_returns_unresolved_when_nothing_matches(tmp_path):
    _write_card(tmp_path, "drug_a.md", "Drug Alpha", "AlphaCode-1")
    resolver = er.EntityResolver(tmp_path)

    result = resolver.resolve_any(["Nope", "Also Nope"])

    assert result.status == er.ResolutionStatus.UNRESOLVED


def test_synonyms_resolve_when_row_occurs_past_first_6000_bytes(tmp_path):
    """Regression test for a real bug found against the live corpus: an
    earlier version of _load() only read the first 6000 bytes of each card,
    which silently dropped the Synonyms row on heavily cross-referenced
    cards (e.g. Trastuzumab deruxtecan.md, 599KB, Synonyms at byte
    ~163,600) — exactly the highest-value, most-studied drugs. This
    constructs a card with >10KB of filler before the Synonyms row so any
    future reintroduction of a fixed-size read window fails loudly here
    instead of only showing up against the real 6GB+ corpus."""
    _write_card(tmp_path, "drug_a.md", "Drug Alpha", "LateCode-1", filler_bytes=10_000)
    resolver = er.EntityResolver(tmp_path)

    result = resolver.resolve("LateCode-1")

    assert result.status == er.ResolutionStatus.EXACT_MATCH
    assert result.asset_ids == ["ADCs/drug_a.md"]
