/* ── state ───────────────────────────────────────────────────────────────── */

let watches = [];
let currentWatchId = null;
let currentVehicles = [];
let currentSort = { col: "best_price", order: "asc" };
let currentFilter = "active";
let currentTab = "vehicles";

/**
 * Tracks active polls: watchId -> {
 *   scrapers: string[], counts: {}, scraperState: {}, scraperErrors: {},
 *   phase: "starting"|"scraping"|"persisting"|"done"|"error"
 * }
 */
const activePolls = new Map();

/* ── api / formatting ───────────────────────────────────────────────────── */

async function api(method, path, body) {
  const opts = { method, headers: { "Content-Type": "application/json" } };
  if (body !== undefined) opts.body = JSON.stringify(body);
  const res = await fetch("/api" + path, opts);
  if (res.status === 204) return null;
  if (!res.ok) throw new Error(`${res.status} ${res.statusText}`);
  return res.json();
}

function formatPrice(p) {
  if (p == null) return "-";
  return "\u00a3" + Number(p).toLocaleString();
}

function formatDate(iso) {
  if (!iso) return "-";
  const d = new Date(iso);
  return d.toLocaleDateString("en-GB", { day: "numeric", month: "short" })
    + " " + d.toLocaleTimeString("en-GB", { hour: "2-digit", minute: "2-digit" });
}

function relativeTime(iso) {
  if (!iso) return "";
  const diff = Date.now() - new Date(iso).getTime();
  const mins = Math.floor(diff / 60000);
  if (mins < 1) return "just now";
  if (mins < 60) return mins + "m ago";
  const hrs = Math.floor(mins / 60);
  if (hrs < 24) return hrs + "h ago";
  const days = Math.floor(hrs / 24);
  return days + "d ago";
}

function duration(startIso, endIso) {
  if (!startIso || !endIso) return "";
  const ms = new Date(endIso) - new Date(startIso);
  const s = Math.round(ms / 1000);
  if (s < 60) return s + "s";
  return Math.floor(s / 60) + "m " + (s % 60) + "s";
}

function esc(s) {
  if (!s) return "";
  const el = document.createElement("span");
  el.textContent = s;
  return el.innerHTML;
}

function toast(msg) {
  const el = document.createElement("div");
  el.className = "toast";
  el.textContent = msg;
  document.body.appendChild(el);
  setTimeout(() => el.remove(), 3000);
}

const HEALTH_ICON = { healthy: "\u25cf", degraded: "\u25cf", failing: "\u25cf", unknown: "\u25cb" };
const HEALTH_CLASS = { healthy: "health-ok", degraded: "health-warn", failing: "health-bad", unknown: "health-unknown" };

/* ── sidebar ────────────────────────────────────────────────────────────── */

async function loadWatches() {
  try {
    watches = await api("GET", "/watches");
    renderWatchList();
  } catch (err) {
    toast("Failed to load watches: " + err.message);
  }
}

function renderWatchList() {
  const container = document.getElementById("watch-list");
  if (!watches.length) {
    container.innerHTML = '<div style="padding:16px;color:var(--text-muted)">No watches yet.</div>';
    return;
  }
  container.innerHTML = watches.map(w => {
    const loc = w.location !== "northern-ireland" ? esc(w.location) : "NI";
    const hi = HEALTH_ICON[w.health] || HEALTH_ICON.unknown;
    const hc = HEALTH_CLASS[w.health] || HEALTH_CLASS.unknown;
    const polling = activePolls.has(w.id);
    const nextRun = w.next_run ? relativeTime(new Date(Date.now() - (Date.now() - new Date(w.next_run).getTime())).toISOString()) : "";

    return `
    <div class="watch-card ${w.id === currentWatchId ? 'active' : ''}"
         onclick="showWatch(${w.id})">
      <div class="watch-card-title">
        <span class="${hc}">${hi}</span> ${esc(w.make)} ${esc(w.model)}
      </div>
      <div class="watch-card-meta">
        ${loc}${w.radius ? " / " + w.radius + " mi" : ""}
        &middot; every ${w.poll_interval_minutes}m
        ${w.last_polled_at ? "&middot; " + relativeTime(w.last_polled_at) : ""}
      </div>
      <div class="watch-card-stats">
        ${polling ? '<span class="polling-indicator"><span class="spinner"></span> Polling</span>' : ''}
        <span class="stat-active">${w.active_count} active</span>
        <span class="stat-gone">${w.gone_count} gone</span>
      </div>
    </div>`;
  }).join("");
}

