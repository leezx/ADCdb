#!/usr/bin/env python3
"""
Build an Obsidian-style Markdown knowledge base from public ADCdb pages.

The crawler is intentionally conservative:
- it only uses the status/name flow exposed by the ADC search form;
- it stores raw HTML so Markdown can be regenerated without refetching;
- it sleeps between requests and can resume from existing files.
"""

from __future__ import annotations

import argparse
import html
import json
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from html.parser import HTMLParser
from http.cookiejar import CookieJar
from pathlib import Path
from typing import Iterable


BASE_URL = "https://adcdb.idrblab.net"
STATUSES = ["Approved", "Phase 3", "Phase 2", "Phase 1", "Investigative"]
USER_AGENT = "ADCdb-Obsidian-Builder/0.1 (+local research; contact site owners for bulk use)"
AUXILIARY_SEARCHES = {
    "antibody": {
        "path": "antibody_search",
        "form_id": "home_search_antibody_by_adc_pair_form",
    },
    "antigen": {
        "path": "antigen_search",
        "form_id": "home_search_abt_by_adc_pair_form",
    },
    "payload": {
        "path": "payload_search",
        "form_id": "home_search_payload_by_adc_pair_form",
    },
    "linker": {
        "path": "linker_search",
        "form_id": "home_search_linker_by_adc_pair_form",
    },
}


def slugify(value: str, fallback: str = "untitled") -> str:
    value = html.unescape(value).strip()
    value = re.sub(r"[^\w\s().,+-]+", "", value, flags=re.UNICODE)
    value = re.sub(r"\s+", " ", value).strip()
    value = value.replace("/", "-")
    return value or fallback


def clean_text(value: str) -> str:
    value = html.unescape(value or "")
    value = value.replace("\xa0", " ")
    value = re.sub(r"[ \t\r\f\v]+", " ", value)
    return value.strip()


def yaml_scalar(value: str) -> str:
    value = clean_text(value)
    return json.dumps(value, ensure_ascii=False)


def yaml_list(values: Iterable[str]) -> str:
    cleaned = [clean_text(v) for v in values if clean_text(v)]
    if not cleaned:
        return "[]"
    return "[" + ", ".join(json.dumps(v, ensure_ascii=False) for v in cleaned) + "]"


class TextExtractor(HTMLParser):
    """Small HTML-to-text extractor that keeps enough line breaks for ADCdb pages."""

    block_tags = {
        "address",
        "article",
        "br",
        "div",
        "h1",
        "h2",
        "h3",
        "h4",
        "h5",
        "h6",
        "hr",
        "li",
        "p",
        "table",
        "tbody",
        "td",
        "th",
        "tr",
        "ul",
    }

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.skip_depth = 0

    def handle_starttag(self, tag: str, attrs) -> None:
        if tag in {"script", "style"}:
            self.skip_depth += 1
            return
        if self.skip_depth:
            return
        if tag in self.block_tags:
            self.parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in {"script", "style"} and self.skip_depth:
            self.skip_depth -= 1
            return
        if self.skip_depth:
            return
        if tag in self.block_tags:
            self.parts.append("\n")

    def handle_data(self, data: str) -> None:
        if self.skip_depth:
            return
        text = clean_text(data)
        if text:
            self.parts.append(text)
            self.parts.append(" ")

    def text(self) -> str:
        text = "".join(self.parts)
        lines = [clean_text(line) for line in text.splitlines()]
        lines = [line for line in lines if line]
        compact: list[str] = []
        for line in lines:
            if compact and compact[-1] == line:
                continue
            compact.append(line)
        return "\n".join(compact)


class LinkExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.links: list[tuple[str, str]] = []
        self._href: str | None = None
        self._text: list[str] = []

    def handle_starttag(self, tag: str, attrs) -> None:
        if tag != "a":
            return
        attrs_d = dict(attrs)
        href = attrs_d.get("href")
        if href:
            self._href = href
            self._text = []

    def handle_data(self, data: str) -> None:
        if self._href is not None:
            self._text.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag == "a" and self._href is not None:
            self.links.append((clean_text(" ".join(self._text)), self._href))
            self._href = None
            self._text = []


class IframeExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.srcs: list[str] = []

    def handle_starttag(self, tag: str, attrs) -> None:
        if tag != "iframe":
            return
        src = dict(attrs).get("src")
        if src:
            self.srcs.append(src)


@dataclass
class SearchRecord:
    status: str
    name: str
    adc_id: str = ""
    result_url: str = ""
    adc_url: str = ""
    antibody_name: str = ""
    antibody_url: str = ""
    payload_name: str = ""
    payload_url: str = ""
    linker_name: str = ""
    linker_url: str = ""
    representative_indication: str = ""


