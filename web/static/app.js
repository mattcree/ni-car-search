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
const ALL_SCRAPERS = ["AutoTrader", "Gumtree", "Motors", "NIVehicleSales", "UsedCarsNI"];

function renderRunScraperList(errObj, scraperCounts) {
  return ALL_SCRAPERS.map(s => {
    const msg = errObj[s];
    const count = (scraperCounts || {})[s];
    if (msg && String(msg).includes("0 results")) {
      return `<div class="scraper-row warn"><span class="scraper-row-icon">\u26a0</span><span class="scraper-row-name">${esc(s)}</span><span class="scraper-row-detail">0 results</span></div>`;
    } else if (msg) {
      return `<div class="scraper-row err"><span class="scraper-row-icon">\u2717</span><span class="scraper-row-name">${esc(s)}</span><span class="scraper-row-detail">${esc(String(msg).substring(0, 60))}</span></div>`;
    } else {
      return `<div class="scraper-row ok"><span class="scraper-row-icon">\u2713</span><span class="scraper-row-name">${esc(s)}</span><span class="scraper-row-count">${count != null ? count : 0}</span></div>`;
    }
  }).join("");
}

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
    const hi = HEALTH_ICON[w.health] || HEALTH_ICON.unknown;
    const hc = HEALTH_CLASS[w.health] || HEALTH_CLASS.unknown;
    const polling = activePolls.has(w.id);

    // Next poll time
    let nextLabel = "";
    if (w.next_run) {
      const mins = Math.max(0, Math.round((new Date(w.next_run) - Date.now()) / 60000));
      nextLabel = `Next: ${mins}m`;
    }

    return `
    <div class="watch-card ${w.id === currentWatchId ? 'active' : ''}"
         onclick="showWatch(${w.id})">
      <div class="watch-card-title">
        <span class="${hc}">${hi}</span> ${esc(w.make)} ${esc(w.model)}
      </div>
      <div class="watch-card-meta">
        <span>${w.active_count} active</span>
        ${nextLabel ? `<span>${nextLabel}</span>` : ''}
        ${polling ? '<span class="polling-indicator"><span class="spinner"></span> Polling</span>' : ''}
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
  if (location.hash !== `#watch/${id}`) { location.hash = `#watch/${id}`; return; }
  currentWatchId = id;
  currentView = "watch";
  showView("view-watch");
  renderWatchList();
  updateSidebarHighlight();

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

  // Clear stale poll progress from another watch, then show if this watch is polling
  hidePollProgress();
  if (activePolls.has(id)) renderPollProgress(id);

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
  const SOURCE_ABBR = { AutoTrader: "AT", Motors: "M", Gumtree: "GT", UsedCarsNI: "UCNI", NIVehicleSales: "NIVS" };

  container.innerHTML = `
    <table class="listing-table">
      <thead><tr>
        <th>Title</th><th>Price</th><th>Year</th><th>Mileage</th><th>Trans</th><th>Sites</th><th>First Seen</th>
      </tr></thead>
      <tbody>
        ${currentVehicles.map(v => {
          // Signal stripe class
          let stripe = "";
          if (v.is_new) stripe = "row-signal-new";
          else if (v.price_direction) stripe = "row-signal-price";

          // Price delta indicator
          let delta = "";
          if (v.price_delta && v.price_direction) {
            const arrow = v.price_direction === "down" ? "\u25be" : "\u25b4";
            const cls = v.price_direction === "down" ? "price-trend down" : "price-trend up";
            delta = ` <span class="${cls}">${arrow}\u00a3${v.price_delta.toLocaleString()}</span>`;
          }

          // Source abbreviations
          const sources = v.sources.map(s => SOURCE_ABBR[s] || s).join(", ");

          // Status: only show for gone
          const gone = v.status !== "active" ? ' <span class="status-badge gone">gone</span>' : "";

          return `
          <tr class="${stripe}" onclick="showVehicle(${v.id})">
            <td title="${esc(v.best_title)}">${esc(v.best_title)}${gone}</td>
            <td class="price">${formatPrice(v.best_price)}${delta}</td>
            <td>${v.year || '-'}</td>
            <td>${v.mileage_bucket != null ? '~' + (v.mileage_bucket * 1000).toLocaleString() : '-'}</td>
            <td>${esc(v.transmission || '-')}</td>
            <td class="source">${esc(sources)}</td>
            <td>${formatDate(v.first_seen_at)}</td>
          </tr>`;
        }).join("")}
      </tbody>
    </table>`;
}

