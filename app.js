const els = {
  q: document.getElementById("q"),
  journal: document.getElementById("journal"),
  starOnly: document.getElementById("starOnly"),
  list: document.getElementById("list"),
  status: document.getElementById("status"),
  generatedAt: document.getElementById("generatedAt"),
  clearStars: document.getElementById("clearStars"),
};

const STAR_KEY = "anes_toc_starred_v1";
const MONTHS = ["JAN","FEB","MAR","APR","MAY","JUN","JUL","AUG","SEP","OCT","NOV","DEC"];

/* Progressive rendering settings */
const RENDER_BATCH = 50;           // items per auto-load step
const AUTO_SCROLL_LIMIT = 1000;    // auto-load until this many are visible
const MANUAL_LOAD_BATCH = 250;     // how many extra per "Load more" click

/* ---------- date helpers ---------- */

function formatDateDMYMMM(iso) {
  if (!iso) return "";
  const parts = iso.split("-");
  if (parts.length !== 3) return iso;
  const [y, m, d] = parts;
  const mi = parseInt(m, 10) - 1;
  if (mi < 0 || mi > 11) return iso;
  return `${d}/${MONTHS[mi]}/${y}`;
}

function formatDateTimeDMYMMM(iso) {
  if (!iso) return "";
  const [date, time] = iso.split("T");
  if (!date || !time) return iso;
  return `${formatDateDMYMMM(date)} ${time.slice(0, 5)}`;
}

function relativeFromMs(pastMs) {
  if (!pastMs) return "";
  const diff = Math.max(0, Date.now() - pastMs);

  const min = Math.floor(diff / 60000);
  if (min < 1) return "just now";
  if (min < 60) return `${min} min ago`;

  const hr = Math.floor(min / 60);
  if (hr < 24) return `${hr} h ago`;

  const d = Math.floor(hr / 24);
  return `${d} d ago`;
}

/* ---------- stars ---------- */

function loadStars() {
  try { return new Set(JSON.parse(localStorage.getItem(STAR_KEY) || "[]")); }
  catch { return new Set(); }
}

function saveStars(stars) {
  localStorage.setItem(STAR_KEY, JSON.stringify([...stars]));
}

/* ---------- query ---------- */

function matchesQuery(item, q) {
  if (!q) return true;
  const hay = `${item.title} ${item.authors} ${item.journal} ${item.doi}`.toLowerCase();
  return hay.includes(q.toLowerCase());
}

/* ---------- SJR helpers ---------- */

function normalizeIssn(s) {
  if (!s) return "";
  return String(s).trim().toUpperCase().replace(/\s+/g, "");
}

function formatSjrLabel(_sjrYear, sjrVal) {
  if (sjrVal == null || !Number.isFinite(sjrVal)) return `SJR: n/a`;
  return `SJR: ${sjrVal.toFixed(2)}`;
}

/* ---------- render (progressive) ---------- */

let DATA = { generated_at: null, items: [], meta: {} };
let SOURCES = [];
let METRICS = { sjr_year: null, by_issn: {} };
let GENERATED_MS = null;

let FILTERED = [];      // current filtered list
let visibleLimit = 0;   // how many are currently rendered
let manualExtra = 0;    // extra beyond AUTO_SCROLL_LIMIT enabled by button

let sentinelEl = null;
let loadMoreWrap = null;
let loadMoreBtn = null;
let io = null;

