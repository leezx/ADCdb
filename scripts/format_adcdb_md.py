#!/usr/bin/env python3
"""Format ADCdb Obsidian markdown files: replace raw scraped text with structured markdown.

Usage:
    python3 format_adcdb_md.py                  # format all files
    python3 format_adcdb_md.py --limit 3        # test on 3 files per directory
    python3 format_adcdb_md.py --dir ADCs       # only one directory
    python3 format_adcdb_md.py --dry-run        # print first result, don't write
"""

import argparse
import re
import sys
from pathlib import Path

BASE_DIR = Path(__file__).parent
DIRS = ["ADCs", "Antigens", "Antibodies", "Linkers", "Payloads", "Targets"]

# UI/button text lines to discard entirely
SKIP_LINES = {
    "Click to Show/Hide", "3D MOL", "2D MOL", "2D", "MOL", ".",
    "Antibody Info", "Antigen Info", "Payload Info", "Target Info", "ADC Info",
    "Visitor Map", "Correspondence", "Structure",
    "Antibody-drug Conjugate Information", "Antibody Information",
    "Antigen Information", "Payload Information", "Linker Information",
    "Click to View the Clearer Original Diagram",
    "Differential expression pattern of antigen in diseases",
    "Disease-specific Antigen Abundances",
    "Pharmaceutical Properties",
    "Each Antibody-drug Conjugate AND It's Component Related to This Antigen",
    "Each Antibody-drug Conjuate AND It's Component Related to This Antigen",  # typo in source
    "Each Antibody-drug Conjugate Related to This Antibody",
    "Each Antibody-drug Conjugate Related to This Payload",
    "Each Antibody-drug Conjugate Related to This Linker",
    "Full List of The ADC Related to This Antigen",
    "Full Information of The Activity Data of The ADC(s) Related to This Antibody",
    "Full Information of The Activity Data of The ADC(s) Related to This Payload",
    # Antigen/Target ADC table column headers
    "ADC Name", "Payload", "Target", "Linker", "Ref",
    "Antibody", "Antigen",  # Target ADC table columns
    # Antigen tissue/disease section headers (before ICD codes)
    "Tissue/Disease specific Abundances of This Antigen",
    "Tissue specific Abundances of This Antigen",
    # Target page
    "Target Information",
    "Full List of The ADC Related to This Target",
}

# Pharmaceutical property fields (for table grouping)
PHARMA_PROPS = [
    "Molecule Weight", "Polar area", "Complexity", "xlogp Value",
    "Heavy Count", "Rot Bonds", "Hbond acc", "Hbond Donor",
]

ADC_INFO_FIELDS = [
    "ADC ID", "ADC Name", "Synonyms", "Drug Status", "Indication",
    "Antibody Name", "Antigen Name", "Payload Name", "Therapeutic Target", "Linker Name",
]

ANTIGEN_INFO_FIELDS = [
    "Antigen ID", "Antigen Name", "Gene Name", "Gene ID",
    "Synonym", "Sequence", "Family", "Function", "Uniprot Entry", "HGNC ID", "KEGG ID",
]

ANTIBODY_INFO_FIELDS = [
    "Antibody ID", "Antibody Name", "Synonyms",
    "Antibody Type", "Antibody Subtype", "Antigen Name",
]

TARGET_INFO_FIELDS = [
    "Target ID", "Target Name", "Gene Name", "Gene ID",
    "Synonym", "Sequence", "Family", "Function", "Uniprot Entry", "HGNC ID", "KEGG ID",
]

LINKER_INFO_FIELDS = ["Linker ID", "Linker Name", "Linker Type", "Antibody-Linker Relation"]

PAYLOAD_INFO_FIELDS = ["Payload ID", "Name", "Synonyms", "Target(s)"]

CHEM_FIELDS = ["Formula", "Isosmiles", "PubChem CID", "InChI", "InChIKey", "IUPAC Name"]

