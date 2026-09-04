// This site never talks to a database -- both charts below are built entirely from
// the small precomputed JSON files in data/, produced by export_data.py. See
// Project Documents/NUFORC Website Plan.docx for the full architecture.

// Shared Plotly layout options so both charts look like one consistent design system
// rather than two unrelated charts.
const BASE_LAYOUT = {
  margin: { l: 60, r: 20, t: 10, b: 50 },
  paper_bgcolor: "rgba(0,0,0,0)",
  plot_bgcolor: "rgba(0,0,0,0)",
  font: { family: "-apple-system, Segoe UI, Roboto, Helvetica, Arial, sans-serif", color: "#1c1e21" },
  xaxis: { gridcolor: "#eee" },
  yaxis: { gridcolor: "#eee", zeroline: false, tickformat: ",d" },
  hoverlabel: {
    bgcolor: "#3a3d42",
    bordercolor: "#3a3d42",
    font: { color: "#fff" },
  },
};

const BASE_CONFIG = {
  displaylogo: false,
  responsive: true,
  modeBarButtonsToRemove: ["lasso2d", "select2d"],
};

async function loadJSON(path) {
  const response = await fetch(path);
  if (!response.ok) {
    throw new Error(`${path} responded with ${response.status}`);
  }
  return response.json();
}

// Shown in a chart's container if its data file can't be loaded -- most commonly
// because the page was opened directly as a file:// URL instead of through a local
// web server, which browsers block fetch() from doing for security reasons.
function showLoadError(containerId) {
  const el = document.getElementById(containerId);
  el.innerHTML =
    '<div class="chart-error">Couldn\'t load chart data. If you\'re viewing this ' +
    "file directly (a file:// address in the browser bar), run a local web server " +
    "from the site/ folder instead — e.g. <code>python -m http.server</code> — " +
    "then open the localhost address it prints.</div>";
}

// --- Chart 1: Sightings by day of year ------------------------------------------

async function renderByDay() {
  let data;
  try {
    data = await loadJSON("data/sightings_by_day.json");
  } catch (err) {
    console.error("Failed to load sightings_by_day.json", err);
    showLoadError("chart-by-day");
    return;
  }

  // The export writes "MM-DD" labels (year-agnostic, since counts are combined across
  // every year). Plotly needs real dates to draw a clean date axis with month ticks,
  // so we pin every point to the same arbitrary leap year (2024, so Feb 29 has
  // somewhere to go) purely for plotting -- the year itself is never shown or used.
  const dates = data.date.map((md) => `2024-${md}`);

  // Find the peak day so it can be called out with an annotation, the way the
  // original Tableau version does -- computed here rather than hardcoded, so it
  // still finds the right day if the underlying data changes.
  let peakIndex = 0;
  for (let i = 1; i < data.count.length; i++) {
    if (data.count[i] > data.count[peakIndex]) peakIndex = i;
  }
  // timeZone: "UTC" matters here -- the date string was built as UTC midnight above,
  // and without pinning the formatter to UTC too, toLocaleDateString silently converts
  // to the viewer's local timezone first. For anyone west of UTC that rolls midnight
  // back to the previous day, mislabeling the peak by one day (e.g. "July 3" for a
  // July 4th peak).
  const peakLabel = new Date(dates[peakIndex]).toLocaleDateString(undefined, {
    month: "long",
    day: "numeric",
    timeZone: "UTC",
  });

  const trace = {
    x: dates,
    y: data.count,
    type: "bar",
    marker: { color: "#3d7a68" },
    hovertemplate: "%{x|%b %d}: %{y} sightings<extra></extra>",
  };

  const layout = {
    ...BASE_LAYOUT,
    bargap: 0,
    xaxis: { ...BASE_LAYOUT.xaxis, type: "date", tickformat: "%b", dtick: "M1" },
    yaxis: { ...BASE_LAYOUT.yaxis, title: "Sightings" },
    annotations: [
      {
        x: dates[peakIndex],
        y: data.count[peakIndex],
        text: `${peakLabel}: ${data.count[peakIndex].toLocaleString()} sightings`,
        showarrow: true,
        arrowhead: 2,
        ax: 0,
        ay: -40,
      },
    ],
  };

  Plotly.newPlot("chart-by-day", [trace], layout, BASE_CONFIG);
}