/* ── views / tabs ───────────────────────────────────────────────────────── */

function showView(id) {
  document.querySelectorAll(".view").forEach(v => v.classList.remove("active"));
  document.getElementById(id).classList.add("active");
}

function switchTab(tab) {
  currentTab = tab;
  document.querySelectorAll(".tab").forEach(t => t.classList.toggle("active", t.dataset.tab === tab));
  document.querySelectorAll(".tab-content").forEach(c => c.classList.remove("active"));
  document.getElementById("tab-" + tab).classList.add("active");
  if (tab === "activity" && currentWatchId) loadActivity(currentWatchId);
}

async function showWatch(id) {
  currentWatchId = id;
  showView("view-watch");
  renderWatchList();
  updateHomeRow();

  const watch = watches.find(w => w.id === id);
  if (!watch) return;

  // Header
  const hdr = document.getElementById("watch-header");
  const tags = [];
  if (watch.min_price) tags.push("\u00a3" + watch.min_price.toLocaleString() + "+");
  if (watch.max_price) tags.push("up to \u00a3" + watch.max_price.toLocaleString());
  if (watch.min_year) tags.push(watch.min_year + "+");
  if (watch.max_year) tags.push("up to " + watch.max_year);
  if (watch.location && watch.location !== "northern-ireland") tags.push(watch.location);
  if (watch.radius) tags.push(watch.radius + " miles");

  hdr.innerHTML = `
    <div class="watch-title-row">
      <h2>${esc(watch.make)} ${esc(watch.model)}</h2>
      <button class="btn btn-sm" onclick="editWatch(${watch.id})">Edit</button>
      <button class="btn btn-sm btn-danger" onclick="deleteWatch(${watch.id})">Delete</button>
      <button class="btn btn-sm btn-primary" id="poll-btn" onclick="pollWatch(${watch.id})">Poll Now</button>
    </div>
    ${tags.length ? '<div class="watch-filters">' + tags.map(t => '<span class="tag">' + esc(String(t)) + '</span>').join("") + '</div>' : ''}
  `;

  // Restore current tab
  switchTab(currentTab);

  if (currentTab === "vehicles") {
    await loadVehiclesTab(id, watch);
  }
}

async function loadVehiclesTab(id, watch) {
  try {
    const stats = await api("GET", `/watches/${id}/stats`);
    document.getElementById("watch-stats").innerHTML = `
      <div class="stats-bar">
        <div class="stat-card"><div class="stat-value">${stats.total_vehicles}</div><div class="stat-label">Vehicles</div></div>
        <div class="stat-card"><div class="stat-value">${stats.active}</div><div class="stat-label">Active</div></div>
        <div class="stat-card"><div class="stat-value">${stats.gone}</div><div class="stat-label">Gone</div></div>
        <div class="stat-card"><div class="stat-value">${stats.total_price_changes}</div><div class="stat-label">Price Changes</div></div>
        ${stats.last_run ? `<div class="stat-card"><div class="stat-value">${relativeTime(stats.last_run.finished_at)}</div><div class="stat-label">Last Polled</div></div>` : ''}
      </div>`;
  } catch (err) { /* stats are supplementary */ }

  document.getElementById("listing-controls").innerHTML = `
    <div class="controls-row">
      <select onchange="currentFilter=this.value; loadVehicles()">
        <option value="active" ${currentFilter==='active'?'selected':''}>Active</option>
        <option value="gone" ${currentFilter==='gone'?'selected':''}>Gone</option>
        <option value="all" ${currentFilter==='all'?'selected':''}>All</option>
      </select>
      <select onchange="currentSort.col=this.value; loadVehicles()">
        <option value="best_price" ${currentSort.col==='best_price'?'selected':''}>Price</option>
        <option value="year" ${currentSort.col==='year'?'selected':''}>Year</option>
        <option value="first_seen_at" ${currentSort.col==='first_seen_at'?'selected':''}>First Seen</option>
        <option value="last_seen_at" ${currentSort.col==='last_seen_at'?'selected':''}>Last Seen</option>
        <option value="listing_count" ${currentSort.col==='listing_count'?'selected':''}>Sources</option>
      </select>
      <select onchange="currentSort.order=this.value; loadVehicles()">
        <option value="asc" ${currentSort.order==='asc'?'selected':''}>Ascending</option>
        <option value="desc" ${currentSort.order==='desc'?'selected':''}>Descending</option>
      </select>
    </div>`;

  await loadVehicles();
}

