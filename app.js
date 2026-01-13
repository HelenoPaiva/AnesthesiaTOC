const els = {
  q: document.getElementById("q"),
  journal: document.getElementById("journal"),
  type: document.getElementById("type"),
  starOnly: document.getElementById("starOnly"),
  advToggle: document.getElementById("advToggle"),
  advancedControls: document.getElementById("advancedControls"),
  list: document.getElementById("list"),
  status: document.getElementById("status"),
  generatedAt: document.getElementById("generatedAt"),
  clearStars: document.getElementById("clearStars"),
};

const STAR_KEY = "anes_toc_starred_v1";
const MONTHS = ["JAN","FEB","MAR","APR","MAY","JUN","JUL","AUG","SEP","OCT","NOV","DEC"];

// Canonical dashboard categories (fallback; primary source is data.json meta.categories)
const DASHBOARD_CATEGORIES = [
  "Systematic Review",
  "Meta-analysis",
  "Randomized Controlled Trial",
  "Observational Study",
  "Cohort Study",
  "Case-Control Study",
  "Guideline / Consensus",
  "Narrative Review",
  "Editorial / Commentary",
];

/* Progressive rendering settings */
const RENDER_BATCH = 50;
const AUTO_SCROLL_LIMIT = 1000;
const MANUAL_LOAD_BATCH = 250;

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
  return item.searchText.includes(q);
}

function debounce(fn, delayMs) {
  let timerId = null;
  return (...args) => {
    if (timerId) clearTimeout(timerId);
    timerId = setTimeout(() => fn(...args), delayMs);
  };
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

/* ---------- state ---------- */

let DATA = { generated_at: null, items: [], meta: {} };
let SOURCES = [];
let METRICS = { sjr_year: null, by_issn: {} };
let GENERATED_MS = null;

let FILTERED = [];
let visibleLimit = 0;
let manualExtra = 0;
let renderToken = 0;
let renderedCount = 0;
let activeToken = 0;

let STARS = new Set();
let JOURNAL_INDEX = new Map();

let sentinelEl = null;
let loadMoreWrap = null;
let loadMoreBtn = null;
let io = null;

// Advanced mode state (UI-only, OFF by default)
let advancedOn = false;

/* ---------- progressive controls ---------- */

function ensureProgressiveControls() {
  if (!sentinelEl) {
    sentinelEl = document.createElement("div");
    sentinelEl.style.height = "1px";
    sentinelEl.style.width = "100%";
    sentinelEl.style.marginTop = "8px";
    els.list.parentElement.appendChild(sentinelEl);
  }

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
      visibleLimit = Math.min(FILTERED.length, AUTO_SCROLL_LIMIT + manualExtra);
      renderVisible();
      updateLoadMoreVisibility();
    });

    loadMoreWrap.appendChild(loadMoreBtn);
    els.list.parentElement.appendChild(loadMoreWrap);
  }

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

/* ---------- render ---------- */

function appendTextSpan(parent, text, className) {
  const span = document.createElement("span");
  if (className) span.className = className;
  span.textContent = text;
  parent.appendChild(span);
}

function buildItemNode(it, stars) {
  const starOn = stars.has(it.key);

  const div = document.createElement("div");
  div.className = "item";

  const top = document.createElement("div");
  top.className = "top";

  const starBtn = document.createElement("button");
  starBtn.className = `star ${starOn ? "on" : ""}`;
  starBtn.type = "button";
  starBtn.setAttribute("aria-label", "star");
  starBtn.dataset.key = it.key;
  starBtn.textContent = starOn ? "★" : "☆";

  const infoWrap = document.createElement("div");

  const title = document.createElement("h3");
  title.className = "title";

  const link = document.createElement("a");
  link.href = it.url;
  link.target = "_blank";
  link.rel = "noopener noreferrer";
  link.textContent = it.title;
  title.appendChild(link);

  const metaLine = document.createElement("div");
  metaLine.className = "metaLine";

  appendTextSpan(metaLine, it.journal_short, "pill");

  // Category chip (PubMed metadata-derived at build time)
  const cat = it.category || "Unclassified";
  appendTextSpan(metaLine, cat, `pill cat ${it.category ? "" : "uncat"}`.trim());

  if (it.aop) appendTextSpan(metaLine, "Ahead of print", "pill aop");

  if (it.published) appendTextSpan(metaLine, formatDateDMYMMM(it.published));
  else appendTextSpan(metaLine, "no date", "muted");

  if (it.authors) appendTextSpan(metaLine, it.authors);
  if (it.doi) appendTextSpan(metaLine, it.doi, "muted");

  if (it.pubmed_url) {
    const pubmed = document.createElement("a");
    pubmed.className = "pill";
    pubmed.href = it.pubmed_url;
    pubmed.target = "_blank";
    pubmed.rel = "noopener noreferrer";
    pubmed.textContent = "PubMed";
    metaLine.appendChild(pubmed);
  }

  infoWrap.appendChild(title);
  infoWrap.appendChild(metaLine);

  top.appendChild(starBtn);
  top.appendChild(infoWrap);
  div.appendChild(top);

  return div;
}

