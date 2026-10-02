const overviewMoney = value => value == null ? "—" : new Intl.NumberFormat("en-US", {style: "currency", currency: "USD", maximumFractionDigits: 0}).format(value);
const signedMoney = value => value == null ? "—" : `${value > 0 ? "+" : value < 0 ? "−" : ""}${overviewMoney(Math.abs(value))}`;

function updateEasternClock() {
  const clock = document.getElementById("easternClock");
  if (!clock) return;
  const now = new Date();
  clock.dateTime = now.toISOString();
  clock.textContent = new Intl.DateTimeFormat("en-US", {
    timeZone: "America/New_York",
    weekday: "long", month: "long", day: "numeric", year: "numeric",
    hour: "numeric", minute: "2-digit", second: "2-digit",
    timeZoneName: "short",
  }).format(now);
}

function laborStatus(value) {
  if (value == null) return "unknown";
  const rounded = Math.round((Number(value) + Number.EPSILON) * 10) / 10;
  return rounded <= 17 ? "goal" : rounded <= 19 ? "warning" : "danger";
}

function overviewMetric(label, value, className = "") {
  const item = document.createElement("div"); item.className = `overview-metric ${className}`.trim();
  const small = document.createElement("small"); small.textContent = label;
  const strong = document.createElement("strong"); strong.textContent = value;
  item.append(small, strong); return item;
}

function standCard(stand) {
  const sos = stand.sos || {}, labor = stand.labor || {}, connectivity = stand.connectivity || {}, thirdMachine = stand.third_machine || {}, comparison = labor.sales_comparison || {};
  const card = document.createElement("a");
  card.href = `/ops/stands/${stand.slug}`;
  card.className = `stand-overview-card ${sos.status || "unknown"}${sos.stale || labor.stale ? " stale" : ""}`;
  const heading = document.createElement("div"); heading.className = "overview-card-heading";
  const name = document.createElement("h2"); name.textContent = stand.name;
  const actions = document.createElement("div"); actions.className = "overview-card-actions";
  const internet = document.createElement("span"); internet.className = `internet-status ${!connectivity.configured ? "unconfigured" : connectivity.online === true ? "online" : connectivity.online === false ? "offline" : "unknown"}`;
  internet.textContent = !connectivity.configured ? "○ Not configured" : connectivity.online === true ? "● Internet online" : connectivity.online === false ? "● Internet offline" : "○ Checking internet";
  const arrow = document.createElement("span"); arrow.className = "view-details"; arrow.textContent = "View details →";
  actions.append(internet, arrow); heading.append(name, actions);
  const metrics = document.createElement("div"); metrics.className = "overview-metrics";
  metrics.append(
    overviewMetric("Current wait", sos.closed ? "Closed" : (sos.average || "—")),
    overviewMetric("Labor", labor.labor_percentage == null ? "—" : `${labor.labor_percentage.toFixed(2)}%`, `labor-status ${laborStatus(labor.labor_percentage)}`),
    overviewMetric("Gross sales", overviewMoney(labor.gross_sales)),
    overviewMetric("Clocked in", labor.clocked_in == null ? "—" : String(labor.clocked_in)),
  );
  const salesComparison = document.createElement("div");
  const differenceClass = comparison.difference > 0 ? "ahead" : comparison.difference < 0 ? "behind" : "even";
  salesComparison.className = `sales-comparison ${comparison.last_week_gross_sales == null ? "unknown" : differenceClass}`;
  const comparisonSales = document.createElement("div");
  const comparisonLabel = document.createElement("small"); comparisonLabel.textContent = "Last week at this time";
  const comparisonValue = document.createElement("strong"); comparisonValue.textContent = overviewMoney(comparison.last_week_gross_sales);
  comparisonSales.append(comparisonLabel, comparisonValue);
  const comparisonDifference = document.createElement("span");
  const percentage = comparison.difference_percent == null ? "" : ` (${comparison.difference_percent > 0 ? "+" : ""}${comparison.difference_percent.toFixed(1)}%)`;
  comparisonDifference.textContent = comparison.last_week_gross_sales == null ? "Unavailable" : `${signedMoney(comparison.difference)}${percentage}`;
  salesComparison.append(comparisonSales, comparisonDifference);
  const machine = document.createElement("div"); machine.className = `third-machine-status ${thirdMachine.state || "unknown"}`;
  const machineLabel = document.createElement("small"); machineLabel.textContent = "Third machine";
  const machineValue = document.createElement("strong"); machineValue.textContent = thirdMachine.label || "Awaiting data";
  machine.append(machineLabel, machineValue);
  const status = document.createElement("p"); status.className = "overview-status";
  status.textContent = sos.last_error || labor.error || sos.window_label || "Waiting for data";
  card.append(heading, metrics, salesComparison, machine, status); return card;
}

async function refreshOverview() {
  const grid = document.getElementById("standGrid"), notice = document.getElementById("overviewNotice");
  try {
    const response = await fetch("/ops/api/dashboard", {cache: "no-store"});
    if (response.status === 403) { location.href = "/"; return; }
    if (response.status === 401) { location.href = "/login?next=/"; return; }
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    const data = await response.json();
    grid.replaceChildren(...data.stands.map(standCard));
    const issues = data.stands.filter(x => x.sos?.last_error || x.labor?.error);
    notice.classList.toggle("error", issues.length > 0);
    notice.textContent = issues.length ? `${issues.length} stand${issues.length === 1 ? "" : "s"} reporting a refresh issue.` : "All stands connected to Xenial reporting.";
  } catch (error) { grid.replaceChildren(); notice.classList.add("error"); notice.textContent = "Ops data is temporarily unavailable. Please try again shortly."; }
}

updateEasternClock(); setInterval(updateEasternClock, 1000);
refreshOverview(); setInterval(refreshOverview, 15000);
