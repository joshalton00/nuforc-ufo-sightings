"""import_population.py

Phase 2, step 1 of the website's data plan (see Project Documents/NUFORC Website Plan.docx):
sourcing U.S. population-by-year data so the yearly sightings chart can eventually be shown
"per capita" rather than as raw counts.

The Census Bureau doesn't publish one continuous file for this -- it re-publishes population
estimates in "vintages" that don't extend backward, so a full historical series has to be
stitched from four separate releases:

    1900-1999   Historical National Population Estimates (a single flat text file)
    2000-2010   National Intercensal Tables: 2000-2010 (Excel, by age/sex, has a Total row)
    2010-2019   National Population Totals and Components of Change: 2010-2019 (Excel, monthly)
    2020-2026   National Population Totals and Components of Change: 2020-2025 (Excel, monthly,
                already includes estimates through Dec 2026)

Each source has its own parsing quirks, so that logic lives in population_parsers.py (see
that file's docstring) rather than inline here -- this script just downloads each source,
hands its bytes to the matching parser, and combines the four results into one annual
series, "as of July 1" to match the convention the earliest source uses.

Where two sources cover the same year (e.g. both the 2000-2010 and 2010-2019 releases
include July 1, 2010), the LATER vintage wins -- it reflects Census's more current
methodology. That overwrite is deliberate and reported to the console, the same way
export_data.py reports its own data-cleaning decisions rather than making them silently.

IMPORTANT -- this script was written without the ability to fetch the real Census files: the
environment it was developed in doesn't have network access to census.gov. The parsing logic
in population_parsers.py is built from the well-documented, long-standing structure of these
specific files (and is checked against small synthetic stand-ins in test_population_parsers.py),
but it has not been run against the real thing. Run this yourself and watch the console
output closely -- each step prints a preview of what it parsed. If a step's output looks
wrong (wrong row count, implausible population figures, a crash), that's the one to send
back for a fix; the other three are very likely fine.

Run this from the repo root with the same Python environment used for scrape_nuforc.py /
export_data.py:

    python import_population.py

It will prompt for the Postgres password the same way those scripts do, then create (or
replace) a new us_population_by_year table.
"""

import importlib.util
import subprocess
import sys


def _install_missing_packages(packages):
    """Install any of `packages` (import name -> pip name) that aren't already
    available. Tries `uv` first, since this project's venv is uv-managed and doesn't
    ship pip by default, falling back to plain pip for environments where uv isn't
    available."""
    for import_name, pip_name in packages.items():
        if importlib.util.find_spec(import_name) is not None:
            continue
        try:
            subprocess.check_call(["uv", "pip", "install", "--python", sys.executable, pip_name])
        except (subprocess.CalledProcessError, FileNotFoundError):
            subprocess.check_call([sys.executable, "-m", "pip", "install", pip_name])


_REQUIRED_PACKAGES = {
    "pandas": "pandas",
    "sqlalchemy": "sqlalchemy",
    "psycopg2": "psycopg2-binary",
    "requests": "requests",
    "openpyxl": "openpyxl",  # reads .xlsx (2010-2019 and 2020-2026 sources)
    "xlrd": "xlrd",  # reads legacy .xls (2000-2010 source)
}
_install_missing_packages(_REQUIRED_PACKAGES)

# --- Everything below is now safe to import normally ---
import getpass  # prompt for the Postgres password without echoing it to the terminal

import pandas as pd
import requests
from sqlalchemy import create_engine, text
from sqlalchemy.exc import SQLAlchemyError

from population_parsers import parse_1900_1999, parse_2000_2010, parse_monthly_national_totals

REQUEST_HEADERS = {
    # Some Census endpoints reject requests with no User-Agent at all.
    "User-Agent": "Mozilla/5.0 (compatible; nuforc-project-population-import/1.0)"
}
REQUEST_TIMEOUT = 60

SOURCES = {
    "1900-1999": "https://www2.census.gov/programs-surveys/popest/tables/1900-1980/national/totals/popclockest.txt",
    "2000-2010": "https://www2.census.gov/programs-surveys/popest/tables/2000-2010/intercensal/national/us-est00int-01.xls",
    "2010-2019": "https://www2.census.gov/programs-surveys/popest/tables/2010-2019/national/totals/na-est2019-01.xlsx",
    "2020-2026": "https://www2.census.gov/programs-surveys/popest/tables/2020-2025/national/totals/NA-EST2025-POP.xlsx",
}