/* ── vehicles tab ───────────────────────────────────────────────────────── */

async function loadVehicles() {
  if (!currentWatchId) return;
  try {
    const params = new URLSearchParams({ status: currentFilter, sort: currentSort.col, order: currentSort.order });
    currentVehicles = await api("GET", `/watches/${currentWatchId}/vehicles?${params}`);
    renderVehicles();
  } catch (err) { toast("Failed to load vehicles: " + err.message); }
}

function renderVehicles() {
  const container = document.getElementById("listing-table");
  if (!currentVehicles.length) {
    container.innerHTML = '<p style="color:var(--text-muted);margin-top:20px">No vehicles found. Hit "Poll Now" to run a scrape.</p>';
    return;
  }
  container.innerHTML = `
    <table class="listing-table">
      <thead><tr>
        <th>Title</th><th>Price</th><th>Year</th><th>Mileage</th><th>Trans</th><th>Sites</th><th>Status</th><th>First Seen</th>
      </tr></thead>
      <tbody>
        ${currentVehicles.map(v => `
          <tr onclick="showVehicle(${v.id})">
            <td title="${esc(v.best_title)}">${esc(v.best_title)}</td>
            <td class="price">${formatPrice(v.best_price)}</td>
            <td>${v.year || '-'}</td>
            <td>${v.mileage_bucket != null ? '~' + (v.mileage_bucket * 1000).toLocaleString() : '-'}</td>
            <td>${esc(v.transmission || '-')}</td>
            <td>${v.sources.map(s => '<span class="source-badge">' + esc(s) + '</span>').join(" ")}</td>
            <td><span class="status-badge ${v.status === 'active' ? 'active' : 'gone'}">${esc(v.status)}</span></td>
            <td>${formatDate(v.first_seen_at)}</td>
          </tr>`).join("")}
      </tbody>
    </table>`;
}

/* ── activity tab ───────────────────────────────────────────────────────── */

async function loadActivity(watchId) {
  const container = document.getElementById("activity-log");
  container.innerHTML = '<p style="color:var(--text-muted)">Loading runs...</p>';
  try {
    const runs = await api("GET", `/watches/${watchId}/runs?limit=50`);
    if (!runs.length) {
      container.innerHTML = '<p style="color:var(--text-muted)">No runs yet. Hit "Poll Now" to start.</p>';
      return;
    }
    container.innerHTML = runs.map(r => {
      const hasErrors = r.errors && r.errors !== "null";
      const errObj = hasErrors ? JSON.parse(r.errors) : {};
      const errCount = Object.keys(errObj).length;
      const statusClass = !r.finished_at ? "running" : errCount ? "warn" : "ok";
      const statusIcon = !r.finished_at ? '<span class="spinner"></span>' : errCount ? "\u26a0" : "\u2713";
      const dur = duration(r.started_at, r.finished_at);

      return `
      <div class="run-card ${statusClass}" onclick="toggleRunDetail(this, ${r.id})">
        <div class="run-card-header">
          <span class="run-status">${statusIcon}</span>
          <span class="run-time">${formatDate(r.started_at)}</span>
          <span class="run-summary">
            ${r.total_found || 0} found
            ${r.new_count ? ', <strong>' + r.new_count + ' new</strong>' : ''}
            ${r.new_source_count ? ', ' + r.new_source_count + ' cross-site' : ''}
            ${r.price_changed_count ? ', ' + r.price_changed_count + ' price changes' : ''}
            ${r.gone_count ? ', ' + r.gone_count + ' gone' : ''}
          </span>
          ${dur ? '<span class="run-duration">' + dur + '</span>' : ''}
          ${errCount ? '<span class="run-errors">' + errCount + ' error' + (errCount > 1 ? 's' : '') + '</span>' : ''}
        </div>
        <div class="run-detail" style="display:none"></div>
      </div>`;
    }).join("");
  } catch (err) { toast("Failed to load runs: " + err.message); }
}