ACTIVITY_KV_KEYS = {
    "Patients Enrolled", "Administration Dosage", "Method Description",
    "In Vivo Model", "In Vitro Model", "Related Clinical Trial",
    "NCT Number", "Clinical Status", "Phase Status",
    "Clinical Description", "Primary Endpoint", "Other Endpoint",
    "Efficacy Data", "Tumor Growth Inhibition value (TGI)",
}

# Regex shortcuts
RE_ICD_CLASS = re.compile(r"^ICD Disease Classification")
RE_ICD_DISEASE = re.compile(r"^(.+)\s+\[ICD-11:[^\]]*\]$")
RE_ADC_STATUS = re.compile(r"^(.+?)\s+\[(Phase \d+[^\]]*|Investigative|Preclinical|Approved|Terminated)\]$")
RE_REF_NUM = re.compile(r"^Ref (\d+)$")
RE_NCTNUMBER = re.compile(r"^NCT\d{8}$")
RE_AMINO = re.compile(r"^[ACDEFGHIKLMNPQRSTVWY]{20,}$")
RE_HTML_TAG = re.compile(r"<[^>]+>")
RE_SKIP_ACTIVITY = re.compile(
    r"Click To (Hide|Show)/?(Show|Hide)? \d+ Activity Data"
    r"|^Identified from (the )?|^Discovered Using "
    r"|^Experiment \d+ Reporting"
)


# ── helpers ────────────────────────────────────────────────────────────────

def strip_html(text: str) -> str:
    return RE_HTML_TAG.sub("", text).strip()


def md_escape_pipe(text: str) -> str:
    return text.replace("|", "\\|")


def make_table(header: list[str], rows: list[list[str]]) -> str:
    widths = [len(h) for h in header]
    for row in rows:
        for i, cell in enumerate(row):
            if i < len(widths):
                widths[i] = max(widths[i], len(cell))
    sep = "| " + " | ".join("-" * w for w in widths) + " |"
    hdr = "| " + " | ".join(h.ljust(widths[i]) for i, h in enumerate(header)) + " |"
    data = [
        "| " + " | ".join(str(row[i] if i < len(row) else "").ljust(widths[i]) for i in range(len(header))) + " |"
        for row in rows
    ]
    return "\n".join([hdr, sep] + data)


def kv_table(kv: dict, fields: list[str]) -> str:
    rows = [(f, md_escape_pipe(kv[f])) for f in fields if kv.get(f)]
    if not rows:
        return ""
    return make_table(["Field", "Value"], rows)


# ── file I/O ───────────────────────────────────────────────────────────────

def read_file(path: Path):
    """Return (pre_content, raw_text) or (None, None) if no extracted block."""
    text = path.read_text(encoding="utf-8")
    marker = "## Extracted Page Text\n\n```text\n"
    if marker not in text:
        return None, None
    idx = text.index(marker)
    pre = text[:idx]
    after = text[idx + len(marker):]
    end = after.find("\n```")
    raw = after[:end] if end >= 0 else after.rstrip("`\n")
    return pre, raw


# ── raw-text cleaning ──────────────────────────────────────────────────────

def clean_lines(raw: str) -> list[str]:
    lines = raw.split("\n")

    # Drop navigation header (everything up to and including "About")
    for i, line in enumerate(lines):
        if line.strip() == "About":
            lines = lines[i + 1:]
            break

    # Drop citation footer (from "Citation" onward)
    for i, line in enumerate(lines):
        if line.strip() == "Citation":
            lines = lines[:i]
            break

    result = []
    for line in lines:
        s = strip_html(line.strip())
        if not s:
            continue
        if s in SKIP_LINES:
            continue
        if re.match(r"^Click to Show/Hide( the \d+)?", s):
            continue
        if re.match(r"^Click To (Hide|Show)", s):
            continue
        if s.startswith("Download "):
            continue
        if re.match(r"^In total \d+ Indication\(s\)", s):
            continue
        result.append(s)

    return result


