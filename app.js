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
  try { return new Set(JSON.parse(localStorage.getItem(STAR_KEY) || "[]")); }
  catch { return new Set(); }
}

function saveStars(stars) {
  localStorage.setItem(STAR_KEY, JSON.stringify([...stars]));
}

function matchesQuery(item, q) {
  if (!q) return true;
  const hay = `${item.title} ${item.authors} ${item.journal} ${item.doi}`.toLowerCase();
  return hay.includes(q.toLowerCase());
}

function formatDateDMYMMM(iso) {
  if (!iso) return "";
  const [y, m, d] = iso.split("-");
  const mi = parseInt(m, 10) - 1;
  if (mi < 0 || mi > 11) return iso;
  return `${d}/${MONTHS[mi]}/${y}`;
}

function formatDateTimeDMYMMM(iso) {
  if (!iso) return "";
  const [date, time] = iso.split("T");
  return `${formatDateDMYMMM(date)} ${time.slice(0,5)}`;
}

function relativeFromMs(pastMs) {
  const diff = Date.now() - pastMs;
  const min = Math.floor(diff / 60000);
  if (min < 1) return "just now";
  if (min < 60) return `${min} min ago`;
  const hr = Math.floor(min / 60);
  if (hr < 24) return `${hr} h ago`;
  const d = Math.floor(hr / 24);
  return `${d} d ago`;
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
        <button class="star ${starOn ? "on" : ""}">${starOn ? "★" : "☆"}</button>
        <div>
          <h3 class="title">
            <a href="${it.url}" target="_blank">${it.title}</a>
          </h3>
          <div class="metaLine">
            <span class="pill">${it.journal_short}</span>
            ${it.tier ? `<span class="pill">T${it.tier}</span>` : ""}
            ${it.aop ? `<span class="pill aop">Ahead of print</span>` : ""}
            ${it.published ? `<span>${formatDateDMYMMM(it.published)}</span>` : ""}
            ${it.authors ? `<span>${it.authors}</span>` : ""}
            ${it.pubmed_url ? `<a class="pill" href="${it.pubmed_url}" target="_blank">PubMed</a>` : ""}
          </div>
        </div>
      </div>
    `;

    div.querySelector(".star").onclick = () => {
      const s = loadStars();
      s.has(key) ? s.delete(key) : s.add(key);
      saveStars(s);
      applyFilters();
    };

    els.list.appendChild(div);
  }
}

/* ---------- filtering & init ---------- */

let DATA = { generated_at: null, items: [] };
let GENERATED_MS = null;

function applyFilters() {
  const stars = loadStars();
  const q = els.q.value.trim();
  const j = els.journal.value;

  let items = DATA.items.slice();
  if (j) items = items.filter(it => it.journal_short === j);
  items = items.filter(it => matchesQuery(it, q));
  if (els.starOnly.checked) {
    items = items.filter(it => stars.has(it.doi || it.url));
  }

  els.status.textContent = `${items.length} articles shown`;
  render(items, stars);
}

async function init() {
  const res = await fetch("./data.json", { cache: "no-store" });
  DATA = await res.json();
  GENERATED_MS = Date.parse(DATA.generated_at);

  const src = await fetch("./sources.json", { cache: "no-store" });
  const SOURCES = await src.json();

  const counts = {};
  DATA.items.forEach(it => counts[it.journal_short] = (counts[it.journal_short] || 0) + 1);

  SOURCES.forEach(s => {
    const opt = document.createElement("option");
    opt.value = s.short;
    opt.textContent = `T${s.tier} · ${s.short} — ${s.name} (${counts[s.short] || 0})`;
    if (!counts[s.short]) opt.disabled = true;
    els.journal.appendChild(opt);
  });

  function updateHeader() {
    els.generatedAt.textContent =
      `Updated: ${formatDateTimeDMYMMM(DATA.generated_at)} UTC · ${relativeFromMs(GENERATED_MS)}`;
  }

  els.q.oninput = applyFilters;
  els.journal.onchange = applyFilters;
  els.starOnly.onchange = applyFilters;
  els.clearStars.onclick = () => { localStorage.removeItem(STAR_KEY); applyFilters(); };

  updateHeader();
  setInterval(updateHeader, 30_000);
  applyFilters();
}

init();
