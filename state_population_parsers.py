"""state_population_parsers.py

Parsing logic for import_state_population.py's three Census sources. Unlike the national
population data (population_parsers.py), which needed three structurally different
parsers, all three state-level sources turned out -- confirmed by downloading and
inspecting the real files via debug_dump_state_sources.py -- to share the same "wide"
table shape: one row per geography (nation, four regions, 50 states + DC, Puerto Rico),
one column per year. So a single parse_state_wide_table() function handles all three
vintages; import_state_population.py just calls it three times with different bytes.

Confirmed real layout (all three files):
  - Row 0-1: title text.
  - A header row naming a "census/estimates base" column (labeled "April 1, <year>",
    sometimes further decorated with a footnote digit or turned into a datetime by
    pandas) immediately followed by the "as of July 1" year columns -- 2000-2010 also
    tags its last column "July 1, <year>3" (a census-anchor postcensal figure) the same
    way. Only "July 1" (bare-year or explicitly labeled) columns are kept, exactly as
    the national 2000-2010 parser does, so the whole series stays on one consistent
    July-1 basis; "April 1"/"Census"/"Estimates Base" columns are deliberately excluded.
  - The next row down holds the bare year headers as ints (2000, 2001.0, ... -- pandas
    hands most of these back as floats, same float64 quirk as the national data).
  - Geography rows: "United States", then "Northeast"/"Midwest"/"South"/"West" (region
    subtotals), then the 50 states + DC each prefixed with a leading "." (e.g.
    ".Alabama"), then a blank row, then "Puerto Rico" -- EXCEPT in the 2020-2025 file,
    where Puerto Rico also gets a "." prefix like a state. So state rows can't be
    reliably picked out by the "." prefix alone; this matches row labels (with any
    leading "." stripped) against an explicit whitelist of the 50 state names + "District
    of Columbia" instead, which sidesteps that inconsistency entirely and would also
    ignore any other non-state row a future vintage happens to add.
"""

import re
from io import BytesIO

import pandas as pd

# A minimum sanity bound for "is this cell actually a state population figure" -- well
# under the smallest real state value in the 2000-2026 range (Wyoming, ~490,000), so it
# still catches a parser grabbing the wrong column/row entirely.
MIN_PLAUSIBLE_STATE_POPULATION = 50_000

STATE_NAMES = frozenset(
    [
        "Alabama", "Alaska", "Arizona", "Arkansas", "California", "Colorado",
        "Connecticut", "Delaware", "District of Columbia", "Florida", "Georgia",
        "Hawaii", "Idaho", "Illinois", "Indiana", "Iowa", "Kansas", "Kentucky",
        "Louisiana", "Maine", "Maryland", "Massachusetts", "Michigan", "Minnesota",
        "Mississippi", "Missouri", "Montana", "Nebraska", "Nevada", "New Hampshire",
        "New Jersey", "New Mexico", "New York", "North Carolina", "North Dakota",
        "Ohio", "Oklahoma", "Oregon", "Pennsylvania", "Rhode Island", "South Carolina",
        "South Dakota", "Tennessee", "Texas", "Utah", "Vermont", "Virginia",
        "Washington", "West Virginia", "Wisconsin", "Wyoming",
    ]
)


def clean_number(raw):
    """Same as population_parsers.clean_number: strip commas/footnote markers/whitespace
    from a cell and parse it as an int. Returns None if what's left isn't a clean number."""
    if raw is None:
        return None
    text_val = str(raw).strip()
    match = re.search(r"[\d,]{4,}", text_val)
    if not match:
        return None
    digits = match.group(0).replace(",", "")
    if not digits.isdigit():
        return None
    return int(digits)


def _find_year_columns(raw):
    """Scan the first few rows for July-1 year columns: either a bare year (as an int or
    a pandas float64, e.g. 2001.0) sitting in the header-year row, or a cell explicitly
    labeled "July 1, <year>" (optionally with a trailing footnote digit stuck to the
    year, as in "July 1, 20103"). April-1/Census/Estimates-Base columns are deliberately
    NOT matched by either pattern, so they're excluded without needing an explicit
    blocklist."""
    bare_year_pattern = re.compile(r"^(19\d{2}|20\d{2})$")
    july_labeled_pattern = re.compile(r"july\s+1,?\s*(\d{4})\d?", re.IGNORECASE)

    year_cols = {}
    for row_idx in range(min(6, len(raw))):
        for col_idx, cell in enumerate(raw.iloc[row_idx]):
            cell_text = str(cell).strip()
            year = None
            try:
                as_int = int(float(cell_text))
                if bare_year_pattern.match(str(as_int)):
                    year = as_int
            except (TypeError, ValueError):
                pass
            if year is None:
                m = july_labeled_pattern.search(cell_text)
                if m:
                    year = int(m.group(1))
            if year is not None:
                year_cols[year] = col_idx
    return year_cols


def parse_state_wide_table(raw_bytes, vintage_label, log=print, excel_engine="openpyxl"):
    """Parse one of the three state-level Census sources into {state_name: {year: population}}."""
    raw = pd.read_excel(BytesIO(raw_bytes), header=None, engine=excel_engine)

    year_cols = _find_year_columns(raw)
    if not year_cols:
        raise ValueError(
            f"Couldn't find any July-1 year columns in the {vintage_label} state source -- "
            "the table layout may have changed. Open the file by hand to check."
        )
    log(f"  Found year columns: {sorted(year_cols)}")

    results = {}
    for row_idx in range(len(raw)):
        first_cell = raw.iat[row_idx, 0]
        if pd.isna(first_cell):
            continue
        label = str(first_cell).strip().lstrip(".").strip()
        if label not in STATE_NAMES:
            continue
        by_year = {}
        for year, col_idx in year_cols.items():
            population = clean_number(raw.iat[row_idx, col_idx])
            if population is not None and population >= MIN_PLAUSIBLE_STATE_POPULATION:
                by_year[year] = population
        results[label] = by_year

    missing_states = STATE_NAMES - set(results)
    if missing_states:
        raise ValueError(
            f"Only found {len(results)} of {len(STATE_NAMES)} states/DC in the "
            f"{vintage_label} state source -- missing: {sorted(missing_states)}. The "
            "table layout may have changed. Open the file by hand to check."
        )

    total_cells = sum(len(v) for v in results.values())
    log(f"  Parsed {len(results)} states/DC x {len(year_cols)} years ({total_cells} values) from the {vintage_label} file.")
    return results