/* ── activity tab ───────────────────────────────────────────────────────── */

function renderRunCard(r, watchName) {
  const errObj = (r.errors && r.errors !== "null") ? JSON.parse(r.errors) : {};
  const hasRealError = Object.entries(errObj).some(([, v]) => !String(v).includes("0 results"));
  const statusClass = !r.finished_at ? "running" : hasRealError ? "warn" : "ok";
  const dur = duration(r.started_at, r.finished_at);

  const scraperList = renderRunScraperList(errObj, r.scraper_counts);

  return `
  <div class="run-card ${statusClass}" onclick="toggleRunDetail(this, ${r.id})">
    <div class="run-card-header">
      <span class="run-time">${formatDate(r.started_at)}</span>
      ${watchName ? `<span class="run-watch-name">${esc(watchName)}</span>` : ''}
      <span class="run-summary">
        ${r.total_found || 0} found
        ${r.new_count ? ', <strong>' + r.new_count + ' new</strong>' : ''}
        ${r.price_changed_count ? ', ' + r.price_changed_count + ' price changes' : ''}
        ${r.gone_count ? ', ' + r.gone_count + ' gone' : ''}
      </span>
      ${dur ? '<span class="run-duration">' + dur + '</span>' : ''}
    </div>
    <div class="run-scraper-list">${scraperList}</div>
    <div class="run-detail" style="display:none"></div>
  </div>`;
}