PARSERS = {
    "1900-1999": parse_1900_1999,
    "2000-2010": parse_2000_2010,
    "2010-2019": lambda b: parse_monthly_national_totals(b, "2010-2019"),
    "2020-2026": lambda b: parse_monthly_national_totals(b, "2020-2026"),
}


def download(vintage, url):
    print(f"\nDownloading {vintage} source:\n  {url}")
    response = requests.get(url, headers=REQUEST_HEADERS, timeout=REQUEST_TIMEOUT)
    response.raise_for_status()
    print(f"  Got {len(response.content):,} bytes.")
    return response.content


def build_unified_series():
    print("Fetching and parsing all four Census sources...")
    per_source_results = {}
    failed_vintages = {}
    for vintage, url in SOURCES.items():
        try:
            raw_bytes = download(vintage, url)
            per_source_results[vintage] = PARSERS[vintage](raw_bytes)
        except Exception as exc:  # noqa: BLE001 -- deliberately broad: one source's
            # parsing bug (a network error, a changed table layout, anything) should
            # not throw away the years that DID parse correctly from the other three.
            print(f"  FAILED to get usable data from the {vintage} source: {exc}")
            failed_vintages[vintage] = exc
            per_source_results[vintage] = {}

    if failed_vintages:
        print(
            f"\n{len(failed_vintages)} of 4 source(s) failed and will be missing from the "
            f"result: {list(failed_vintages)}. The years from the other "
            f"{4 - len(failed_vintages)} source(s) below are still usable -- send the "
            f"error(s) above back for a fix to the failed one(s)."
        )
    if len(failed_vintages) == len(SOURCES):
        raise RuntimeError("All four sources failed -- nothing to load. See errors above.")

    # Combine in chronological vintage order, so a later vintage's value for a shared
    # year (e.g. July 1, 2010 appears in both the 2000-2010 and 2010-2019 releases)
    # overwrites an earlier one -- later vintages reflect more current methodology.
    unified = {}
    source_vintage_by_year = {}
    for vintage in ["1900-1999", "2000-2010", "2010-2019", "2020-2026"]:
        for year, population in per_source_results[vintage].items():
            if year in unified and unified[year] != population:
                print(
                    f"  Note: {year} appears in both an earlier source and {vintage} with "
                    f"different values ({unified[year]:,} vs {population:,}) -- keeping the "
                    f"{vintage} figure as the more current one."
                )
            unified[year] = population
            source_vintage_by_year[year] = vintage

    if not unified:
        raise RuntimeError("No years were parsed from any source -- nothing to load. See errors above.")

    print(f"\nCombined into a single series: {min(unified)}-{max(unified)} ({len(unified)} years).")
    missing_years = sorted(set(range(min(unified), max(unified) + 1)) - set(unified))
    if missing_years:
        print(f"  Warning: {len(missing_years)} year(s) have no value and will be skipped: {missing_years}")

    return (
        pd.DataFrame(
            {
                "year": list(unified.keys()),
                "population": list(unified.values()),
                "source_vintage": [source_vintage_by_year[y] for y in unified],
            }
        )
        .sort_values("year")
        .reset_index(drop=True)
    )


def main():
    population_df = build_unified_series()

    # --- Postgres connection settings (matches scrape_nuforc.py / export_data.py) ---
    DB_USER = "postgres"
    DB_PASSWORD = getpass.getpass(prompt="Enter the Postgres password for the 'postgres' user: ")
    DB_HOST = "localhost"
    DB_PORT = "5432"
    DB_NAME = "nuforc"
    TABLE_NAME = "us_population_by_year"

    engine = create_engine(f"postgresql+psycopg2://{DB_USER}:{DB_PASSWORD}@{DB_HOST}:{DB_PORT}/{DB_NAME}")

    try:
        with engine.connect():
            pass
        print(f"\nConnected to Postgres database '{DB_NAME}' -- proceeding with the load.")
    except SQLAlchemyError as exc:
        print(f"Could not connect to Postgres:\n{exc}")
        print("Check DB_USER/DB_HOST/DB_PORT/DB_NAME above, and make sure you entered the correct password, then try again.")
        sys.exit(1)

    # Replaces the table wholesale on every run, same convention as the NUFORC scrape
    # itself (see pipeline.html's "What's still rough" note -- this project doesn't do
    # incremental writes anywhere yet).
    population_df.to_sql(TABLE_NAME, engine, if_exists="replace", index=False)

    with engine.connect() as conn:
        row_count = conn.execute(text(f"SELECT COUNT(*) FROM {TABLE_NAME}")).scalar()

    print(f"\nWrote {row_count} rows to '{TABLE_NAME}' (columns: year, population, source_vintage).")


if __name__ == "__main__":
    main()