# ── key-value parser ───────────────────────────────────────────────────────

def parse_kv(lines: list[str], known_fields: list[str]) -> dict:
    """Extract key-value pairs where keys are exact-match field names."""
    field_set = set(known_fields)
    result = {}
    i = 0
    while i < len(lines):
        if lines[i] in field_set:
            key = lines[i]
            vals = []
            i += 1
            while i < len(lines) and lines[i] not in field_set:
                vals.append(lines[i])
                i += 1
            result[key] = " ".join(vals).strip()
        else:
            i += 1
    return result


# ── section finders ────────────────────────────────────────────────────────

def find_line(lines: list[str], keyword: str) -> int:
    for i, l in enumerate(lines):
        if keyword in l:
            return i
    return -1


def split_at(lines: list[str], keyword: str):
    idx = find_line(lines, keyword)
    if idx < 0:
        return lines, []
    return lines[:idx], lines[idx:]


# ── references ─────────────────────────────────────────────────────────────

def format_references(lines: list[str]) -> str:
    refs = []
    in_refs = False
    pending_num = None
    for line in lines:
        if line == "References":
            in_refs = True
            continue
        if not in_refs:
            continue
        m = RE_REF_NUM.match(line)
        if m:
            pending_num = m.group(1)
        elif pending_num:
            refs.append(f"{pending_num}. {line}")
            pending_num = None
    if not refs:
        return ""
    return "## References\n\n" + "\n".join(refs)


# ── pharmaceutical properties ───────────────────────────────────────────────

def format_pharma(kv: dict) -> str:
    rows = [(p, kv[p]) for p in PHARMA_PROPS if kv.get(p)]
    if not rows:
        return ""
    return "### Pharmaceutical Properties\n\n" + make_table(["Property", "Value"], rows)


# ── chemical structure ──────────────────────────────────────────────────────

def format_chem(kv: dict) -> str:
    mono = ["Formula", "PubChem CID", "InChIKey", "Molecule Weight"]
    long_fields = ["Isosmiles", "InChI", "IUPAC Name"]
    parts = []

    rows = [(f, kv[f]) for f in mono if kv.get(f)]
    if rows:
        parts.append(make_table(["Property", "Value"], rows))

    for f in long_fields:
        if kv.get(f):
            parts.append(f"**{f}:**\n```\n{kv[f]}\n```")

    if not parts:
        return ""
    return "## Chemical Structure\n\n" + "\n\n".join(parts)


# ── ADC formatter ───────────────────────────────────────────────────────────

def format_adc(lines: list[str]) -> str:
    kv = parse_kv(lines, ADC_INFO_FIELDS)

    # Clean up Indication: "Pancreatic cancer Investigative" → "Pancreatic cancer (Investigative)"
    if kv.get("Indication"):
        ind = kv["Indication"]
        ind = re.sub(r"(Investigative|Approved|Preclinical|Terminated)$", r"(\1)", ind).strip()
        kv["Indication"] = ind

    parts = ["## General Information", "", kv_table(kv, ADC_INFO_FIELDS)]
    refs = format_references(lines)
    if refs:
        parts += ["", refs]
    return "\n".join(parts)


# ── Antigen ADC table ───────────────────────────────────────────────────────

def format_antigen_adc_section(lines: list[str]) -> str:
    """List ADC-related entries from antigen ADC table (no status in these entries)."""
    entries = []

    for line in lines:
        # Stop at disease section
        if RE_ICD_CLASS.match(line) or line == "References":
            break
        # Skip ref markers, button text, already-filtered column headers
        if re.match(r"^\[ \d+ \]$", line):
            continue
        if line in {"Antibody Info", "ADC Info"}:
            continue
        # Skip very short, purely numeric, or generic placeholder lines
        if len(line) < 3 or re.match(r"^\d+$", line):
            continue
        if line.lower() in {"undisclosed", "n/a", "unknown"}:
            continue
        if "Tissue" in line and "Abundances" in line:
            continue
        entries.append(line)

    if not entries:
        return ""

    return "\n".join(f"- {e}" for e in entries)


