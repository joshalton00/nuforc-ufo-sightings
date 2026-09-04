"""test_state_population_parsers.py

Exercises parse_state_wide_table() against small synthetic tables. Unlike the national
parser tests (which had to guess at fixture layout before any real file was seen), these
fixtures are built directly from the REAL state source files -- confirmed by downloading
all three via debug_dump_state_sources.py and inspecting them directly -- so this is
checking the parsing logic itself, not guessing at the shape it needs to handle.

Run with:  python test_state_population_parsers.py
"""

from io import BytesIO

import pandas as pd

from state_population_parsers import STATE_NAMES, clean_number, parse_state_wide_table

FAILURES = []


def check(label, condition, detail=""):
    status = "PASS" if condition else "FAIL"
    print(f"[{status}] {label}" + (f" -- {detail}" if detail and not condition else ""))
    if not condition:
        FAILURES.append(label)


def silent(*args, **kwargs):
    pass


def _write_xlsx(rows):
    buf = BytesIO()
    pd.DataFrame(rows).to_excel(buf, header=False, index=False, engine="openpyxl")
    buf.seek(0)
    return buf.read()


# A handful of states (not all 51) stand in for the full set in most fixtures below --
# tests that need the complete whitelist to pass (i.e. don't expect a "missing states"
# error) build the full 51-row table via _full_state_rows() instead.
def _full_state_rows(value_fn):
    """value_fn(state_name, index) -> the single year-column value to use for that state."""
    return [[f".{name}", value_fn(name, i)] for i, name in enumerate(sorted(STATE_NAMES))]


def test_clean_number():
    check("clean_number strips commas", clean_number("39,512,223") == 39512223)
    check("clean_number strips a trailing footnote marker", clean_number("578,759 1") == 578759)
    check("clean_number handles a bare int", clean_number(494300) == 494300)
    check("clean_number rejects a short/non-number cell", clean_number("N/A") is None)


def test_parse_2000_2010_shape():
    # Mirrors the REAL 2000-2010 file: a decorated "April 1, 20001" column (excluded), a
    # bare-year row for 2000-2009, a "July 1, 20103" labeled column for the final year,
    # region subtotal rows that must NOT be picked up as states, dot-prefixed states, a
    # blank row, then a bare (non-dot-prefixed) "Puerto Rico" row that must be excluded.
    header = [
        ["Table 1. Intercensal Estimates ...", None, None, None],
        ["Geographic Area", "April 1, 20001", "Intercensal Estimates (as of July 1)", "July 1, 20103"],
        [None, None, 2000, None],
        ["United States", 281424600, 282162411, 309349689],
        ["Northeast", 53594810, 53666295, 55361036],
    ]
    state_rows = _full_state_rows(lambda name, i: 500000 + i * 1000)
    # Give each state row a 4th (April-1) column value too, to confirm it's excluded --
    # only the 3rd column (the bare-2000 July-1 column) should end up in the result.
    state_rows = [[label, val + 1, val] for label, val in state_rows]
    footer = [[None, None, None], ["Puerto Rico", 3808605, 3810605]]
    rows = header + state_rows + footer

    raw_bytes = _write_xlsx(rows)
    result = parse_state_wide_table(raw_bytes, "2000-2010 (synthetic)", log=silent)

    check(
        "all 51 states/DC found, Puerto Rico and region rows excluded",
        set(result) == STATE_NAMES,
        detail=str(sorted(set(result) ^ STATE_NAMES)),
    )
    alabama_val = 500000 + sorted(STATE_NAMES).index("Alabama") * 1000
    check(
        "the April-1 column (col index 1) is excluded -- only the July-1 bare-year value is kept",
        result["Alabama"] == {2000: alabama_val},
        detail=str(result.get("Alabama")),
    )


def test_parse_puerto_rico_dot_prefix_quirk():
    # Mirrors the REAL 2020-2025 file's one structural difference from the other two
    # vintages: Puerto Rico ALSO gets a "." prefix there, unlike the bare "Puerto Rico"
    # row in the 2000-2010 and 2010-2019 files. A parser that used "starts with '.'" as
    # its state-detection rule (rather than the explicit name whitelist) would wrongly
    # include Puerto Rico as if it were a state in this vintage -- this fixture exists
    # specifically to catch that regression.
    header = [
        ["Annual Estimates ...", None, None],
        ["Geographic Area", "April 1, 2020 Estimates Base", "Population Estimate (as of July 1)"],
        [None, None, 2020],
        ["United States", 331516113, 331578104],
    ]
    state_rows = [[f".{name}", 900000 + i, 1000000 + i] for i, name in enumerate(sorted(STATE_NAMES))]
    footer = [[".Puerto Rico", 3285874, 3281591]]
    rows = header + state_rows + footer

    raw_bytes = _write_xlsx(rows)
    result = parse_state_wide_table(raw_bytes, "2020-2025 (synthetic)", log=silent)

    check(
        "a dot-prefixed Puerto Rico row is still excluded (matched against the state whitelist, not the '.' prefix)",
        "Puerto Rico" not in result,
        detail=str(sorted(result)),
    )
    check("all 51 states/DC still found", set(result) == STATE_NAMES)


def test_missing_state_raises():
    # If a future vintage renames or drops a state row, this should fail loudly rather
    # than silently loading an incomplete table -- confirms that guard actually fires.
    header = [
        ["Geographic Area", "Population Estimate (as of July 1)"],
        [None, 2020],
    ]
    incomplete_rows = [[f".{name}", 1000000] for name in sorted(STATE_NAMES) if name != "Wyoming"]
    raw_bytes = _write_xlsx(header + incomplete_rows)

    raised = False
    try:
        parse_state_wide_table(raw_bytes, "incomplete (synthetic)", log=silent)
    except ValueError as exc:
        raised = "Wyoming" in str(exc)
    check("a missing state raises ValueError naming the missing state", raised)


if __name__ == "__main__":
    test_clean_number()
    test_parse_2000_2010_shape()
    test_parse_puerto_rico_dot_prefix_quirk()
    test_missing_state_raises()

    print()
    if FAILURES:
        print(f"{len(FAILURES)} check(s) failed: {FAILURES}")
        raise SystemExit(1)
    print("All checks passed.")
