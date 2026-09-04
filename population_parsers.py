"""population_parsers.py

The four Census sources import_population.py pulls from don't share a format -- a flat
text file, two different Excel layouts -- so the parsing logic for each lives here as its
own small, pure function (bytes in, {year: population} out) rather than inline in the
main script. That split is what makes test_population_parsers.py possible: these
functions can be exercised against small synthetic files that mimic each real source's
known layout, without touching the network or a database.

See import_population.py's module docstring for the full context on why four sources
are needed and how they get combined.
"""

import re
from io import BytesIO

import pandas as pd

# A minimum sanity bound for "is this cell actually a national population figure" --
# used repeatedly below to tell real population numbers apart from years, footnote
# markers, and other small numbers that share a table with them. The US has been above
# this since well before 1900, so it's safe for our whole date range.
MIN_PLAUSIBLE_POPULATION = 1_000_000


def clean_number(raw):
    """Strip commas/footnote markers/whitespace from a cell and parse it as an int.
    Returns None if what's left isn't a clean number."""
    if raw is None:
        return None
    text_val = str(raw).strip()
    # Keep only digits and commas, in case a footnote marker (an asterisk, a superscript
    # digit, etc.) is stuck to the number -- e.g. "281,421,906 1" -> "281,421,906".
    match = re.search(r"[\d,]{4,}", text_val)
    if not match:
        return None
    digits = match.group(0).replace(",", "")
    if not digits.isdigit():
        return None
    return int(digits)


def parse_1900_1999(raw_bytes, log=print):
    """The historical estimates file is a simple text table: title/footnote lines mixed
    in with one data line per year, of the exact form " July 1, 1999      272,690,813
    2,442,810              0.90" (date, population, year-over-year change, percent
    change). Matching on the "July 1, <year>" lead-in -- rather than a bare year at the
    start of the line -- is what distinguishes real data rows from everything else in
    the file, including a footnote block later on that gives alternate 1917-1919
    figures "including Armed Forces overseas": those lines start with a bare year
    ("1919  105,063,000") and are deliberately NOT matched here, so the main series
    stays on one consistent basis (resident population, as the file's own header
    table reports it) rather than mixing in that one-off adjustment for 3 years."""
    text_content = raw_bytes.decode("utf-8", errors="replace")
    line_pattern = re.compile(r"^\s*July\s+1,\s*(\d{4})\s+([\d,]+)", re.IGNORECASE)

    results = {}
    for line in text_content.splitlines():
        m = line_pattern.match(line)
        if not m:
            continue
        year = int(m.group(1))
        population = clean_number(m.group(2))
        if population is not None and population >= MIN_PLAUSIBLE_POPULATION:
            results[year] = population

    log(f"  Parsed {len(results)} years from the 1900-1999 file.")
    if results:
        # dict.fromkeys(...) dedupes while keeping order, so a small result set (first
        # 3 == last 3) doesn't get printed twice.
        sample_years = list(dict.fromkeys(sorted(results)[:3] + sorted(results)[-3:]))
        for y in sample_years:
            log(f"    {y}: {results[y]:,}")
    return results


def parse_2000_2010(raw_bytes, log=print, excel_engine="xlrd"):
    """This table has age groups down the rows and years across the columns, with a
    "BOTH SEXES" row near the top holding the actual national totals (confirmed against
    the real file -- it's not labeled "Total" anywhere, which is what the first version
    of this parser assumed and got wrong).

    The year columns aren't all in one header row either: 2000-2009 are bare years
    ("2000", "2001", ...) in one row, while the 2000 and 2010 census-anchor columns and
    the July 1, 2010 postcensal column live in the row above, decorated like
    "April 1, 20001" / "July 1, 20103" -- a footnote-reference digit stuck directly onto
    the year with no separating space. So this scans every cell across the first few
    rows (not just a single "best" row) for either shape, and deliberately keeps only
    "July 1, <year>" columns (never "April 1, <year>") so the whole series stays on the
    same July-1 basis the 1900-1999 and later sources use -- April 1 is a census day
    count, not a mid-year estimate, and would be a slightly different kind of figure.
    """
    raw = pd.read_excel(BytesIO(raw_bytes), header=None, engine=excel_engine)

    bare_year_pattern = re.compile(r"^(200\d|2010)$")
    july_labeled_pattern = re.compile(r"july\s+1,?\s*(\d{4})\d?", re.IGNORECASE)

    year_cols = {}
    for row_idx in range(min(6, len(raw))):
        for col_idx, cell in enumerate(raw.iloc[row_idx]):
            cell_text = str(cell).strip()
            year = None
            # A "bare year" header cell like 2001 often comes back from pandas as a
            # float (2001.0, since the column is a mix of numbers), not the string
            # "2001" -- normalize through int(float(...)) so both that and an actual
            # text cell match the same way.
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

    if not year_cols:
        raise ValueError(
            "Couldn't find any year columns in the 2000-2010 source's first few rows -- "
            "the table layout may have changed. Open the file by hand to check."
        )
    log(f"  Found year columns: {sorted(year_cols)}")

    total_row_idx = None
    for row_idx in range(len(raw)):
        first_cells = " ".join(str(c) for c in raw.iloc[row_idx, :4].tolist()).lower()
        if "total" in first_cells or "both sexes" in first_cells:
            total_row_idx = row_idx
            break

    if total_row_idx is None:
        raise ValueError(
            "Couldn't find a 'Total'/'Both Sexes' row in the 2000-2010 source -- the "
            "table layout may have changed. Open the file by hand to check."
        )
    log(f"  Found totals row at index {total_row_idx}.")

    results = {}
    for year, col_idx in year_cols.items():
        population = clean_number(raw.iat[total_row_idx, col_idx])
        if population is not None and population >= MIN_PLAUSIBLE_POPULATION:
            results[year] = population

    log(f"  Parsed {len(results)} years from the 2000-2010 file.")
    for y in sorted(results):
        log(f"    {y}: {results[y]:,}")
    return results