@dataclass
class Crawler:
    outdir: Path
    delay: float
    refresh: bool = False
    cookiejar: CookieJar = field(default_factory=CookieJar)

    def __post_init__(self) -> None:
        self.opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(self.cookiejar))
        self.raw_dir = self.outdir / "_raw"
        self.html_dir = self.raw_dir / "html"
        self.data_dir = self.outdir / "_data"
        self.asset_dir = self.outdir / "assets"
        for path in [self.html_dir, self.data_dir, self.asset_dir]:
            path.mkdir(parents=True, exist_ok=True)

    def request(self, url: str, data: dict[str, str] | None = None, headers: dict[str, str] | None = None) -> str:
        encoded = None
        if data is not None:
            encoded = urllib.parse.urlencode(data).encode()
        req = urllib.request.Request(url, data=encoded)
        req.add_header("User-Agent", USER_AGENT)
        if headers:
            for key, value in headers.items():
                req.add_header(key, value)
        with self.opener.open(req, timeout=60) as resp:
            body = resp.read()
            charset = resp.headers.get_content_charset() or "utf-8"
        time.sleep(self.delay)
        return body.decode(charset, errors="replace")

    def cached_get(self, url: str, cache_name: str) -> str:
        path = self.html_dir / cache_name
        if path.exists() and not self.refresh:
            return path.read_text(encoding="utf-8")
        text = self.request(url)
        path.write_text(text, encoding="utf-8")
        return text

    def search_page(self) -> str:
        return self.cached_get(f"{BASE_URL}/search/adc_search", "search_adc_search.html")

    def generic_search_page(self, path: str) -> str:
        return self.cached_get(f"{BASE_URL}/search/{path}", f"search_{path}.html")

    def form_build_id(self, search_html: str) -> str:
        match = re.search(r'name="form_build_id"\s+value="([^"]+)"', search_html)
        if not match:
            raise RuntimeError("Could not find form_build_id on search page")
        return html.unescape(match.group(1))

    def status_options(self, status: str) -> list[str]:
        search_html = self.search_page()
        form_id = self.form_build_id(search_html)
        data = {
            "a": status,
            "b": "",
            "form_build_id": form_id,
            "form_id": "home_search_adc_by_adc_pair_form",
            "_triggering_element_name": "a",
            "_drupal_ajax": "1",
        }
        response = self.request(
            f"{BASE_URL}/search/adc_search?ajax_form=1",
            data=data,
            headers={"X-Requested-With": "XMLHttpRequest"},
        )
        (self.data_dir / f"ajax_options_{slugify(status)}.json").write_text(response, encoding="utf-8")
        option_html = response
        try:
            commands = json.loads(response)
            option_html = "\n".join(clean_text(command.get("data", "")) for command in commands if command.get("data"))
        except json.JSONDecodeError:
            pass
        options = [clean_text(o) for o in re.findall(r'<option[^>]+value="([^"]*)"', option_html)]
        return [o for o in options if o and not o.startswith("Step 2:")]

    def auxiliary_status_options(self, kind: str, status: str) -> list[str]:
        config = AUXILIARY_SEARCHES[kind]
        path = config["path"]
        search_html = self.generic_search_page(path)
        form_build_id = self.form_build_id(search_html)
        data = {
            "a": status,
            "b": "",
            "form_build_id": form_build_id,
            "form_id": config["form_id"],
            "_triggering_element_name": "a",
            "_drupal_ajax": "1",
        }
        response = self.request(
            f"{BASE_URL}/search/{path}?ajax_form=1",
            data=data,
            headers={"X-Requested-With": "XMLHttpRequest"},
        )
        (self.data_dir / f"ajax_options_{kind}_{slugify(status)}.json").write_text(response, encoding="utf-8")
        option_html = response
        try:
            commands = json.loads(response)
            option_html = "\n".join(clean_text(command.get("data", "")) for command in commands if command.get("data"))
        except json.JSONDecodeError:
            pass
        options = [clean_text(o) for o in re.findall(r'<option[^>]+value="([^"]*)"', option_html)]
        return [o for o in options if o and not o.startswith("Step 2:")]

    def collect_auxiliary_options(self, statuses: list[str]) -> dict[str, dict[str, list[str]]]:
        all_options: dict[str, dict[str, list[str]]] = {}
        for kind in AUXILIARY_SEARCHES:
            all_options[kind] = {}
            for status in statuses:
                options = self.auxiliary_status_options(kind, status)
                all_options[kind][status] = options
                print(f"[options] {kind} {status}: {len(options)}")

        (self.data_dir / "auxiliary_options_by_status.json").write_text(
            json.dumps(all_options, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        unique_options = {
            kind: sorted({name for names in by_status.values() for name in names})
            for kind, by_status in all_options.items()
        }
        (self.data_dir / "auxiliary_options_unique.json").write_text(
            json.dumps(unique_options, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        return all_options

    def result_url(self, status: str, name: str) -> str:
        query = urllib.parse.urlencode({"a": status, "b": name})
        return f"{BASE_URL}/search/result/search-adc-by-adc-pair?{query}"

    def parse_search_result(self, status: str, name: str, page_html: str, result_url: str) -> SearchRecord:
        record = SearchRecord(status=status, name=name, result_url=result_url)
        text = TextExtractor()
        text.feed(page_html)
        lines = text.text().splitlines()
        joined = "\n".join(lines)
        patterns = {
            "adc_id": r"ADC ID:\s*([A-Z0-9]+)",
            "representative_indication": r"Representative Indication:\s*(.+?)(?:\n|$)",
            "antibody_name": r"Antibody Name:\s*(.+?)(?:\n|$)",
            "payload_name": r"Payload Name:\s*(.+?)(?:\n|$)",
            "linker_name": r"Linker Name:\s*(.+?)(?:\n|$)",
        }
        for attr, pattern in patterns.items():
            match = re.search(pattern, joined)
            if match:
                setattr(record, attr, clean_text(match.group(1)))

        links = LinkExtractor()
        links.feed(page_html)
        for _, href in links.links:
            if "/data/adc/details/" in href:
                record.adc_url = urllib.parse.urljoin(BASE_URL, href)
            elif "/data/antibody/details/" in href:
                record.antibody_url = urllib.parse.urljoin(BASE_URL, href)
            elif "/data/payload/details/" in href:
                record.payload_url = urllib.parse.urljoin(BASE_URL, href)
            elif "/data/linker/details/" in href:
                record.linker_url = urllib.parse.urljoin(BASE_URL, href)
        return record

    def collect_inventory(self, statuses: list[str], limit: int | None = None) -> list[SearchRecord]:
        records: list[SearchRecord] = []
        seen: set[str] = set()
        for status in statuses:
            names = self.status_options(status)
            for name in names:
                if limit is not None and len(records) >= limit:
                    return records
                result_url = self.result_url(status, name)
                cache = f"result_{slugify(status)}_{slugify(name)}.html"
                page_html = self.cached_get(result_url, cache)
                record = self.parse_search_result(status, name, page_html, result_url)
                key = record.adc_id or f"{status}:{name}"
                if key in seen:
                    continue
                seen.add(key)
                records.append(record)
                print(f"[inventory] {len(records):04d} {status} {record.adc_id} {name}")
        self.write_inventory(records)
        return records

    def write_inventory(self, records: list[SearchRecord]) -> None:
        payload = [r.__dict__ for r in records]
        (self.data_dir / "adc_inventory.json").write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")

    def load_inventory(self) -> list[SearchRecord]:
        path = self.data_dir / "adc_inventory.json"
        if not path.exists():
            return []
        payload = json.loads(path.read_text(encoding="utf-8"))
        return [SearchRecord(**item) for item in payload]

    def harvest_adc_links_from_raw(self) -> list[SearchRecord]:
        records: dict[str, SearchRecord] = {}
        for record in self.load_inventory():
            if record.adc_url:
                records[record.adc_url] = record

        for path in self.html_dir.glob("*.html"):
            try:
                text = path.read_text(encoding="utf-8", errors="ignore")
            except OSError:
                continue
            for adc_id in re.findall(r"/data/adc/details/(DRG[A-Z0-9]+)", text):
                url = f"{BASE_URL}/data/adc/details/{adc_id}"
                records.setdefault(
                    url,
                    SearchRecord(
                        status="",
                        name=adc_id,
                        adc_id=adc_id,
                        adc_url=url,
                        result_url=f"raw:{path.name}",
                    ),
                )

        out = sorted(records.values(), key=lambda r: r.adc_id or r.adc_url)
        self.write_inventory(out)
        print(f"[harvest] ADC detail URLs from raw HTML: {len(out)}")
        return out

    def fetch_detail(self, url: str, prefix: str, id_hint: str) -> str:
        cache = f"{prefix}_{id_hint}.html"
        return self.cached_get(url, cache)


def page_text(page_html: str) -> str:
    parser = TextExtractor()
    parser.feed(page_html)
    return parser.text()


def page_links(page_html: str) -> list[tuple[str, str]]:
    parser = LinkExtractor()
    parser.feed(page_html)
    return parser.links


def page_iframes(page_html: str) -> list[str]:
    parser = IframeExtractor()
    parser.feed(page_html)
    return parser.srcs


def value_after_label(text: str, label: str) -> str:
    lines = text.splitlines()
    for i, line in enumerate(lines):
        if line == label and i + 1 < len(lines):
            return clean_text(lines[i + 1])
        match = re.match(re.escape(label) + r":?\s+(.+)$", line)
        if match:
            return clean_text(match.group(1))
    return ""


def value_after_anchor_label(text: str, anchor: str, label: str) -> str:
    lines = text.splitlines()
    for i, line in enumerate(lines):
        if line != anchor:
            continue
        for j in range(i + 1, min(i + 8, len(lines))):
            if lines[j] == label and j + 1 < len(lines):
                return clean_text(lines[j + 1])
    return ""


def extract_title(text: str, fallback: str, folder: str | None = None) -> str:
    if folder == "ADCs":
        title = value_after_label(text, "ADC Name")
    elif folder == "Antibodies":
        title = value_after_label(text, "Antibody Name") or value_after_anchor_label(text, "Antibody ID", "Name")
    elif folder == "Payloads":
        title = value_after_label(text, "Payload Name") or value_after_anchor_label(text, "Payload ID", "Name")
    elif folder == "Linkers":
        title = value_after_label(text, "Linker Name") or value_after_anchor_label(text, "Linker ID", "Name")
    elif folder == "Antigens":
        title = value_after_label(text, "Antigen Name") or value_after_anchor_label(text, "Antigen ID", "Name")
    elif folder == "Targets":
        title = value_after_label(text, "Target Name") or value_after_anchor_label(text, "Target ID", "Name")
    else:
        title = ""
    if title:
        return title

    for key in ["ADC Name", "Antibody Name", "Payload Name", "Linker Name", "Antigen Name", "Target Name"]:
        title = value_after_label(text, key)
        if title:
            return title
    return fallback


def extract_id_from_url(url: str) -> str:
    return urllib.parse.urlparse(url).path.rstrip("/").split("/")[-1]


def obsidian_link(name: str, folder: str | None = None) -> str:
    name = clean_text(name)
    if not name:
        return ""
    if folder:
        return f"[[{folder}/{slugify(name)}|{name}]]"
    return f"[[{slugify(name)}|{name}]]"


def existing_markdown_for_source(target_dir: Path, source_url: str) -> Path | None:
    if not target_dir.exists():
        return None
    needle = f"source_url: {yaml_scalar(source_url)}"
    for candidate in target_dir.glob("*.md"):
        try:
            head = candidate.read_text(encoding="utf-8", errors="ignore")[:600]
        except OSError:
            continue
        if needle in head:
            return candidate
    return None


def folder_for_data_url(url: str) -> str | None:
    path = urllib.parse.urlparse(url).path
    if "/data/adc/details/" in path:
        return "ADCs"
    if "/data/antibody/details/" in path:
        return "Antibodies"
    if "/data/payload/details/" in path:
        return "Payloads"
    if "/data/linker/details/" in path:
        return "Linkers"
    if "/data/abt/details/" in path:
        return "Antigens"
    if "/data/plt/details/" in path:
        return "Targets"
    return None


def prefix_for_folder(folder: str) -> str:
    return folder.rstrip("s").lower()


def friendly_link_label(label: str, href: str, aliases: dict[str, str] | None = None) -> str:
    full = urllib.parse.urljoin(BASE_URL, href)
    if aliases and full in aliases:
        return aliases[full]
    cleaned = clean_text(label)
    if cleaned and cleaned not in {"ADC Info", "Antibody Info", "Payload Info", "Linker Info", "Antigen Info", "Target Info", "Info"}:
        return cleaned
    return extract_id_from_url(full)


def write_detail_markdown(
    outdir: Path,
    folder: str,
    name: str,
    entity_id: str,
    url: str,
    text: str,
    links: list[tuple[str, str]],
    related: dict[str, str] | None = None,
    url_aliases: dict[str, str] | None = None,
    iframe_urls: list[str] | None = None,
    overwrite: bool = False,
) -> Path:
    target_dir = outdir / folder
    target_dir.mkdir(parents=True, exist_ok=True)
    filename = f"{slugify(name, entity_id)}.md"
    path = target_dir / filename
    existing = existing_markdown_for_source(target_dir, url)
    if existing and not overwrite:
        return existing
    if path.exists() and not overwrite:
        try:
            head = path.read_text(encoding="utf-8", errors="ignore")[:800]
        except OSError:
            head = ""
        if f"source_url: {yaml_scalar(url)}" in head:
            return path
        path = target_dir / f"{slugify(name, entity_id)} ({entity_id}).md"
        if path.exists():
            return path

    internal_links = []
    for label, href in links:
        if "/data/" not in href:
            continue
        full = urllib.parse.urljoin(BASE_URL, href)
        internal_links.append((friendly_link_label(label, full, url_aliases), full))

    lines = [
        "---",
        f"id: {yaml_scalar(entity_id)}",
        f"name: {yaml_scalar(name)}",
        f"entity_type: {yaml_scalar(folder.rstrip('s'))}",
        f"source_url: {yaml_scalar(url)}",
        "---",
        "",
        f"# {name}",
        "",
        f"Source: {url}",
        "",
    ]
    if related:
        lines.extend(["## Related", ""])
        for key, value in related.items():
            if value:
                folder_map = {
                    "antibody": "Antibodies",
                    "payload": "Payloads",
                    "linker": "Linkers",
                    "antigen": "Antigens",
                    "target": "Targets",
                }
                lines.append(f"- {key}: {obsidian_link(value, folder_map.get(key))}")
        lines.append("")

    if internal_links:
        lines.extend(["## ADCdb Links", ""])
        seen = set()
        for label, href in internal_links:
            key = (label, href)
            if key in seen:
                continue
            seen.add(key)
            folder = folder_for_data_url(href)
            if folder:
                lines.append(f"- {obsidian_link(label, folder)} ([source]({href}))")
            else:
                lines.append(f"- [{label}]({href})")
        lines.append("")

    if iframe_urls:
        lines.extend(["## Embedded ADCdb Views", ""])
        for src in iframe_urls:
            full = urllib.parse.urljoin(BASE_URL, src)
            lines.append(f"- [embedded view]({full})")
        lines.append("")

    lines.extend(["## Extracted Page Text", "", "```text", text, "```", ""])
    path.write_text("\n".join(lines), encoding="utf-8")
    return path


def collect_data_links(page_html: str, wanted_folders: set[str] | None = None) -> dict[str, tuple[str, str]]:
    found: dict[str, tuple[str, str]] = {}
    for label, href in page_links(page_html):
        if "/data/" not in href:
            continue
        full = urllib.parse.urljoin(BASE_URL, href)
        folder = folder_for_data_url(full)
        if not folder:
            continue
        if wanted_folders and folder not in wanted_folders:
            continue
        found.setdefault(full, (folder, clean_text(label) or extract_id_from_url(full)))
    return found


def collect_antigen_inventory(crawler: Crawler, statuses: list[str], limit: int | None = None) -> list[dict[str, str]]:
    aux_path = crawler.data_dir / "auxiliary_options_by_status.json"
    if aux_path.exists() and not crawler.refresh:
        aux = json.loads(aux_path.read_text(encoding="utf-8"))
    else:
        aux = crawler.collect_auxiliary_options(statuses)

    names = sorted({name for status in statuses for name in aux.get("antigen", {}).get(status, [])})
    records: dict[str, dict[str, str]] = {}
    for name in names:
        if limit is not None and len(records) >= limit:
            break
        query = urllib.parse.urlencode({"search_api_fulltext": f'"{name}"'})
        url = f"{BASE_URL}/search/result/abt?{query}"
        cache = f"result_antigen_fulltext_{slugify(name)}.html"
        page_html = crawler.cached_get(url, cache)
        links = collect_data_links(page_html, {"Antigens"})
        for antigen_url, (_, label) in links.items():
            antigen_id = extract_id_from_url(antigen_url)
            records.setdefault(
                antigen_url,
                {
                    "name_query": name,
                    "label": label,
                    "antigen_id": antigen_id,
                    "antigen_url": antigen_url,
                    "result_url": url,
                },
            )
        print(f"[antigen-inventory] {name}: {len(links)} result link(s), {len(records)} unique")

    payload = list(records.values())
    (crawler.data_dir / "antigen_inventory.json").write_text(
        json.dumps(payload, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    return payload


def normalize_antigen_url(value: str) -> str:
    value = clean_text(value)
    if not value:
        return ""
    if value.startswith("http://") or value.startswith("https://"):
        return value
    if value.startswith("/data/abt/details/"):
        return urllib.parse.urljoin(BASE_URL, value)
    if re.fullmatch(r"TAR[A-Z0-9]+", value):
        return f"{BASE_URL}/data/abt/details/{value}"
    raise ValueError(f"Cannot interpret antigen id/url: {value}")


def collect_direct_antigen_records(values: list[str]) -> list[dict[str, str]]:
    records: list[dict[str, str]] = []
    seen: set[str] = set()
    for value in values:
        url = normalize_antigen_url(value)
        if not url or url in seen:
            continue
        seen.add(url)
        antigen_id = extract_id_from_url(url)
        records.append(
            {
                "name_query": antigen_id,
                "label": antigen_id,
                "antigen_id": antigen_id,
                "antigen_url": url,
                "result_url": "",
            }
        )
    return records


def generate_antigen_centric(crawler: Crawler, records: list[dict[str, str]], include_support: bool = True) -> None:
    index_lines = [
        "# ADCdb Antigen-Centric Knowledge Base",
        "",
        f"Generated from {BASE_URL}",
        "",
        "## Antigen Index",
        "",
        "| Antigen | ID | Query | Source |",
        "| --- | --- | --- | --- |",
    ]
    support_urls: dict[str, tuple[str, str]] = {}
    url_aliases: dict[str, str] = {}
    antigen_pages: list[dict[str, object]] = []

    for idx, record in enumerate(records, start=1):
        url = record["antigen_url"]
        antigen_id = record["antigen_id"]
        page_html = crawler.fetch_detail(url, "antigen", antigen_id)
        text = page_text(page_html)
        title = extract_title(text, record.get("label") or antigen_id, "Antigens")
        links = page_links(page_html)
        iframe_urls = page_iframes(page_html)
        for iframe_url in iframe_urls:
            full_iframe_url = urllib.parse.urljoin(BASE_URL, iframe_url)
            iframe_id = slugify(urllib.parse.urlparse(full_iframe_url).path.strip("/").replace("/", "_"))
            crawler.cached_get(full_iframe_url, f"iframe_{iframe_id}.html")
        url_aliases[url] = title
        antigen_pages.append(
            {
                "idx": idx,
                "record": record,
                "url": url,
                "antigen_id": antigen_id,
                "text": text,
                "title": title,
                "links": links,
                "iframe_urls": iframe_urls,
            }
        )

        if include_support:
            for support_url, (folder, label) in collect_data_links(page_html).items():
                if folder == "Antigens" and support_url == url:
                    continue
                support_urls.setdefault(support_url, (folder, label))

    if include_support:
        support_pages: list[dict[str, object]] = []
        for url, (folder, label) in sorted(support_urls.items()):
            entity_id = extract_id_from_url(url)
            page_html = crawler.fetch_detail(url, prefix_for_folder(folder), entity_id)
            text = page_text(page_html)
            title = extract_title(text, label or entity_id, folder)
            links = page_links(page_html)
            url_aliases[url] = title
            support_pages.append(
                {
                    "url": url,
                    "folder": folder,
                    "entity_id": entity_id,
                    "text": text,
                    "title": title,
                    "links": links,
                }
            )
            print(f"[support] {folder} {entity_id} {title}")
    else:
        support_pages = []

    for page in antigen_pages:
        write_detail_markdown(
            crawler.outdir,
            "Antigens",
            str(page["title"]),
            str(page["antigen_id"]),
            str(page["url"]),
            str(page["text"]),
            page["links"],  # type: ignore[arg-type]
            url_aliases=url_aliases,
            iframe_urls=page["iframe_urls"],  # type: ignore[arg-type]
            overwrite=crawler.refresh,
        )
        record = page["record"]  # type: ignore[assignment]
        index_lines.append(
            "| "
            + " | ".join(
                [
                    obsidian_link(str(page["title"]), "Antigens"),
                    str(page["antigen_id"]),
                    clean_text(record.get("name_query", "")),  # type: ignore[union-attr]
                    f"[source]({page['url']})",
                ]
            )
            + " |"
        )
        print(f"[markdown] Antigen {int(page['idx']):04d} {page['antigen_id']} {page['title']}")

    for page in support_pages:
        write_detail_markdown(
            crawler.outdir,
            str(page["folder"]),
            str(page["title"]),
            str(page["entity_id"]),
            str(page["url"]),
            str(page["text"]),
            page["links"],  # type: ignore[arg-type]
            url_aliases=url_aliases,
            overwrite=crawler.refresh,
        )

    (crawler.outdir / "Antigen_Index.md").write_text("\n".join(index_lines) + "\n", encoding="utf-8")


def generate_obsidian(crawler: Crawler, records: list[SearchRecord], limit: int | None = None) -> None:
    index_lines = [
        "# ADCdb Obsidian Knowledge Base",
        "",
        f"Generated from {BASE_URL}",
        "",
        "## ADC Index",
        "",
        "| ADC | ID | Status | Antibody | Payload | Linker | Indication |",
        "| --- | --- | --- | --- | --- | --- | --- |",
    ]
    entity_urls: dict[str, tuple[str, str]] = {}
    url_aliases: dict[str, str] = {}

    for idx, record in enumerate(records):
        if limit is not None and idx >= limit:
            break
        if not record.adc_url:
            continue
        adc_id = record.adc_id or extract_id_from_url(record.adc_url)
        page_html = crawler.fetch_detail(record.adc_url, "adc", adc_id)
        text = page_text(page_html)
        title = extract_title(text, record.name, "ADCs")
        links = page_links(page_html)

        related = {
            "antibody": record.antibody_name or value_after_label(text, "Antibody Name"),
            "antigen": value_after_label(text, "Antigen Name"),
            "payload": record.payload_name or value_after_label(text, "Payload Name"),
            "target": value_after_label(text, "Therapeutic Target"),
            "linker": record.linker_name or value_after_label(text, "Linker Name"),
        }
        if record.antibody_url:
            entity_urls.setdefault(record.antibody_url, ("Antibodies", record.antibody_name or extract_id_from_url(record.antibody_url)))
            if record.antibody_name:
                url_aliases[record.antibody_url] = record.antibody_name
        if record.payload_url:
            entity_urls.setdefault(record.payload_url, ("Payloads", record.payload_name or extract_id_from_url(record.payload_url)))
            if record.payload_name:
                url_aliases[record.payload_url] = record.payload_name
        if record.linker_url:
            entity_urls.setdefault(record.linker_url, ("Linkers", record.linker_name or extract_id_from_url(record.linker_url)))
            if record.linker_name:
                url_aliases[record.linker_url] = record.linker_name

        for label, href in links:
            full = urllib.parse.urljoin(BASE_URL, href)
            entity_id = extract_id_from_url(full)
            if "/data/antibody/details/" in href:
                entity_urls.setdefault(full, ("Antibodies", clean_text(label) or record.antibody_name or entity_id))
                if record.antibody_name:
                    url_aliases.setdefault(full, record.antibody_name)
            elif "/data/payload/details/" in href:
                entity_urls.setdefault(full, ("Payloads", clean_text(label) or record.payload_name or entity_id))
                if record.payload_name:
                    url_aliases.setdefault(full, record.payload_name)
            elif "/data/linker/details/" in href:
                entity_urls.setdefault(full, ("Linkers", clean_text(label) or record.linker_name or entity_id))
                if record.linker_name:
                    url_aliases.setdefault(full, record.linker_name)
            elif "/data/abt/details/" in href:
                entity_urls.setdefault(full, ("Antigens", related.get("antigen") or clean_text(label) or entity_id))
                if related.get("antigen"):
                    url_aliases.setdefault(full, related["antigen"])
            elif "/data/plt/details/" in href:
                entity_urls.setdefault(full, ("Targets", related.get("target") or clean_text(label) or entity_id))
                if related.get("target"):
                    url_aliases.setdefault(full, related["target"])

        url_aliases.setdefault(record.adc_url, title)
        adc_md = crawler.outdir / "ADCs" / f"{slugify(title, adc_id)}.md"
        adc_existed = adc_md.exists()
        write_detail_markdown(
            crawler.outdir,
            "ADCs",
            title,
            adc_id,
            record.adc_url,
            text,
            links,
            related,
            url_aliases,
            overwrite=crawler.refresh,
        )
        index_lines.append(
            "| "
            + " | ".join(
                [
                    obsidian_link(title, "ADCs"),
                    adc_id,
                    record.status or value_after_label(text, "Drug Status"),
                    obsidian_link(related.get("antibody", ""), "Antibodies"),
                    obsidian_link(related.get("payload", ""), "Payloads"),
                    obsidian_link(related.get("linker", ""), "Linkers"),
                    clean_text(record.representative_indication),
                ]
            )
            + " |"
        )
        action = "skip-md" if adc_existed and not crawler.refresh else "markdown"
        print(f"[{action}] ADC {idx + 1:04d} {adc_id} {title}")

    for url, (folder, label) in sorted(entity_urls.items()):
        entity_id = extract_id_from_url(url)
        page_html = crawler.fetch_detail(url, folder.rstrip("s").lower(), entity_id)
        text = page_text(page_html)
        title = extract_title(text, label or entity_id, folder)
        links = page_links(page_html)
        url_aliases.setdefault(url, title)
        md_path = crawler.outdir / folder / f"{slugify(title, entity_id)}.md"
        existed = md_path.exists()
        write_detail_markdown(
            crawler.outdir,
            folder,
            title,
            entity_id,
            url,
            text,
            links,
            url_aliases=url_aliases,
            overwrite=crawler.refresh,
        )
        action = "skip-md" if existed and not crawler.refresh else "markdown"
        print(f"[{action}] {folder} {entity_id} {title}")

    (crawler.outdir / "Index.md").write_text("\n".join(index_lines) + "\n", encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description="Download ADCdb pages and build an Obsidian Markdown vault.")
    parser.add_argument("--outdir", default="ADCdb_Obsidian", help="Output vault directory")
    parser.add_argument("--delay", type=float, default=1.5, help="Delay between HTTP requests")
    parser.add_argument("--limit", type=int, default=None, help="Limit ADC records for testing")
    parser.add_argument("--statuses", nargs="*", default=STATUSES, help="Statuses to collect")
    parser.add_argument("--refresh", action="store_true", help="Refetch pages even if cached")
    parser.add_argument("--inventory-only", action="store_true", help="Only collect inventory JSON")
    parser.add_argument("--from-inventory", action="store_true", help="Skip collection and use existing inventory JSON")
    parser.add_argument("--harvest-adc-links-from-raw", action="store_true", help="Merge all /data/adc/details/DRG... links found in cached raw HTML into adc_inventory.json before generating Markdown")
    parser.add_argument("--skip-auxiliary", action="store_true", help="Do not collect antibody/antigen/payload/linker search option JSON")
    parser.add_argument("--auxiliary-only", action="store_true", help="Only collect antibody/antigen/payload/linker search option JSON")
    parser.add_argument("--antigen-centric", action="store_true", help="Build a vault primarily from antigen search results and antigen detail pages")
    parser.add_argument("--no-support-pages", action="store_true", help="In antigen-centric mode, do not download linked ADC/antibody/payload/linker/target support pages")
    parser.add_argument("--antigen-id", action="append", default=[], help="Directly download an antigen details page by TAR id or /data/abt/details URL. Repeatable.")
    parser.add_argument("--antigen-ids-file", default="", help="File with one antigen TAR id or details URL per line")
    args = parser.parse_args()

    crawler = Crawler(Path(args.outdir), delay=args.delay, refresh=args.refresh)
    if not args.skip_auxiliary:
        crawler.collect_auxiliary_options(args.statuses)

    if args.auxiliary_only:
        print(f"Done. Output: {Path(args.outdir).resolve()}")
        return

    if args.antigen_centric:
        direct_antigens = list(args.antigen_id)
        if args.antigen_ids_file:
            ids_path = Path(args.antigen_ids_file)
            direct_antigens.extend(
                line.strip()
                for line in ids_path.read_text(encoding="utf-8").splitlines()
                if line.strip() and not line.strip().startswith("#")
            )
        if direct_antigens:
            records = collect_direct_antigen_records(direct_antigens)
            if args.limit is not None:
                records = records[: args.limit]
            (crawler.data_dir / "antigen_inventory.json").write_text(
                json.dumps(records, indent=2, ensure_ascii=False),
                encoding="utf-8",
            )
        else:
            records = collect_antigen_inventory(crawler, args.statuses, limit=args.limit)
        generate_antigen_centric(crawler, records, include_support=not args.no_support_pages)
        print(f"Done. Output: {Path(args.outdir).resolve()}")
        return

    if args.harvest_adc_links_from_raw:
        records = crawler.harvest_adc_links_from_raw()
        if args.limit is not None:
            records = records[: args.limit]
    elif args.from_inventory:
        records = crawler.load_inventory()
        if not records:
            inventory_path = crawler.data_dir / "adc_inventory.json"
            raise SystemExit(
                "No inventory records found. "
                f"Expected a populated inventory at {inventory_path}. "
                "For the first run, omit --from-inventory so the script can collect ADC names from ADCdb."
            )
        if args.limit is not None:
            records = records[: args.limit]
    else:
        records = crawler.collect_inventory(args.statuses, limit=args.limit)

    if not args.inventory_only:
        generate_obsidian(crawler, records, limit=args.limit)
    print(f"Done. Output: {Path(args.outdir).resolve()}")


if __name__ == "__main__":
    main()
