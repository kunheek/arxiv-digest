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
// Papers with an explanation (Opus read the whole PDF). Both sites show them; only the local
// server can request a new one (EXPLAIN_ON: explain.enabled and an API key on the server).
let explained = new Set(), EXPLAIN_ON = false;
const post = (path, body) => fetch(path, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) })
  .then(() => { clearTimeout(post.t); post.t = setTimeout(() => refreshTaste(false), 800); }).catch(() => {});  // picks changed → new taste
// Taste scores (local only): "for you ♥" sort + ♥ badges, re-fetched whenever your picks change.
// After a pick we only update the numbers (shown on the next render) so the list doesn't jump.
function refreshTaste(rerender = true) {
  fetch("api/taste").then((r) => (r.ok ? r.json() : {})).then((t) => {
    taste = t;
    const has = Object.keys(t).length > 0, opt = $("#sort").querySelector('option[value="taste"]');
    if (has && !opt) $("#sort").insertAdjacentHTML("beforeend", '<option value="taste">for you ♥</option>');
    if (!has && opt) opt.remove();
    if (readState().sort === "taste") $("#sort").value = has ? "taste" : "score";
    if (DATA && rerender) view();
  }).catch(() => {});
}
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
  p.set("d", s.d);   // always set: "#/?d=" is all days, a bare "#/" means the latest day
  if (s.q) p.set("q", s.q); if (s.min !== "0") p.set("min", s.min);
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
      : t.v === "explained" ? explained.has(r[0]) : t.v === "saved" ? saved.has(r[0]) : t.v === "liked" ? liked.has(r[0]) : t.v === "rated" ? !!ratings[r[0]] : t.v === "unrated" ? !ratings[r[0]] : false; break;
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
      <span class="ic">${watch ? '<span title="watched author / institution">⭐</span>' : ""}${code ? ' <span title="has code">💻</span>' : ""}${(r[11] ?? 0) >= 7 ? ' <span title="big / foundational paper (field-wide impact ' + r[11].toFixed(0) + ')">📌</span>' : ""}${explained.has(id) ? ' <span title="has an explanation (expand the row)">📖</span>' : ""}</span>
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
    `<p class="acts">${explainBtn(r[0])}<button class="act bibbtn">Copy BibTeX</button>` +
    (LOCAL ? ` <button class="act simbtn">Show similar papers</button>` : "") + `</p><div class="xp" data-id="${r[0]}" hidden></div><div class="sim" data-id="${r[0]}"></div>`;
}
// ── explanations: the paper's key points, written by Opus from the whole PDF ──
const explainBtn = (id) => explained.has(id) ? '<button class="act xbtn on">📖 Key points</button> '
  : EXPLAIN_ON ? '<button class="act xbtn" title="Opus reads the whole PDF, figures included, and writes down the key points (≈ $0.20, about a minute)">📖 Explain this paper</button> ' : "";
