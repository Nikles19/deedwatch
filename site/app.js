"use strict";
/* Deedwatch front end: reads static JSON built nightly by the GitHub Action. */
const AREAS = [
  ["ALL", "UH & neighbors"], ["UNIVERSITY HEIGHTS", "University Hts"], ["SOUTH EUCLID", "South Euclid"],
  ["CLEVELAND HEIGHTS", "Cleveland Hts"], ["SHAKER HEIGHTS", "Shaker Hts"], ["BEACHWOOD", "Beachwood"], ["LYNDHURST", "Lyndhurst"]];
const UH = [41.4975, -81.5375];
const MAP_DAYS = 60, WEEK_DAYS = 7;

const $ = s => document.querySelector(s);
const esc = s => String(s ?? "").replace(/[&<>"']/g, c => ({"&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"}[c]));
const money = v => v ? "$" + Math.round(v).toLocaleString("en-US") : "—";
const moneyS = v => { if (!v) return "—"; if (v >= 1e6) return "$" + (v / 1e6).toFixed(v >= 1e7 ? 1 : 2) + "M"; if (v >= 1e3) return "$" + Math.round(v / 1e3) + "K"; return "$" + v; };
const fmtPin = p => p && p.length === 8 ? `${p.slice(0, 3)}-${p.slice(3, 5)}-${p.slice(5)}` : p;
const title = s => String(s || "").toLowerCase().replace(/\b([a-z])/g, m => m.toUpperCase()).replace(/\b(Llc|Lp|Inc|Ii|Iii|Usa|Us|Na|Oh)\b/g, m => m.toUpperCase());
const cityShort = c => (AREAS.find(a => a[0] === c) || [c, title(c)])[1];
const todayISO = () => { const d = new Date(); return new Date(d.getTime() - d.getTimezoneOffset() * 6e4).toISOString().slice(0, 10); };
const daysAgo = n => { const d = new Date(todayISO() + "T12:00:00"); d.setDate(d.getDate() - n); return d.toISOString().slice(0, 10); };
const fmtDate = iso => { if (!iso) return ""; const [y, m, d] = iso.split("-").map(Number); return new Date(y, m - 1, d).toLocaleDateString("en-US", {month: "short", day: "numeric", year: "numeric"}); };
const store = { get(k, d) { try { const v = localStorage.getItem("dw:" + k); return v == null ? d : JSON.parse(v); } catch { return d; } },
                set(k, v) { try { localStorage.setItem("dw:" + k, JSON.stringify(v)); } catch {} } };

const S = { area: store.get("area", "ALL"), kind: "res", nonmarket: false, hidePublic: true, ownerMode: "name",
  meta: null, recent: [], changes: [], prices: {}, owners: {}, index: null, indexP: null, hist: {}, markers: new Map(), ownerLayer: null, sel: null };

async function getJSON(path) { const r = await fetch(path, {cache: "no-cache"}); if (!r.ok) throw new Error(path + " " + r.status); return r.json(); }

/* ---------------- map ---------------- */
let map, saleLayer;
function houseIcon(market, recentWeek) {
  const fill = market ? (recentWeek ? "#c2410c" : "#1f5f4a") : "#9a958a";
  const size = market ? 22 : 17;
  return L.divIcon({className: "house", iconSize: [size, size], iconAnchor: [size / 2, size - 2],
    html: `<svg width="${size}" height="${size}" viewBox="0 0 24 24"><path d="M12 2.5 1.8 11.3h3v9.7h5.6v-6h3.2v6h5.6v-9.7h3z" fill="${fill}" stroke="#fff" stroke-width="1.6" stroke-linejoin="round"/></svg>`});
}
const COUNTY = "https://gis.cuyahogacounty.gov/server/rest/services/";
const PARCEL_ZOOM = 16, LABEL_ZOOM = 18;
function initMap() {
  map = L.map("map", {zoomControl: false, maxZoom: 21, rotate: true, bearing: 0, touchRotate: true, shiftKeyRotate: true,
    rotateControl: {closeOnZeroBearing: false, position: "bottomright"}}).setView(UH, 15);
  L.control.zoom({position: "bottomright"}).addTo(map);
  const esriAttr = 'Tiles &copy; Esri, HERE, Garmin, &copy; OpenStreetMap contributors';
  const bases = {
    "Streets": L.tileLayer("https://server.arcgisonline.com/ArcGIS/rest/services/World_Street_Map/MapServer/tile/{z}/{y}/{x}", {maxZoom: 21, maxNativeZoom: 19, attribution: esriAttr}),
    "Aerial (County 2026)": L.layerGroup([
      L.tileLayer(COUNTY + "IMAGERY/2026_Fall_Aerial/MapServer/tile/{z}/{y}/{x}", {maxZoom: 21, maxNativeZoom: 21, attribution: "Imagery &copy; Cuyahoga County"}),
      L.tileLayer("https://server.arcgisonline.com/ArcGIS/rest/services/Reference/World_Transportation/MapServer/tile/{z}/{y}/{x}", {maxZoom: 21, maxNativeZoom: 19, opacity: .9}),
      L.tileLayer("https://server.arcgisonline.com/ArcGIS/rest/services/Reference/World_Boundaries_and_Places/MapServer/tile/{z}/{y}/{x}", {maxZoom: 21, maxNativeZoom: 19})]),
    "MyPlace (County)": L.tileLayer(COUNTY + "MyPlace/MyPLACE_Parcel_Fabric_Basemap/MapServer/tile/{z}/{y}/{x}", {minZoom: 10, maxZoom: 21, zoomOffset: -10, attribution: "&copy; Cuyahoga County"}),
  };
  const pick = bases[store.get("base", "Streets")] || bases.Streets;
  pick.addTo(map);
  L.control.layers(bases, null, {position: "topright", collapsed: true}).addTo(map);
  map.on("baselayerchange", e => store.set("base", e.name));
  map.createPane("parcels", map.getPane("rotatePane") || undefined); map.getPane("parcels").style.zIndex = 390;
  parcelLayer = L.featureGroup([], {pane: "parcels"}).addTo(map);
  labelLayer = L.layerGroup().addTo(map);
  saleLayer = L.layerGroup().addTo(map);
  map.on("moveend", () => { clearTimeout(parcelTimer); parcelTimer = setTimeout(loadParcels, 250); });
  map.on("rotate", () => { clearTimeout(S.rotTimer); S.rotTimer = setTimeout(() => { if (S.lastFeats && map.getZoom() >= PARCEL_ZOOM) drawParcels(S.lastFeats); }, 200); });
  const Full = L.Control.extend({options: {position: "bottomright"}, onAdd() {
    const b = L.DomUtil.create("button", "leaflet-bar fullbtn");
    b.type = "button"; b.title = "Full-screen map"; b.setAttribute("aria-label", "Full-screen map");
    b.innerHTML = '<svg viewBox="0 0 20 20" width="18" height="18" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><path class="o" d="M3 7V3h4M13 3h4v4M17 13v4h-4M7 17H3v-4"/><path class="c" d="M7 3v4H3M13 7h4M13 3v4M17 13h-4v4M7 17v-4H3"/></svg>';
    L.DomEvent.disableClickPropagation(b);
    b.onclick = () => setFull(!document.body.classList.contains("mapfull"));
    return b; }});
  map.addControl(new Full());
}
function setFull(on) {
  document.body.classList.toggle("mapfull", on);
  setTimeout(() => { map.invalidateSize(); loadParcels(); }, 60);
}

/* ---------------- every parcel, once zoomed in ---------------- */
let parcelLayer, labelLayer, parcelTimer, parcelAbort, parcelRenderer;
async function countyParcels(b, signal) {
  const feats = [];
  for (const layer of [1, 0]) {
    for (let offset = 0; offset < 6000; offset += 2000) {
      const q = new URLSearchParams({geometry: [b.getWest(), b.getSouth(), b.getEast(), b.getNorth()].join(","), geometryType: "esriGeometryEnvelope",
        inSR: 4326, spatialRel: "esriSpatialRelIntersects", outFields: "parcelpin,par_addr_all,par_city,deeded_owner", returnGeometry: true,
        outSR: 4326, geometryPrecision: 6, f: "geojson", resultOffset: offset, resultRecordCount: 2000});
      const r = await fetch(`${COUNTY}Open_Data_Parcels/MapServer/${layer}/query?${q}`, {signal});
      const j = await r.json();
      const fs = j.features || [];
      feats.push(...fs);
      if (fs.length < 2000 && !(j.properties && j.properties.exceededTransferLimit)) break;
    }
  }
  return feats;
}
function parcelInfo(f) {
  const p = f.properties || {}, pin = String(p.parcelpin || "").trim();
  const ix = S.indexMap && S.indexMap.get(pin);
  const addr = (ix && ix[1]) || String(p.par_addr_all || "").split(",")[0].replace(/\s+/g, " ").trim();
  return {pin, addr, owner: (ix && ix[3]) || p.deeded_owner || "", city: (ix && ix[2]) || p.par_city || ""};
}
async function loadParcels() {
  const z = map.getZoom();
  if (z < PARCEL_ZOOM) { parcelLayer.clearLayers(); labelLayer.clearLayers(); S.parcelBounds = null; return; }
  const b = map.getBounds().pad(0.15);
  if (S.parcelBounds && S.parcelBounds.contains(map.getBounds())) {
    if (S.parcelLabels !== (z >= LABEL_ZOOM) || z >= LABEL_ZOOM) { S.parcelLabels = z >= LABEL_ZOOM; drawParcels(S.lastFeats); }
    return;
  }
  loadIndex().then(() => { if (S.lastFeats) drawParcels(S.lastFeats); }).catch(() => {});
  if (parcelAbort) parcelAbort.abort();
  parcelAbort = new AbortController();
  try {
    const feats = await countyParcels(b, parcelAbort.signal);
    S.parcelBounds = b; S.parcelLabels = z >= LABEL_ZOOM; S.lastFeats = feats;
    drawParcels(feats);
  } catch (e) { if (e.name !== "AbortError") console.warn("parcels", e); }
}
function drawParcels(feats) {
  parcelRenderer = parcelRenderer || L.canvas({pane: "parcels", padding: .3, tolerance: 3});
  parcelLayer.clearLayers(); labelLayer.clearLayers();
  const showLabels = map.getZoom() >= LABEL_ZOOM, view = map.getBounds();
  let labels = 0; const placed = [];
  L.geoJSON({type: "FeatureCollection", features: feats}, {
    renderer: parcelRenderer, pane: "parcels",
    style: {color: "#5b4a8a", weight: 1, opacity: .75, fillColor: "#7a3fb0", fillOpacity: .04},
    onEachFeature: (f, lyr) => {
      const info = parcelInfo(f);
      lyr.bindTooltip(`<b>${esc(title(info.addr) || fmtPin(info.pin))}</b><br>${esc(title(info.owner))}`, {sticky: true});
      lyr.on("mouseover", () => lyr.setStyle({fillOpacity: .22, weight: 2}));
      lyr.on("mouseout", () => lyr.setStyle({fillOpacity: .04, weight: 1}));
      lyr.on("click", () => openParcel(info.pin, {a: info.addr, c: info.city, owner: info.owner}));
      if (showLabels && labels < 600 && info.owner) {
        const lab = fitLabel(lyr, info.owner, view);
        if (lab && !placed.some(q => lab.box[0] < q[2] && lab.box[2] > q[0] && lab.box[1] < q[3] && lab.box[3] > q[1])) {
          labels++; placed.push(lab.box);
          labelLayer.addLayer(L.marker(lab.at, {interactive: false, keyboard: false,
            icon: L.divIcon({className: "plabel", iconSize: null,
              html: `<span style="transform:translate(-50%,-50%) rotate(${lab.deg}deg)">${esc(lab.txt)}</span>`})}));
        }
      }
    }}).eachLayer(l => parcelLayer.addLayer(l));
}
/* Fit an owner label inside a parcel: run it along the lot's long side, trimmed to the lot's length. */
const CHAR_W = 5.4, LABEL_H = 13;
function fitLabel(lyr, owner, view) {
  let ring = lyr.getLatLngs(); while (Array.isArray(ring[0])) ring = ring[0];
  if (!ring || ring.length < 3) return null;
  const pts = ring.map(ll => map.latLngToContainerPoint(ll));
  let best = 0, ang = 0;
  for (let i = 0; i < pts.length; i++) {
    const a = pts[i], b = pts[(i + 1) % pts.length], dx = b.x - a.x, dy = b.y - a.y, len = dx * dx + dy * dy;
    if (len > best) { best = len; ang = Math.atan2(dy, dx); }
  }
  if (ang > Math.PI / 2) ang -= Math.PI; else if (ang < -Math.PI / 2) ang += Math.PI;
  if (ang > Math.PI / 3) ang -= Math.PI;  // steep lots: always read bottom-to-top, like the rest
  const ux = Math.cos(ang), uy = Math.sin(ang), vx = -uy, vy = ux;
  let u0 = Infinity, u1 = -Infinity, v0 = Infinity, v1 = -Infinity;
  for (const p of pts) { const u = p.x * ux + p.y * uy, v = p.x * vx + p.y * vy; u0 = Math.min(u0, u); u1 = Math.max(u1, u); v0 = Math.min(v0, v); v1 = Math.max(v1, v); }
  const long = u1 - u0, short = v1 - v0;
  if (short < LABEL_H - 1) return null;
  const fit = Math.floor((long - 10) / CHAR_W);
  if (fit < 4) return null;
  let txt = title(owner).replace(/\s+/g, " ");
  if (txt.length > fit) txt = txt.slice(0, fit - 1).trimEnd() + "…";
  const um = (u0 + u1) / 2, vm = (v0 + v1) / 2, cx = um * ux + vm * vx, cy = um * uy + vm * vy;
  const at = map.containerPointToLatLng([cx, cy]);
  if (!view.contains(at)) return null;
  const w = txt.length * CHAR_W + 8, h = LABEL_H, W = Math.abs(w * ux) + Math.abs(h * vx), H = Math.abs(w * uy) + Math.abs(h * vy);
  return {txt, at, deg: ang * 180 / Math.PI, box: [cx - W / 2, cy - H / 2, cx + W / 2, cy + H / 2]};
}
const shortOwner = o => { const t = title(o).replace(/,?\s+(LLC|Inc|Et Al|Trustee|Tr|Trs)\.?$/i, m => m); return t.length > 22 ? t.slice(0, 21) + "…" : t; };
function inArea(c) { return S.area === "ALL" || c === S.area; }
function renderMap(fit) {
  saleLayer.clearLayers(); S.markers.clear();
  const since = daysAgo(MAP_DAYS), week = daysAgo(WEEK_DAYS);
  const seen = new Set();
  const rows = S.recent.filter(r => r.d >= since && inArea(r.c) && (r.m || S.nonmarket) && r.lat);
  let n = 0;
  for (const r of rows) {
    if (seen.has(r.pin)) continue; seen.add(r.pin); n++;
    const mk = L.marker([r.lat, r.lng], {icon: houseIcon(r.m, r.d >= week), riseOnHover: true, zIndexOffset: r.m ? 100 : 0});
    const own = (S.indexMap && S.indexMap.get(r.pin) && S.indexMap.get(r.pin)[3]) || r.buyer;
    mk.bindTooltip(`<b>${esc(title(r.a))}</b><br>${esc(title(own))}<br><span class="tt-d">Sold ${fmtDate(r.d)}</span>`, {direction: "top", offset: [0, -14]});
    mk.on("click", () => openParcel(r.pin, r));
    mk.addTo(saleLayer); S.markers.set(r.pin, mk);
  }
  $("#mapcount").textContent = `${n} ${S.nonmarket ? "transfers" : "sales"} in the last ${MAP_DAYS} days`;
  if (fit && rows.length && S.area !== "ALL") map.fitBounds(L.latLngBounds(rows.map(r => [r.lat, r.lng])).pad(0.1), {maxZoom: 15});
  if (fit && S.area === "ALL") map.setView(UH, 14);
}

/* ---------------- this week ---------------- */
function renderWeek() {
  const since = daysAgo(WEEK_DAYS);
  const rows = S.changes.filter(c => c.d >= since && inArea(c.c) && (S.nonmarket || c.kind === "sale"));
  $("#weekSub").textContent = rows.length ? `${rows.length} ${S.nonmarket ? "transfer" : "sale"}${rows.length === 1 ? "" : "s"} since ${fmtDate(since)}` : "";
  if (!rows.length) { $("#week").innerHTML = `<li class="none" style="cursor:default">No recorded ${S.nonmarket ? "transfers" : "sales"} in the last 7 days${S.area === "ALL" ? "" : " in " + cityShort(S.area)}.</li>`; return; }
  $("#week").innerHTML = rows.slice(0, 60).map((c, i) => {
    const [y, m, d] = c.d.split("-").map(Number); const dt = new Date(y, m - 1, d);
    const tag = c.kind === "sale" ? '<span class="tag">Sold</span>' : c.kind === "owner" ? '<span class="tag o">New owner</span>' : `<span class="tag g">${esc(shortDeed(c.t) || "Transfer")}</span>`;
    return `<li data-i="${i}"><div class="dt">${dt.toLocaleDateString("en-US", {month: "short"})}<b>${d}</b></div>
      <div class="main"><div class="addr">${tag}${esc(title(c.a) || fmtPin(c.pin))}</div>
      <div class="who">${c.to ? "To " + esc(title(c.to)) : ""}${c.from ? " · from " + esc(title(c.from)) : ""}</div></div>
      <div class="amt">${c.kind === "sale" ? moneyS(c.p) : ""}</div></li>`;
  }).join("");
  $("#week").querySelectorAll("li[data-i]").forEach(li => li.onclick = () => { const c = rows[+li.dataset.i]; openParcel(c.pin, c, true); });
}
const shortDeed = t => String(t || "").replace(/ Deed$/i, "").replace(/Quit Claim/i, "Quit claim").replace(/Sheriffs?/i, "Sheriff");

/* ---------------- prices ---------------- */
function renderPrices() {
  const area = S.prices[S.area] || {};
  const months = S.kind === "res" ? (area.res || []) : mergeMonths(area.res || [], area.other || []);
  // 60-day tiles straight from transfers so they match the map
  const since = daysAgo(MAP_DAYS), deals = new Set();
  const sales = S.recent.filter(r => r.m && r.d >= since && inArea(r.c) && (S.kind === "all" || r.cls === "R"))
    .filter(r => { const k = r.d + "|" + r.p + "|" + r.buyer; if (deals.has(k)) return false; deals.add(k); return true; });
  const sorted = [...sales].sort((a, b) => a.p - b.p);
  const med = sorted.length ? (sorted.length % 2 ? sorted[sorted.length >> 1].p : (sorted[sorted.length / 2 - 1].p + sorted[sorted.length / 2].p) / 2) : 0;
  const hi = sorted[sorted.length - 1], lo = sorted[0];
  $("#priceTiles").innerHTML = `
    <div class="tile" ${hi ? `data-pin="${hi.pin}"` : ""}><div class="k">Highest</div><div class="v">${moneyS(hi && hi.p)}</div><div class="s">${hi ? esc(title(hi.a)) : "&nbsp;"}</div></div>
    <div class="tile"><div class="k">Median</div><div class="v">${moneyS(med)}</div><div class="s">${sales.length} sale${sales.length === 1 ? "" : "s"}, last ${MAP_DAYS} days</div></div>
    <div class="tile" ${lo ? `data-pin="${lo.pin}"` : ""}><div class="k">Lowest</div><div class="v">${moneyS(lo && lo.p)}</div><div class="s">${lo ? esc(title(lo.a)) : "&nbsp;"}</div></div>`;
  $("#priceTiles").querySelectorAll("[data-pin]").forEach(t => t.onclick = () => { const r = S.recent.find(x => x.pin === t.dataset.pin); openParcel(t.dataset.pin, r, true); });
  renderChart(months.slice(-12));
}
function mergeMonths(a, b) {
  const m = new Map();
  for (const x of [...a, ...b]) {
    const g = m.get(x.m) || {m: x.m, n: 0, hi: 0, lo: Infinity, meds: []};
    g.n += x.n; g.hi = Math.max(g.hi, x.hi); g.lo = Math.min(g.lo, x.lo); g.meds.push([x.med, x.n]); m.set(x.m, g);
  }
  return [...m.values()].sort((x, y) => x.m < y.m ? -1 : 1).map(g => ({...g, med: g.meds.reduce((s, [v, n]) => s + v * n, 0) / g.n}));
}
function renderChart(ms) {
  const el = $("#priceChart");
  if (ms.length < 2) { el.innerHTML = `<p class="none">Not enough sales yet for a monthly trend.</p>`; return; }
  const W = 360, H = 150, P = {l: 44, r: 8, t: 8, b: 20};
  const max = Math.max(...ms.map(m => m.hi)) * 1.05, min = 0;
  const x = i => P.l + i * (W - P.l - P.r) / (ms.length - 1);
  const y = v => P.t + (H - P.t - P.b) * (1 - (v - min) / (max - min));
  const band = ms.map((m, i) => `${x(i)},${y(m.hi)}`).join(" ") + " " + ms.map((m, i) => `${x(i)},${y(m.lo)}`).reverse().join(" ");
  const line = ms.map((m, i) => `${i ? "L" : "M"}${x(i)},${y(m.med)}`).join("");
  const ticks = [0, .5, 1].map(f => min + f * (max - min));
  const lab = m => new Date(m + "-15").toLocaleDateString("en-US", {month: "short"});
  el.innerHTML = `<svg viewBox="0 0 ${W} ${H}" role="img" aria-label="Monthly median sale price with high and low range">
    ${ticks.map(t => `<line x1="${P.l}" x2="${W - P.r}" y1="${y(t)}" y2="${y(t)}" stroke="#ece7dc"/><text class="lbl" x="${P.l - 6}" y="${y(t) + 3}" text-anchor="end">${moneyS(t) === "—" ? "$0" : moneyS(t)}</text>`).join("")}
    <polygon points="${band}" fill="#1f5f4a" opacity=".12"/>
    <path d="${line}" fill="none" stroke="#1f5f4a" stroke-width="2.2" stroke-linejoin="round"/>
    ${ms.map((m, i) => `<circle cx="${x(i)}" cy="${y(m.med)}" r="3" fill="#fff" stroke="#1f5f4a" stroke-width="1.6"><title>${lab(m.m)} ${m.m.slice(0, 4)}: median ${money(m.med)}, high ${money(m.hi)}, low ${money(m.lo)} (${m.n} sales)</title></circle>`).join("")}
    ${ms.map((m, i) => (i % 2 === (ms.length - 1) % 2) ? `<text class="lbl" x="${x(i)}" y="${H - 5}" text-anchor="middle">${lab(m.m)}</text>` : "").join("")}
  </svg><div class="legend"><span><i style="background:#1f5f4a"></i>Median</span><span><i style="background:#1f5f4a;opacity:.25;height:8px"></i>High–low range</span></div>`;
}

/* ---------------- owners ---------------- */
const ENTITY = /\b(LLC|L L C|INC|CORP|CORPORATION|COMPANY|CO|LP|LLP|LTD|TRUST|PARTNERS|PARTNERSHIP|HOLDINGS|PROPERTIES|ASSOCIATION|FOUNDATION)\b/i;
function ownerRows() {
  const src = (S.owners && (S.owners[S.ownerMode] || (S.ownerMode === "name" && !S.owners.name ? S.owners : null))) || {};
  return (src[S.area] || []).filter(o => !(S.hidePublic && o.pub)).slice(0, 12);
}
function renderOwners() {
  const rows = ownerRows();
  $("#ownersSub").textContent = rows.length ? `${title(rows[0].name).slice(0, 26)}${rows[0].name.length > 26 ? "…" : ""} leads with ${rows[0].n}` : "";
  if (!rows.length) { $("#owners").innerHTML = `<p class="none">No owner data yet.</p>`; return; }
  const max = rows[0].n, byMail = S.ownerMode === "mail";
  $("#owners").innerHTML = rows.map((o, i) => `<div class="row ${o.pub ? "pub" : ""}" data-i="${i}" title="${esc(o.name)}">
    <div class="nm">${esc(title(o.name))}${byMail && o.entities > 1 ? ` <span class="sub">· ${o.entities} owner names</span>` : ""}</div><div class="ct">${o.n}</div>
    <div class="bar" style="width:${Math.max(3, 100 * o.n / max)}%"></div></div>`).join("") +
    `<p class="fine">Parcels owned${S.area === "ALL" ? " across the area" : " in " + cityShort(S.area)}. ${byMail ? "Grouped by where the county mails the tax bills, which often ties together LLCs run by the same people." : "Name variants (LLC, Inc., punctuation) are merged."} Tap a row for details and to map its parcels.</p>`;
  $("#owners").querySelectorAll(".row").forEach(r => r.onclick = () => showOwner(rows[+r.dataset.i], r));
}
function ownerDetail(o) {
  const byMail = S.ownerMode === "mail";
  const chips = (list) => `<div class="chips">${list.map(([nm, c]) => `<button class="chip" data-q="${esc(nm)}">${esc(title(nm))} <b>${c}</b></button>`).join("")}</div>`;
  let h = "";
  if (!byMail && o.mail && o.mail.length) h += `<div class="lbl">Tax bills mailed to</div><div>${o.mail.map(m => esc(title(m))).join("<br>")}</div>`;
  if (o.mailto && o.mailto.length) h += `<div class="lbl">Bill addressed to</div><div>${o.mailto.map(m => esc(title(m))).join("<br>")}</div>`;
  if (o.related && o.related.length) h += `<div class="lbl">${byMail ? "Owners using this address" : "Other owners at the same mailing address"}</div>${chips(o.related)}`;
  else if (!byMail) h += `<div class="lbl">Other owners at the same mailing address</div><div class="sub">None found${o.mail && o.mail.length ? "" : " (mailing address not loaded yet)"}.</div>`;
  const entity = !byMail && ENTITY.test(o.name);
  h += `<div class="links">${entity ? `<a class="btn" href="https://businesssearch.ohiosos.gov/" target="_blank" rel="noopener" style="text-decoration:none;color:inherit">Ohio business filings</a><button class="btn" data-copy="${esc(o.name)}">Copy name</button>` : ""}</div>`;
  if (entity) h += `<p class="fine">On the Ohio site, search the name to see the statutory agent and the organizer who signed the filing. Ohio doesn't list LLC members.</p>`;
  const el = document.createElement("div"); el.className = "odetail"; el.id = "ownerDetail"; el.innerHTML = h;
  el.querySelectorAll("[data-q]").forEach(b => b.onclick = e => { e.stopPropagation(); $("#q").value = b.dataset.q; onSearch(); $("#q").focus(); window.scrollTo({top: 0, behavior: "smooth"}); });
  el.querySelectorAll("[data-copy]").forEach(b => b.onclick = async e => { e.stopPropagation(); try { await navigator.clipboard.writeText(b.dataset.copy); b.textContent = "Copied"; } catch { b.textContent = b.dataset.copy; } });
  return el;
}
function clearOwner() {
  const d = document.getElementById("ownerDetail"); if (d) d.remove();
  document.querySelectorAll(".owners .row.on").forEach(x => x.classList.remove("on"));
  if (S.ownerLayer) { map.removeLayer(S.ownerLayer); S.ownerLayer = null; }
  const pill = document.getElementById("ownerPill"); if (pill) pill.remove();
  if (S.sel && S.prevView) map.setView(S.prevView[0], S.prevView[1]);
  S.sel = null;
}
async function showOwner(o, rowEl) {
  if (S.sel === o) { clearOwner(); return; }
  const had = !!S.sel; clearOwner();
  if (!had) S.prevView = [map.getCenter(), map.getZoom()];
  S.sel = o; rowEl.classList.add("on");
  rowEl.after(ownerDetail(o));
  const pill = document.createElement("button"); pill.id = "ownerPill"; pill.className = "pill owner";
  pill.innerHTML = `${esc(title(o.name))} <b aria-hidden="true">×</b>`; pill.title = "Clear this owner from the map";
  pill.onclick = clearOwner; $(".maptools").appendChild(pill);
  await loadIndex();
  const byPin = S.indexMap;
  const pts = o.pins.map(p => byPin.get(p)).filter(r => r && r[9]);
  S.ownerLayer = L.layerGroup(pts.map(r => L.circleMarker([r[9], r[10]], {radius: 6, color: "#fff", weight: 1.5, fillColor: "#7a3fb0", fillOpacity: .9})
    .bindTooltip(`<b>${esc(title(r[1]))}</b><br>${esc(title(r[3]))}`).on("click", () => openParcel(r[0])))).addTo(map);
  if (pts.length) map.fitBounds(L.latLngBounds(pts.map(r => [r[9], r[10]])).pad(0.15), {maxZoom: 16});
}

/* ---------------- search ---------------- */
async function loadIndex() {
  if (S.index) return S.index;
  if (!S.indexP) S.indexP = getJSON("data/index.json").then(ix => {
    S.index = ix; S.indexMap = new Map(ix.map(r => [r[0], r]));
    S.hay = ix.map(r => [(r[1] + " " + r[2]).toUpperCase(), (r[3] || "").toUpperCase(), (r[4] || "").toUpperCase()]);
    return ix; });
  return S.indexP;
}
let qTimer, active = -1, lastHits = [];
function onSearch() {
  clearTimeout(qTimer);
  qTimer = setTimeout(async () => {
    const q = $("#q").value.trim().toUpperCase();
    const box = $("#results");
    if (q.length < 2) { box.hidden = true; return; }
    if (!S.index) { box.hidden = false; box.innerHTML = `<div class="empty">Loading the parcel index…</div>`; }
    await loadIndex();
    const words = q.replace(/[^A-Z0-9 ]/g, " ").split(/\s+/).filter(Boolean);
    const hits = [];
    for (let i = 0; i < S.index.length && hits.length < 40; i++) {
      const [addr, own, past] = S.hay[i];
      const all = addr + " " + own + " " + past;
      if (!words.every(w => all.includes(w))) continue;
      const why = words.every(w => (addr + " " + own).includes(w)) ? (words.every(w => addr.includes(w)) ? "addr" : "owner") : "past";
      hits.push({r: S.index[i], why, score: (why === "addr" ? 0 : why === "owner" ? 1 : 2)});
    }
    hits.sort((a, b) => a.score - b.score);
    lastHits = hits; active = -1;
    box.hidden = false;
    if (!hits.length) { box.innerHTML = `<div class="empty">No parcels match “${esc($("#q").value)}”.</div>`; return; }
    const hl = s => { let h = esc(title(s)); for (const w of words) h = h.replace(new RegExp(`(${w.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")})`, "ig"), "<mark>$1</mark>"); return h; };
    box.innerHTML = hits.map((h, i) => {
      const r = h.r;
      const pastName = h.why === "past" ? (r[4].split("|").find(n => words.some(w => n.toUpperCase().includes(w))) || "") : "";
      return `<div class="r" data-i="${i}"><div><b>${hl(r[1] || fmtPin(r[0]))}</b> <span class="why">${esc(cityShort(r[2]))}</span></div>
        <div class="why">${h.why === "past" ? "Past owner: " + hl(pastName) + " · now " + esc(title(r[3])) : "Owner: " + hl(r[3])}</div></div>`;
    }).join("") + (hits.length >= 40 ? `<div class="empty">Showing the first 40. Add a word to narrow it down.</div>` : "");
    box.querySelectorAll(".r").forEach(el => el.onclick = () => pickHit(+el.dataset.i));
  }, 140);
}
function pickHit(i) { const h = lastHits[i]; if (!h) return; $("#results").hidden = true; openParcel(h.r[0], null, true); }

/* ---------------- parcel drawer ---------------- */
async function loadHistory(pin) {
  const b = pin.slice(0, 3);
  if (!S.hist[b]) S.hist[b] = getJSON(`data/h/${b}.json`).catch(() => ({}));
  return (await S.hist[b])[pin] || null;
}
async function openParcel(pin, row, pan) {
  await loadIndex().catch(() => {});
  const ix = S.indexMap && S.indexMap.get(pin);
  const a = (ix && ix[1]) || (row && row.a) || fmtPin(pin), city = (ix && ix[2]) || (row && row.c) || "";
  const lat = (ix && ix[9]) || (row && row.lat), lng = (ix && ix[10]) || (row && row.lng);
  $("#dTitle").textContent = title(a);
  $("#dSub").innerHTML = `${esc(cityShort(city))} · Parcel ${esc(fmtPin(pin))}`;
  $("#dBody").innerHTML = `<p class="none">Loading history…</p>`;
  $("#drawer").classList.add("on"); $("#drawer").setAttribute("aria-hidden", "false");
  if (lat && pan) map.setView([lat, lng], Math.max(map.getZoom(), 16));
  document.querySelectorAll(".house.sel").forEach(x => x.classList.remove("sel"));
  const mk = S.markers.get(pin); if (mk && mk._icon) mk._icon.classList.add("sel");
  const hist = await loadHistory(pin);
  const kv = pairs => `<dl class="kv">${pairs.filter(p => p[1]).map(([k, v]) => `<dt>${k}</dt><dd>${v}</dd>`).join("")}</dl>`;
  const owner = (ix && ix[3]) || (row && (row.owner || row.buyer)) || "";
  const histHtml = hist == null ? `<p class="none">Sale history for this parcel hasn't been loaded yet. The nightly job is still working through the area.</p>`
    : !hist.length ? `<p class="none">No recorded transfers.</p>`
    : `<ul class="hist">${hist.map(t => { const market = t.p >= 10000 && t.n === 1 && !/quit|sheriff/i.test(t.t);
        return `<li class="${market ? "" : "g"}"><div class="h1"><span>${fmtDate(t.d)}</span><span>${t.p ? money(t.p) : "$0"}</span></div>
        <div class="h2">${esc(t.t || "Transfer")}${t.n > 1 ? ` · ${t.n} parcels in one sale` : ""}</div>
        <div class="h2">To ${esc((t.ge || []).map(title).join(", ") || "—")}${t.gr && t.gr.length ? ` · from ${esc(t.gr.map(title).join(", "))}` : ""}</div></li>`; }).join("")}</ul>`;
  $("#dBody").innerHTML = kv([
    ["Owner", owner ? `<b>${esc(title(owner))}</b>` : ""],
    ["Land use", ix && ix[6] ? esc(title(ix[6])) : ""],
    ["Living area", ix && ix[8] ? Math.round(ix[8]).toLocaleString() + " sq ft" : ""],
    ["County value", ix && ix[7] ? money(ix[7]) : ""]]) +
    `<div style="display:flex;gap:8px;flex-wrap:wrap">${owner ? `<button class="btn" id="dOwn">Other parcels of this owner</button>` : ""}
      <a class="btn" href="https://myplace.cuyahogacounty.gov/" target="_blank" rel="noopener" style="text-decoration:none;color:inherit">Open MyPlace</a></div>
     <div class="sect">Sale &amp; transfer history</div>${histHtml}
     <p class="fine">Source: Cuyahoga County Fiscal Office (MyPlace). County data is provided “as is” and is not the official record. On MyPlace, search parcel ${esc(fmtPin(pin))}.</p>`;
  if ($("#dOwn")) $("#dOwn").onclick = () => { $("#q").value = owner; closeDrawer(); onSearch(); $("#q").focus(); };
}
function closeDrawer() { $("#drawer").classList.remove("on"); $("#drawer").setAttribute("aria-hidden", "true"); document.querySelectorAll(".house.sel").forEach(x => x.classList.remove("sel")); }

/* ---------------- wiring ---------------- */
function renderAreas() {
  $("#areas").innerHTML = AREAS.map(([k, n]) => `<button role="tab" data-a="${k}" class="${k === S.area ? "on" : ""}">${n}</button>`).join("");
  $("#areas").querySelectorAll("button").forEach(b => b.onclick = () => { S.area = b.dataset.a; store.set("area", S.area); renderAreas(); renderAll(true); });
}
function renderAll(fit) {
  if (S.sel) { S.prevView = null; clearOwner(); }
  renderMap(fit); renderWeek(); renderPrices(); renderOwners();
}
function renderFoot() {
  const m = S.meta || {};
  const built = m.built ? new Date(m.built).toLocaleString("en-US", {month: "short", day: "numeric", hour: "numeric", minute: "2-digit"}) : "—";
  const pct = m.parcels ? Math.round(100 * (m.withHistory || 0) / m.parcels) : 0;
  $("#foot").innerHTML = `Updated ${esc(built)} from Cuyahoga County Fiscal Office records (<a href="https://myplace.cuyahogacounty.gov/" target="_blank" rel="noopener">MyPlace</a>). ${(m.parcels || 0).toLocaleString()} parcels tracked${pct < 100 ? `; sale history loaded for ${pct}% so far` : ""}. Not an official record.`;
  if (pct < 100 && m.parcels) {
    const b = document.createElement("span"); b.className = "pill loading";
    b.title = "Sale history is still being loaded for the rest of the area; the map fills in as it finishes.";
    b.textContent = `History ${pct}% loaded`;
    $(".maptools").appendChild(b);
  }
}
async function boot() {
  initMap(); renderAreas();
  $("#q").addEventListener("input", onSearch);
  $("#q").addEventListener("focus", () => { loadIndex(); if (lastHits.length && $("#q").value.trim().length >= 2) $("#results").hidden = false; });
  $("#q").addEventListener("keydown", e => {
    const items = [...document.querySelectorAll("#results .r")];
    if (e.key === "ArrowDown" || e.key === "ArrowUp") { e.preventDefault(); active = Math.max(0, Math.min(items.length - 1, active + (e.key === "ArrowDown" ? 1 : -1))); items.forEach((x, i) => x.classList.toggle("on", i === active)); items[active] && items[active].scrollIntoView({block: "nearest"}); }
    if (e.key === "Enter") pickHit(active < 0 ? 0 : active);
    if (e.key === "Escape") $("#results").hidden = true;
  });
  document.addEventListener("click", e => { if (!e.target.closest(".search")) $("#results").hidden = true; });
  document.addEventListener("keydown", e => { if (e.key === "Escape") { if ($("#drawer").classList.contains("on")) closeDrawer(); else if (document.body.classList.contains("mapfull")) setFull(false); } });
  $("#dClose").onclick = closeDrawer;
  $("#nonmarket").onchange = e => { S.nonmarket = e.target.checked; renderMap(false); renderWeek(); };
  $("#hidePublic").onchange = e => { S.hidePublic = e.target.checked; clearOwner(); renderOwners(); };
  $("#ownerMode").querySelectorAll("button").forEach(b => b.onclick = () => { S.ownerMode = b.dataset.k; $("#ownerMode").querySelectorAll("button").forEach(x => x.classList.toggle("on", x === b)); clearOwner(); renderOwners(); });
  const setOwnersOpen = open => { $("#ownersToggle").setAttribute("aria-expanded", open); $("#ownersBody").hidden = !open; if (!open) clearOwner(); store.set("ownersOpen", open); };
  $("#ownersToggle").onclick = () => setOwnersOpen($("#ownersBody").hidden);
  setOwnersOpen(store.get("ownersOpen", false));
  $("#priceKind").querySelectorAll("button").forEach(b => b.onclick = () => { S.kind = b.dataset.k; $("#priceKind").querySelectorAll("button").forEach(x => x.classList.toggle("on", x === b)); renderPrices(); });
  try {
    [S.meta, S.recent, S.changes, S.prices, S.owners] = await Promise.all(["meta", "recent", "changes", "prices", "owners"].map(n => getJSON(`data/${n}.json`)));
  } catch (e) {
    $("#week").innerHTML = `<li class="none">Couldn't load data (${esc(e.message)}). The first nightly update may not have finished yet.</li>`;
    return;
  }
  renderAll(true); renderFoot();
}
boot();
