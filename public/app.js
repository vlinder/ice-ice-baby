"use strict";
// Static frontend: reads data/index.json and each hall's schedule file.
const STALE_HOURS = 36;
const DAYS_AHEAD = 7;
const STICK = { shared: "Delad is", with_stick: "Med klubba", no_stick: "Utan klubba", unknown: "Okänt" };
const STICK_HELP = { shared: "en del med klubba, en del utan" };
const WD = ["söndag", "måndag", "tisdag", "onsdag", "torsdag", "fredag", "lördag"];
const MON = ["januari", "februari", "mars", "april", "maj", "juni", "juli", "augusti", "september", "oktober", "november", "december"];

const $ = (id) => document.getElementById(id);
const esc = (s) => String(s ?? "").replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
const mins = (t) => { const [h, m] = t.split(":").map(Number); return h * 60 + m; };
const fmtT = (t) => t.replace(":", ".");

function stockholmNow() {
  const p = Object.fromEntries(new Intl.DateTimeFormat("sv-SE", {
    timeZone: "Europe/Stockholm", year: "numeric", month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit", hourCycle: "h23",
  }).formatToParts(new Date()).map((x) => [x.type, x.value]));
  return { date: `${p.year}-${p.month}-${p.day}`, minutes: Number(p.hour) * 60 + Number(p.minute) };
}
function addDays(iso, n) { const d = new Date(iso + "T12:00:00Z"); d.setUTCDate(d.getUTCDate() + n); return d.toISOString().slice(0, 10); }
function dayInfo(iso) { const d = new Date(iso + "T12:00:00Z"); return { wd: WD[d.getUTCDay()], day: d.getUTCDate(), mon: MON[d.getUTCMonth()] }; }
function fmtStamp(iso) {
  if (!iso) return "okänt";
  return new Intl.DateTimeFormat("sv-SE", { timeZone: "Europe/Stockholm", day: "numeric", month: "long", hour: "2-digit", minute: "2-digit" }).format(new Date(iso));
}

const state = { index: null, halls: [], days: [], day: null, munis: new Set(), today: null };

function isStale(h) {
  if (!h.data || h.data.status !== "ok") return true;
  return (Date.now() - new Date(h.data.fetched_at).getTime()) / 36e5 > STALE_HOURS;
}
function sessionsOn(h, day) { return h.data ? h.data.sessions.filter((s) => s.date === day) : []; }
function covered(h, day) { return !!h.data && h.data.covers.from <= day && day <= h.data.covers.to; }
function visibleHalls() { return state.halls.filter((h) => state.munis.has(h.municipality)); }
function chip(s) { return `<span class="chip c-${s.stick}">${STICK[s.stick] || s.stick}${s.note ? ` · ${esc(s.note)}` : ""}</span>`; }
function timeLink(s, cls = "") {
  return `<a class="${cls}" href="${esc(s.source_url)}" target="_blank" rel="noopener" title="Öppna källan för att verifiera">${fmtT(s.start)}–${fmtT(s.end)}</a>`;
}
function staleBadge(h) { return isStale(h) ? `<span class="badge" title="Kunde inte uppdateras nyligen">kan vara inaktuell</span>` : ""; }

async function load() {
  const index = await fetch("data/index.json", { cache: "no-cache" }).then((r) => r.json());
  state.index = index;
  const results = await Promise.allSettled(index.halls.map((h) => fetch("data/" + h.schedule, { cache: "no-cache" }).then((r) => r.json())));
  state.halls = index.halls.map((h, i) => ({ ...h, data: results[i].status === "fulfilled" ? results[i].value : null }));
  const now = stockholmNow();
  state.today = now;
  state.days = Array.from({ length: DAYS_AHEAD }, (_, i) => addDays(now.date, i));
  state.day = state.days[0];
  state.munis = new Set(state.halls.map((h) => h.municipality));
  renderStatic();
  render();
}