# ── Disease expression section ──────────────────────────────────────────────

def format_disease_section(lines: list[str]) -> str:
    # Find where ICD sections begin
    start = -1
    for i, l in enumerate(lines):
        if RE_ICD_CLASS.match(l):
            start = i
            break
    if start < 0:
        return ""

    disease_lines = lines[start:]
    output = ["## Disease Expression Data", ""]

    current_icd = None
    current_category = None
    tissue = ""
    specific_disease = ""
    table_rows: list[list[str]] = []

    def flush_icd():
        nonlocal table_rows
        if table_rows and current_icd:
            output.append(f"### {current_icd}")
            output.append("")
            output.append(make_table(
                ["Disease", "Tissue", "vs", "p-value", "Fold-change", "Z-score"],
                table_rows,
            ))
            output.append("")
            table_rows = []

    i = 0
    while i < len(disease_lines):
        line = disease_lines[i]

        if RE_ICD_CLASS.match(line):
            flush_icd()
            current_icd = line
            i += 1
            continue

        m = RE_ICD_DISEASE.match(line)
        if m:
            current_category = m.group(1).strip()
            i += 1
            continue

        if line == "The Studied Tissue":
            i += 1
            tissue = disease_lines[i] if i < len(disease_lines) else ""
            i += 1
            continue

        if line == "The Specific Disease":
            i += 1
            specific_disease = disease_lines[i] if i < len(disease_lines) else ""
            i += 1
            continue

        if line.startswith("The Expression Level"):
            comp = "Healthy" if "Healthy" in line else ("Adjacent" if "Adjacent" in line else "Other")
            i += 1
            p_val = fc_val = z_val = ""
            while i < len(disease_lines):
                curr = disease_lines[i]
                if curr.startswith("p-value:"):
                    p_val = curr.split("p-value:")[1].strip().rstrip(";")
                elif curr.startswith("Fold-change:"):
                    fc_val = curr.split("Fold-change:")[1].strip().rstrip(";")
                elif curr.startswith("Z-score:"):
                    z_val = curr.split("Z-score:")[1].strip()
                    i += 1
                    break
                else:
                    break
                i += 1
            disease_name = specific_disease or current_category or ""
            table_rows.append([
                md_escape_pipe(disease_name),
                md_escape_pipe(tissue),
                comp,
                p_val, fc_val, z_val,
            ])
            continue

        if line == "References":
            break

        i += 1

    flush_icd()

    return "\n".join(output) if len(output) > 2 else ""


# ── Antigen formatter ───────────────────────────────────────────────────────

def parse_kv_antigen(lines: list[str]) -> tuple[dict, int]:
    return _parse_kv_fields(lines, ANTIGEN_INFO_FIELDS)


def format_antigen(lines: list[str]) -> str:
    icd_start = find_line(lines, "ICD Disease")
    pre_icd = lines[:icd_start] if icd_start >= 0 else lines

    # Separate sequence lines from the info block
    seq_parts = []
    non_seq_info = []
    in_seq = False
    for line in pre_icd:
        if line == "Sequence":
            in_seq = True
            non_seq_info.append(line)
        elif in_seq and RE_AMINO.match(line):
            seq_parts.append(line)
        else:
            in_seq = False
            non_seq_info.append(line)

    kv, info_end = parse_kv_antigen(non_seq_info)
    if seq_parts:
        kv["Sequence"] = "".join(seq_parts)

    adc_block = non_seq_info[info_end:]

    parts = ["## General Information", "", kv_table(kv, [f for f in ANTIGEN_INFO_FIELDS if f != "Sequence"])]

    # Protein sequence block
    seq = kv.get("Sequence", "")
    if seq:
        parts += ["", "### Protein Sequence", "", "```"]
        for j in range(0, len(seq), 60):
            parts.append(seq[j:j + 60])
        parts.append("```")

    # Related ADCs (lines between end of info fields and ICD Disease section)
    if adc_block:
        adc_md = format_antigen_adc_section(adc_block)
        if adc_md:
            parts += ["", "## Related ADCs", "", adc_md]

    # Disease expression
    disease_md = format_disease_section(lines)
    if disease_md:
        parts += ["", disease_md]

    # References
    refs_md = format_references(lines)
    if refs_md:
        parts += ["", refs_md]

    return "\n".join(parts)