async function loadActivity(watchId) {
  const container = document.getElementById("activity-log");
  container.innerHTML = '<p style="color:var(--text-muted)">Loading runs...</p>';
  try {
    const runs = await api("GET", `/watches/${watchId}/runs?limit=50`);
    if (!runs.length) {
      container.innerHTML = '<p style="color:var(--text-muted)">No runs yet. Hit "Poll Now" to start.</p>';
      return;
    }
    container.innerHTML = runs.map(r => renderRunCard(r)).join("");
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

/* ── feed (replaces dashboard) ───────────────────────────────────────────── */

const SIGNAL_LABELS = {
  FOUND: "NEW", NEW_SOURCE: "CROSS-LISTED", PRICE_CHANGE: "PRICE",
  SOURCE_GONE: "DELISTED", GONE: "GONE", RETURNED: "RETURNED",
};
const SIGNAL_CLASS = {
  FOUND: "signal-new", NEW_SOURCE: "signal-lead", PRICE_CHANGE: "signal-price",
  SOURCE_GONE: "signal-gone", GONE: "signal-gone", RETURNED: "signal-lead",
};

async function renderFeed() {
  const el = document.getElementById("feed");
  if (!watches.length) {
    el.innerHTML = '<div class="empty-state"><h2>No watches yet</h2><p>Add a watch and run your first poll.</p></div>';
    return;
  }

  const totalActive = watches.reduce((a, w) => a + w.active_count, 0);
  const totalGone = watches.reduce((a, w) => a + w.gone_count, 0);

  // Find next poll
  let nextPoll = "";
  for (const w of watches) {
    if (w.next_run) {
      const mins = Math.max(0, Math.round((new Date(w.next_run) - Date.now()) / 60000));
      nextPoll = `Next poll: ${esc(w.make)} ${esc(w.model)} in ${mins}m`;
      break;
    }
  }

  const strip = `<div class="stats-strip">
    <span><strong>${watches.length}</strong> watches</span>
    <span class="stats-sep">\u00b7</span>
    <span><strong>${totalActive}</strong> active</span>
    <span class="stats-sep">\u00b7</span>
    <span><strong>${totalGone}</strong> gone</span>
    ${nextPoll ? `<span class="stats-sep">\u00b7</span><span class="stats-strip-next">${nextPoll}</span>` : ''}
  </div>`;

  // Load feed events
  const lastSeen = localStorage.getItem("feed_last_seen") || "";
  let feedHtml = "";

  try {
    const events = await api("GET", `/feed?limit=200`);
    if (!events.length) {
      feedHtml = '<p style="color:var(--ink-faint);padding:var(--sp-8) 0;text-align:center">No activity yet. Poll a watch to see events here.</p>';
    } else {
      const newCount = lastSeen ? events.filter(e => e.timestamp > lastSeen).length : 0;
      if (newCount > 0) {
        feedHtml += `<div class="overline">${newCount} new since last visit</div>`;
      }
      feedHtml += events.map(e => {
        const label = SIGNAL_LABELS[e.event_type] || e.event_type;
        const cls = SIGNAL_CLASS[e.event_type] || "";
        let desc = esc(e.vehicle_title || "");
        if (e.event_type === "PRICE_CHANGE" && e.old_price != null && e.price != null) {
          const delta = e.price - e.old_price;
          const pct = Math.round((delta / e.old_price) * 100);
          const dir = delta < 0 ? "\u25be" : "\u25b4";
          desc = `${formatPrice(e.old_price)} \u2192 ${formatPrice(e.price)} (${dir}${Math.abs(pct)}%)`;
        } else if (e.price != null) {
          desc += ` \u2014 ${formatPrice(e.price)}`;
        }
        if (e.source) desc += ` on ${esc(e.source)}`;

        const isNew = lastSeen && e.timestamp > lastSeen;
        return `<div class="feed-item${isNew ? ' feed-item-new' : ''}" onclick="showVehicleFromFeed(${e.vehicle_id}, ${e.watch_id})">
          <span class="feed-time">${relativeTime(e.timestamp)}</span>
          <span class="feed-signal ${cls}">${label}</span>
          <span class="feed-watch">${esc(e.watch_make)} ${esc(e.watch_model)}</span>
          <span class="feed-desc">${desc}</span>
        </div>`;
      }).join("");
    }
  } catch (err) {
    feedHtml = `<p style="color:var(--signal-gone)">Failed to load feed.</p>`;
  }

  el.innerHTML = `<h2 class="feed-title">Feed</h2>${strip}${feedHtml}`;

  // Mark as seen
  localStorage.setItem("feed_last_seen", new Date().toISOString());
}

function showVehicleFromFeed(vehicleId, watchId) {
  showVehicle(vehicleId);
}

/* ── vehicle detail panel ───────────────────────────────────────────────── */

async function showVehicle(id) {
  try {
    const v = await api("GET", `/vehicles/${id}`);
    const panel = document.getElementById("listing-panel");
    document.getElementById("panel-title").textContent = v.best_title;

    // Show first available image
    const imgListing = v.listings.find(l => l.image_url);
    let imageHtml = imgListing ? `<img class="vehicle-image" src="${esc(imgListing.image_url)}" alt="">` : '';

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

    document.getElementById("panel-body").innerHTML = imageHtml + listings + timeline;
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

let catalogueMakes = null; // cached on first load
let locations = null; // cached on first load

async function loadLocations() {
  if (locations) return locations;
  try {
    locations = await api("GET", "/locations");
  } catch {
    locations = [{ name: "Lisburn", value: "lisburn" }];
  }
  return locations;
}

function populateLocationDropdown(selectEl, selectedValue) {
  if (!locations) return;
  selectEl.innerHTML = locations.map(l =>
    `<option value="${esc(l.value)}" ${l.value === (selectedValue || 'lisburn') ? 'selected' : ''}>${esc(l.name)}</option>`
  ).join("");
}

async function loadCatalogueMakes() {
  if (catalogueMakes !== null) return catalogueMakes;
  try {
    catalogueMakes = await api("GET", "/catalogue/makes");
  } catch {
    catalogueMakes = [];
  }
  return catalogueMakes;
}

async function populateMakeDropdown(form, selectedNormalized) {
  const makes = await loadCatalogueMakes();
  const select = form.elements.make_id;
  const input = form.elements.make;

  if (!makes.length) {
    // No catalogue — show free text
    select.style.display = "none";
    input.style.display = "block";
    return;
  }

  select.innerHTML = '<option value="">Select make...</option>' +
    makes.map(m => `<option value="${m.id}" data-norm="${esc(m.normalized)}">${esc(m.name)}</option>`).join("");
  select.style.display = "block";
  input.style.display = "none";

  if (selectedNormalized) {
    const match = makes.find(m => m.normalized === selectedNormalized);
    if (match) select.value = String(match.id);
  }
}

async function onMakeSelected(select) {
  const form = select.closest("form");
  const makeId = select.value;
  const modelSelect = form.elements.model_id;
  const modelInput = form.elements.model;

  // Set hidden text input to normalized value
  const opt = select.selectedOptions[0];
  if (opt) form.elements.make.value = opt.dataset.norm || "";

  if (!makeId) {
    modelSelect.innerHTML = '<option value="">Select model...</option>';
    return;
  }

  try {
    const models = await api("GET", `/catalogue/makes/${makeId}/models`);
    modelSelect.innerHTML = '<option value="">Select model...</option>' +
      models.map(m => `<option value="${m.id}" data-norm="${esc(m.normalized)}">${esc(m.name)}</option>`).join("");
    modelSelect.style.display = "block";
    modelInput.style.display = "none";
  } catch {
    modelSelect.style.display = "none";
    modelInput.style.display = "block";
  }
}

function onModelSelected(select) {
  const form = select.closest("form");
  const opt = select.selectedOptions[0];
  if (opt) form.elements.model.value = opt.dataset.norm || "";
}

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

  // Populate location dropdown
  loadLocations().then(() => {
    populateLocationDropdown(form.elements.location, watch ? watch.location : "lisburn");
  });

  // Populate catalogue dropdowns
  populateMakeDropdown(form, watch ? watch.make : null).then(() => {
    if (watch && watch.make) {
      // Trigger model population for edit case
      const makeSelect = form.elements.make_id;
      if (makeSelect.value) {
        onMakeSelected(makeSelect).then(() => {
          const modelSelect = form.elements.model_id;
          const models = modelSelect.options;
          for (const opt of models) {
            if (opt.dataset.norm === watch.model) { modelSelect.value = opt.value; break; }
          }
        });
      }
    }
  });
}

function closeModal(e) {
  if (e && e.target !== e.currentTarget) return;
  document.getElementById("modal-overlay").classList.remove("open");
}

async function submitWatch(e) {
  e.preventDefault();
  const form = e.target;
  // make/model come from the hidden text inputs (set by dropdown onChange or typed directly)
  const data = {
    make: form.elements.make.value.trim().toLowerCase(),
    model: form.elements.model.value.trim().toLowerCase(),
    location: form.elements.location.value || "lisburn",
    radius: parseInt(form.elements.radius.value) || 80,
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
  if (location.hash !== "#settings") { location.hash = "#settings"; return; }
  currentWatchId = null; currentView = "settings"; renderWatchList(); updateSidebarHighlight(); showView("view-settings");
  try {
    const settings = await api("GET", "/settings");
    const form = document.getElementById("settings-form");
    form.elements.ntfy_url.value = settings.ntfy_url || "";
    form.elements.ntfy_topic.value = settings.ntfy_topic || "";
    form.elements.app_url.value = settings.app_url || "";
  } catch (err) { toast("Failed to load settings: " + err.message); }
}

async function saveSettings(e) {
  e.preventDefault();
  try {
    const form = e.target;
    await api("PUT", "/settings", { ntfy_url: form.elements.ntfy_url.value, ntfy_topic: form.elements.ntfy_topic.value, app_url: form.elements.app_url.value });
    toast("Settings saved");
  } catch (err) { toast("Failed to save settings: " + err.message); }
}

/* ── init ────────────────────────────────────────────────────────────────── */

/* ── global activity (poll history) ──────────────────────────────────────── */

async function showGlobalActivity() {
  if (location.hash !== "#activity") { location.hash = "#activity"; return; }
  currentWatchId = null;
  currentView = "activity";
  renderWatchList();
  updateSidebarHighlight();
  showView("view-activity");
  await renderGlobalActivity();
}

async function renderGlobalActivity() {
  const el = document.getElementById("global-activity");
  el.innerHTML = '<p style="color:var(--ink-muted)">Loading poll history...</p>';

  try {
    // Load runs from all watches
    const allRuns = [];
    for (const w of watches) {
      const runs = await api("GET", `/watches/${w.id}/runs?limit=20`);
      for (const r of runs) {
        r._watch_name = `${w.make} ${w.model}`;
        allRuns.push(r);
      }
    }
    // Sort by start time, newest first
    allRuns.sort((a, b) => b.started_at.localeCompare(a.started_at));

    if (!allRuns.length) {
      el.innerHTML = '<div class="empty-state"><h2>No polls yet</h2><p>Poll a watch to see history here.</p></div>';
      return;
    }

    el.innerHTML = `<h2 class="feed-title">Poll History</h2>` +
      allRuns.map(r => renderRunCard(r, r._watch_name)).join("");
  } catch (err) {
    el.innerHTML = '<p style="color:var(--signal-gone)">Failed to load poll history.</p>';
    toast("Failed to load poll history: " + err.message);
  }
}

/* ── catalogue ───────────────────────────────────────────────────────────── */

async function showCatalogue() {
  if (location.hash !== "#catalogue") { location.hash = "#catalogue"; return; }
  currentWatchId = null;
  currentView = "catalogue";
  renderWatchList();
  updateSidebarHighlight();
  showView("view-catalogue");
  await renderCatalogueHome();
}

async function renderCatalogueHome() {
  const el = document.getElementById("catalogue-content");
  el.innerHTML = '<p style="color:var(--text-muted)">Loading catalogue...</p>';

  try {
    const [makes, status] = await Promise.all([
      api("GET", "/catalogue/makes"),
      api("GET", "/catalogue/harvest/status"),
    ]);

    catalogueMakes = makes;
    const latestBySource = {};
    for (const s of status) {
      if (!latestBySource[s.source]) latestBySource[s.source] = s;
    }

    let harvestHtml = '<div class="catalogue-section"><div class="run-section-title">Sources</div>';
    for (const [src, s] of Object.entries(latestBySource)) {
      const icon = s.status === "completed" ? "\u2713" : s.status === "failed" ? "\u2717" : "\u23f3";
      const cls = s.status === "completed" ? "done" : s.status === "failed" ? "error" : "active";
      harvestHtml += `<div class="run-scraper ${cls}">
        <span>${icon} ${esc(src)}</span>
        <span>${s.makes_found} makes, ${s.models_found} models &middot; ${relativeTime(s.finished_at || s.started_at)}</span>
      </div>`;
    }
    if (!Object.keys(latestBySource).length) {
      harvestHtml += '<p style="color:var(--text-muted)">No harvest runs yet.</p>';
    }
    harvestHtml += '</div>';

    let makesHtml = '<div class="catalogue-section"><div class="run-section-title">Makes (' + makes.length + ')</div>';
    makesHtml += '<div class="catalogue-makes-grid">';
    for (const m of makes) {
      makesHtml += `<div class="catalogue-make-card" onclick="showCatalogueMake(${m.id})">
        <span class="catalogue-make-name">${esc(m.name)}</span>
        <span class="catalogue-make-count">${m.model_count} models</span>
      </div>`;
    }
    makesHtml += '</div></div>';

    el.innerHTML = `
      <div class="watch-title-row">
        <h2>Catalogue</h2>
        <button class="btn btn-sm btn-primary" id="harvest-btn" onclick="runHarvest()">Sync Now</button>
      </div>
      ${harvestHtml}
      ${makesHtml}
    `;

    document.getElementById("catalogue-row-meta").textContent = makes.length + " makes";
  } catch (err) {
    el.innerHTML = '<p style="color:var(--red)">Failed to load catalogue.</p>';
    toast("Failed to load catalogue: " + err.message);
  }
}

async function showCatalogueMake(makeId) {
  const el = document.getElementById("catalogue-content");
  try {
    const make = await api("GET", `/catalogue/makes/${makeId}`);
    const allSources = [...new Set(make.source_aliases.map(a => a.source))].sort();

    // Source names table
    let aliasRows = make.source_aliases.map(a =>
      `<tr>
        <td><span class="source-badge">${esc(a.source)}</span></td>
        <td>${esc(a.source_make)}</td>
        <td class="text-muted">${a.source_make_id ? esc(a.source_make_id) : '-'}</td>
      </tr>`
    ).join("");
    let aliasesHtml = aliasRows
      ? `<table class="catalogue-table">
          <thead><tr><th>Source</th><th>Name Used</th><th>ID</th></tr></thead>
          <tbody>${aliasRows}</tbody>
        </table>`
      : '<p class="text-muted">No source aliases.</p>';

    // Models table with source coverage columns
    const sourceHeaders = allSources.map(s => `<th class="source-col">${esc(s)}</th>`).join("");
    let modelRows = make.models.map(m => {
      const sourceSet = new Set(m.source_aliases.map(a => a.source));
      const cells = allSources.map(s =>
        sourceSet.has(s)
          ? '<td class="source-col has">\u2713</td>'
          : '<td class="source-col"></td>'
      ).join("");
      return `<tr><td class="model-name">${esc(m.name)}</td>${cells}</tr>`;
    }).join("");

    el.innerHTML = `
      <div class="watch-title-row">
        <button class="btn btn-sm" onclick="renderCatalogueHome()">\u2190 Back</button>
        <h2>${esc(make.canonical_name)}</h2>
      </div>
      <div class="catalogue-section">
        <div class="run-section-title">Source Names</div>
        ${aliasesHtml}
      </div>
      <div class="catalogue-section">
        <div class="run-section-title">Models (${make.models.length})</div>
        <table class="catalogue-table">
          <thead><tr><th>Model</th>${sourceHeaders}</tr></thead>
          <tbody>${modelRows}</tbody>
        </table>
      </div>
    `;
  } catch (err) {
    toast("Failed to load make: " + err.message);
  }
}

async function runHarvest() {
  const btn = document.getElementById("harvest-btn");
  if (btn) { btn.disabled = true; btn.innerHTML = '<span class="spinner"></span> Syncing\u2026'; }
  try {
    const results = await api("POST", "/catalogue/harvest");
    const parts = Object.entries(results).map(([s, r]) =>
      r.status === "completed" ? `${s}: ${r.makes} makes` : `${s}: ${r.status}`
    );
    toast("Harvest done: " + parts.join(", "));
    catalogueMakes = null;
    await renderCatalogueHome();
  } catch (err) {
    toast("Harvest failed: " + err.message);
  } finally {
    if (btn) { btn.disabled = false; btn.textContent = "Sync Now"; }
  }
}

/* ── init ────────────────────────────────────────────────────────────────── */

document.addEventListener("DOMContentLoaded", async () => {
  await loadLocations();
  await loadWatches();
  navigate(location.hash);
});

window.addEventListener("hashchange", () => navigate(location.hash));

document.addEventListener("keydown", (e) => {
  if (e.key === "Escape") { closeModal(); closePanel(); }
});

function toggleSidebar() {
  document.getElementById("sidebar").classList.toggle("open");
}

// Close sidebar on navigation (mobile)
function closeSidebarOnMobile() {
  document.getElementById("sidebar").classList.remove("open");
}

function goHome() {
  location.hash = "#feed";
}

function navigate(hash) {
  closeSidebarOnMobile();
  hash = hash || "#feed";
  if (hash === "#feed" || hash === "#" || hash === "") {
    currentWatchId = null;
    currentView = "dashboard";
    renderWatchList();
    updateSidebarHighlight();
    showView("view-empty");
    renderFeed();
  } else if (hash === "#activity") {
    showGlobalActivity();
  } else if (hash === "#catalogue") {
    showCatalogue();
  } else if (hash.startsWith("#catalogue/")) {
    showCatalogue().then(() => {
      const makeId = parseInt(hash.split("/")[1]);
      if (makeId) showCatalogueMake(makeId);
    });
  } else if (hash === "#settings") {
    showSettings();
  } else if (hash.startsWith("#watch/")) {
    const id = parseInt(hash.split("/")[1]);
    if (id) showWatch(id);
  }
}

let currentView = "dashboard"; // "dashboard" | "catalogue" | "watch" | "settings"

function updateSidebarHighlight() {
  for (const [id, view] of [["home-row","dashboard"],["activity-row","activity"],["catalogue-row","catalogue"],["settings-row","settings"]]) {
    const el = document.getElementById(id);
    if (el) el.classList.toggle("active", currentView === view);
  }
}