function renderStatic() {
  const munis = [...state.munis].sort((a, b) => a.localeCompare(b, "sv"));
  $("munis").textContent = munis.length > 6
    ? `${munis.length} kommuner i Stockholms län`
    : munis.length > 1 ? `${munis.slice(0, -1).join(", ")} och ${munis[munis.length - 1]}` : munis.join("");
  $("checked").textContent = `Senast kontrollerad ${fmtStamp(state.index.generated_at)}.`;
  const alerts = [];
  for (const s of state.index.sources) {
    if (s.status !== "ok") alerts.push(`Källan för ${esc(s.municipality)} kunde inte läsas vid senaste kontrollen. Tiderna nedan är från ${fmtStamp(s.fetched_at)} och kan ha ändrats.`);
  }
  const ageH = (Date.now() - new Date(state.index.generated_at).getTime()) / 36e5;
  if (ageH > STALE_HOURS) alerts.push(`Datan har inte uppdaterats på ${Math.round(ageH)} timmar. Kontrollera tiderna hos källan.`);
  $("alerts").innerHTML = alerts.map((a) => `<div class="alert" role="status">${a}</div>`).join("");
  $("legend").innerHTML = ["shared", "with_stick", "no_stick"].map((k) => `<span><i class="c-${k}"></i>${STICK[k]}${STICK_HELP[k] ? `: ${STICK_HELP[k]}` : ""}</span>`).join("");
  // With many municipalities: the first click shows only that one, later clicks add or
  // remove, and "Alla" (or removing the last one) shows everything again.
  $("munifilter").innerHTML = munis.length > 1
    ? `<button type="button" data-all="1" aria-pressed="true">Alla</button>` +
      munis.map((m) => `<button type="button" data-m="${esc(m)}" aria-pressed="false">${esc(m)}</button>`).join("")
    : "";
  $("munifilter").onclick = (e) => {
    const b = e.target.closest("button"); if (!b) return;
    const all = state.munis.size === munis.length;
    if (b.dataset.all) state.munis = new Set(munis);
    else if (all) state.munis = new Set([b.dataset.m]);
    else if (state.munis.has(b.dataset.m)) state.munis.delete(b.dataset.m);
    else state.munis.add(b.dataset.m);
    if (state.munis.size === 0) state.munis = new Set(munis);
    const nowAll = state.munis.size === munis.length;
    $("munifilter").querySelectorAll("button").forEach((x) => x.setAttribute("aria-pressed",
      x.dataset.all ? nowAll : !nowAll && state.munis.has(x.dataset.m)));
    render();
  };
  $("sourcesList").innerHTML = state.index.sources.map((s) =>
    `<li>${esc(s.municipality)}: <a href="${esc(s.source_url)}">${esc(new URL(s.source_url).host)}</a> – ${s.status === "ok" ? "läst" : "<strong>fel</strong> vid"} ${fmtStamp(s.last_attempt_at)}</li>`).join("");
}

function render() { renderDays(); renderList(); renderCalendar(); renderWeek(); renderMap(); }

function renderDays() {
  const hs = visibleHalls();
  $("days").innerHTML = state.days.map((d, i) => {
    const di = dayInfo(d);
    const n = hs.reduce((a, h) => a + sessionsOn(h, d).length, 0);
    const label = i === 0 ? "Idag" : i === 1 ? "Imorgon" : di.wd[0].toUpperCase() + di.wd.slice(1, 3);
    return `<button type="button" data-d="${d}" aria-pressed="${d === state.day}">${label} ${di.day}/${d.slice(5, 7) * 1}<small>${n} ${n === 1 ? "tid" : "tider"}</small></button>`;
  }).join("");
  $("days").onclick = (e) => { const b = e.target.closest("button"); if (b) { state.day = b.dataset.d; render(); } };
}

function dayTitle(d) { const di = dayInfo(d); return `${di.wd[0].toUpperCase() + di.wd.slice(1)} ${di.day} ${di.mon}`; }

function renderList() {
  const d = state.day;
  $("listTitle").textContent = `Lista – ${dayTitle(d)}`;
  const rows = [];
  for (const h of visibleHalls()) for (const s of sessionsOn(h, d)) rows.push({ h, s });
  rows.sort((a, b) => mins(a.s.start) - mins(b.s.start) || mins(a.s.end) - mins(b.s.end) || a.h.name.localeCompare(b.h.name, "sv"));
  const passed = (s) => d === state.today.date && mins(s.end) < state.today.minutes;
  $("list").innerHTML = rows.length ? rows.map(({ h, s }) => `<li${passed(s) ? ' style="opacity:.55"' : ""}>
      <div class="t">${timeLink(s)}</div>
      <div class="n">${esc(h.name)}${staleBadge(h)}<span class="sub">${esc(h.rink || "")}${h.rink ? " · " : ""}${esc(h.municipality)}${s.changed ? " · ändrad tid" : ""}</span>${chip(s)}</div></li>`).join("")
    : `<li><div class="t">–</div><div class="n"><span class="empty">Inga tider den här dagen.</span></div></li>`;
  const none = visibleHalls().filter((h) => covered(h, d) && !sessionsOn(h, d).length).map((h) => h.short_name);
  const unknown = visibleHalls().filter((h) => !covered(h, d)).map((h) => h.short_name);
  $("noneList").innerHTML = [
    none.length ? `Inga tider: ${none.map(esc).join(", ")}.` : "",
    unknown.length ? `Inte publicerat än: ${unknown.map(esc).join(", ")}.` : "",
  ].filter(Boolean).join(" ");
}