async function toggleRunDetail(card, runId) {
  const detail = card.querySelector(".run-detail");
  if (detail.style.display !== "none") {
    detail.style.display = "none";
    return;
  }

  detail.innerHTML = '<p style="color:var(--text-muted);padding:8px 0"><span class="spinner"></span> Loading...</p>';
  detail.style.display = "block";

  try {
    const run = await api("GET", `/runs/${runId}`);
    let html = "";

    // Scraper breakdown
    if (run.run_events.length) {
      const scrapers = {};
      for (const e of run.run_events) {
        if (!e.source) continue;
        if (!scrapers[e.source]) scrapers[e.source] = { status: "unknown", count: null, error: null };
        const s = scrapers[e.source];
        switch (e.event_type) {
          case "SCRAPER_START": s.status = "started"; break;
          case "SCRAPER_PROGRESS": s.count = e.count; s.status = "running"; break;
          case "SCRAPER_DONE": s.count = e.count; s.status = "done"; break;
          case "SCRAPER_ERROR": s.status = "error"; s.error = e.message; break;
          case "SCRAPER_RETRY": s.status = "retrying"; break;
        }
      }

      html += '<div class="run-section"><div class="run-section-title">Scrapers</div>';
      for (const [name, s] of Object.entries(scrapers)) {
        const cls = s.status === "error" ? "error" : s.status === "done" ? "done" : "active";
        const label = s.status === "error" ? esc(s.error || "failed")
          : (s.count != null ? s.count + " listings" : "no results");
        html += `<div class="run-scraper ${cls}"><span>${esc(name)}</span><span>${label}</span></div>`;
      }
      html += '</div>';
    }

    // Vehicle events from this run
    if (run.vehicle_events.length) {
      html += '<div class="run-section"><div class="run-section-title">Changes</div>';
      for (const e of run.vehicle_events) {
        let text = e.event_type;
        const src = e.source ? esc(e.source) : "";
        switch (e.event_type) {
          case "FOUND": text = "New vehicle at " + formatPrice(e.price) + " on " + src; break;
          case "NEW_SOURCE": text = "Also listed on " + src + " at " + formatPrice(e.price); break;
          case "PRICE_CHANGE": text = formatPrice(e.old_price) + " \u2192 " + formatPrice(e.price) + " on " + src; break;
          case "SOURCE_GONE": text = "Removed from " + src; break;
          case "GONE": text = "Gone from all sites"; break;
          case "RETURNED": text = "Returned on " + src + " at " + formatPrice(e.price); break;
        }
        const evClass = e.event_type.toLowerCase().replace("_", "-");
        html += `<div class="run-event ${evClass}"><span class="run-event-type">${esc(e.event_type)}</span><span>${text}</span></div>`;
      }
      html += '</div>';
    } else {
      html += '<div class="run-section"><div class="run-section-title">Changes</div><p style="color:var(--text-muted)">No changes in this run.</p></div>';
    }

    // Full event log
    html += '<div class="run-section"><div class="run-section-title">Event Log</div>';
    html += '<div class="event-log">';
    for (const e of run.run_events) {
      const time = formatDate(e.timestamp).split(" ").pop(); // just the time
      html += `<div class="log-line"><span class="log-time">${time}</span><span class="log-type">${esc(e.event_type)}</span>`;
      if (e.source) html += `<span class="log-source">${esc(e.source)}</span>`;
      if (e.count != null) html += `<span class="log-count">${e.count}</span>`;
      if (e.message) html += `<span class="log-msg">${esc(e.message)}</span>`;
      html += '</div>';
    }
    html += '</div></div>';

    detail.innerHTML = html;
  } catch (err) {
    detail.innerHTML = '<p style="color:var(--red)">Failed to load run detail.</p>';
  }
}

/* ── dashboard (no watch selected) ──────────────────────────────────────── */

