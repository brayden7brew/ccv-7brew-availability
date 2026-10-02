const detailMoney = value => value == null ? "—" : new Intl.NumberFormat("en-US", {style: "currency", currency: "USD"}).format(value);

function laborStatus(value) {
  if (value == null) return "unknown";
  const rounded = Math.round((Number(value) + Number.EPSILON) * 10) / 10;
  return rounded <= 17 ? "goal" : rounded <= 19 ? "warning" : "danger";
}

function detailTile(label, value, note = "", className = "") {
  const tile = document.createElement("article"); tile.className = `detail-tile ${className}`.trim();
  const small = document.createElement("small"); small.textContent = label;
  const strong = document.createElement("strong"); strong.textContent = value;
  const span = document.createElement("span"); span.textContent = note;
  tile.append(small, strong, span); return tile;
}

function historyTile(label, value, detail) {
  return detailTile(label, value || "—", detail || "No data yet");
}

async function refreshStand() {
  const page = document.querySelector(".stand-detail-page"), notice = document.getElementById("detailNotice");
  try {
    const response = await fetch(`/ops/api/stands/${page.dataset.standSlug}`, {cache: "no-store"});
    if (response.status === 403) { location.href = "/"; return; }
    if (response.status === 401) { location.href = `/login?next=${encodeURIComponent(location.pathname)}`; return; }
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    const stand = await response.json(), sos = stand.sos || {}, labor = stand.labor || {}, connectivity = stand.connectivity || {}, thirdMachine = stand.third_machine || {};
    document.getElementById("detailMetrics").replaceChildren(
      detailTile("Current wait", sos.closed ? "Closed" : (sos.average || "—"), sos.window_label || "", `wait-status ${sos.status || "unknown"}`),
      detailTile("Current labor", labor.labor_percentage == null ? "—" : `${labor.labor_percentage.toFixed(2)}%`, `${detailMoney(labor.labor_cost)} labor`, `labor-status ${laborStatus(labor.labor_percentage)}`),
      detailTile("Gross sales", detailMoney(labor.gross_sales), "All closed channels"),
      detailTile("Clocked in", labor.clocked_in == null ? "—" : String(labor.clocked_in), "People currently working"),
      detailTile("Third machine", thirdMachine.label || "Awaiting data", `${thirdMachine.reason || ""} · Machine usage connection coming later`, `third-machine-detail ${thirdMachine.state || "unknown"}`),
      detailTile("Orders", String(sos.orders || 0), sos.window_label || "Current window"),
      detailTile("Internet", !connectivity.configured ? "Not configured" : connectivity.online === true ? "Online" : connectivity.online === false ? "Offline" : "Checking", connectivity.latency_ms == null ? (connectivity.error || "Checked every minute") : `${connectivity.latency_ms} ms response`),
    );
    const dailyWait = sos.daily_average || {};
    document.getElementById("waitHistory").replaceChildren(
      historyTile("Today", dailyWait.average, `${dailyWait.orders || 0} orders`),
      ...(sos.shift_averages || []).map(x => historyTile(x.label, x.average, `${x.orders || 0} orders`)),
    );
    document.getElementById("laborHistory").replaceChildren(
      detailTile("Today", labor.labor_percentage == null ? "—" : `${labor.labor_percentage.toFixed(2)}%`, `${detailMoney(labor.labor_cost)} / ${detailMoney(labor.gross_sales)}`, `labor-status ${laborStatus(labor.labor_percentage)}`),
      ...(labor.shift_averages || []).map(x => detailTile(x.label, x.labor_percentage == null ? "—" : `${x.labor_percentage.toFixed(2)}%`, `${detailMoney(x.labor_cost)} / ${detailMoney(x.gross_sales)}`, `labor-status ${laborStatus(x.labor_percentage)}`)),
    );
    const issue = sos.last_error || labor.error;
    notice.classList.toggle("error", Boolean(issue)); notice.textContent = issue || "Live data connected.";
  } catch (error) { ["detailMetrics","waitHistory","laborHistory"].forEach(id => document.getElementById(id).replaceChildren()); notice.classList.add("error"); notice.textContent = "Stand data is temporarily unavailable. Please try again shortly."; }
}

refreshStand(); setInterval(refreshStand, 15000);