# ── Activity data (shared by Antibody / Payload) ────────────────────────────

def format_activity_data(lines: list[str]) -> str:
    output = []
    i = 0
    in_experiment = False

    while i < len(lines):
        line = lines[i]

        if line == "References":
            break

        # ADC name + status header
        m = RE_ADC_STATUS.match(line)
        if m:
            output.append(f"### {m.group(1)} ({m.group(2)})")
            output.append("")
            i += 1
            continue

        # Source type
        if line.startswith("Identified from") or line.startswith("Discovered Using"):
            output.append(f"**Source:** {line}")
            i += 1
            continue

        # Experiment header
        if re.match(r"^Experiment \d+ Reporting", line):
            in_experiment = True
            output.append(f"**{line}**")
            output.append("")
            i += 1
            continue

        # Skip ref markers
        if re.match(r"^\[ \d+ \]$", line):
            i += 1
            continue

        # NCT number line (standalone)
        if RE_NCTNUMBER.match(line):
            output.append(f"- **NCT:** {line}")
            i += 1
            continue

        # Key-value items
        if line in ACTIVITY_KV_KEYS:
            key = line
            i += 1
            vals = []
            while i < len(lines) and lines[i] not in ACTIVITY_KV_KEYS and lines[i] != "References":
                # Stop if we hit a new ADC header or source line
                if RE_ADC_STATUS.match(lines[i]):
                    break
                if lines[i].startswith("Identified from") or lines[i].startswith("Discovered Using"):
                    break
                if re.match(r"^Experiment \d+ Reporting", lines[i]):
                    break
                vals.append(lines[i])
                i += 1
            val_text = " ".join(vals).strip()
            if val_text:
                output.append(f"- **{key}:** {val_text}")
            continue

        # Cell line IDs or short descriptors (just append as plain text)
        if in_experiment and len(line) > 3:
            output.append(f"  {line}")

        i += 1

    return "\n".join(output).strip()


# ── Antibody formatter ──────────────────────────────────────────────────────

def _find_activity_start(lines: list[str], section_keyword: str) -> int:
    """Find start of activity data section. Falls back to first ADC status line."""
    idx = find_line(lines, section_keyword)
    if idx >= 0:
        return idx
    # Fallback: first line matching ADC [Status] pattern
    for i, line in enumerate(lines):
        if RE_ADC_STATUS.match(line):
            return i
    return -1


def format_antibody(lines: list[str]) -> str:
    adc_start = _find_activity_start(lines, "Each Antibody-drug Conjugate Related to This Antibody")
    info_lines = lines[:adc_start] if adc_start >= 0 else lines

    kv = parse_kv(info_lines, ANTIBODY_INFO_FIELDS)
    parts = ["## General Information", "", kv_table(kv, ANTIBODY_INFO_FIELDS)]

    if adc_start >= 0:
        act_md = format_activity_data(lines[adc_start:])
        if act_md:
            parts += ["", "## Related ADC Activity Data", "", act_md]

    refs_md = format_references(lines)
    if refs_md:
        parts += ["", refs_md]

    return "\n".join(parts)


# ── Linker formatter ────────────────────────────────────────────────────────

