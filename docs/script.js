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

  // The most recent year is likely still in progress as of export time (see
  // export_data.py's partial_year field) -- give it a visually distinct, lighter
  // color so its shorter bar doesn't read as a real decline in sightings.
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
    xaxis: {
      ...BASE_LAYOUT.xaxis,
      title: "Year",
      dtick: 10,
      range: [minYear - 0.5, maxYear + 0.5],
    },
    yaxis: { ...BASE_LAYOUT.yaxis, title: "Sightings" },
  };

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

renderByDay();
renderByYear();