function renderDashboard() {
  const el = document.getElementById("dashboard");
  if (!watches.length) {
    el.innerHTML = '<div class="empty-state"><h2>No watches yet</h2><p>Add a watch to start tracking car listings.</p></div>';
    return;
  }

  const healthy = watches.filter(w => w.health === "healthy").length;
  const degraded = watches.filter(w => w.health === "degraded").length;
  const failing = watches.filter(w => w.health === "failing").length;
  const totalVehicles = watches.reduce((a, w) => a + w.vehicle_count, 0);
  const totalActive = watches.reduce((a, w) => a + w.active_count, 0);

  el.innerHTML = `
    <div class="dashboard">
      <h2>Dashboard</h2>
      <div class="stats-bar">
        <div class="stat-card"><div class="stat-value">${watches.length}</div><div class="stat-label">Watches</div></div>
        <div class="stat-card"><div class="stat-value health-ok">${healthy}</div><div class="stat-label">Healthy</div></div>
        ${degraded ? `<div class="stat-card"><div class="stat-value health-warn">${degraded}</div><div class="stat-label">Degraded</div></div>` : ''}
        ${failing ? `<div class="stat-card"><div class="stat-value health-bad">${failing}</div><div class="stat-label">Failing</div></div>` : ''}
        <div class="stat-card"><div class="stat-value">${totalVehicles}</div><div class="stat-label">Vehicles</div></div>
        <div class="stat-card"><div class="stat-value">${totalActive}</div><div class="stat-label">Active</div></div>
      </div>
      <h3 style="margin-top:24px;margin-bottom:12px">Watches</h3>
      ${watches.map(w => {
        const hc = HEALTH_CLASS[w.health] || HEALTH_CLASS.unknown;
        const hi = HEALTH_ICON[w.health] || HEALTH_ICON.unknown;
        return `
        <div class="dashboard-watch" onclick="showWatch(${w.id})">
          <span class="${hc}">${hi}</span>
          <span class="dashboard-watch-name">${esc(w.make)} ${esc(w.model)}</span>
          <span class="dashboard-watch-stats">${w.active_count} active, ${w.gone_count} gone</span>
          <span class="dashboard-watch-last">${w.last_polled_at ? relativeTime(w.last_polled_at) : 'never polled'}</span>
        </div>`;
      }).join("")}
    </div>`;
}

/* ── vehicle detail panel ───────────────────────────────────────────────── */

async function showVehicle(id) {
  try {
    const v = await api("GET", `/vehicles/${id}`);
    const panel = document.getElementById("listing-panel");
    document.getElementById("panel-title").textContent = v.best_title;

    let listings = '<h4 style="margin-bottom:8px">Listings</h4>';
    for (const l of v.listings) {
      const statusClass = l.status === "active" ? "active" : "gone";
      listings += `
        <div class="source-listing ${statusClass}">
          <div class="source-listing-header">
            <span class="source-badge">${esc(l.source)}</span>
            <span class="price">${formatPrice(l.price)}</span>
            <span class="status-badge ${statusClass}">${esc(l.status)}</span>
          </div>
          <div class="source-listing-meta">
            ${esc(l.location || '')}
            ${l.mileage ? ' &middot; ' + esc(l.mileage) : ''}
            ${l.fuel_type && l.fuel_type !== '-' ? ' &middot; ' + esc(l.fuel_type) : ''}
            ${l.transmission && l.transmission !== '-' ? ' &middot; ' + esc(l.transmission) : ''}
          </div>
          <div class="source-listing-link">
            <a href="${esc(l.url)}" target="_blank" rel="noopener">View on ${esc(l.source)}</a>
          </div>
        </div>`;
    }

    let timeline = '<h4 style="margin:16px 0 8px">Timeline</h4><div class="timeline">';
    for (const ev of v.events) {
      let text = ev.event_type;
      const src = ev.source ? esc(ev.source) : "";
      switch (ev.event_type) {
        case "FOUND": text = "Found at " + formatPrice(ev.price) + (src ? " on " + src : ""); break;
        case "NEW_SOURCE": text = "Listed on " + src + " at " + formatPrice(ev.price); break;
        case "PRICE_CHANGE": text = formatPrice(ev.old_price) + " \u2192 " + formatPrice(ev.price) + (src ? " on " + src : ""); break;
        case "SOURCE_GONE": text = "Removed from " + src; break;
        case "GONE": text = "Gone from all sites"; break;
        case "RETURNED": text = "Returned" + (src ? " on " + src : "") + " at " + formatPrice(ev.price); break;
      }
      timeline += `
        <div class="timeline-item ${esc(ev.event_type)}">
          <div class="timeline-date">${formatDate(ev.timestamp)}</div>
          <div class="timeline-text">${esc(text)}</div>
        </div>`;
    }
    timeline += "</div>";

    document.getElementById("panel-body").innerHTML = listings + timeline;
    panel.classList.add("open");
  } catch (err) { toast("Failed to load vehicle: " + err.message); }
}