def format_linker(lines: list[str]) -> str:
    all_fields = LINKER_INFO_FIELDS + CHEM_FIELDS + PHARMA_PROPS
    kv = parse_kv(lines, all_fields)

    parts = ["## General Information", "", kv_table(kv, LINKER_INFO_FIELDS)]

    chem_md = format_chem(kv)
    if chem_md:
        parts += ["", chem_md]

    pharma_md = format_pharma(kv)
    if pharma_md:
        parts += ["", pharma_md]

    refs_md = format_references(lines)
    if refs_md:
        parts += ["", refs_md]

    return "\n".join(parts)


# ── Payload formatter ───────────────────────────────────────────────────────

def format_payload(lines: list[str]) -> str:
    adc_start = _find_activity_start(lines, "Each Antibody-drug Conjugate Related to This Payload")
    info_lines = lines[:adc_start] if adc_start >= 0 else lines

    all_fields = PAYLOAD_INFO_FIELDS + CHEM_FIELDS + PHARMA_PROPS
    kv = parse_kv(info_lines, all_fields)

    parts = ["## General Information", "", kv_table(kv, PAYLOAD_INFO_FIELDS)]

    chem_md = format_chem(kv)
    if chem_md:
        parts += ["", chem_md]

    pharma_md = format_pharma(kv)
    if pharma_md:
        parts += ["", pharma_md]

    if adc_start >= 0:
        act_md = format_activity_data(lines[adc_start:])
        if act_md:
            parts += ["", "## Related ADC Activity Data", "", act_md]

    refs_md = format_references(lines)
    if refs_md:
        parts += ["", refs_md]

    return "\n".join(parts)


# ── Target formatter ────────────────────────────────────────────────────────

def format_target_adc_table(lines: list[str]) -> str:
    """Parse target ADC table: groups of 5 lines = ADC, Antibody, Antigen, Payload, Linker."""
    entries = []
    for line in lines:
        if line == "References":
            break
        # Skip stage labels ("Investigative", "Phase 1", "Preclinical", etc.)
        if re.match(r"^(Investigative|Preclinical|Approved|Phase \d|Terminated)", line):
            continue
        if re.match(r"^\[ \d+ \]$", line):
            continue
        if len(line) < 2:
            continue
        entries.append(line)

    if not entries:
        return ""

    # Group into rows of 5: [ADC, Antibody, Antigen, Payload, Linker]
    rows = []
    for i in range(0, len(entries), 5):
        chunk = entries[i:i + 5]
        if len(chunk) == 5:
            rows.append(chunk)
        else:
            # Incomplete row: just list remaining entries
            rows.append(chunk + [""] * (5 - len(chunk)))

    if not rows:
        return ""
    return make_table(["ADC", "Antibody", "Antigen", "Payload", "Linker"], rows)


_STAGE_RE = re.compile(r"^(Investigative|Preclinical|Approved|Terminated|Phase \d)")


def _parse_kv_fields(lines: list[str], info_fields: list[str]) -> tuple[dict, int]:
    """Generic info-field parser for Antigen/Target: limits value lines per field."""
    field_set = set(info_fields)
    multiline_ok = {"Synonym", "Function"}
    result = {}
    i = 0
    last_info_end = 0

    while i < len(lines):
        line = lines[i]
        if line not in field_set:
            i += 1
            continue
        key = line
        vals = []
        i += 1
        max_val_lines = 5 if key in multiline_ok else 2
        for _ in range(max_val_lines):
            if i >= len(lines):
                break
            next_line = lines[i]
            if next_line in field_set:
                break
            # Stop on stage labels, ADC table data, or references
            if _STAGE_RE.match(next_line):
                break
            if re.match(r"^\[ \d+ \]$", next_line):
                break
            if next_line == "References":
                break
            if key == "KEGG ID" and not re.match(r"^[a-z]{2,5}:\d+", next_line):
                break
            vals.append(next_line)
            i += 1
        result[key] = " ".join(vals).strip()
        last_info_end = i

    return result, last_info_end


