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

/* ---------- helpers ---------- */

function loadStars() {
  try {
    return new Set(JSON.parse(localStorage.getItem(STAR_KEY) || "[]"));
  } catch {
    return new Set();
  }
}

function saveStars(stars) {
  localStorage.setItem(STAR_KEY, JSON.stringify([...stars]));
}

function matchesQuery(item, q) {
  if (!q) return true;
  const hay = `${item.title} ${item.authors} ${item.journal} ${item.doi}`.toLowerCase();
  return hay.includes(q.toLowerCase());
}

/* Force DD/MM/YYYY from YYYY-MM-DD */
function formatDateDMY(iso) {
  if (!iso) return "";
  const [y, m, d] = iso.split("-");
  if (!y || !m || !d) return iso;
  return `${d}/${m}/${y}`;
}

/* Force DD/MM/YYYY HH:MM from ISO timestamp */
function formatDateTimeDMY(iso) {
  if (!iso) return "";
  const [date, time] = iso.split("T");
  if (!date || !time) return iso;

  const [y, m, d] = date.split("-");
  const hhmm = time.slice(0, 5); // HH:MM
  return `${d}/${m}/${y} ${hhmm}`;
}

/* Parse an ISO string like 2026-01-05T08:00:00+00:00 safely */
function parseIsoToMs(iso) {
  // Date.parse handles ISO with timezone reliably.
  const ms = Date.parse(iso);
  return Number.isFinite(ms) ? ms : null;
}

function formatRelativeFromMs(pastMs, nowMs) {
  if (pastMs == null) return "";
  let diff = Math.max(0, nowMs - pastMs);

  const sec = Math.floor(diff / 1000);
  if (sec < 10) return "just now";
  if (sec < 60) return `${sec} sec ago`;

  const min = Math.floor(sec / 60);
  if (min < 60) return `${min} min ago`;

  const hr = Math.floor(min / 60);
  if (hr < 24) return `${hr} h ago`;

  const day = Math.floor(hr / 24);
  if (day < 7) return `${day} d ago`;

  const wk = Math.floor(day / 7);
  if (wk < 5) return `${wk} wk ago`;

  const mo = Math.floor(day / 30);
  if (mo < 12) return `${mo} mo ago`;

  const yr = Math.floor(day / 365);
  return `${yr} yr ago`;
}

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
            ${it.tier ? `<span class="pill">T${it.tier}</span>` : ""}
            ${it.published ? `<span>${formatDateDMY(it.published)}</span>` : `<span class="muted">no date</span>`}
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
let SOURCES = null;
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

function updateGeneratedAtLabel() {
  if (!DATA.generated_at || GENERATED_MS == null) {
    els.generatedAt.textContent = "";
    return;
  }
  const abs = formatDateTimeDMY(DATA.generated_at);
  const rel = formatRelativeFromMs(GENERATED_MS, Date.now());
  els.generatedAt.textContent = `Updated: ${abs} UTC · ${rel}`;
}

/* ---------- init ---------- */

async function init() {
  els.status.textContent = "Loading data…";

  const res = await fetch("./data.json", { cache: "no-store" });
  DATA = await res.json();
  GENERATED_MS = parseIsoToMs(DATA.generated_at);

  const srcRes = await fetch("./sources.json", { cache: "no-store" });
  SOURCES = await srcRes.json();

  const counts = new Map();
  for (const it of DATA.items) {
    counts.set(it.journal_short, (counts.get(it.journal_short) || 0) + 1);
  }

  const options = SOURCES
    .map(s => ({
      short: s.short || s.name,
      name: s.name,
      tier: s.tier ?? 0,
      count: counts.get(s.short || s.name) || 0,
    }))
    .sort((a, b) => (a.tier - b.tier) || a.short.localeCompare(b.short));

  for (const o of options) {
    const opt = document.createElement("option");
    opt.value = o.short;
    opt.textContent = `T${o.tier} · ${o.short} — ${o.name} (${o.count})`;
    if (o.count === 0) opt.disabled = true;
    els.journal.appendChild(opt);
  }

  // Wire events
  els.q.addEventListener("input", applyFilters);
  els.journal.addEventListener("change", applyFilters);
  els.starOnly.addEventListener("change", applyFilters);

  els.clearStars.addEventListener("click", () => {
    localStorage.removeItem(STAR_KEY);
    applyFilters();
  });

  // Initial render + relative-time updater
  updateGeneratedAtLabel();
  setInterval(updateGeneratedAtLabel, 30_000);

  applyFilters();
}

init().catch(err => {
  console.error(err);
  els.status.textContent = "Failed to load data.json";
  els.list.innerHTML = `<div class="muted">Error loading data.</div>`;
});