// --- Chart 2: Sightings by year --------------------------------------------------

async function renderByYear() {
  let data;
  try {
    data = await loadJSON("data/sightings_by_year.json");
  } catch (err) {
    console.error("Failed to load sightings_by_year.json", err);
    showLoadError("chart-by-year");
    return;
  }

  // Population data failing to load shouldn't take down the whole chart -- it just
  // means the raw-count view is all that's available, same as before this data existed.
  let populationData = null;
  try {
    populationData = await loadJSON("data/population_national.json");
  } catch (err) {
    console.error("Failed to load population_national.json -- per-million toggle disabled", err);
  }

  // Per-million sightings, one value per entry in data.year -- null anywhere a
  // matching population figure isn't available, which keeps this array the same
  // length/order as data.count so it can be swapped in directly via Plotly restyle.
  let perMillion = null;
  if (populationData) {
    const populationByYear = {};
    populationData.year.forEach((y, i) => {
      populationByYear[y] = populationData.population[i];
    });
    perMillion = data.year.map((y, i) => {
      const population = populationByYear[y];
      return population ? (data.count[i] / population) * 1_000_000 : null;
    });
  }
  const hasPerMillion = perMillion !== null && perMillion.some((v) => v !== null);

  // The most recent year is likely still in progress as of export time (see
  // export_data.py's partial_year field) -- give it a visually distinct, lighter
  // color so its shorter bar doesn't read as a real decline in sightings. Applies the
  // same way in both raw-count and per-million view, since it's about the year, not
  // which metric is currently shown.
  const colors = data.year.map((y) =>
    y === data.partial_year ? "rgba(47, 84, 150, 0.35)" : "#2f5496"
  );

  const trace = {
    x: data.year,
    y: data.count,
    type: "bar",
    marker: { color: colors },
    hovertemplate: "%{x}: %{y} sightings<extra></extra>",
  };

  const minYear = data.year[0];
  const maxYear = data.year[data.year.length - 1];

  const layout = {
    ...BASE_LAYOUT,
    // Extra top margin makes room for the toggle buttons below, which sit just above
    // the plot area -- BASE_LAYOUT's normal 10px top margin is fine for the by-day
    // chart, which has no buttons, but would clip these.
    margin: { ...BASE_LAYOUT.margin, t: hasPerMillion ? 50 : BASE_LAYOUT.margin.t },
    xaxis: {
      ...BASE_LAYOUT.xaxis,
      title: "Year",
      dtick: 10,
      range: [minYear - 0.5, maxYear + 0.5],
    },
    yaxis: { ...BASE_LAYOUT.yaxis, title: "Sightings" },
  };

  // A Tableau-style toggle button pair (the "layout.updatemenus" technique the plan
  // doc calls out for exactly this kind of thing) swaps the trace between raw counts
  // and sightings per million people, via Plotly.restyle/relayout rather than
  // re-fetching data -- both series are already loaded above. Raw counts stays the
  // default view (unchanged from before population data existed); Per Million is one
  // click away rather than the two datasets always needing a joined export.
  if (hasPerMillion) {
    layout.updatemenus = [
      {
        type: "buttons",
        direction: "left",
        x: 0,
        xanchor: "left",
        y: 1.2,
        yanchor: "top",
        pad: { r: 6, t: 0 },
        bgcolor: "#eef1f6",
        activecolor: "#2f5496",
        bordercolor: "#c7c9cc",
        font: { color: "#1c1e21", size: 12 },
        active: 0,
        buttons: [
          {
            label: "Raw Counts",
            method: "update",
            args: [
              { y: [data.count], hovertemplate: ["%{x}: %{y} sightings<extra></extra>"] },
              { "yaxis.title.text": "Sightings", "yaxis.tickformat": ",d" },
            ],
          },
          {
            label: "Per Million People",
            method: "update",
            args: [
              { y: [perMillion], hovertemplate: ["%{x}: %{y:.1f} sightings per million<extra></extra>"] },
              { "yaxis.title.text": "Sightings per million people", "yaxis.tickformat": ",.1f" },
            ],
          },
        ],
      },
    ];
  }

  if (data.partial_year) {
    layout.annotations = [
      {
        // "x domain"/"y domain" (rather than data coordinates) position
        // this relative to the plot rectangle itself, so it stays put in
        // the corner regardless of the year range currently zoomed to.
        xref: "x domain",
        yref: "y domain",
        x: 0.02,
        y: 0.03,
        xanchor: "left",
        yanchor: "bottom",
        text: `<i>${data.partial_year} is in progress — not a full year yet</i>`,
        showarrow: false,
        font: { size: 11, color: "#8a8d93" },
        align: "left",
      },
    ];
  }

  Plotly.newPlot("chart-by-year", [trace], layout, BASE_CONFIG);

  if (data.generated_at) {
    const generated = new Date(data.generated_at);
    document.getElementById("generated-note").textContent =
      "Data last exported " + generated.toLocaleDateString(undefined, { dateStyle: "long" });
  }

  setupYearFilter(minYear, maxYear);
}

