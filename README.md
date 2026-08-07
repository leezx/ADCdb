# ADCdb Obsidian Builder

This workspace contains a conservative crawler/renderer for building an
Obsidian-style Markdown vault from public ADCdb pages.

## What It Does

- Reads ADC names from the ADCdb status selector:
  `Approved`, `Phase 3`, `Phase 2`, `Phase 1`, `Investigative`.
- Resolves each ADC search result to its public detail page.
- Saves raw HTML under `_raw/html`.
- Writes structured inventory to `_data/adc_inventory.json`.
- Generates Markdown pages with Obsidian double links:
  `ADCs/`, `Antibodies/`, `Payloads/`, `Linkers/`, `Antigens/`, `Targets/`.
- Creates `Index.md`.

## Run A Small Test

```bash
python3 scripts/adcdb_to_obsidian.py --outdir ADCdb_Obsidian_test --limit 1 --delay 1.5
```

## First Full Vault Build

```bash
python3 scripts/adcdb_to_obsidian.py --outdir ADCdb_Obsidian --delay 2.0
```

The run is resumable. Existing raw HTML files are reused by default.
By default this also downloads the Step 2 option JSON from these extra search
entrances, which helps audit missing entities:

- `/search/antibody_search`
- `/search/antigen_search`
- `/search/payload_search`
- `/search/linker_search`

The parsed outputs are:

- `_data/auxiliary_options_by_status.json`
- `_data/auxiliary_options_unique.json`

The raw AJAX responses are saved as `_data/ajax_options_<kind>_<status>.json`.

Do not use `--from-inventory` on the first run. That flag only works after
`ADCdb_Obsidian/_data/adc_inventory.json` has already been created.

## Regenerate Markdown From Existing Inventory

```bash
python3 scripts/adcdb_to_obsidian.py --outdir ADCdb_Obsidian --from-inventory --delay 2.0
```

Use this after an earlier run created `_data/adc_inventory.json`, especially if
you changed Markdown rendering rules and want to rebuild pages without
collecting the search inventory again.

To skip downloading auxiliary option JSON during a Markdown regeneration:

```bash
python3 scripts/adcdb_to_obsidian.py --outdir ADCdb_Obsidian --from-inventory --skip-auxiliary --delay 2.0
```

## Expand ADC Pages From Links Found In Raw HTML

The ADC status dropdown exposes only a smaller named subset. Many more ADC
details pages appear as links inside antigen, payload, linker, antibody, and ADC
details pages. To merge every discovered `/data/adc/details/DRG...` URL from
cached raw HTML into `adc_inventory.json` and then generate those ADC pages:

```bash
python3 scripts/adcdb_to_obsidian.py --outdir ADCdb_Obsidian --harvest-adc-links-from-raw --skip-auxiliary --delay 2.0
```

## Download Only Auxiliary Search JSON

```bash
python3 scripts/adcdb_to_obsidian.py --outdir ADCdb_Obsidian --auxiliary-only --delay 2.0
```

## Build An Antigen-Centric Vault

Use this when you want antigen pages to be the primary knowledge layer, with
linked ADC/antibody/payload/linker/target pages saved as supporting pages.
The core pages are ADCdb Antigen Info details pages such as
`/data/abt/details/TAR0TAYXC`.

```bash
python3 scripts/adcdb_to_obsidian.py --outdir ADCdb_Antigen --antigen-centric --delay 2.0
```

This creates:

- `Antigen_Index.md`
- `Antigens/*.md`
- supporting `ADCs/`, `Antibodies/`, `Payloads/`, `Linkers/`, and `Targets/`
  pages when they are linked from antigen pages
- `_data/antigen_inventory.json`

To download only antigen pages and skip supporting pages:

```bash
python3 scripts/adcdb_to_obsidian.py --outdir ADCdb_Antigen --antigen-centric --no-support-pages --delay 2.0
```

To directly download known Antigen Info details pages by TAR id:

```bash
python3 scripts/adcdb_to_obsidian.py --outdir ADCdb_Antigen --antigen-centric --antigen-id TAR0TAYXC --no-support-pages --skip-auxiliary --delay 2.0
```

For a file of TAR ids or details URLs:

```bash
python3 scripts/adcdb_to_obsidian.py --outdir ADCdb_Antigen --antigen-centric --antigen-ids-file antigen_ids.txt --delay 2.0
```

## Refresh Cached Pages

```bash
python3 scripts/adcdb_to_obsidian.py --outdir ADCdb_Obsidian --refresh --delay 2.0
```

## Notes

ADCdb's `robots.txt` disallows `/search/`, so full bulk use should be done
sparingly and preferably after asking the ADCdb maintainers for a data dump or
permission. The script uses a delay between requests and stores raw pages so you
do not need to refetch repeatedly.