function renderCalendar() {
  const d = state.day;
  $("calTitle").textContent = `Kalender – ${dayTitle(d)}`;
  const hs = visibleHalls();
  const all = hs.flatMap((h) => sessionsOn(h, d));
  let h0 = 8, h1 = 20;
  for (const s of all) { h0 = Math.min(h0, Math.floor(mins(s.start) / 60)); h1 = Math.max(h1, Math.ceil(mins(s.end) / 60)); }
  const pct = (m) => ((m - h0 * 60) / ((h1 - h0) * 60)) * 100;
  const hours = Array.from({ length: h1 - h0 + 1 }, (_, i) => h0 + i);
  const grid = hours.map((h) => `<span class="gl" style="left:${pct(h * 60)}%"></span>`).join("");
  const now = d === state.today.date && state.today.minutes >= h0 * 60 && state.today.minutes <= h1 * 60
    ? `<span class="now" style="left:${pct(state.today.minutes)}%" title="Nu"></span>` : "";
  const first = (h) => sessionsOn(h, d)[0];
  const sorted = [...hs].sort((a, b) => {
    const sa = first(a), sb = first(b);
    if (!sa || !sb) return (sa ? -1 : sb ? 1 : 0) || a.name.localeCompare(b.name, "sv");
    return mins(sa.start) - mins(sb.start) || mins(sa.end) - mins(sb.end);
  });
  $("cal").innerHTML = `<div class="axis"><div></div><div class="ticks">${hours.map((h) => `<span style="left:${pct(h * 60)}%">${String(h).padStart(2, "0")}</span>`).join("")}</div></div>` +
    sorted.map((h) => {
      const ss = sessionsOn(h, d);
      const bars = ss.map((s) => `<a class="bar c-${s.stick}" style="left:${pct(mins(s.start))}%;width:${pct(mins(s.end)) - pct(mins(s.start))}%" href="${esc(s.source_url)}" target="_blank" rel="noopener" title="${esc(h.name)} ${fmtT(s.start)}–${fmtT(s.end)}, ${STICK[s.stick]}">${fmtT(s.start)}–${fmtT(s.end)}</a>`).join("");
      const empty = covered(h, d) ? "Inga tider" : "Inte publicerat än";
      return `<div class="row"><div class="lbl"><b>${esc(h.name)}</b>${staleBadge(h)}<br><small>${esc(h.rink || h.municipality)}</small></div><div class="track">${grid}${now}${bars || `<span class="na">${empty}</span>`}</div></div>`;
    }).join("");
}

function renderWeek() {
  const hs = visibleHalls();
  const head = `<thead><tr><th>Hall</th>${state.days.map((d) => { const di = dayInfo(d); return `<th class="${d === state.day ? "sel" : ""}">${di.wd.slice(0, 3)} ${di.day}/${d.slice(5, 7) * 1}</th>`; }).join("")}</tr></thead>`;
  const body = hs.map((h) => `<tr><th>${esc(h.short_name)}${staleBadge(h)}<small>${esc(h.municipality)}</small></th>${state.days.map((d) => {
    if (!covered(h, d)) return `<td class="unk" title="Inte publicerat än">?</td>`;
    return `<td>${sessionsOn(h, d).map((s) => `<a href="${esc(s.source_url)}" target="_blank" rel="noopener" title="${STICK[s.stick]}"><i class="c-${s.stick}"></i>${fmtT(s.start)}–${fmtT(s.end)}</a>`).join("")}</td>`;
  }).join("")}</tr>`).join("");
  $("week").innerHTML = head + `<tbody>${body}</tbody>`;
}

let map, layer;
function renderMap() {
  $("mapTitle").textContent = `Karta – ${dayTitle(state.day)}`;
  if (typeof L === "undefined") { $("map").textContent = "Kartan kunde inte laddas."; return; }
  if (!map) {
    map = L.map("map", { scrollWheelZoom: false });
    L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", {
      maxZoom: 18, attribution: '© <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a>',
    }).addTo(map);
    layer = L.layerGroup().addTo(map);
  }
  layer.clearLayers();
  const pts = [];
  for (const h of visibleHalls()) {
    if (h.lat == null || h.lon == null) continue;
    const ss = sessionsOn(h, state.day);
    const icon = L.divIcon({ className: "", html: `<div class="pin${ss.length ? "" : " off"}"></div>`, iconSize: [18, 18], iconAnchor: [9, 9] });
    const week = state.days.map((d) => {
      const x = sessionsOn(h, d);
      const di = dayInfo(d);
      const label = `${di.wd.slice(0, 3)} ${di.day}/${d.slice(5, 7) * 1}`;
      const val = !covered(h, d) ? "?" : x.length ? x.map((s) => `${timeLink(s)} <small>${STICK[s.stick]}</small>`).join(", ") : "–";
      return `<div${d === state.day ? ' style="font-weight:600"' : ""}>${label}: ${val}</div>`;
    }).join("");
    const html = `<b>${esc(h.name)}</b><br><span style="color:var(--muted)">${esc(h.rink || "")} · ${esc(h.municipality)}</span>${isStale(h) ? '<div class="badge" style="margin:4px 0 0">kan vara inaktuell</div>' : ""}<div style="margin-top:6px">${week}</div>${h.info_url ? `<div style="margin-top:6px"><a href="${esc(h.info_url)}" target="_blank" rel="noopener">Om anläggningen</a></div>` : ""}`;
    L.marker([h.lat, h.lon], { icon, title: h.name, zIndexOffset: ss.length ? 1000 : 0 }).bindPopup(html, { maxWidth: 300 }).addTo(layer);
    pts.push([h.lat, h.lon]);
  }
  if (pts.length && !map._fitted) { map.fitBounds(pts, { padding: [30, 30] }); map._fitted = true; }
}

load().catch((err) => {
  console.error(err);
  $("checked").textContent = "Kunde inte ladda datan. Försök igen om en stund.";
});