def parse_monthly_national_totals(raw_bytes, vintage_label, log=print, excel_engine="openpyxl"):
    """Confirmed against both real files (2010-2019 and 2020-2026) directly -- this is
    NOT the "one row per month with a decorated date label" shape a first guess assumed.
    The real layout is hierarchical: a standalone row holding just a bare year (e.g.
    "2010", every other cell blank) is followed by twelve sub-rows, one per month, each
    labeled like ".July 1" with NO year in that row at all -- the year only appears once,
    on its own header row above that whole block, and applies to every month-row below
    it until the next bare-year row starts a new block. So this tracks "the most recent
    bare-year row seen" while scanning down, and pairs it with the July-1 sub-row.

    It also matters WHICH column gets read: each month-row has five different
    population-like figures side by side (Resident Population; Resident Population Plus
    Armed Forces Overseas; Civilian Population; Civilian Noninstitutionalized Population;
    Household Population), and "Plus Armed Forces Overseas" is always the larger of the
    first two -- so a "just take the biggest number in the row" heuristic would silently
    grab the wrong column. This instead finds the "Resident Population" column
    specifically (EXACT match against the header row, not merely a substring -- "Resident
    Population Plus Armed Forces Overseas" starts with the same words and must not match),
    matching the plain "resident population" basis the 1900-1999 source uses for most of
    its range."""
    raw = pd.read_excel(BytesIO(raw_bytes), header=None, engine=excel_engine)

    population_col = None
    for row_idx in range(min(6, len(raw))):
        for col_idx, cell in enumerate(raw.iloc[row_idx]):
            if str(cell).strip().lower() == "resident population":
                population_col = col_idx
                break
        if population_col is not None:
            break

    if population_col is None:
        raise ValueError(
            f"Couldn't find a 'Resident Population' column in the {vintage_label} "
            "source -- the table layout may have changed. Open the file by hand to check."
        )
    log(f"  Found 'Resident Population' column at index {population_col}.")

    # A trailing footnote reference like "2019 [1]" (confirmed in both real files, on
    # whichever year a mid-series methodology change happened) means this can't require
    # the WHOLE cell to be just the 4 digits -- matching only the leading year and
    # ignoring anything after it is what makes those rows count. (Missing this the first
    # time silently mis-attributed two full years of July figures to the prior year,
    # each overwriting the last, rather than erroring -- worth being extra sure of here.)
    year_row_pattern = re.compile(r"^((?:19|20)\d{2})\b")
    july_row_pattern = re.compile(r"july\s*1\b", re.IGNORECASE)

    results = {}
    current_year = None
    for row_idx in range(len(raw)):
        first_cell = str(raw.iat[row_idx, 0]).strip()
        year_match = year_row_pattern.match(first_cell)
        if year_match:
            current_year = int(year_match.group(1))
            continue
        if current_year is not None and july_row_pattern.search(first_cell):
            population = clean_number(raw.iat[row_idx, population_col])
            if population is not None and population >= MIN_PLAUSIBLE_POPULATION:
                results[current_year] = population

    log(f"  Parsed {len(results)} July-1 years from the {vintage_label} file.")
    for y in sorted(results):
        log(f"    {y}: {results[y]:,}")
    return results
