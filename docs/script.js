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
  yaxis: { gridcolor: "#eee", zeroline: false },
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
    type: "scatter",
    mode: "lines",
    fill: "tozeroy",
    line: { color: "#3d7a68", width: 1.5 },
    fillcolor: "rgba(61, 122, 104, 0.25)",
    hovertemplate: "%{x|%b %d}: %{y} sightings<extra></extra>",
  };

  const layout = {
    ...BASE_LAYOUT,
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

  const layout = {
    ...BASE_LAYOUT,
    xaxis: { ...BASE_LAYOUT.xaxis, title: "Year", dtick: 10 },
    yaxis: { ...BASE_LAYOUT.yaxis, title: "Sightings" },
  };

  if (data.partial_year) {
    layout.annotations = [
      {
        x: data.partial_year,
        y: data.count[data.year.indexOf(data.partial_year)],
        text: `${data.partial_year} is in progress —<br>not a full year yet`,
        showarrow: true,
        arrowhead: 2,
        ax: 40,
        ay: -40,
      },
    ];
  }

  Plotly.newPlot("chart-by-year", [trace], layout, BASE_CONFIG);

  if (data.generated_at) {
    const generated = new Date(data.generated_at);
    document.getElementById("generated-note").textContent =
      "Data last exported " + generated.toLocaleDateString(undefined, { dateStyle: "long" });
  }
}

renderByDay();
renderByYear();