// A Tableau-style dual-handle year filter: dragging either handle (or typing
// directly into the start/end boxes) zooms the bar chart to that year range,
// via Plotly.relayout rather than re-fetching or re-filtering the data --
// the full dataset stays loaded, only the visible x-axis window changes.
function setupYearFilter(minYear, maxYear) {
  const sliderEl = document.getElementById("year-slider");
  const startInput = document.getElementById("year-start-input");
  const endInput = document.getElementById("year-end-input");

  if (!sliderEl || typeof noUiSlider === "undefined") {
    // noUiSlider failed to load (e.g. offline) -- the chart itself still
    // works, it just won't have the interactive filter.
    return;
  }

  noUiSlider.create(sliderEl, {
    start: [minYear, maxYear],
    connect: true,
    range: { min: minYear, max: maxYear },
    step: 1,
    tooltips: false,
  });

  function applyRange(start, end) {
    startInput.value = start;
    endInput.value = end;
    Plotly.relayout("chart-by-year", { "xaxis.range": [start - 0.5, end + 0.5] });
  }

  sliderEl.noUiSlider.on("update", (values) => {
    applyRange(Math.round(values[0]), Math.round(values[1]));
  });

  // Typing a year directly and pressing Enter/tabbing away moves the slider
  // (which in turn re-renders the chart via the "update" handler above).
  function onInputCommit(which) {
    return () => {
      const current = sliderEl.noUiSlider.get().map(Number);
      let start = which === "start" ? Number(startInput.value) : current[0];
      let end = which === "end" ? Number(endInput.value) : current[1];
      if (Number.isNaN(start) || Number.isNaN(end)) return;
      start = Math.min(Math.max(start, minYear), maxYear);
      end = Math.min(Math.max(end, minYear), maxYear);
      if (start > end) [start, end] = [end, start];
      sliderEl.noUiSlider.set([start, end]);
    };
  }

  startInput.addEventListener("change", onInputCommit("start"));
  endInput.addEventListener("change", onInputCommit("end"));
}

// --- Chart 3: Sightings by state (Geographic Slice Map) --------------------------

