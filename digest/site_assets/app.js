"use strict";
const $ = (s) => document.querySelector(s), main = $("#main");
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
const PAGE = 50;
let DATA, byDate = {}, dates = [], shards = {};

const shard = (m) => (shards[m] ||= fetch(`data/${m}.json`).then((r) => r.json()).catch(() => ({})));
const cls = (s) => (s >= 8 ? "hi" : s >= 6 ? "mid" : "");
const fmtDate = (d) => new Date(d + "T00:00:00").toLocaleDateString(undefined, {weekday: "long", year: "numeric", month: "long", day: "numeric"});

function authors(list, more) {
  const n = 5, head = list.slice(0, n).map(esc).join(", ");
  return head + (list.length > n ? ` <span title="${esc(list.slice(n).join(", "))}">+${list.length - n} more</span>` : "");
}

function card(row, d, showDate) {
  const [id, date, title, score, , tags, , watch] = row;
  d = d || {};
  const badges = [
    d.ah?.length ? `<span class="badge">👤 ${d.ah.map(esc).join(", ")}</span>` : "",
    d.ih?.length ? `<span class="badge">🏛 ${d.ih.slice(0, 3).map(esc).join(", ")}</span>` : "",
    d.code ? `<a class="badge code" href="${esc(d.code)}" target="_blank" rel="noopener">code ↗</a>` : "",
  ].join("");
  return `<article class="p">
    <div class="top"><div class="sc ${cls(score)}" title="score">${score.toFixed(1)}</div><div>
      <h3><a href="https://arxiv.org/abs/${id}" target="_blank" rel="noopener">${esc(title)}</a></h3>
      <div class="meta">${authors(d.au || row[6])}</div>
      <div class="meta">arXiv:${id}${d.cat ? " · " + d.cat.slice(0, 3).map(esc).join(", ") : ""}${showDate ? ` · <a href="#/day/${date}">${date}</a>` : ""}</div>
    </div></div>
    <div class="body">
      ${d.tldr ? `<p class="tldr">${esc(d.tldr)}</p>` : ""}
      ${d.why ? `<p class="why"><b>Why you</b>${esc(d.why)}</p>` : ""}
      <div class="chips">${badges}${tags.map((t) => `<button class="tag" data-tag="${esc(t)}">${esc(t)}</button>`).join("")}</div>
      ${d.abs ? `<details><summary>Abstract</summary><p>${esc(d.abs)}</p></details>` : ""}
    </div></article>`;
}

async function render(rows, limit, showDate, split) {
  const shown = rows.slice(0, limit);
  const months = [...new Set(shown.map((r) => r[1].slice(0, 7)))];
  await Promise.all(months.map(shard));
  let html = "", last = null;
  for (const r of shown) {
    if (split) {
      const g = r[3] >= 7 ? "Top picks" : "Also scored";
      if (g !== last) html += `<div class="sect">${g}</div>`;
      last = g;
    }
    html += card(r, (await shard(r[1].slice(0, 7)))[r[0]], showDate);
  }
  $("#list").innerHTML = html || '<p class="empty">Nothing found.</p>';
  const more = $("#more");
  more.hidden = rows.length <= limit;
  more.textContent = `Show more (${rows.length - limit} left)`;
  more.onclick = () => render(rows, limit + PAGE, showDate, split);
}

function view() {
  const q = $("#q").value.trim().toLowerCase(), min = +$("#min").value;
  if (q || min) {
    const terms = q.split(/\s+/).filter(Boolean);
    const rows = DATA.papers.filter((r) => r[3] >= min && terms.every((t) =>
      (r[2] + " " + r[5].join(" ") + " " + r[6].join(" ") + " " + r[0]).toLowerCase().includes(t)))
      .sort((a, b) => (a[1] < b[1] ? 1 : a[1] > b[1] ? -1 : b[3] - a[3]));
    main.innerHTML = `<div class="dayhead"><h1>${rows.length} result${rows.length === 1 ? "" : "s"}</h1>
      <a class="btn" href="#/">Clear</a></div><div id="list"></div><button id="more" class="more" hidden></button>`;
    return render(rows, PAGE, true, false);
  }
  if (!dates.length) { main.innerHTML = '<p class="empty">No papers yet.</p>'; return; }
  const m = location.hash.match(/^#\/day\/(\d{4}-\d{2}-\d{2})/);
  const date = m && byDate[m[1]] ? m[1] : dates[0], i = dates.indexOf(date);
  const rows = byDate[date].slice().sort((a, b) => b[3] - a[3]);
  const total = DATA.days[date]?.total;
  main.innerHTML = `<div class="dayhead"><div><h1>${fmtDate(date)}</h1>
    <div class="sub">${rows.length} papers${total ? ` kept from ${total} scanned` : ""}</div></div>
    <div class="nav">${dates[i + 1] ? `<a class="btn" href="#/day/${dates[i + 1]}">← Older</a>` : ""}
    <select id="jump" aria-label="Jump to day">${dates.map((d) => `<option ${d === date ? "selected" : ""}>${d}</option>`).join("")}</select>
    ${dates[i - 1] ? `<a class="btn" href="#/day/${dates[i - 1]}">Newer →</a>` : ""}</div></div>
    <div id="list"></div><button id="more" class="more" hidden></button>`;
  $("#jump").onchange = (e) => (location.hash = `#/day/${e.target.value}`);
  render(rows, PAGE, false, true);
}

main.addEventListener("click", (e) => {
  const t = e.target.closest(".tag");
  if (t) { $("#q").value = t.dataset.tag; view(); window.scrollTo(0, 0); }
});
$("#q").addEventListener("input", () => { clearTimeout(view.t); view.t = setTimeout(view, 150); });
$("#min").addEventListener("change", view);
window.addEventListener("hashchange", () => { $("#q").value = ""; $("#min").value = "0"; view(); window.scrollTo(0, 0); });

fetch("data/index.json").then((r) => r.json()).then((d) => {
  DATA = d;
  for (const r of d.papers) (byDate[r[1]] ||= []).push(r);
  dates = Object.keys(byDate).sort().reverse();
  view();
}).catch((e) => { main.textContent = "Failed to load data: " + e; });