const getX = (id) => fetch(`data/x/${id}.json`, { cache: "no-cache" }).then((r) => (r.ok ? r.json() : null)).catch(() => null);
const sleep = (ms) => new Promise((ok) => setTimeout(ok, ms));
async function explainPaper(btn) {
  const box = btn.closest(".det").querySelector(".xp"), id = box.dataset.id;
  if (box.dataset.done) { box.hidden = !box.hidden; return; }          // already shown: toggle
  box.hidden = false; box.textContent = "…";
  let x = explained.has(id) ? await getX(id) : null;   // else ask the server, which returns any existing one
  if (!x || x.status === "failed") {
    if (!EXPLAIN_ON) { box.textContent = "No explanation for this paper yet."; return; }
    if (!confirm("Opus will read the whole PDF and write down this paper's key points.\nAbout $0.20 and a minute. Go ahead?")) { box.hidden = true; return; }
    const r = await fetch("api/explain", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ id }) });
    x = await r.json().catch(() => ({}));
    if (!r.ok) { box.textContent = "Couldn't start: " + (x.error || r.status); return; }
  }
  btn.disabled = true;
  const t0 = Date.now();
  while (x.status === "pending") {                                      // written in the background; poll
    box.innerHTML = `<p class="qn">Opus is reading the paper… ${Math.round((Date.now() - t0) / 1000)} s (usually about a minute; you can keep browsing)</p>`;
    await sleep(4000);
    x = (await getX(id)) || x;
  }
  btn.disabled = false;
  if (x.status !== "done") { box.innerHTML = `<p class="qn">Explanation failed: ${esc(x.error || "unknown error")}. Click to try again.</p>`; return; }
  explained.add(id); btn.textContent = "📖 Key points"; btn.classList.add("on");
  box.dataset.done = 1;
  box.innerHTML = md(x.text) + `<p class="qn">Written by ${esc(x.model)} from the PDF on ${esc((x.at || "").slice(0, 10))} · $${(+x.cost || 0).toFixed(2)} · check numbers against the paper before citing</p>`;
}
// Tiny Markdown → HTML for explanations: ## headings, paragraphs, - / 1. lists (two levels),
// **bold**, *italic*, `code`, pipe tables and \\* escapes. Everything is HTML-escaped first.
function md(src) {
  const inl = (s) => esc(s).replace(/\\([\\`*_|#])/g, (m, c) => `&#${c.charCodeAt(0)};`).replace(/`([^`]+)`/g, "<code>$1</code>").replace(/\*\*(.+?)\*\*/g, "<b>$1</b>").replace(/(^|[\s(])\*([^*\s][^*]*?)\*(?=[\s.,;:)]|$)/g, "$1<i>$2</i>");
  let html = "", para = [], depth = 0, rows = null;
  const lists = (to) => { while (depth > to) { html += "</ul>"; depth--; } };
  const flush = () => {
    if (para.length) { html += `<p>${inl(para.join(" "))}</p>`; para = []; }
    if (rows) { html += "<table>" + rows.map((r, i) => "<tr>" + r.map((c) => (i ? `<td>${inl(c)}</td>` : `<th>${inl(c)}</th>`)).join("") + "</tr>").join("") + "</table>"; rows = null; }
  };
  for (const line of src.split("\n")) {
    const t = line.trim(), h = t.match(/^#{1,6}\s+(.*)/), li = line.match(/^(\s*)(?:[-*]|\d+[.)])\s+(.*)/);
    if (h) { flush(); lists(0); html += `<h4>${inl(h[1])}</h4>`; }
    else if (li) { flush(); const d = li[1].length >= 2 ? 2 : 1; while (depth < d) { html += "<ul>"; depth++; } lists(d); html += `<li>${inl(li[2])}</li>`; }
    else if (t.startsWith("|")) { if (para.length) flush(); lists(0); if (!/^\|[\s:|-]+\|$/.test(t)) (rows ||= []).push(t.replace(/^\||\|$/g, "").split("|").map((c) => c.trim())); }
    else if (!t) { flush(); lists(0); }
    else if (depth && /^\s/.test(line)) html = html.replace(/<\/li>$/, " " + inl(t) + "</li>");   // wrapped list item
    else { if (rows) flush(); lists(0); para.push(t); }
  }
  flush(); lists(0);
  return html;
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
// Same entry as digest/bibtex.py: Google Scholar's @article form, built from arXiv's metadata.
const STOP = new Set("a an the on of for in to and with towards toward via is".split(" "));
const ascii = (s) => s.normalize("NFKD").replace(/[^\x00-\x7f]/g, "");
const protect = (w) => /[A-Z]/.test(w.slice(1)) || (/\d/.test(w) && /[A-Za-z]/.test(w)) ? `{${w}}` : w;
const bibTitle = (t) => t.split(/\s+/).join(" ").replace(/(?<!\\)([&%#])/g, "\\$1").split(" ")
  .map((w) => /[$\\{}]/.test(w) ? w : w.split(/([-/])/).map(protect).join("")).join(" ");
function bibtex(r, d) {
  const [id, date, title] = r, au = d.au || r[5];
  const year = /^\d{4}\./.test(id) ? "20" + id.slice(0, 2) : date.slice(0, 4);
  const last = au.length ? ascii(au[0].split(/\s+/).pop()).toLowerCase().replace(/[^a-z]/g, "") : "anon";
  const word = (ascii(title).toLowerCase().match(/[a-z0-9]+/g) || []).find((w) => !STOP.has(w)) || "";
  return `@article{${last}${year}${word},\n  title={${bibTitle(title)}},\n  author={${au.join(" and ")}},\n` +
    `  journal={arXiv preprint arXiv:${id}},\n  year={${year}}\n}\n`;
}
async function copyBib(btn) {
  const r = rows[+btn.closest(".r").dataset.i], d = (await shard(r[1]))[r[0]] || {}, text = bibtex(r, d);
  try { await navigator.clipboard.writeText(text); btn.textContent = "Copied ✓"; }
  catch { showBib(text); }  // the clipboard API needs https or localhost, e.g. not plain http on the LAN
  setTimeout(() => { btn.textContent = "Copy BibTeX"; }, 1500);
}
function showBib(text) {
  const ta = $("#bibtext");
  ta.value = text;
  ta.rows = text.split("\n").length;
  $("#bibbox").showModal();
  ta.focus(); ta.select();
  let ok = false;
  try { ok = document.execCommand("copy"); } catch {}  // older API, still works without https
  $("#bibmsg").textContent = ok ? "copied ✓" : "selected, press ⌘C / Ctrl+C";
}
function showSimilar(p) {
  p.textContent = "…";
  fetch("api/similar?id=" + encodeURIComponent(p.dataset.id)).then((r) => r.json().then((l) => {
    if (!r.ok) throw new Error(l.error || r.status);
    return l;
  })).then((l) => {
    p.innerHTML = l.length ? "<b>Similar</b><ul>" + l.map((x) => `<li><a href="#/?q=${encodeURIComponent("id:" + x.id)}">${esc(x.title || x.id)}</a> <span class="mute">${(x.date || "").slice(5)} · ${x.sim.toFixed(2)}</span></li>`).join("") + "</ul>"
      : "No vectors yet (run: python run.py --embed-backfill)";
  }).catch((e) => { p.textContent = "Similar papers unavailable: " + e.message; });
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
    <span>${updatedHTML()}${s.q || s.min !== "0" || s.w || s.c || s.d ? `<a href="#/?d=${dates[0]}">reset</a>` : ""}</span>`;
  const counts = {};
  for (const r of rows) for (const t of r[4]) counts[t] = (counts[t] || 0) + 1;
  const top = Object.entries(counts).sort((a, b) => b[1] - a[1]).slice(0, 14);
  $("#facets").innerHTML = rows.length > 8 ? top.map(([t, n]) => `<button class="tag" data-tag="${esc(t)}">${esc(t)}<i>${n}</i></button>`).join("") : "";
  if (!rows.length) { $("#list").innerHTML = '<p class="empty">Nothing found. Try fewer filters.</p>'; return; }
  await renderMore();
  if (!keepScroll) window.scrollTo(0, 0);
}

function syncControls(s) {
  const day = $("#day"), i = dates.indexOf(s.d);
  day.value = s.d; day.textContent = dayLabel(s.d); day.title = s.d ? dayInfo(s.d) : "Pick a day";
  $("#dprev").disabled = i < 0 || i === dates.length - 1; $("#dnext").disabled = i <= 0;
  closeCal(); $("#min").value = s.min; $("#q").value !== s.q && document.activeElement !== $("#q") && ($("#q").value = s.q);
  $("#sort").value = s.sort || (s.d ? "score" : "new"); $("#w").checked = s.w; $("#c").checked = s.c; $("#a").checked = s.a; $("#s").checked = s.s;
}
function setDensity(v) {
  document.body.className = "dens-" + v; store.set("dens", v);
  document.querySelectorAll("#dens button").forEach((b) => b.classList.toggle("on", b.dataset.v === v));
}

// ── events ─────────────────────────────────────────────────────────────
const push = (replace) => { writeState(uiState(), replace); if (replace) view(true); };
["min", "w", "c", "a", "s"].forEach((id) => $("#" + id).addEventListener("change", () => push(false)));
$("#sort").addEventListener("change", (e) => { e.target.dataset.user = 1; push(false); });
$("#q").addEventListener("input", () => { clearTimeout(view.t); view.t = setTimeout(() => push(true), 200); });
window.addEventListener("hashchange", () => { $("#sort").dataset.user = readState().sort ? 1 : ""; view(); });
$("#dens").addEventListener("click", (e) => {
  const v = e.target.dataset?.v; if (!v) return;
  setDensity(v);
  if (v === "full") $("#list").querySelectorAll(".r").forEach((r) => fillDet(r));
});
$("#help").onclick = () => $("#helpbox").showModal();
// when the last run wrote papers, in the viewer's own time zone
const updatedHTML = () => {
  const t = DATA.updated && new Date(DATA.updated);
  if (!t || isNaN(t)) return "";
  const f = t.toLocaleString(undefined, { year: "numeric", month: "short", day: "numeric", hour: "2-digit", minute: "2-digit", timeZoneName: "short" });
  return `<span class="upd" title="Last time new papers were added">Updated ${esc(f)}</span>`;
};
// "About this digest" (delivery.about): shown once on a visitor's first visit, then from ⓘ or i
const about = () => { const d = $("#aboutbox"); if (d && !d.open) d.showModal(); };
if ($("#aboutbox")) {
  $("#about").hidden = false;
  $("#about").onclick = about;
  if (!store.get("aboutSeen")) { about(); store.set("aboutSeen", "1"); }
}
$("#list").addEventListener("click", (e) => {
  if (e.target.closest("a")) return;
  const bb = e.target.closest(".bibbtn");
  if (bb) return copyBib(bb);
  const xb = e.target.closest(".xbtn");
  if (xb) return explainPaper(xb);
  const sb = e.target.closest(".simbtn");
  if (sb) { const box = sb.closest(".det").querySelector(".sim"); sb.remove(); return showSimilar(box); }
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
// ── day picker: ‹ day › plus a month calendar of the days that have papers ──
const iso = (y, m, n) => `${y}-${String(m).padStart(2, "0")}-${String(n).padStart(2, "0")}`;
const dayDate = (d) => { const [y, m, n] = d.split("-").map(Number); return new Date(y, m - 1, n); };  // local, not UTC
const addDays = (d, k) => { const t = dayDate(d); t.setDate(t.getDate() + k); return iso(t.getFullYear(), t.getMonth() + 1, t.getDate()); };
function dayLabel(d) {
  if (!d) return "All days";
  const o = { weekday: "short", month: "short", day: "numeric" };
  if (d.slice(0, 4) !== dates[0]?.slice(0, 4)) o.year = "numeric";
  return dayDate(d).toLocaleDateString("en-US", o);
}
function dayInfo(d) {
  const r = byDate[d] || [], top = r.filter((x) => x[3] >= 7).length, total = DATA.days[d]?.total;
  return `${dayLabel(d)} · ${r.length} paper${r.length === 1 ? "" : "s"}${top ? `, ${top} top` : ""}${total ? ` (of ${total} scanned)` : ""}`;
}
function stepDay(k) {                      // k = 1 older, -1 newer
  const i = dates.indexOf($("#day").value), n = i < 0 ? null : dates[i + k];
  if (n) { $("#day").value = n; push(false); }
}
function pickDay(d) {
  closeCal(true);
  if (d !== $("#day").value) { $("#day").value = d; push(false); }
}
const cal = { ym: "", focus: "" };         // month shown ("2026-10") and the day that holds keyboard focus
const months = () => [...new Set(dates.map((d) => d.slice(0, 7)))];   // newest first
function renderCal() {
  const sel = $("#day").value, [y, m] = cal.ym.split("-").map(Number), ms = months(), mi = ms.indexOf(cal.ym);
  const lead = (new Date(y, m - 1, 1).getDay() + 6) % 7, n = new Date(y, m, 0).getDate();
  const max = Math.max(...dates.map((d) => byDate[d].length)), now = new Date(), today = iso(now.getFullYear(), now.getMonth() + 1, now.getDate());
  let g = ["Mo", "Tu", "We", "Th", "Fr", "Sa", "Su"].map((w) => `<span class="wd">${w}</span>`).join("") + "<span></span>".repeat(lead);
  for (let i = 1; i <= n; i++) {
    const d = iso(y, m, i), c = byDate[d]?.length || 0, td = d === today ? " today" : "";
    g += c ? `<button data-d="${d}" class="h${Math.ceil((4 * c) / max)}${d === sel ? " on" : ""}${td}" title="${esc(dayInfo(d))}" tabindex="${d === cal.focus ? 0 : -1}">${i}<small>${c}</small></button>`
           : `<button disabled class="${td}" tabindex="-1">${i}</button>`;
  }
  $("#calg").innerHTML = g;
  $("#calm").textContent = new Date(y, m - 1, 1).toLocaleDateString("en-US", { month: "long", year: "numeric" });
  const [older, newer] = $("#cal").querySelectorAll(".cal-nav");
  older.disabled = mi >= ms.length - 1; newer.disabled = mi <= 0;
  $("#cal").querySelector('.cal-f [data-d=""]').classList.toggle("on", !sel);
  $("#callatest").dataset.d = dates[0];
  $("#calsum").textContent = `${dates.length} day${dates.length === 1 ? "" : "s"} · ${DATA.papers.length.toLocaleString()} papers`;
}
function showMonth(ym, focus) {            // focus: the day to move keyboard focus to, if any
  cal.ym = ym;
  const sel = $("#day").value, inMonth = dates.filter((d) => d.startsWith(ym));
  cal.focus = focus || (sel.startsWith(ym) ? sel : inMonth[0]);
  renderCal();
  if (focus) $(`#calg [data-d="${focus}"]`)?.focus();
}
function openCal() {
  if (!dates.length) return;
  const d = dates.includes($("#day").value) ? $("#day").value : dates[0];   // also for a hand-typed day with no papers
  $("#cal").hidden = false; $("#day").setAttribute("aria-expanded", "true");
  showMonth(d.slice(0, 7), d); placeCal();
}
function closeCal(refocus) {
  if ($("#cal").hidden) return;
  $("#cal").hidden = true; $("#day").setAttribute("aria-expanded", "false");
  if (refocus) $("#day").focus();
}
function placeCal() {                      // under the day button, kept on screen (the toolbar scrolls sideways on phones)
  const b = $("#day").getBoundingClientRect(), h = $("header").getBoundingClientRect(), w = $("#cal").offsetWidth;
  $("#cal").style.top = b.bottom - h.top + 6 + "px";
  $("#cal").style.left = Math.max(8, Math.min(b.left - h.left, innerWidth - w - 8)) + "px";
}
$("#day").addEventListener("click", () => ($("#cal").hidden ? openCal() : closeCal()));
$("#dprev").addEventListener("click", () => stepDay(1));
$("#dnext").addEventListener("click", () => stepDay(-1));
$("#cal").addEventListener("click", (e) => {
  const b = e.target.closest("button"); if (!b || b.disabled) return;
  if (b.dataset.step) { const ms = months(); showMonth(ms[ms.indexOf(cal.ym) + +b.dataset.step]); }
  else if ("d" in b.dataset) pickDay(b.dataset.d);
});
$("#cal").addEventListener("keydown", (e) => {
  e.stopPropagation();                     // page shortcuts are off while the calendar has focus
  if (e.key === "Escape") { closeCal(true); return; }
  const d = e.target.closest("#calg") && e.target.dataset.d;
  if (!d) return;
  const i = dates.indexOf(d), ms = months(), mi = ms.indexOf(cal.ym);
  const firstOnOrAfter = (t) => [...dates].reverse().find((x) => x >= t), lastOnOrBefore = (t) => dates.find((x) => x <= t);
  let n;
  switch (e.key) {
    case "ArrowLeft": n = dates[i + 1]; break;
    case "ArrowRight": n = dates[i - 1]; break;
    case "ArrowUp": n = lastOnOrBefore(addDays(d, -7)); break;
    case "ArrowDown": n = firstOnOrAfter(addDays(d, 7)); break;
    case "PageUp": n = ms[mi + 1] && dates.find((x) => x.startsWith(ms[mi + 1])); break;
    case "PageDown": n = ms[mi - 1] && dates.find((x) => x.startsWith(ms[mi - 1])); break;
    case "Home": n = dates.at(-1); break;
    case "End": n = dates[0]; break;
    default: return;
  }
  e.preventDefault(); e.stopPropagation();
  if (n) showMonth(n.slice(0, 7), n);
});
document.addEventListener("pointerdown", (e) => { if (!$("#cal").hidden && !e.target.closest("#cal, #day")) closeCal(); });
window.addEventListener("resize", () => $("#cal").hidden || placeCal());
function setCur(i) {
  const els = $("#list").querySelectorAll(".r"); if (!els.length) return;
  cur = Math.max(0, Math.min(els.length - 1, i));
  els.forEach((e) => e.classList.remove("cur")); els[cur].classList.add("cur"); els[cur].scrollIntoView({ block: "nearest" });
}
document.addEventListener("keydown", (e) => {
  if (e.metaKey || e.ctrlKey || e.altKey) return;
  const typing = /INPUT|SELECT|TEXTAREA/.test(document.activeElement.tagName);
  if (e.key === "Escape") { closeCal(); document.activeElement.blur(); return; }
  if (typing || (e.key === "Enter" && document.activeElement.tagName === "BUTTON")) return;   // let a focused button take Enter
  const els = $("#list").querySelectorAll(".r"), at = cur >= 0 ? els[cur] : null;
  switch (e.key) {
    case "/": e.preventDefault(); $("#q").focus(); $("#q").select(); break;
    case "j": setCur(cur + 1); break;
    case "k": setCur(cur - 1); break;
    case "Enter": case "x": if (at) { e.preventDefault(); open(at); } break;
    case "o": if (at) window.open(at.querySelector(".t").href, "_blank", "noopener"); break;
    case "e": { const all = [...els].some((x) => !x.classList.contains("open")); els.forEach((x) => open(x, all)); break; }
    case "[": case "]": stepDay(e.key === "[" ? 1 : -1); break;
    case "l": if (at) like(rows[+at.dataset.i][0]); break;
    case "b": if (at) bookmark(rows[+at.dataset.i][0]); break;
    case "?": $("#helpbox").showModal(); break;
    case "i": about(); break;
    default: if (at && /^[0-5]$/.test(e.key)) rate(rows[+at.dataset.i][0], +e.key);
  }
});
new IntersectionObserver((es) => { if (es[0].isIntersecting && shown < rows.length) renderMore(); }, { rootMargin: "600px" }).observe($("#sentinel"));

// ── boot ───────────────────────────────────────────────────────────────
setDensity(store.get("dens", "compact"));
fetch("api/state").then((r) => (r.ok ? r.json() : null)).catch(() => null).then((st) => {
  if (st && st.ratings) { LOCAL = true;
    refreshTaste(); ratings = st.ratings; saved = new Set(st.bookmarks); liked = new Set(st.likes); document.title += " ·local";
    explained = new Set(st.explained || []); EXPLAIN_ON = !!st.explain_on; return; }
  document.body.classList.add("ro");  // public site: read-only mirror of the local picks
  return fetch("data/prefs.json").then((r) => (r.ok ? r.json() : null)).catch(() => null)
    .then((pf) => { if (pf) { ratings = pf.ratings || {}; saved = new Set(pf.bookmarks || []); liked = new Set(pf.likes || []); explained = new Set(pf.explained || []); } });
}).then(() => fetch("data/index.json")).then((r) => r.json()).then((d) => {
  DATA = d;
  for (const r of d.papers) (byDate[r[1]] ||= []).push(r);
  dates = Object.keys(byDate).sort().reverse();
  const s = readState();
  $("#sort").dataset.user = s.sort ? 1 : "";
  view();
}).catch((e) => { $("#list").textContent = "Failed to load data: " + e; });