function ensureProgressiveControls() {
  // Sentinel for intersection observer
  if (!sentinelEl) {
    sentinelEl = document.createElement("div");
    sentinelEl.style.height = "1px";
    sentinelEl.style.width = "100%";
    sentinelEl.style.marginTop = "8px";
    els.list.parentElement.appendChild(sentinelEl);
  }

  // Load more button
  if (!loadMoreWrap) {
    loadMoreWrap = document.createElement("div");
    loadMoreWrap.style.display = "none";
    loadMoreWrap.style.marginTop = "12px";
    loadMoreWrap.style.textAlign = "center";

    loadMoreBtn = document.createElement("button");
    loadMoreBtn.className = "btn";
    loadMoreBtn.type = "button";
    loadMoreBtn.textContent = "Load more";

    loadMoreBtn.addEventListener("click", () => {
      manualExtra += MANUAL_LOAD_BATCH;
      // increase limit and re-render
      visibleLimit = Math.min(FILTERED.length, AUTO_SCROLL_LIMIT + manualExtra);
      renderVisible();
      updateLoadMoreVisibility();
    });

    loadMoreWrap.appendChild(loadMoreBtn);
    els.list.parentElement.appendChild(loadMoreWrap);
  }

  // Intersection observer to auto-load
  if (!io) {
    io = new IntersectionObserver((entries) => {
      const ent = entries[0];
      if (!ent || !ent.isIntersecting) return;

      const maxAuto = Math.min(FILTERED.length, AUTO_SCROLL_LIMIT);
      if (visibleLimit >= maxAuto) {
        updateLoadMoreVisibility();
        return;
      }

      visibleLimit = Math.min(maxAuto, visibleLimit + RENDER_BATCH);
      renderVisible();
      updateLoadMoreVisibility();
    }, { root: null, threshold: 0.1 });

    io.observe(sentinelEl);
  }
}

function render(items, stars) {
  els.list.innerHTML = "";
  if (!items.length) {
    els.list.innerHTML = `<div class="muted">No results.</div>`;
    return;
  }

  for (const it of items) {
    const key = it.doi || it.url || `${it.journal_short}|${it.title}`;
    const starOn = stars.has(key);

    const div = document.createElement("div");
    div.className = "item";
    div.innerHTML = `
      <div class="top">
        <button class="star ${starOn ? "on" : ""}" aria-label="star">${starOn ? "★" : "☆"}</button>
        <div>
          <h3 class="title">
            <a href="${it.url}" target="_blank" rel="noopener noreferrer">${it.title}</a>
          </h3>
          <div class="metaLine">
            <span class="pill">${it.journal_short}</span>
            ${it.aop ? `<span class="pill aop">Ahead of print</span>` : ""}
            ${it.published ? `<span>${formatDateDMYMMM(it.published)}</span>` : `<span class="muted">no date</span>`}
            ${it.authors ? `<span>${it.authors}</span>` : ""}
            ${it.doi ? `<span class="muted">${it.doi}</span>` : ""}
            ${it.pubmed_url ? `<a class="pill" href="${it.pubmed_url}" target="_blank" rel="noopener noreferrer">PubMed</a>` : ""}
          </div>
        </div>
      </div>
    `;

    div.querySelector(".star").addEventListener("click", () => {
      const s = loadStars();
      s.has(key) ? s.delete(key) : s.add(key);
      saveStars(s);
      applyFilters(true); // keep current scroll limits where possible
    });

    els.list.appendChild(div);
  }
}

function updateStatus() {
  const shown = Math.min(visibleLimit, FILTERED.length);
  els.status.textContent = `${shown} / ${FILTERED.length} articles shown`;
}

function updateLoadMoreVisibility() {
  if (!loadMoreWrap) return;

  const maxAuto = Math.min(FILTERED.length, AUTO_SCROLL_LIMIT);
  const canManual = FILTERED.length > maxAuto;
  const isAutoDone = visibleLimit >= maxAuto;

  if (canManual && isAutoDone && visibleLimit < FILTERED.length) {
    loadMoreWrap.style.display = "block";
  } else {
    loadMoreWrap.style.display = "none";
  }

  updateStatus();
}

function renderVisible() {
  const stars = loadStars();
  const slice = FILTERED.slice(0, Math.min(visibleLimit, FILTERED.length));
  render(slice, stars);
  updateLoadMoreVisibility();
}

/* ---------- filtering ---------- */

function computeFiltered() {
  const stars = loadStars();
  const q = els.q.value.trim();
  const j = els.journal.value;

  let items = DATA.items.slice();

  if (j) items = items.filter(it => it.journal_short === j);
  items = items.filter(it => matchesQuery(it, q));

  if (els.starOnly.checked) {
    items = items.filter(it => {
      const key = it.doi || it.url || `${it.journal_short}|${it.title}`;
      return stars.has(key);
    });
  }
  return items;
}

