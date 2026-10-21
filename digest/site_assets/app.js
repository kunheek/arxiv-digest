"use strict";
const $ = (s) => document.querySelector(s);
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
const PAGE = 80;
// index row: 0 id, 1 date, 2 title, 3 score, 4 tags, 5 authors, 6 watch, 7 code, 8 cats, 9 nAuthors, 10 quality, 11 impact
let DATA, byDate = {}, dates = [], shards = {};
let rows = [], shown = 0, cur = -1, freeTerms = [], renderToken = 0;

// Ratings and bookmarks are set only on the local server (`run.py --serve`, LOCAL) and saved to
// the database. The public GitHub Pages site is read-only: it shows the same picks, published
// from the database as data/prefs.json.
let ratings = {}, saved = new Set(), liked = new Set(), taste = {}, LOCAL = false;
const post = (path, body) => fetch(path, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) }).catch(() => {});
const store = { get: (k, d) => { try { return localStorage.getItem(k) ?? d; } catch { return d; } },
                set: (k, v) => { try { localStorage.setItem(k, v); } catch {} } };

// ── state lives in the URL hash so searches are shareable ──────────────
function readState() {
  const h = location.hash.replace(/^#\/?/, ""), p = new URLSearchParams(h.replace(/^\?/, ""));
  return { d: p.get("d") || "", q: p.get("q") || "", min: p.get("min") || "0", sort: p.get("sort") || "",
           w: p.get("w") === "1", c: p.get("c") === "1", a: p.get("a") === "1", s: p.get("s") === "1" };
}
function writeState(s, replace) {
  const p = new URLSearchParams();
  if (s.d) p.set("d", s.d); if (s.q) p.set("q", s.q); if (s.min !== "0") p.set("min", s.min);
  if (s.sort) p.set("sort", s.sort); if (s.w) p.set("w", "1"); if (s.c) p.set("c", "1"); if (s.a) p.set("a", "1"); if (s.s) p.set("s", "1");
  const url = "#/" + (p.toString() ? "?" + p : "");
  if (replace) history.replaceState(null, "", url); else location.hash = url;
}
const uiState = () => ({ d: $("#day").value, q: $("#q").value.trim(), min: $("#min").value,
  sort: $("#sort").dataset.user ? $("#sort").value : "", w: $("#w").checked, c: $("#c").checked, a: $("#a").checked, s: $("#s").checked });

// ── data ───────────────────────────────────────────────────────────────
const shard = (d) => (shards[d] ||= fetch(`data/d/${d}.json`).then((r) => r.json()).catch(() => ({})));
async function loadShards(ds) {            // small concurrency pool
  const q = [...ds];
  await Promise.all(Array.from({ length: 6 }, async () => { while (q.length) await shard(q.pop()); }));
}

// ── query language ─────────────────────────────────────────────────────
function parseQuery(q) {
  const toks = [];
  for (let t of q.match(/-?(?:\w+(?:[:<>=]+))?(?:"[^"]*"|\S+)/g) || []) {
    let neg = false;
    if (t[0] === "-" && t.length > 1) { neg = true; t = t.slice(1); }
    const unq = (v) => v.replace(/^"|"$/g, "").toLowerCase();
    let m = t.match(/^score(>=|<=|>|<|=|:)(?:>=|<=|>|<|=)?(\d+(?:\.\d+)?)$/i) || t.match(/^score:(>=|<=|>|<|=)(\d+(?:\.\d+)?)$/i);
    if (m) { toks.push({ k: "score", op: m[1] === ":" ? "=" : m[1], v: +m[2], neg }); continue; }
    m = t.match(/^(quality|impact|rating)(>=|<=|>|<|=|:)(?:>=|<=|>|<|=)?(\d+(?:\.\d+)?)$/i) || t.match(/^(quality|impact|rating):(>=|<=|>|<|=)(\d+(?:\.\d+)?)$/i);
    if (m) { toks.push({ k: m[1].toLowerCase(), op: m[2] === ":" ? "=" : m[2], v: +m[3], neg }); continue; }
    m = t.match(/^(au|author|tag|cat|id|date|has|is):(.+)$/i);
    if (m) { toks.push({ k: m[1].toLowerCase().replace("author", "au"), v: unq(m[2]), neg }); continue; }
    toks.push({ k: "free", v: unq(t), neg });
  }
  return toks;
}
const cmp = { ">=": (a, b) => a >= b, "<=": (a, b) => a <= b, ">": (a, b) => a > b, "<": (a, b) => a < b, "=": (a, b) => a === b };
function matchTok(t, r, full) {
  let ok;
  switch (t.k) {
    case "score": ok = cmp[t.op](r[3], t.v); break;
    case "quality": ok = cmp[t.op](r[10] ?? 5, t.v); break;
    case "impact": ok = cmp[t.op](r[11] ?? 0, t.v); break;
    case "rating": ok = cmp[t.op](ratings[r[0]] || 0, t.v); break;
    case "au": ok = r[5].some((a) => a.toLowerCase().includes(t.v)); break;
    case "tag": ok = r[4].some((a) => a.toLowerCase().includes(t.v)); break;
    case "cat": ok = r[8].some((a) => a.toLowerCase().startsWith(t.v)); break;
    case "id": ok = r[0].startsWith(t.v); break;
    case "date": ok = r[1].startsWith(t.v); break;
    case "has": ok = t.v === "code" ? !!r[7] : false; break;
    case "is": ok = t.v === "watch" ? !!r[6] : t.v === "top" ? r[3] >= 7
      : t.v === "landmark" ? (r[11] ?? 0) >= 7
      : t.v === "saved" ? saved.has(r[0]) : t.v === "liked" ? liked.has(r[0]) : t.v === "rated" ? !!ratings[r[0]] : t.v === "unrated" ? !ratings[r[0]] : false; break;
    default: {
      let hay = (r[2] + " " + r[4].join(" ") + " " + r[5].join(" ") + " " + r[0]).toLowerCase();
      if (full) hay += " " + (full.tldr + " " + full.why + " " + full.abs).toLowerCase();
      ok = hay.includes(t.v);
    }
  }
  return t.neg ? !ok : ok;
}

// ── filter / sort / facets ─────────────────────────────────────────────
async function compute(s) {
  const toks = parseQuery(s.q), min = +s.min;
  let cand = s.d ? byDate[s.d] || [] : DATA.papers;
  cand = cand.filter((r) => r[3] >= min && (!s.w || r[6]) && (!s.c || r[7]) && (!s.s || saved.has(r[0])));
  const free = toks.filter((t) => t.k === "free" && !t.neg);
  cand = cand.filter((r) => toks.filter((t) => t.k !== "free").every((t) => matchTok(t, r)));
  freeTerms = free.map((t) => t.v);
  const fts = toks.filter((t) => t.k === "free");
  if (fts.length) {
    if (s.a) {
      const need = [...new Set(cand.map((r) => r[1]))];
      $("#summary").textContent = `Searching abstracts in ${need.length} day file${need.length === 1 ? "" : "s"}…`;
      await loadShards(need);
      const full = await Promise.all(cand.map(async (r) => (await shard(r[1]))[r[0]]));
      cand = cand.filter((r, i) => fts.every((t) => matchTok(t, r, full[i] || { tldr: "", why: "", abs: "" })));
    } else cand = cand.filter((r) => fts.every((t) => matchTok(t, r)));
  }
  const sort = s.sort || (s.d ? "score" : "new");
  cand = cand.slice().sort(sort === "taste" ? (a, b) => (taste[b[0]] ?? -1) - (taste[a[0]] ?? -1) || b[3] - a[3]
    : sort === "score" ? (a, b) => b[3] - a[3] || (a[1] < b[1] ? 1 : -1)
    : sort === "old" ? (a, b) => (a[1] < b[1] ? -1 : a[1] > b[1] ? 1 : b[3] - a[3])
    : (a, b) => (a[1] < b[1] ? 1 : a[1] > b[1] ? -1 : b[3] - a[3]));
  return cand;
}

// ── rendering ──────────────────────────────────────────────────────────
const cls = (s) => (s >= 8 ? "hi" : s >= 6 ? "mid" : "");
function hl(text) {
  let h = esc(text);
  for (const t of freeTerms) if (t.length > 1) h = h.replace(new RegExp(esc(t).replace(/[.*+?^${}()|[\]\\]/g, "\\$&"), "gi"), (m) => `<mark>${m}</mark>`);
  return h;
}
function authorsShort(r, n) {
  const a = r[5], total = r[9];
  const head = a.slice(0, n).join(", ");
  return head + (total > n ? " et al." : "");
}
function rowHTML(r, i, showDate) {
  const [id, date, title, score, tags, , watch, code, cats] = r;
  return `<article class="r" data-i="${i}">
    <div class="sc ${cls(score)}" title="score">${score.toFixed(1)}</div>
    <div class="main">
      <a class="t" href="https://arxiv.org/abs/${id}" target="_blank" rel="noopener">${hl(title)}</a>
      <span class="ic">${watch ? '<span title="watched author / institution">⭐</span>' : ""}${code ? ' <span title="has code">💻</span>' : ""}${(r[11] ?? 0) >= 7 ? ' <span title="big / foundational paper (field-wide impact ' + r[11].toFixed(0) + ')">📌</span>' : ""}</span>
      <span class="rb">${ratings[id] ? "★" + ratings[id] : ""}</span><button class="lk${liked.has(id) ? " on" : ""}" title="like (l)">♥</button><button class="bm${saved.has(id) ? " on" : ""}" title="bookmark (b)">🔖</button>
      <span class="fa">${esc(authorsShort(r, 1))}</span>
      <div class="sub"><span title="estimated paper quality (0-10)">Q${(r[10] ?? 5).toFixed(0)}</span>${taste[id] != null ? ` · <span title="how close to the papers you liked (0-10, local embeddings)">♥${taste[id].toFixed(0)}</span>` : ""} · ${esc(authorsShort(r, 4))} · ${tags.map((t) => `<button class="tag" data-tag="${esc(t)}">${esc(t)}</button>`).join(" ")}</div>
      <div class="det"></div>
    </div>
    <div class="side">${showDate ? `<a href="#/?d=${date}" style="color:inherit">${date.slice(5)}</a> · ` : ""}${esc(cats[0] || "")}</div></article>`;
}
async function fillDet(el) {
  if (el.dataset.filled) return;
  el.dataset.filled = 1;
  const r = rows[+el.dataset.i], d = (await shard(r[1]))[r[0]] || {};
  const badges = [d.ah?.length ? `<span class="badge">👤 ${d.ah.map(esc).join(", ")}</span>` : "",
    d.ih?.length ? `<span class="badge">🏛 ${d.ih.slice(0, 3).map(esc).join(", ")}</span>` : "",
    d.code ? `<a class="badge code" href="${esc(d.code)}" target="_blank" rel="noopener">code ↗</a>` : ""].join("");
  el.querySelector(".det").innerHTML =
    (r[9] > 4 ? `<p class="au">${(d.au || r[5]).map(esc).join(", ")}</p>` : "") +
    (d.tldr ? `<p>${hl(d.tldr)}</p>` : d.reason ? `<p>${hl(d.reason)}</p>` : "") +
    (d.why ? `<p class="why"><b>Why you</b>${esc(d.why)}</p>` : "") +
    (badges ? `<p>${badges}</p>` : "") +
    `<p class="qn">Quality ${(r[10] ?? 5).toFixed(1)} · Impact ${(r[11] ?? 0).toFixed(1)}${d.qn ? " — " + esc(d.qn) : ""}</p>` +
    (d.abs ? `<p class="abs">${hl(d.abs)}</p>` : "") + `<div class="stars" data-id="${r[0]}">${starsHTML(r[0])}</div>` +
    (LOCAL ? `<p class="sim" data-id="${r[0]}"><button class="simbtn">Show similar papers</button></p>` : "");
}
const starsHTML = (id) => !LOCAL ? (ratings[id] ? "Your rating: " + "★".repeat(ratings[id]) : "") : "Your rating: " + [1, 2, 3, 4, 5].map((n) =>
  `<button class="star${(ratings[id] || 0) >= n ? " on" : ""}" data-n="${n}" title="${n} (key ${n})">★</button>`).join("");
function rate(id, n) {
  if (!LOCAL) return;
  if (!n || ratings[id] === n) delete ratings[id]; else ratings[id] = n;
  post("api/rating", { id, rating: ratings[id] || 0 });
  document.querySelectorAll(".r").forEach((el) => {
    const r = rows[+el.dataset.i]; if (!r || r[0] !== id) return;
    el.querySelector(".rb").textContent = ratings[id] ? "★" + ratings[id] : "";
    const st = el.querySelector(".stars"); if (st) st.innerHTML = starsHTML(id);
  });
}
function showSimilar(p) {
  p.textContent = "…";
  fetch("api/similar?id=" + encodeURIComponent(p.dataset.id)).then((r) => r.json()).then((l) => {
    p.innerHTML = l.length ? "<b>Similar</b><ul>" + l.map((x) => `<li><a href="#/?q=${encodeURIComponent("id:" + x.id)}">${esc(x.title || x.id)}</a> <span class="mute">${(x.date || "").slice(5)} · ${x.sim.toFixed(2)}</span></li>`).join("") + "</ul>"
      : "No vectors yet (run: python run.py --embed-backfill)";
  }).catch(() => { p.textContent = "unavailable"; });
}
function like(id) {
  if (!LOCAL) return;
  const on = !liked.has(id); on ? liked.add(id) : liked.delete(id);
  post("api/like", { id, on });
  document.querySelectorAll(".r").forEach((el) => { const r = rows[+el.dataset.i]; if (r && r[0] === id) el.querySelector(".lk").classList.toggle("on", on); });
}
function bookmark(id) {
  if (!LOCAL) return;
  const on = !saved.has(id); on ? saved.add(id) : saved.delete(id);
  post("api/bookmark", { id, on });
  document.querySelectorAll(".r").forEach((el) => { const r = rows[+el.dataset.i]; if (r && r[0] === id) el.querySelector(".bm").classList.toggle("on", on); });
}
const open = (el, on) => { el.classList.toggle("open", on ?? !el.classList.contains("open")); if (el.classList.contains("open")) fillDet(el); };

async function renderMore() {
  const batch = rows.slice(shown, shown + PAGE), showDate = !readState().d;
  const dens = document.body.className.match(/dens-(\w+)/)?.[1];
  const ds = [...new Set(batch.map((r) => r[1]))];
  if (dens === "full") await loadShards(ds);
  const split = !readState().sort || readState().sort === "score";
  let html = "", last = lastGroup;
  batch.forEach((r, k) => {
    if (split && readState().d) {
      const g = r[3] >= 7 ? "Top picks" : "Also scored";
      if (g !== last) { html += `<div class="sect">${g}</div>`; last = g; }
    }
    html += rowHTML(r, shown + k, showDate);
  });
  lastGroup = last;
  $("#list").insertAdjacentHTML("beforeend", html);
  shown += batch.length;
  if (dens === "full") $("#list").querySelectorAll(".r:not([data-filled])").forEach(fillDet);
}
let lastGroup = null;

async function view(keepScroll) {
  const my = ++renderToken, s = readState();
  if (!location.hash.replace(/^#\/?/, "") && dates.length) { s.d = dates[0]; writeState(s, true); }   // bare URL → latest day
  syncControls(s);
  const result = await compute(s);
  if (my !== renderToken) return;
  rows = result; shown = 0; cur = -1; lastGroup = null;
  $("#list").innerHTML = "";
  const total = s.d ? DATA.days[s.d]?.total : null;
  $("#summary").innerHTML = `<span><b>${rows.length}</b> paper${rows.length === 1 ? "" : "s"}${s.d ? ` on <b>${s.d}</b>${total ? ` (kept from ${total} scanned)` : ""}` : " across all days"}</span>
    <span>${s.q || s.min !== "0" || s.w || s.c || s.d ? '<a href="#/?d=${dates[0]}">reset</a>' : ""}</span>`;
  const counts = {};
  for (const r of rows) for (const t of r[4]) counts[t] = (counts[t] || 0) + 1;
  const top = Object.entries(counts).sort((a, b) => b[1] - a[1]).slice(0, 14);
  $("#facets").innerHTML = rows.length > 8 ? top.map(([t, n]) => `<button class="tag" data-tag="${esc(t)}">${esc(t)}<i>${n}</i></button>`).join("") : "";
  if (!rows.length) { $("#list").innerHTML = '<p class="empty">Nothing found. Try fewer filters.</p>'; return; }
  await renderMore();
  if (!keepScroll) window.scrollTo(0, 0);
}

function syncControls(s) {
  const ds = ["", ...dates];
  const day = $("#day");
  if (!day.dataset.built) { day.innerHTML = ds.map((d) => `<option value="${d}">${d || "all days"}</option>`).join(""); day.dataset.built = 1; }
  day.value = s.d; $("#min").value = s.min; $("#q").value !== s.q && document.activeElement !== $("#q") && ($("#q").value = s.q);
  $("#sort").value = s.sort || (s.d ? "score" : "new"); $("#w").checked = s.w; $("#c").checked = s.c; $("#a").checked = s.a; $("#s").checked = s.s;
}
function setDensity(v) {
  document.body.className = "dens-" + v; store.set("dens", v);
  document.querySelectorAll("#dens button").forEach((b) => b.classList.toggle("on", b.dataset.v === v));
}

// ── events ─────────────────────────────────────────────────────────────
const push = (replace) => { writeState(uiState(), replace); if (replace) view(true); };
["day", "min", "w", "c", "a", "s"].forEach((id) => $("#" + id).addEventListener("change", () => push(false)));
$("#sort").addEventListener("change", (e) => { e.target.dataset.user = 1; push(false); });
$("#q").addEventListener("input", () => { clearTimeout(view.t); view.t = setTimeout(() => push(true), 200); });
window.addEventListener("hashchange", () => { $("#sort").dataset.user = readState().sort ? 1 : ""; view(); });
$("#dens").addEventListener("click", (e) => {
  const v = e.target.dataset?.v; if (!v) return;
  setDensity(v);
  if (v === "full") $("#list").querySelectorAll(".r").forEach((r) => fillDet(r));
});
$("#help").onclick = () => $("#helpbox").showModal();
$("#list").addEventListener("click", (e) => {
  if (e.target.closest("a")) return;
  const sb = e.target.closest(".simbtn");
  if (sb) return showSimilar(sb.parentElement);
  const lk = e.target.closest(".lk");
  if (lk) return like(rows[+lk.closest(".r").dataset.i][0]);
  const bm = e.target.closest(".bm");
  if (bm) return bookmark(rows[+bm.closest(".r").dataset.i][0]);
  const st = e.target.closest(".star");
  if (st) return rate(st.parentElement.dataset.id, +st.dataset.n);
  const tag = e.target.closest(".tag");
  if (tag) return addTag(tag.dataset.tag);
  const r = e.target.closest(".r"); if (r) { setCur(+r.dataset.i); open(r); }
});
$("#facets").addEventListener("click", (e) => { const t = e.target.closest(".tag"); if (t) addTag(t.dataset.tag); });
function addTag(t) { const q = $("#q"), term = `tag:"${t}"`; if (!q.value.includes(term)) q.value = (q.value + " " + term).trim(); push(false); }
function setCur(i) {
  const els = $("#list").querySelectorAll(".r"); if (!els.length) return;
  cur = Math.max(0, Math.min(els.length - 1, i));
  els.forEach((e) => e.classList.remove("cur")); els[cur].classList.add("cur"); els[cur].scrollIntoView({ block: "nearest" });
}
document.addEventListener("keydown", (e) => {
  if (e.metaKey || e.ctrlKey || e.altKey) return;
  const typing = /INPUT|SELECT|TEXTAREA/.test(document.activeElement.tagName);
  if (e.key === "Escape") { document.activeElement.blur(); return; }
  if (typing) return;
  const els = $("#list").querySelectorAll(".r"), at = cur >= 0 ? els[cur] : null;
  const day = () => dates.indexOf($("#day").value);
  switch (e.key) {
    case "/": e.preventDefault(); $("#q").focus(); $("#q").select(); break;
    case "j": setCur(cur + 1); break;
    case "k": setCur(cur - 1); break;
    case "Enter": case "x": if (at) { e.preventDefault(); open(at); } break;
    case "o": if (at) window.open(at.querySelector(".t").href, "_blank", "noopener"); break;
    case "e": { const all = [...els].some((x) => !x.classList.contains("open")); els.forEach((x) => open(x, all)); break; }
    case "[": case "]": { const i = day(); if (i < 0) break; const n = dates[i + (e.key === "[" ? 1 : -1)]; if (n) { $("#day").value = n; push(false); } break; }
    case "l": if (at) like(rows[+at.dataset.i][0]); break;
    case "b": if (at) bookmark(rows[+at.dataset.i][0]); break;
    case "?": $("#helpbox").showModal(); break;
    default: if (at && /^[0-5]$/.test(e.key)) rate(rows[+at.dataset.i][0], +e.key);
  }
});
new IntersectionObserver((es) => { if (es[0].isIntersecting && shown < rows.length) renderMore(); }, { rootMargin: "600px" }).observe($("#sentinel"));

// ── boot ───────────────────────────────────────────────────────────────
setDensity(store.get("dens", "compact"));
fetch("api/state").then((r) => (r.ok ? r.json() : null)).catch(() => null).then((st) => {
  if (st && st.ratings) { LOCAL = true;
    fetch("api/taste").then((r) => r.json()).then((t) => {
      taste = t;
      if (Object.keys(t).length) { $("#sort").insertAdjacentHTML("beforeend", '<option value="taste">for you ♥</option>'); if (DATA) view(); }
    }).catch(() => {}); ratings = st.ratings; saved = new Set(st.bookmarks); liked = new Set(st.likes); document.title += " ·local"; return; }
  document.body.classList.add("ro");  // public site: read-only mirror of the local picks
  return fetch("data/prefs.json").then((r) => (r.ok ? r.json() : null)).catch(() => null)
    .then((pf) => { if (pf) { ratings = pf.ratings || {}; saved = new Set(pf.bookmarks || []); liked = new Set(pf.likes || []); } });
}).then(() => fetch("data/index.json")).then((r) => r.json()).then((d) => {
  DATA = d;
  for (const r of d.papers) (byDate[r[1]] ||= []).push(r);
  dates = Object.keys(byDate).sort().reverse();
  const s = readState();
  $("#sort").dataset.user = s.sort ? 1 : "";
  view();
}).catch((e) => { $("#list").textContent = "Failed to load data: " + e; });