// Plotly's USA-states choropleth locations need USPS 2-letter codes, not full state
// names -- this is the JS-side mirror of export_sightings_by_state.py's POSTAL_TO_STATE
// table (inverted). Duplicated across the Python/JS boundary because there's no way to
// share it directly; keep the two in sync if a state entry ever changes.
const STATE_TO_POSTAL = {
  Alabama: "AL", Alaska: "AK", Arizona: "AZ", Arkansas: "AR", California: "CA",
  Colorado: "CO", Connecticut: "CT", Delaware: "DE", "District of Columbia": "DC",
  Florida: "FL", Georgia: "GA", Hawaii: "HI", Idaho: "ID", Illinois: "IL",
  Indiana: "IN", Iowa: "IA", Kansas: "KS", Kentucky: "KY", Louisiana: "LA",
  Maine: "ME", Maryland: "MD", Massachusetts: "MA", Michigan: "MI",
  Minnesota: "MN", Mississippi: "MS", Missouri: "MO", Montana: "MT",
  Nebraska: "NE", Nevada: "NV", "New Hampshire": "NH", "New Jersey": "NJ",
  "New Mexico": "NM", "New York": "NY", "North Carolina": "NC", "North Dakota": "ND",
  Ohio: "OH", Oklahoma: "OK", Oregon: "OR", Pennsylvania: "PA",
  "Rhode Island": "RI", "South Carolina": "SC", "South Dakota": "SD", Tennessee: "TN",
  Texas: "TX", Utah: "UT", Vermont: "VT", Virginia: "VA", Washington: "WA",
  "West Virginia": "WV", Wisconsin: "WI", Wyoming: "WY",
};

// A single-hue sequential teal ramp (light = near zero, dark = high) -- magnitude
// encoding for a choropleth, per the dataviz style guide's sequential palette. Colors
// pulled directly from the reference hex-map palette Josh sent over (sampled from the
// actual image, not eyeballed) rather than the site's earlier blue accent.
const SEQUENTIAL_TEAL = [
  [0, "#eaf0f0"],
  [1 / 6, "#d8e4e4"],
  [2 / 6, "#c0d8d8"],
  [3 / 6, "#96c0ba"],
  [4 / 6, "#6ca8a2"],
  [5 / 6, "#488a84"],
  [1, "#1e6660"],
];

// Approximate label-placement centroids (not precise geographic centers -- nudged so
// the text lands in open water/interior, e.g. Michigan's sits in the Lower Peninsula)
// for states with enough on-map room to hold a direct value label. Two groups are
// deliberately left out and fall back to tooltip-only:
//   - Alaska and Hawaii: Plotly's USA-scope choropleth automatically repositions them
//     into the small inset boxes bottom-left, but that repositioning is internal to the
//     choropleth trace and does NOT apply to a second scattergeo trace plotted by raw
//     lon/lat -- a label for either would render out in the ocean at its true
//     coordinates, not inside the inset. There's no supported way to hook into Plotly's
//     inset placement from a second trace.
//   - The small Northeast/Mid-Atlantic states (plus DC): physically too small on a
//     national-scale map for a legible number to fit inside their borders.
const STATE_CENTROIDS = {
  Alabama: { lat: 32.7, lon: -86.8 },
  Arizona: { lat: 34.2, lon: -111.6 },
  Arkansas: { lat: 34.8, lon: -92.2 },
  California: { lat: 37.2, lon: -119.6 },
  Colorado: { lat: 39.0, lon: -105.5 },
  Florida: { lat: 28.6, lon: -82.4 },
  Georgia: { lat: 32.6, lon: -83.4 },
  Idaho: { lat: 44.4, lon: -114.6 },
  Illinois: { lat: 40.0, lon: -89.2 },
  Indiana: { lat: 39.9, lon: -86.3 },
  Iowa: { lat: 42.0, lon: -93.5 },
  Kansas: { lat: 38.5, lon: -98.4 },
  Kentucky: { lat: 37.5, lon: -85.3 },
  Louisiana: { lat: 31.0, lon: -92.0 },
  Maine: { lat: 45.4, lon: -69.2 },
  Michigan: { lat: 44.3, lon: -84.5 },
  Minnesota: { lat: 46.3, lon: -94.3 },
  Mississippi: { lat: 32.7, lon: -89.7 },
  Missouri: { lat: 38.4, lon: -92.5 },
  Montana: { lat: 47.0, lon: -109.6 },
  Nebraska: { lat: 41.5, lon: -99.8 },
  Nevada: { lat: 39.3, lon: -116.6 },
  "New Mexico": { lat: 34.4, lon: -106.1 },
  "New York": { lat: 42.9, lon: -75.5 },
  "North Carolina": { lat: 35.5, lon: -79.2 },
  "North Dakota": { lat: 47.5, lon: -100.5 },
  Ohio: { lat: 40.3, lon: -82.8 },
  Oklahoma: { lat: 35.5, lon: -97.5 },
  Oregon: { lat: 44.0, lon: -120.5 },
  Pennsylvania: { lat: 40.9, lon: -77.8 },
  "South Carolina": { lat: 33.9, lon: -80.9 },
  "South Dakota": { lat: 44.5, lon: -100.2 },
  Tennessee: { lat: 35.9, lon: -86.4 },
  Texas: { lat: 31.5, lon: -99.3 },
  Utah: { lat: 39.3, lon: -111.7 },
  Virginia: { lat: 37.5, lon: -78.9 },
  Washington: { lat: 47.4, lon: -120.6 },
  "West Virginia": { lat: 38.9, lon: -80.5 },
  Wisconsin: { lat: 44.6, lon: -89.9 },
  Wyoming: { lat: 43.0, lon: -107.5 },
};