function renderSlice(items, stars, startIndex, endIndex) {
  const fragment = document.createDocumentFragment();
  for (let i = startIndex; i < endIndex; i += 1) {
    fragment.appendChild(buildItemNode(items[i], stars));
  }
  els.list.appendChild(fragment);
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

  loadMoreWrap.style.display = (canManual && isAutoDone && visibleLimit < FILTERED.length) ? "block" : "none";
  updateStatus();
}

function renderVisible() {
  const maxVisible = Math.min(visibleLimit, FILTERED.length);

  if (!FILTERED.length) {
    els.list.innerHTML = `<div class="muted">No results.</div>`;
    renderedCount = 0;
    updateLoadMoreVisibility();
    return;
  }

  if (activeToken !== renderToken) {
    activeToken = renderToken;
    renderedCount = 0;
    els.list.innerHTML = "";
  }

  if (renderedCount < maxVisible) {
    renderSlice(FILTERED, STARS, renderedCount, maxVisible);
    renderedCount = maxVisible;
  }

  updateLoadMoreVisibility();
}

/* ---------- filtering ---------- */

function computeFiltered() {
  // Journal selection is always allowed (it is NOT part of Advanced)
  const j = els.journal.value;
  const baseItems = j ? (JOURNAL_INDEX.get(j) || []) : DATA.items;

  // When Advanced is OFF: no advanced filters at all (search/type/starred)
  if (!advancedOn) return baseItems;

  const q = els.q.value.trim().toLowerCase();
  const selectedType = els.type.value || "";
  const starredOnly = !!els.starOnly.checked;

  let items = baseItems;

  if (q) items = items.filter(it => matchesQuery(it, q));
  if (selectedType) items = items.filter(it => (it.category || "") === selectedType);
  if (starredOnly) items = items.filter(it => STARS.has(it.key));

  return items;
}

function applyFilters(resetLimits = true) {
  FILTERED = computeFiltered();
  renderToken += 1;

  if (resetLimits) {
    manualExtra = 0;
    visibleLimit = Math.min(RENDER_BATCH, FILTERED.length);
  } else {
    visibleLimit = Math.min(visibleLimit, FILTERED.length);
    if (FILTERED.length > 0 && visibleLimit === 0) visibleLimit = Math.min(RENDER_BATCH, FILTERED.length);
  }

  renderVisible();
}

/* ---------- advanced UI (curtain + disabled controls) ---------- */

function clearAdvancedFilters() {
  els.q.value = "";
  els.type.value = "";
  els.starOnly.checked = false;
}

function setAdvancedUI(on) {
  advancedOn = !!on;

  els.advToggle.checked = advancedOn;
  els.advToggle.setAttribute("aria-expanded", String(advancedOn));

  // Curtain animation via CSS class
  els.advancedControls.classList.toggle("open", advancedOn);
  els.advancedControls.setAttribute("aria-hidden", String(!advancedOn));

  // Not selectable when hidden
  els.q.disabled = !advancedOn;
  els.type.disabled = !advancedOn;
  els.starOnly.disabled = !advancedOn;
}

/* ---------- init ---------- */

