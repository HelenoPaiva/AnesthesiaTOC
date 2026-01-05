const els = {
  q: document.getElementById("q"),
  journal: document.getElementById("journal"),
  days: document.getElementById("days"),
  starOnly: document.getElementById("starOnly"),
  list: document.getElementById("list"),
  status: document.getElementById("status"),
  generatedAt: document.getElementById("generatedAt"),
  clearStars: document.getElementById("clearStars"),
};

const STAR_KEY = "anes_toc_starred_v1";

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

function daysAgoISO(n) {
  const d = new Date();
  d.setDate(d.getDate() - n);
  return d.toISOString().slice(0, 10);
}

function norm(s) {
  return (s || "").toLowerCase();
}

function matchesQuery(item, q) {
  if (!q) return true;
  const hay = `${item.title} ${item.authors} ${item.journal} ${item.doi}`.toLowerCase();
  return hay.includes(q.toLowerCase());
}

function render(items, stars) {
  els.list.innerHTML = "";
  if (!items.length) {
    els.list.innerHTML = `<div class="muted">No results.</div>`;
    return;
  }

  for (const it of items) {
    const starOn = stars.has(it.doi || it.url || `${it.journal_short}|${it.title}`);
    const key = it.doi || it.url || `${it.journal_short}|${it.title}`;

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
            ${it.published ? `<span>${it.published}</span>` : `<span class="muted">no date</span>`}
            ${it.authors ? `<span>${it.authors}</span>` : ""}
            ${it.doi ? `<span class="muted">${it.doi}</span>` : ""}
          </div>
        </div>
      </div>
    `;

    div.querySelector(".star").addEventListener("click", () => {
      if (stars.has(key)) stars.delete(key);
      else stars.add(key);
      saveStars(stars);
      applyFilters();
    });

    els.list.appendChild(div);
  }
}

let DATA = { generated_at: null, items: [] };

function applyFilters() {
  const stars = loadStars();
  const q = els.q.value.trim();
  const j = els.journal.value;
  const days = els.days.value ? parseInt(els.days.value, 10) : null;
  const cutoff = days ? daysAgoISO(days) : null;

  let items = DATA.items.slice();

  if (j) items = items.filter(it => it.journal_short === j);
  if (cutoff) items = items.filter(it => (it.published || "0000-00-00") >= cutoff);
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

async function init() {
  els.status.textContent = "Loading data…";
  const res = await fetch("./data.json", { cache: "no-store" });
  DATA = await res.json();

  // Populate journal dropdown
  const jset = new Map();
  for (const it of DATA.items) jset.set(it.journal_short, it.journal);
  const options = [...jset.entries()].sort((a,b) => a[0].localeCompare(b[0]));
  for (const [short, name] of options) {
    const opt = document.createElement("option");
    opt.value = short;
    opt.textContent = `${short} — ${name}`;
    els.journal.appendChild(opt);
  }

  els.generatedAt.textContent = DATA.generated_at ? `Updated: ${DATA.generated_at.replace("T"," ").replace("+00:00"," UTC")}` : "";

  // Wire events
  ["input", "change"].forEach(evt => {
    els.q.addEventListener(evt, applyFilters);
  });
  els.journal.addEventListener("change", applyFilters);
  els.days.addEventListener("change", applyFilters);
  els.starOnly.addEventListener("change", applyFilters);

  els.clearStars.addEventListener("click", () => {
    localStorage.removeItem(STAR_KEY);
    applyFilters();
  });

  applyFilters();
}

init().catch(err => {
  console.error(err);
  els.status.textContent = "Failed to load data.json";
  els.list.innerHTML = `<div class="muted">Error loading data. Check console.</div>`;
});
