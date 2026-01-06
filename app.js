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

/* YYYY-MM-DD -> DD/MMM/YYYY */
function formatDateDMYMMM(iso) {
  if (!iso) return "";
  const parts = iso.split("-");
  if (parts.length !== 3) return iso;

  const [y, m, d] = parts;
  const mi = parseInt(m, 10) - 1;
  if (mi < 0 || mi > 11) return iso;

  return `${d}/${MONTHS[mi]}/${y}`;
}

/* YYYY-MM-DDTHH:MM:SS -> DD/MMM/YYYY HH:MM */
function formatDateTimeDMYMMM(iso) {
  if (!iso) return "";
  const [date, time] = iso.split("T");
  if (!date || !time) return iso;

  const formattedDate = formatDateDMYMMM(date);
  const hhmm = time.slice(0, 5);

  return `${formattedDate} ${hhmm}`;
}

/* Relative time */
function relativeFromMs(pastMs) {
  if (!pastMs) return "";
  const diff = Math.max(0, Date.now() - pastMs);

  const min = Math.floor(diff / 60000);
  if (min < 1) return "just now";
  if (min < 60) return `${min} min ago`;

  const hr = Math.floor(min / 60);
  if (hr < 24) return `${hr} h ago`;

  const d = Math.floor(hr / 24);
  if (d < 7) return `${d} d ago`;

  const w = Math.floor(d / 7);
  if (w < 5) return `${w} wk ago`;

  const mo = Math.floor(d / 30);
  if (mo < 12) return `${mo} mo ago`;

  const y = Math.floor(d / 365);
  return `${y} yr ago`;
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
            ${it.published ? `<span>${formatDateDMYMMM(it.published)}</span>` : `<span class="muted">no date</span>`}
            ${it.authors ? `<span>${it.authors}</span>` : ""}
            ${it.doi ? `<span class="muted">${it.doi}</span>` : ""}
            ${it.pubmed_url ? `<a class="pill" href="${it.pubmed_url}" target="_blank">PubMed</a>` : ""}
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

/* ---------- init ---------- */

async function init() {
  els.status.textContent = "Loading data…";

  const res = await fetch("./data.json", { cache: "no-store" });
  DATA = await res.json();
  GENERATED_MS = DATA.generated_at ? Date.parse(DATA.generated_at) : null;

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

  function updateGeneratedAt() {
    if (!DATA.generated_at || !GENERATED_MS) return;
    const abs = formatDateTimeDMYMMM(DATA.generated_at);
    const rel = relativeFromMs(GENERATED_MS);
    els.generatedAt.textContent = `Updated: ${abs} UTC · ${rel}`;
  }

  els.q.addEventListener("input", applyFilters);
  els.journal.addEventListener("change", applyFilters);
  els.starOnly.addEventListener("change", applyFilters);

  els.clearStars.addEventListener("click", () => {
    localStorage.removeItem(STAR_KEY);
    applyFilters();
  });

  updateGeneratedAt();
  setInterval(updateGeneratedAt, 30_000);

  applyFilters();
}

init().catch(err => {
  console.error(err);
  els.status.textContent = "Failed to load data.json";
  els.list.innerHTML = `<div class="muted">Error loading data.</div>`;
});