def _parse_kv_target(lines: list[str]) -> tuple[dict, int]:
    return _parse_kv_fields(lines, TARGET_INFO_FIELDS)


def format_target(lines: list[str]) -> str:
    seq_parts = []
    non_seq_info = []
    in_seq = False
    for line in lines:
        if line == "Sequence":
            in_seq = True
            non_seq_info.append(line)
        elif in_seq and RE_AMINO.match(line):
            seq_parts.append(line)
        else:
            in_seq = False
            non_seq_info.append(line)

    kv, info_end = _parse_kv_target(non_seq_info)
    if seq_parts:
        kv["Sequence"] = "".join(seq_parts)

    adc_block = non_seq_info[info_end:]

    parts = ["## General Information", "",
             kv_table(kv, [f for f in TARGET_INFO_FIELDS if f != "Sequence"])]

    seq = kv.get("Sequence", "")
    if seq:
        parts += ["", "### Protein Sequence", "", "```"]
        for j in range(0, len(seq), 60):
            parts.append(seq[j:j + 60])
        parts.append("```")

    if adc_block:
        adc_md = format_target_adc_table(adc_block)
        if adc_md:
            parts += ["", "## Related ADCs", "", adc_md]

    refs_md = format_references(lines)
    if refs_md:
        parts += ["", refs_md]

    return "\n".join(parts)


# ── dispatch ────────────────────────────────────────────────────────────────

FORMATTERS = {
    '"ADC"': format_adc,
    '"Antigen"': format_antigen,
    '"Antibodie"': format_antibody,
    '"Antibody"': format_antibody,
    '"Linker"': format_linker,
    '"Payload"': format_payload,
    '"Target"': format_target,
}


def detect_entity_type(pre: str):
    m = re.search(r'entity_type:\s*"([^"]+)"', pre)
    if m:
        return m.group(1)
    return None


def format_file(path: Path, dry_run: bool = False) -> bool:
    pre, raw = read_file(path)
    if raw is None:
        return False

    entity_type = detect_entity_type(pre or "")
    formatter = None
    for key, fn in FORMATTERS.items():
        if key in (pre or ""):
            formatter = fn
            break

    if formatter is None:
        print(f"  SKIP (unknown type): {path.name}")
        return False

    lines = clean_lines(raw)
    formatted = formatter(lines)

    # Ensure pre ends with exactly two newlines before the new content
    pre_stripped = pre.rstrip("\n")
    new_content = pre_stripped + "\n\n" + formatted + "\n"

    if dry_run:
        print(f"\n{'='*60}\n{path.name}\n{'='*60}")
        print(new_content[:3000])
        return True

    path.write_text(new_content, encoding="utf-8")
    return True


# ── main ────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=None, help="Max files per directory")
    parser.add_argument("--dir", default=None, help="Process only this directory")
    parser.add_argument("--dry-run", action="store_true", help="Print first result, don't write")
    args = parser.parse_args()

    dirs = [args.dir] if args.dir else DIRS

    total_ok = total_skip = total_err = 0

    for dirname in dirs:
        dir_path = BASE_DIR / dirname
        if not dir_path.is_dir():
            print(f"Directory not found: {dir_path}")
            continue

        files = sorted(dir_path.glob("*.md"))
        if args.limit:
            files = files[: args.limit]

        ok = skip = err = 0
        for path in files:
            try:
                result = format_file(path, dry_run=args.dry_run)
                if result:
                    ok += 1
                else:
                    skip += 1
                if args.dry_run and ok >= 1:
                    break
            except Exception as e:
                print(f"  ERROR {path.name}: {e}")
                import traceback; traceback.print_exc()
                err += 1

        print(f"{dirname:12s}: {ok:4d} formatted  {skip:3d} skipped  {err:3d} errors")
        total_ok += ok; total_skip += skip; total_err += err

    print(f"\nTotal: {total_ok} formatted, {total_skip} skipped, {total_err} errors")


if __name__ == "__main__":
    main()
