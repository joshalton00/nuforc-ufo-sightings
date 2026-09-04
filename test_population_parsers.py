"""test_population_parsers.py

Exercises each parser in population_parsers.py against a small synthetic file built to
mimic the real Census source's known layout -- title/footnote noise, decorated header
cells, a Total row buried among other rows, etc. This can't replace running the real
thing (no network access here to actually fetch and compare against it), but it does
catch logic bugs in the parsing approach itself before Josh spends a run finding them.

Run with:  python test_population_parsers.py
"""

from io import BytesIO

import pandas as pd

from population_parsers import (
    clean_number,
    parse_1900_1999,
    parse_2000_2010,
    parse_monthly_national_totals,
)

FAILURES = []


def check(label, condition, detail=""):
    status = "PASS" if condition else "FAIL"
    print(f"[{status}] {label}" + (f" -- {detail}" if detail and not condition else ""))
    if not condition:
        FAILURES.append(label)


def silent(*args, **kwargs):
    pass


def test_clean_number():
    check("clean_number strips commas", clean_number("281,421,906") == 281421906)
    check("clean_number strips a trailing footnote marker", clean_number("281,421,906 1") == 281421906)
    check("clean_number handles a bare int", clean_number(76094000) == 76094000)
    check("clean_number rejects a short/non-number cell", clean_number("N/A") is None)
    check("clean_number rejects None", clean_number(None) is None)


def test_parse_1900_1999():
    # This mirrors the REAL file's exact shape (confirmed by downloading it directly):
    # a title block, a "Date / Population / Change / Percent Change" header, data lines
    # of the form " July 1, <year>      <population>      <change>      <pct>", and a
    # footnote block giving ALTERNATE 1917-1919 figures "including Armed Forces
    # overseas" -- those bare-year footnote lines must NOT be picked up, since they'd
    # silently override the main series' figures for those 3 years with a different
    # population basis than the rest of the file uses.
    synthetic = """
Historical National Population Estimates:  July 1, 1900 to July 1, 1999

Source: Population Estimates Program, Population Division, U.S. Census Bureau

                     National           Population        Average Annual
     Date           Population            Change          Percent Change

 July 1, 1999      272,690,813          2,442,810              0.90
 July 1, 1950      152,271,417          3,083,287              2.05
 July 1, 1919      104,514,000          1,306,000              1.26
 July 1, 1900       76,094,000             ---                  ---

NOTE:
Estimates of the population including Armed Forces overseas are as follows:

1919  105,063,000
1918  104,550,000
1917  103,414,000
""".encode("utf-8")

    result = parse_1900_1999(synthetic, log=silent)
    check(
        "parse_1900_1999 extracts the main-table rows only, ignoring the footnote block",
        result == {1900: 76094000, 1919: 104514000, 1950: 152271417, 1999: 272690813},
        detail=str(result),
    )


def _write_xlsx(rows):
    buf = BytesIO()
    pd.DataFrame(rows).to_excel(buf, header=False, index=False, engine="openpyxl")
    buf.seek(0)
    return buf.read()


def test_parse_2000_2010():
    # Mirrors the REAL file's exact (and initially surprising) shape, confirmed by
    # downloading and inspecting it directly:
    #   - the total row is labeled "BOTH SEXES", never "Total"
    #   - year headers are split across TWO rows: 2000-2009 are bare years in one row,
    #     while the April-1-census-day and July-1-2010 columns are decorated with a
    #     footnote digit stuck directly onto the year ("April 1, 20001") in the row above
    #   - "April 1" columns must be excluded -- only "July 1" (or bare, which already
    #     means July 1 here) belongs in a July-1-consistent series
    #   - pandas reads the bare year cells back as floats (2001.0), not the string "2001"
    rows = [
        ["Table 1. Intercensal Estimates ... for the United States: April 1, 2000 to July 1, 2010", None, None, None, None, None],
        ["Sex and Age", "April 1, 20001", "Intercensal Estimates (as of July 1)", None, "April 1, 20102", "July 1, 20103"],
        [None, None, 2000, 2001, None, None],
        ["BOTH SEXES", 281424600, 282162411, 284968955, 308745538, 309349689],
        [".Under 5 years", 19176154, 19178293, 19298217, 20201362, 20200529],
    ]
    # Row 2 above only has 2 of the 10 real bare-year columns (2000, 2001) -- enough to
    # exercise the split-header/float-cell logic without retyping all 10.
    raw_bytes = _write_xlsx(rows)
    result = parse_2000_2010(raw_bytes, log=silent, excel_engine="openpyxl")
    check(
        "parse_2000_2010 finds BOTH SEXES values for bare years and the July-1-2010 column, excluding April-1 columns",
        result == {2000: 282162411, 2001: 284968955, 2010: 309349689},
        detail=str(result),
    )