/**
 * applyFilters(resetLimits=true):
 * - true  => reset progressive rendering (new search/filter)
 * - false => keep limits (e.g. starring an item)
 */
function applyFilters(resetLimits = true) {
  const prevLen = FILTERED.length;
  FILTERED = computeFiltered();

  if (resetLimits) {
    manualExtra = 0;
    visibleLimit = Math.min(RENDER_BATCH, FILTERED.length);
  } else {
    // Keep visibleLimit but don't exceed new filtered size
    // If we had fewer items before and now more, keep current visibleLimit as is
    visibleLimit = Math.min(visibleLimit, FILTERED.length);
    // If list shrank to zero but was not reset, ensure at least initial slice
    if (FILTERED.length > 0 && visibleLimit === 0) {
      visibleLimit = Math.min(RENDER_BATCH, FILTERED.length);
    }
  }

  renderVisible();
}

/* ---------- init ---------- */

async function init() {
  els.status.textContent = "Loading data…";

  const res = await fetch("./data.json", { cache: "no-store" });
  DATA = await res.json();
  GENERATED_MS = DATA.generated_at ? Date.parse(DATA.generated_at) : null;

  const srcRes = await fetch("./sources.json", { cache: "no-store" });
  SOURCES = await srcRes.json();

  try {
    const mRes = await fetch("./journal_metrics.json", { cache: "no-store" });
    METRICS = await mRes.json();
  } catch (e) {
    console.warn("journal_metrics.json not available yet:", e);
    METRICS = { sjr_year: null, by_issn: {} };
  }

  // counts per journal_short (for dropdown)
  const counts = new Map();
  for (const it of DATA.items) {
    counts.set(it.journal_short, (counts.get(it.journal_short) || 0) + 1);
  }

  const sjrYear = METRICS.sjr_year ?? "—";
  const byIssn = METRICS.by_issn || {};

  const options = SOURCES.map(s => {
    const short = s.short || s.name;
    const name = s.name;
    const issn = normalizeIssn(s.issn);

    const metric = byIssn[issn];
    const sjrVal = metric && Number.isFinite(metric.sjr) ? Number(metric.sjr) : null;

    return {
      short,
      name,
      sjrVal,
      count: counts.get(short) || 0,
      sjrText: formatSjrLabel(sjrYear, sjrVal),
    };
  }).sort((a, b) => {
    const aHas = a.sjrVal != null;
    const bHas = b.sjrVal != null;
    if (aHas && bHas) {
      if (b.sjrVal !== a.sjrVal) return b.sjrVal - a.sjrVal;
    } else if (aHas !== bHas) {
      return aHas ? -1 : 1;
    }
    return a.short.localeCompare(b.short);
  });

  for (const o of options) {
    const opt = document.createElement("option");
    opt.value = o.short;
    opt.textContent = `${o.short} — ${o.name} — ${o.sjrText} (${o.count})`;
    els.journal.appendChild(opt);
  }

  function updateHeader() {
    if (!DATA.generated_at || !GENERATED_MS) {
      els.generatedAt.textContent = "";
      return;
    }
    els.generatedAt.textContent =
      `Updated: ${formatDateTimeDMYMMM(DATA.generated_at)} UTC · ${relativeFromMs(GENERATED_MS)}`;
  }

  els.q.addEventListener("input", () => applyFilters(true));
  els.journal.addEventListener("change", () => applyFilters(true));
  els.starOnly.addEventListener("change", () => applyFilters(true));

  els.clearStars.addEventListener("click", () => {
    localStorage.removeItem(STAR_KEY);
    applyFilters(true);
  });

  updateHeader();
  setInterval(updateHeader, 30_000);

  // Progressive UI controls (sentinel + load more)
  ensureProgressiveControls();

  // Initial render
  FILTERED = computeFiltered();
  visibleLimit = Math.min(RENDER_BATCH, FILTERED.length);
  renderVisible();
}

init().catch(err => {
  console.error(err);
  els.status.textContent = "Failed to load data.json";
  els.list.innerHTML = `<div class="muted">Error loading data.</div>`;
});