// Sentinel for the "All years" dropdown entry -- distinct from any real year (all of
// which are numbers), so it can sit in the same array/lookup keys as actual years
// without ever colliding with one.
const ALL_YEARS_VALUE = "all";

// --- Small color-math helpers, used only to pick each direct label's ink color -----

function hexToRgb(hex) {
  const clean = hex.replace("#", "");
  return {
    r: parseInt(clean.substring(0, 2), 16),
    g: parseInt(clean.substring(2, 4), 16),
    b: parseInt(clean.substring(4, 6), 16),
  };
}

// WCAG 2.x relative luminance -- https://www.w3.org/TR/WCAG21/#dfn-relative-luminance
function relativeLuminance({ r, g, b }) {
  const [rs, gs, bs] = [r, g, b].map((c) => {
    const s = c / 255;
    return s <= 0.03928 ? s / 12.92 : Math.pow((s + 0.055) / 1.055, 2.4);
  });
  return 0.2126 * rs + 0.7152 * gs + 0.0722 * bs;
}

// WCAG 2.x contrast ratio between two relative luminances.
function contrastRatio(lumA, lumB) {
  const [lighter, darker] = lumA > lumB ? [lumA, lumB] : [lumB, lumA];
  return (lighter + 0.05) / (darker + 0.05);
}

const WHITE_LUMINANCE = relativeLuminance({ r: 255, g: 255, b: 255 });
const INK_LUMINANCE = relativeLuminance(hexToRgb("#1c1e21")); // matches body text color

function lerpHex(hexA, hexB, t) {
  const a = hexToRgb(hexA);
  const b = hexToRgb(hexB);
  const channel = (from, to) => Math.round(from + (to - from) * t);
  const r = channel(a.r, b.r);
  const g = channel(a.g, b.g);
  const bl = channel(a.b, b.b);
  return `#${[r, g, bl].map((v) => v.toString(16).padStart(2, "0")).join("")}`;
}

// Interpolates SEQUENTIAL_TEAL (Plotly's own colorscale stop format) at an arbitrary
// point 0-1, so a label's fill color can be computed the same way Plotly colors the
// choropleth fill under it, without re-implementing Plotly's own shading.
function colorScaleAt(colorscale, t) {
  const clamped = Math.max(0, Math.min(1, t));
  for (let i = 0; i < colorscale.length - 1; i++) {
    const [t0, c0] = colorscale[i];
    const [t1, c1] = colorscale[i + 1];
    if (clamped >= t0 && clamped <= t1) {
      return lerpHex(c0, c1, (clamped - t0) / (t1 - t0));
    }
  }
  return colorscale[colorscale.length - 1][1];
}