def test_parse_monthly_national_totals():
    # Mirrors the REAL file's exact (and initially surprising) shape, confirmed by
    # downloading and inspecting BOTH the 2010-2019 and 2020-2026 releases directly:
    #   - the year is NOT on the same row as the month -- it's a standalone row (bare
    #     "2010", every other cell blank), followed by 12 month-only sub-rows like
    #     ".July 1" that carry no year at all and apply to whichever year-row came before
    #   - a mid-series methodology-change year is suffixed with a footnote reference,
    #     e.g. "2019 [1]" -- getting this wrong the first time silently mis-attributed
    #     two real years of data to the prior year rather than erroring, which is why
    #     this is tested explicitly rather than trusted to look obviously broken
    #   - each month-row has FIVE population-like columns side by side (Resident
    #     Population; Resident Population Plus Armed Forces Overseas; ...), and the
    #     "Plus Armed Forces Overseas" figure is always the larger of the first two --
    #     so the fixture makes sure a "just take the biggest number" approach would get
    #     the wrong column, to guard against that regression too
    rows = [
        ["Table 1. Monthly Population Estimates for the United States"],
        ["Year and Month", "Resident Population", "Resident Population Plus Armed Forces Overseas"],
        ["2010", None, None],
        [".April 1", "308,758,105", "309,191,211"],
        [".July 1", "309,321,666", "309,741,279"],
        ["2011", None, None],
        [".July 1", "311,556,874", "311,986,682"],
        ["2019 [1]", None, None],
        [".July 1", "328,239,523", "328,475,998"],
    ]
    raw_bytes = _write_xlsx(rows)
    result = parse_monthly_national_totals(raw_bytes, "2010-2019", log=silent, excel_engine="openpyxl")
    check(
        "parse_monthly_national_totals reads the Resident Population column, keyed to the "
        "correct year even across a footnote-suffixed year row",
        result == {2010: 309321666, 2011: 311556874, 2019: 328239523},
        detail=str(result),
    )


def test_unification_overlap_logic():
    # Not a parser test, but confirms the later-vintage-wins merge rule behaves as
    # documented when two sources disagree on an overlapping year (e.g. 2010 appears
    # in both the 2000-2010 and 2010-2019 releases with slightly different figures --
    # a real, expected discrepancy between vintages, not a bug).
    per_source_results = {
        "1900-1999": {1999: 272690813},
        "2000-2010": {2000: 281421906, 2010: 309326085},  # slightly different 2010 figure
        "2010-2019": {2010: 309326295, 2019: 328239523},  # the "current" 2010 figure
        "2020-2026": {2020: 331449281},
    }
    unified = {}
    for vintage in ["1900-1999", "2000-2010", "2010-2019", "2020-2026"]:
        for year, population in per_source_results[vintage].items():
            unified[year] = population
    check(
        "later vintage's 2010 figure wins over the earlier one",
        unified[2010] == 309326295,
        detail=str(unified),
    )
    check("all 5 distinct years present", sorted(unified) == [1999, 2000, 2010, 2019, 2020])


if __name__ == "__main__":
    test_clean_number()
    test_parse_1900_1999()
    test_parse_2000_2010()
    test_parse_monthly_national_totals()
    test_unification_overlap_logic()

    print()
    if FAILURES:
        print(f"{len(FAILURES)} check(s) failed: {FAILURES}")
        raise SystemExit(1)
    print("All checks passed.")
