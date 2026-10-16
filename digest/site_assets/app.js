"use strict";
const $ = (s) => document.querySelector(s), main = $("#main");
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
let DATA, byDate = {}, shards = {};
const PAGE = 100;

async function shard(month) {
  if (!shards[month]) shards[month] = fetch(`data/${month}.json`).then((r) => r.json());
  return shards[month];
}
const cls = (s) => (s >= 9 ? "hi" : s >= 7 ? "mid" : "");

function card(row, open) {
  const [id, date, title, score, interest, tags, authors, watch] = row;
  const au = authors.join(", ") + (authors.length >= 6 ? " et al." : "");
  return `<article class="p" data-id="${id}" data-month="${date.slice(0, 7)}">
    <h3><span class="sc ${cls(score)}">${+score.toFixed(1)}</span>${watch ? "⭐ " : ""}<a href="https://arxiv.org/abs/${id}" target="_blank" rel="noopener">${esc(title)}</a></h3>
    <div class="meta">${esc(au)} · <a href="#/day/${date}">${date}</a></div>
    <div>${tags.map((t) => `<span class="tag" data-tag="${esc(t)}">${esc(t)}</span>`).join("")}</div>
    <div class="det"></div></article>`;
}

async function fillDetails(root) {
  for (const el of root.querySelectorAll(".p")) {
    const d = (await shard(el.dataset.month))[el.dataset.id];
    if (!d) continue;
    const badges = [d.ah.length ? "👤 " + d.ah.join(", ") : "", d.ih.length ? "🏛 " + d.ih.slice(0, 3).join(", ") : "",
      d.code ? `<a href="${esc(d.code)}" target="_blank" rel="noopener">💻 code</a>` : ""].filter(Boolean).join(" · ");
    el.querySelector(".det").innerHTML =
      (d.tldr ? `<p><b>TL;DR</b> ${esc(d.tldr)}</p>` : "") + (d.why ? `<p><b>Why you</b> ${esc(d.why)}</p>` : "") +
      (badges ? `<p class="meta">${badges}</p>` : "") +
      `<details><summary class="meta">abstract</summary><p>${esc(d.abs)}</p></details>`;
  }
}

function list(rows, limit) {
  const shown = rows.slice(0, limit);
  main.querySelector("#list").innerHTML = shown.map((r) => card(r)).join("") || "<p class=meta>Nothing found.</p>";
  const more = main.querySelector("#more");
  more.hidden = rows.length <= limit;
  more.textContent = `Show more (${rows.length - limit} left)`;
  more.onclick = () => list(rows, limit + PAGE);
  if (shown.length <= PAGE * 2) fillDetails(main.querySelector("#list"));
}

function view() {
  const q = $("#q").value.trim().toLowerCase(), min = +$("#min").value;
  const hash = location.hash.replace(/^#/, "") || "/";
  const dates = Object.keys(byDate).sort().reverse();
  const m = hash.match(/^\/day\/(\d{4}-\d{2}-\d{2})/);
  const date = m ? m[1] : dates[0];
  if (q || min) {
    const terms = q.split(/\s+/).filter(Boolean);
    const rows = DATA.papers.filter((r) => r[3] >= min && terms.every((t) =>
      (r[2] + " " + r[5].join(" ") + " " + r[6].join(" ") + " " + r[0]).toLowerCase().includes(t)))
      .sort((a, b) => (a[1] < b[1] ? 1 : a[1] > b[1] ? -1 : b[3] - a[3]));
    main.innerHTML = `<h2>${rows.length} result${rows.length === 1 ? "" : "s"}</h2><div id="list"></div><button id="more" class="more"></button>`;
    return list(rows, PAGE);
  }
  if (!date) { main.innerHTML = "<p>No papers yet.</p>"; return; }
  const i = dates.indexOf(date), rows = (byDate[date] || []).slice().sort((a, b) => b[3] - a[3]);
  const total = DATA.days[date]?.total;
  main.innerHTML = `<div class="nav"><span>${dates[i + 1] ? `<a href="#/day/${dates[i + 1]}">← ${dates[i + 1]}</a>` : ""}</span>
    <span>${dates[i - 1] ? `<a href="#/day/${dates[i - 1]}">${dates[i - 1]} →</a>` : ""}</span></div>
    <h2>${date} <span class="meta">· ${rows.length} papers${total ? ` of ${total} scanned` : ""}</span></h2>
    <div id="list"></div><button id="more" class="more"></button>`;
  list(rows, PAGE);
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
  view();
}).catch((e) => { main.textContent = "Failed to load data: " + e; });