// Picks white or the site's dark ink for a label, whichever clears more contrast
// against that state's current fill color -- per the dataviz skill's rule for text on
// a variable-luminance fill, rather than a single hardcoded threshold that would read
// fine on some states and fail on others as the fill lightens/darkens with the data.
function labelInkFor(fillHex) {
  const fillLuminance = relativeLuminance(hexToRgb(fillHex));
  const whiteContrast = contrastRatio(fillLuminance, WHITE_LUMINANCE);
  const inkContrast = contrastRatio(fillLuminance, INK_LUMINANCE);
  return whiteContrast >= inkContrast ? "#fff" : "#1c1e21";
}

// Rounds a value up to a "nice" round number for a color scale's top end -- the step
// size scales with the value's own magnitude (nearest 5 for a value in the tens,
// nearest 500 for a value in the thousands) so the scale's max isn't an oddly specific
// number, and the same function works for both the per-capita view's small rates and
// the total-sightings view's much larger counts.
function niceMax(value) {
  if (!value) return 1;
  const magnitude = Math.pow(10, Math.floor(Math.log10(value)));
  const step = magnitude / 2;
  return Math.ceil(value / step) * step;
}

async function renderByState() {
  let sightings, population;
  try {
    [sightings, population] = await Promise.all([
      loadJSON("data/sightings_by_state.json"),
      loadJSON("data/population_state.json"),
    ]);
  } catch (err) {
    console.error("Failed to load state data", err);
    showLoadError("chart-by-state");
    return;
  }

  // Both files are flat parallel arrays (state/year/count or state/year/population) --
  // reshape into { state: { year: value } } once, for O(1) lookups while building each
  // frame below.
  function toNestedLookup(stateArr, yearArr, valueArr) {
    const lookup = {};
    for (let i = 0; i < stateArr.length; i++) {
      const state = stateArr[i];
      if (!lookup[state]) lookup[state] = {};
      lookup[state][yearArr[i]] = valueArr[i];
    }
    return lookup;
  }

  const sightingsByState = toNestedLookup(sightings.state, sightings.year, sightings.count);
  const populationByState = toNestedLookup(population.state, population.year, population.population);

  // The states plotted come from the population data specifically -- it's the one
  // guaranteed to cover every state/DC for every year in range (verified when it was
  // built; see the plan doc). Sightings data is sparse by comparison: a state/year with
  // no reports simply has no entry at all, which this treats as a real 0, not missing.
  const states = Object.keys(populationByState).sort();
  const years = [...new Set(population.year)].sort((a, b) => a - b);
  const defaultYear = years[years.length - 1];
  const allYearsLabel = `${years[0]}–${defaultYear}`;

  // Only states with both enough room on the map AND real coordinates on file get a
  // direct label; everything else stays tooltip-only (see STATE_CENTROIDS above).
  const labelStates = states.filter((s) => STATE_CENTROIDS[s]);

  // Both measures, every (state, year) plus an "All years" aggregate, computed once up
  // front -- so every dropdown/toggle combination is an instant swap with no
  // recomputation per click. "All years" per-capita divides the summed count by the
  // LATEST year's population (not a sum of populations across years, which would badly
  // understate the rate) -- an approximation, since it ignores population change over
  // the range, but a reasonable one for a headline "all-time" figure.
  const rawCount = {}; // state -> year|"all" -> sightings
  const perCapita = {}; // state -> year|"all" -> per-100K rate (or null if no population figure)

  for (const state of states) {
    rawCount[state] = {};
    perCapita[state] = {};
    let allCount = 0;
    for (const year of years) {
      const count = sightingsByState[state]?.[year] ?? 0;
      rawCount[state][year] = count;
      allCount += count;

      const pop = populationByState[state]?.[year];
      const rate = pop ? (count / pop) * 100_000 : null;
      perCapita[state][year] = rate;
    }
    rawCount[state][ALL_YEARS_VALUE] = allCount;

    const latestPop = populationByState[state]?.[defaultYear];
    const allRate = latestPop ? (allCount / latestPop) * 100_000 : null;
    perCapita[state][ALL_YEARS_VALUE] = allRate;
  }

  // Builds everything Plotly needs for one (year, measure) combination: the
  // choropleth's z/text/zmax, and the direct-label trace's positions/text/ink colors.
  // Kept as one function (rather than separately recomputing the choropleth and the
  // labels) since a label's ink color depends on the choropleth's own fill at that
  // point, which in turn depends on this same combination's zmax.
  //
  // zmax is deliberately recomputed fresh for every frame, from that frame's own
  // values only -- not a single fixed max shared across years. Josh asked for this
  // after seeing the "All years" view's full-contrast blues next to a washed-out
  // single year: with one fixed scale, only the very highest year/state combination
  // ever reaches the darkest end, so a typical single year barely uses the ramp. The
  // trade-off is real (a color's meaning now shifts between years -- "dark blue" in
  // 2005 is a different rate than "dark blue" in 2020) but that's the choice made here.
  function frameFor(year, measure) {
    const isAll = year === ALL_YEARS_VALUE;
    const key = isAll ? ALL_YEARS_VALUE : year;

    const z = states.map((s) => (measure === "total" ? rawCount[s][key] : perCapita[s][key]));
    const zmax = niceMax(Math.max(0, ...z.filter((v) => v !== null)));

    const text = states.map((s) => {
      const count = rawCount[s][key];
      const rate = perCapita[s][key];
      const rateText = rate === null ? "no population data for this year" : `${rate.toFixed(1)} per 100,000 residents`;
      const yearText = isAll ? allYearsLabel : year;
      return `${s} — ${yearText}: ${count.toLocaleString()} sightings, ${rateText}`;
    });

    const labelLon = [];
    const labelLat = [];
    const labelText = [];
    const labelColor = [];
    for (const s of labelStates) {
      const value = measure === "total" ? rawCount[s][key] : perCapita[s][key];
      if (value === null || value === undefined) continue; // no per-capita figure for this state/year
      const centroid = STATE_CENTROIDS[s];
      const fillHex = colorScaleAt(SEQUENTIAL_TEAL, zmax ? value / zmax : 0);
      labelLon.push(centroid.lon);
      labelLat.push(centroid.lat);
      labelText.push(measure === "total" ? value.toLocaleString() : value.toFixed(1));
      labelColor.push(labelInkFor(fillHex));
    }

    return { z, text, zmax, labelLon, labelLat, labelText, labelColor };
  }

  // Mutable view state -- updated from the plotly_buttonclicked handler below, since
  // the year dropdown and the total/per-capita toggle are two independent updatemenus
  // whose combination Plotly can't express declaratively (each button there would need
  // static, precomputed args, but which args are right depends on the OTHER menu's
  // current selection too). Both menus use method:"skip" instead, so clicking one
  // updates Plotly's own active-button highlighting but does nothing else; this
  // listener does the actual work, recomputing from both current selections together.
  let currentYear = defaultYear;
  let currentMeasure = "percapita";

  const initial = frameFor(currentYear, currentMeasure);

  const choroplethTrace = {
    type: "choropleth",
    locationmode: "USA-states",
    locations: states.map((s) => STATE_TO_POSTAL[s]),
    z: initial.z,
    zmin: 0,
    zmax: initial.zmax,
    text: initial.text,
    hovertemplate: "%{text}<extra></extra>",
    colorscale: SEQUENTIAL_TEAL,
    marker: { line: { color: "#fff", width: 0.5 } },
    showscale: false,
  };

  // A second trace, drawn on top of the choropleth, for the direct value labels --
  // Plotly has no built-in way to print a number inside each choropleth region, so this
  // plots plain text at each labeled state's centroid instead. hoverinfo:"skip" avoids
  // a second, redundant tooltip layered on top of the choropleth's own.
  const labelTrace = {
    type: "scattergeo",
    mode: "text",
    lon: initial.labelLon,
    lat: initial.labelLat,
    text: initial.labelText,
    textfont: { size: 10, color: initial.labelColor },
    hoverinfo: "skip",
  };

  // Dropdown options: "All years" first (a summary view, set apart from the
  // chronological list rather than tacked onto one end of it), then every year
  // ascending. The default selection stays a specific year (2025), not this entry.
  const yearMenuValues = [ALL_YEARS_VALUE, ...years];

  const layout = {
    ...BASE_LAYOUT,
    margin: { l: 0, r: 0, t: 50, b: 0 },
    geo: {
      scope: "usa",
      bgcolor: "rgba(0,0,0,0)",
      lakecolor: "#f7f7f8",
    },
    updatemenus: [
      {
        type: "dropdown",
        x: 0,
        xanchor: "left",
        y: 1.1,
        yanchor: "top",
        bgcolor: "#eef1f6",
        activecolor: "#2f5496",
        bordercolor: "#c7c9cc",
        font: { color: "#1c1e21", size: 12 },
        active: yearMenuValues.indexOf(currentYear),
        buttons: yearMenuValues.map((year) => ({
          label: year === ALL_YEARS_VALUE ? "All Years" : String(year),
          method: "skip",
        })),
      },
      {
        type: "buttons",
        direction: "left",
        x: 0.4,
        xanchor: "left",
        y: 1.1,
        yanchor: "top",
        pad: { r: 6, t: 0 },
        bgcolor: "#eef1f6",
        activecolor: "#2f5496",
        bordercolor: "#c7c9cc",
        font: { color: "#1c1e21", size: 12 },
        active: 0,
        buttons: [
          { label: "Per 100K", method: "skip" },
          { label: "Total Sightings", method: "skip" },
        ],
      },
    ],
  };

  Plotly.newPlot("chart-by-state", [choroplethTrace, labelTrace], layout, BASE_CONFIG).then((gd) => {
    gd.on("plotly_buttonclicked", (evt) => {
      // Distinguish the two menus by their button count (27 years+"All" vs. 2 measures)
      // rather than array position, so this keeps working correctly even if the two
      // updatemenus above are reordered later.
      if (!evt || !evt.menu || !Array.isArray(evt.menu.buttons)) return;
      if (evt.menu.buttons.length === 2) {
        currentMeasure = evt.active === 1 ? "total" : "percapita";
      } else {
        currentYear = yearMenuValues[evt.active];
      }

      const frame = frameFor(currentYear, currentMeasure);
      Plotly.restyle("chart-by-state", { z: [frame.z], zmax: [frame.zmax], text: [frame.text] }, [0]);
      Plotly.restyle(
        "chart-by-state",
        {
          lon: [frame.labelLon],
          lat: [frame.labelLat],
          text: [frame.labelText],
          "textfont.color": [frame.labelColor],
        },
        [1]
      );
    });
  });

  // Plotly's own dropdown (once open) doesn't stop a mouse-wheel scroll from bubbling
  // up past its own option list -- there's no overflow:scroll element for the browser
  // to target, so it keeps looking outward and finds the page itself, scrolling that
  // instead of the list. This suppresses that specific case: a wheel event over the
  // open menu's own elements (identifiable by Plotly's own "updatemenu-container"
  // class, which it applies to plain SVG <g>/<rect>/<text> nodes, not just HTML ones)
  // no longer escapes to scroll the page. The dropdown's own drag handle (visible in
  // the screenshot Josh sent) still works for scrolling the list itself -- this only
  // stops the accidental page-jump, it doesn't add wheel-scroll support to the list.
  document.getElementById("chart-by-state").addEventListener(
    "wheel",
    (event) => {
      if (event.target.closest && event.target.closest(".updatemenu-container")) {
        event.preventDefault();
      }
    },
    { passive: false }
  );
}

renderByDay();
renderByYear();
renderByState();
