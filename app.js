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
  // We keep year internally, but do not display it
  if (sjrVal == null || !Number.isFinite(sjrVal)) {
    return `SJR: n/a`;
  }
  return `SJR: ${sjrVal.toFixed(2)}`;
}

const SJR_TOOLTIP =
  "SCImago Journal Rank (SJR). Uses the latest year available in the dataset at update time.";

/* ---------- render ---------- */

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
      applyFilters();
    });

    els.list.appendChild(div);
  }
}

/* ---------- filtering ---------- */

let DATA = { generated_at: null, items: [] };
let SOURCES = [];
let METRICS = { sjr_year: null, by_issn: {} };
let GENERATED_MS = null;

function applyFilters() {
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

  els.status.textContent = `${items.length} articles shown`;
  render(items, stars);
}

/* ---------- init ---------- */

async function init() {
  els.status.textContent = "Loading data…";

  const res = await fetch("./data.json", { cache: "no-store" });
  DATA = await res.json();
  GENERATED_MS = DATA.generated_at ? Date.parse(DATA.generated_at) : null;

  const srcRes = await fetch("./sources.json", { cache: "no-store" });
  SOURCES = await srcRes.json();

  // metrics is optional; site should still run without it
  try {
    const mRes = await fetch("./journal_metrics.json", { cache: "no-store" });
    METRICS = await mRes.json();
  } catch (e) {
    console.warn("journal_metrics.json not available yet:", e);
    METRICS = { sjr_year: null, by_issn: {} };
  }

  // counts per journal_short
  const counts = new Map();
  for (const it of DATA.items) {
    counts.set(it.journal_short, (counts.get(it.journal_short) || 0) + 1);
  }

  const sjrYear = METRICS.sjr_year ?? "—";
  const byIssn = METRICS.by_issn || {};

  // Build dropdown data
  const options = SOURCES.map(s => {
    const short = s.short || s.name;
    const name = s.name;
    const issn = normalizeIssn(s.issn);

    const metric = byIssn[issn];
    const sjrVal = metric && Number.isFinite(metric.sjr) ? Number(metric.sjr) : null;

    return {
      short,
      name,
      issn,
      sjrVal,
      count: counts.get(short) || 0,
    };
  }).sort((a, b) => {
    // Sort: SJR desc, then missing SJR bottom, then short alphabetical
    const aHas = a.sjrVal != null;
    const bHas = b.sjrVal != null;
    if (aHas && bHas) {
      if (b.sjrVal !== a.sjrVal) return b.sjrVal - a.sjrVal;
    } else if (aHas !== bHas) {
      return aHas ? -1 : 1; // has SJR first
    }
    return a.short.localeCompare(b.short);
  });

  // Populate dropdown
  for (const o of options) {
    const opt = document.createElement("option");
    opt.value = o.short;

    const sjrText = formatSjrLabel(sjrYear, o.sjrVal);
    opt.textContent = `${o.short} — ${o.name} — ${sjrText} (${o.count})`;

    // Tooltip (browser support varies for <option>, but this is the simplest no-HTML-change approach)
    opt.title = SJR_TOOLTIP;

    // Per your decision: leave selectable even if (0)
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

  els.q.addEventListener("input", applyFilters);
  els.journal.addEventListener("change", applyFilters);
  els.starOnly.addEventListener("change", applyFilters);

  els.clearStars.addEventListener("click", () => {
    localStorage.removeItem(STAR_KEY);
    applyFilters();
  });

  updateHeader();
  setInterval(updateHeader, 30_000);

  applyFilters();
}

init().catch(err => {
  console.error(err);
  els.status.textContent = "Failed to load data.json";
  els.list.innerHTML = `<div class="muted">Error loading data.</div>`;
});