function closePanel() {
  document.getElementById("listing-panel").classList.remove("open");
}

/* ── poll with SSE progress ─────────────────────────────────────────────── */

async function pollWatch(id) {
  if (activePolls.has(id)) { toast("Already polling this watch"); return; }

  const btn = document.getElementById("poll-btn");
  if (btn) { btn.disabled = true; btn.innerHTML = '<span class="spinner"></span>Polling\u2026'; }

  const poll = { scrapers: [], counts: {}, scraperState: {}, scraperErrors: {}, phase: "starting" };
  activePolls.set(id, poll);
  renderWatchList();
  renderPollProgress(id);

  try {
    const res = await fetch("/api/watches/" + id + "/poll", { method: "POST" });
    const reader = res.body.getReader();
    const decoder = new TextDecoder();
    let buffer = "";

    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });
      const lines = buffer.split("\n");
      buffer = lines.pop();

      for (const line of lines) {
        if (!line.startsWith("data: ")) continue;
        const data = JSON.parse(line.slice(6));
        switch (data.type) {
          case "started": poll.scrapers = data.scrapers; poll.phase = "scraping"; break;
          case "scraper_start": poll.scraperState[data.source] = "running"; break;
          case "progress": poll.counts[data.source] = data.count; break;
          case "scraper_done":
            poll.scraperState[data.source] = "done";
            if (data.count != null) poll.counts[data.source] = data.count;
            break;
          case "scraper_error":
            poll.scraperState[data.source] = "error";
            poll.scraperErrors[data.source] = data.message;
            break;
          case "persisting": poll.phase = "persisting"; break;
          case "done":
            poll.phase = "done";
            poll.errors = data.result.errors || {};
            const r = data.result;
            toast(`Done: ${r.total_found} found, ${r.new} new, ${r.new_sources} cross-site, ${r.price_changed} price changes`);
            break;
          case "error": poll.phase = "error"; toast("Poll failed: " + data.message); break;
        }
        renderPollProgress(id);
      }
    }
  } catch (err) { toast("Poll failed: " + err.message); }
  finally {
    await new Promise(r => setTimeout(r, 2000));
    activePolls.delete(id);
    renderWatchList();
    hidePollProgress();
    if (btn) { btn.disabled = false; btn.textContent = "Poll Now"; }
    await loadWatches();
    if (currentWatchId === id) await showWatch(id);
  }
}

function renderPollProgress(watchId) {
  const el = document.getElementById("poll-progress");
  if (!el || currentWatchId !== watchId) return;
  const poll = activePolls.get(watchId);
  if (!poll) { el.className = "poll-progress"; return; }

  const total = Object.values(poll.counts).reduce((a, b) => a + b, 0);
  const finished = poll.phase === "done" || poll.phase === "error";

  let title = "";
  switch (poll.phase) {
    case "starting": title = "Starting scrapers\u2026"; break;
    case "scraping": title = `Scraping\u2026 ${total} listings so far`; break;
    case "persisting": title = `Saving ${total} listings\u2026`; break;
    case "done": title = `Complete \u2014 ${total} listings found`; break;
    case "error": title = "Failed"; break;
  }

  let rows = "";
  for (const name of poll.scrapers) {
    const count = poll.counts[name];
    const state = poll.scraperState[name];
    const err = poll.scraperErrors[name] || (poll.errors && poll.errors[name]);

    let status, statusClass, icon;
    if (err) { status = esc(err); statusClass = "error"; icon = ""; }
    else if (state === "done" || (finished && count != null)) { status = (count || 0) + " listings"; statusClass = "done"; icon = ""; }
    else if (finished && count == null) { status = "No results"; statusClass = "none"; icon = ""; }
    else if (count != null) { status = count + " listings"; statusClass = "active"; icon = '<span class="spinner"></span> '; }
    else if (state === "running") { status = "Scraping"; statusClass = "active"; icon = '<span class="spinner"></span> '; }
    else { status = "Waiting"; statusClass = "waiting"; icon = ""; }

    rows += `<div class="poll-scraper-row ${statusClass}">
      <span class="poll-scraper-name">${esc(name)}</span>
      <span class="poll-scraper-status">${icon}${status}</span>
    </div>`;
  }

  const titleIcon = finished ? "" : '<span class="spinner"></span> ';
  el.className = "poll-progress active";
  el.innerHTML = `<div class="poll-progress-title">${titleIcon}${title}</div>${rows}`;
}