async function init() {
  els.status.textContent = "Loading data…";

  const res = await fetch("./data.json", { cache: "no-store" });
  DATA = await res.json();
  GENERATED_MS = DATA.generated_at ? Date.parse(DATA.generated_at) : null;

  // Precompute searchText + stable key
  DATA.items.forEach((it) => {
    const pts = Array.isArray(it.pubmed_publication_types) ? it.pubmed_publication_types.join(" ") : "";
    const hay = `${it.title} ${it.authors} ${it.journal} ${it.doi} ${it.category || ""} ${pts}`.toLowerCase();
    it.searchText = hay;
    it.key = it.doi || it.url || `${it.journal_short}|${it.title}`;
  });

  STARS = loadStars();

  // Journal index (journal_short)
  JOURNAL_INDEX = new Map();
  DATA.items.forEach((it) => {
    if (!JOURNAL_INDEX.has(it.journal_short)) JOURNAL_INDEX.set(it.journal_short, []);
    JOURNAL_INDEX.get(it.journal_short).push(it);
  });

  // Load sources + metrics
  const srcRes = await fetch("./sources.json", { cache: "no-store" });
  SOURCES = await srcRes.json();

  try {
    const mRes = await fetch("./journal_metrics.json", { cache: "no-store" });
    METRICS = await mRes.json();
  } catch {
    METRICS = { sjr_year: null, by_issn: {} };
  }

  const sjrYear = METRICS.sjr_year ?? "—";
  const byIssn = METRICS.by_issn || {};

  const options = SOURCES.map(s => {
    const short = s.short || s.name;
    const name = s.name;
    const issn = normalizeIssn(s.issn);
    const metric = byIssn[issn];
    const sjrVal = metric && Number.isFinite(metric.sjr) ? Number(metric.sjr) : null;
    return { short, name, sjrVal, sjrText: formatSjrLabel(sjrYear, sjrVal) };
  }).sort((a, b) => {
    const aHas = a.sjrVal != null;
    const bHas = b.sjrVal != null;
    if (aHas && bHas && b.sjrVal !== a.sjrVal) return b.sjrVal - a.sjrVal;
    if (aHas !== bHas) return aHas ? -1 : 1;
    return a.short.localeCompare(b.short);
  });

  for (const o of options) {
    const opt = document.createElement("option");
    opt.value = o.short;
    opt.textContent = `${o.short} — ${o.name} — ${o.sjrText}`;
    els.journal.appendChild(opt);
  }

  // Populate type selector from data.json meta if present
  const cats = (DATA.meta && Array.isArray(DATA.meta.categories) && DATA.meta.categories.length)
    ? DATA.meta.categories
    : DASHBOARD_CATEGORIES;

  while (els.type.options.length > 1) els.type.remove(1);
  for (const c of cats) {
    const opt = document.createElement("option");
    opt.value = c;
    opt.textContent = c;
    els.type.appendChild(opt);
  }

  // Advanced OFF by default: hidden + disabled + cleared
  clearAdvancedFilters();
  setAdvancedUI(false);

  // Header timestamps
  function updateHeader() {
    if (!DATA.generated_at || !GENERATED_MS) {
      els.generatedAt.textContent = "";
      return;
    }
    els.generatedAt.textContent =
      `Updated: ${formatDateTimeDMYMMM(DATA.generated_at)} UTC · ${relativeFromMs(GENERATED_MS)}`;
  }
  updateHeader();
  setInterval(updateHeader, 30_000);

  // Debounced search (only when advanced is on)
  const debouncedFilter = debounce(() => applyFilters(true), 150);

  // Journal always applies
  els.journal.addEventListener("change", () => applyFilters(true));

  // Advanced controls apply only when advanced is on
  els.q.addEventListener("input", () => { if (advancedOn) debouncedFilter(); });
  els.type.addEventListener("change", () => { if (advancedOn) applyFilters(true); });
  els.starOnly.addEventListener("change", () => { if (advancedOn) applyFilters(true); });

  // Toggle behavior:
  // ON  -> show curtain, enable controls, DO NOT filter automatically
  // OFF -> clear advanced filters, hide curtain, disable controls, show everything
  els.advToggle.addEventListener("change", () => {
    const on = !!els.advToggle.checked;
    if (on) {
      setAdvancedUI(true);
      return; // no filtering on toggle-on
    }
    clearAdvancedFilters();
    setAdvancedUI(false);
    applyFilters(true);
  });

  // Star click handling
  els.list.addEventListener("click", (event) => {
    const target = event.target;
    if (!(target instanceof Element)) return;
    const starBtn = target.closest("button.star");
    if (!starBtn || !els.list.contains(starBtn)) return;

    const key = starBtn.dataset.key;
    if (!key) return;

    if (STARS.has(key)) STARS.delete(key);
    else STARS.add(key);

    saveStars(STARS);

    // If starred-only is active, refresh the filtered list
    if (advancedOn && els.starOnly.checked) applyFilters(true);
    else applyFilters(false);
  });

  els.clearStars.addEventListener("click", () => {
    STARS.clear();
    localStorage.removeItem(STAR_KEY);
    applyFilters(true);
  });

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