function hidePollProgress() {
  const el = document.getElementById("poll-progress");
  if (el) el.className = "poll-progress";
}

/* ── watch form ─────────────────────────────────────────────────────────── */

function showWatchForm(watch) {
  const form = document.getElementById("watch-form");
  form.reset();
  if (watch) {
    document.getElementById("watch-form-title").textContent = "Edit Watch";
    form.elements.id.value = watch.id;
    form.elements.make.value = watch.make;
    form.elements.model.value = watch.model;
    form.elements.location.value = watch.location;
    form.elements.radius.value = watch.radius || "";
    form.elements.min_price.value = watch.min_price || "";
    form.elements.max_price.value = watch.max_price || "";
    form.elements.min_year.value = watch.min_year || "";
    form.elements.max_year.value = watch.max_year || "";
    form.elements.poll_interval_minutes.value = watch.poll_interval_minutes;
  } else {
    document.getElementById("watch-form-title").textContent = "Add Watch";
  }
  document.getElementById("modal-overlay").classList.add("open");
}

function closeModal(e) {
  if (e && e.target !== e.currentTarget) return;
  document.getElementById("modal-overlay").classList.remove("open");
}

async function submitWatch(e) {
  e.preventDefault();
  const form = e.target;
  const data = {
    make: form.elements.make.value,
    model: form.elements.model.value,
    location: form.elements.location.value || "northern-ireland",
    radius: form.elements.radius.value ? parseInt(form.elements.radius.value) : null,
    min_price: form.elements.min_price.value ? parseInt(form.elements.min_price.value) : null,
    max_price: form.elements.max_price.value ? parseInt(form.elements.max_price.value) : null,
    min_year: form.elements.min_year.value ? parseInt(form.elements.min_year.value) : null,
    max_year: form.elements.max_year.value ? parseInt(form.elements.max_year.value) : null,
    poll_interval_minutes: parseInt(form.elements.poll_interval_minutes.value) || 30,
  };
  try {
    const id = form.elements.id.value;
    if (id) { await api("PUT", `/watches/${id}`, data); toast("Watch updated"); }
    else { await api("POST", "/watches", data); toast("Watch created"); }
    closeModal();
    await loadWatches();
    if (currentWatchId) showWatch(currentWatchId);
  } catch (err) { toast("Failed to save watch: " + err.message); }
}

async function editWatch(id) {
  try { showWatchForm(await api("GET", `/watches/${id}`)); }
  catch (err) { toast("Failed to load watch: " + err.message); }
}

async function deleteWatch(id) {
  if (!confirm("Delete this watch and all its data?")) return;
  try {
    await api("DELETE", `/watches/${id}`);
    currentWatchId = null; showView("view-empty"); toast("Watch deleted");
    await loadWatches(); renderDashboard();
  } catch (err) { toast("Failed to delete watch: " + err.message); }
}

/* ── settings ───────────────────────────────────────────────────────────── */

async function showSettings() {
  currentWatchId = null; renderWatchList(); showView("view-settings");
  try {
    const settings = await api("GET", "/settings");
    const form = document.getElementById("settings-form");
    form.elements.ntfy_url.value = settings.ntfy_url || "";
    form.elements.ntfy_topic.value = settings.ntfy_topic || "";
  } catch (err) { toast("Failed to load settings: " + err.message); }
}

async function saveSettings(e) {
  e.preventDefault();
  try {
    const form = e.target;
    await api("PUT", "/settings", { ntfy_url: form.elements.ntfy_url.value, ntfy_topic: form.elements.ntfy_topic.value });
    toast("Settings saved");
  } catch (err) { toast("Failed to save settings: " + err.message); }
}

/* ── init ────────────────────────────────────────────────────────────────── */

document.addEventListener("DOMContentLoaded", async () => {
  await loadWatches();
  renderDashboard();
  updateHomeRow();
});

document.addEventListener("keydown", (e) => {
  if (e.key === "Escape") { closeModal(); closePanel(); }
});

function goHome() {
  currentWatchId = null;
  renderWatchList();
  updateHomeRow();
  showView("view-empty");
  renderDashboard();
}

function updateHomeRow() {
  const row = document.getElementById("home-row");
  if (row) row.classList.toggle("active", currentWatchId === null);
}
