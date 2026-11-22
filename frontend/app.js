// Akıllı Hastane paneli. Derleme gerektirmez; sunucu bu dosyayı doğrudan verir.
import { api, auth } from "./api.js";

const $ = (s, el = document) => el.querySelector(s);
const esc = (v) => String(v ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const fmt = (v, d = 0) => (v == null || Number.isNaN(v) ? "–" : Number(v).toLocaleString("tr-TR", { maximumFractionDigits: d, minimumFractionDigits: d }));
const timeOf = (iso) => new Date(iso).toLocaleTimeString("tr-TR", { hour: "2-digit", minute: "2-digit", second: "2-digit" });

const STATUS_TR = { ok: "Normal", warning: "Uyarı", critical: "Kritik", offline: "Veri yok" };
const TYPE_TR = { icu: "Yoğun bakım", patient: "Hasta odası", waiting: "Bekleme", corridor: "Koridor", other: "Diğer" };
const ACT_TR = { fan: "Taze hava fanı", purifier: "Hava temizleyici", light: "Aydınlatma" };
const AGENT_TYPE_TR = { room: "Oda", energy: "Enerji", coordinator: "Koordinatör", custom: "Özel", baseline: "Temel" };

const state = { overview: null, selected: null, planMetric: "status", seenDecisions: new Set(), timer: null, series: [] };

// ---------------- Giriş ----------------
function showLogin() {
  clearInterval(state.timer);
  $("#app-view").hidden = true;
  $("#login-view").hidden = false;
}

$("#login-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const f = new FormData(e.target);
  $("#login-error").hidden = true;
  try {
    await auth.login(f.get("username"), f.get("password"));
    start();
  } catch (err) {
    $("#login-error").textContent = err.message;
    $("#login-error").hidden = false;
  }
});
$("#logout").addEventListener("click", () => { auth.logout(); showLogin(); });
window.addEventListener("auth:expired", showLogin);

function toast(msg) {
  const t = $("#toast");
  t.textContent = msg; t.hidden = false;
  clearTimeout(toast._t); toast._t = setTimeout(() => (t.hidden = true), 2600);
}

// ---------------- Sekmeler ----------------
document.querySelectorAll(".tabs .tab").forEach((b) => b.addEventListener("click", () => {
  document.querySelectorAll(".tabs .tab").forEach((x) => x.classList.toggle("active", x === b));
  $("#dashboard").hidden = b.dataset.view !== "dashboard";
  $("#admin").hidden = b.dataset.view !== "admin";
  if (b.dataset.view === "admin") renderAdmin(adminTab);
}));

async function start() {
  const user = auth.user;
  $("#login-view").hidden = true;
  $("#app-view").hidden = false;
  $("#who").textContent = `${user.full_name || user.username} · ${{ admin: "Yönetici", operator: "Operatör", viewer: "İzleyici" }[user.role]}`;
  document.querySelectorAll(".admin-only").forEach((el) => (el.hidden = user.role !== "admin"));
  await refresh();
  clearInterval(state.timer);
  state.timer = setInterval(refresh, 3000);
}

async function refresh() {
  try {
    state.overview = await api.get("/overview");
    $("#conn").className = "dot ok";
    renderEnergy(); renderPlan(); renderDetail(); renderFeed();
  } catch (e) {
    if (e.status !== 401) $("#conn").className = "dot bad";
  }
}

// ---------------- Enerji şeridi ----------------
function renderEnergy() {
  const { energy: e, alerts, agents_online, rooms } = state.overview;
  const site = rooms.length ? null : null;
  const net = (e.pv_kw ?? 0) - (e.load_kw ?? 0);
  const selfUse = e.load_kw ? Math.min(100, (100 * (e.pv_kw ?? 0)) / e.load_kw) : null;
  const bat = e.battery_pct;
  $("#energy").innerHTML = `
    <div class="kpi"><div class="label">☀️ Güneş üretimi</div>
      <div class="value">${fmt(e.pv_kw, 2)} <small>kW</small></div>
      <div class="sub">${net >= 0 ? `+${fmt(net, 2)} kW fazla` : `${fmt(-net, 2)} kW açık`}</div></div>
    <div class="kpi"><div class="label">⚡ Toplam tüketim</div>
      <div class="value">${fmt(e.load_kw, 2)} <small>kW</small></div>
      <div class="sub">Güneşten karşılanan: %${fmt(selfUse)}</div></div>
    <div class="kpi"><div class="label">🔋 Batarya</div>
      <div class="value ${bat < 30 ? "v-warning" : ""}">%${fmt(bat)}</div>
      <div class="bar"><i style="width:${bat ?? 0}%"></i></div></div>
    <div class="kpi"><div class="label">🏭 Şebekeden</div>
      <div class="value">${fmt(Math.max(0, e.grid_kw ?? 0), 2)} <small>kW</small></div>
      <div class="sub">${(e.grid_kw ?? 0) < 0 ? `${fmt(-e.grid_kw, 2)} kW şebekeye veriliyor` : "&nbsp;"}</div></div>
    <div class="kpi"><div class="label">🚨 Kritik oda / 🤖 Agent</div>
      <div class="value"><span class="${alerts ? "v-critical" : "v-ok"}">${alerts}</span> <small>/</small> ${agents_online} <small>çevrimiçi</small></div>
      <div class="sub">${rooms.filter((r) => r.status === "warning").length} odada uyarı</div></div>`;
  void site;
  const clock = $("#sim-clock");
  if (e.sim_hour != null) {
    const h = Math.floor(e.sim_hour), mm = Math.floor((e.sim_hour - h) * 60);
    clock.textContent = `Simülasyon saati ${String(h).padStart(2, "0")}:${String(mm).padStart(2, "0")}`;
    clock.hidden = false;
  } else clock.hidden = true;
}

// ---------------- Kat planı ----------------
function scaleClass(r) {
  if (state.planMetric === "status") return r.status;
  const v = r.metrics[state.planMetric];
  if (v == null) return "offline";
  const t = r.thresholds || {};
  const lim = {
    co2_ppm: [t.co2_warn ?? 1000, t.co2_crit ?? 1400],
    pm25: [t.pm25_warn ?? 25, t.pm25_crit ?? 50],
    occupancy: [r.type === "waiting" ? 12 : 2, r.type === "waiting" ? 20 : 3],
  }[state.planMetric];
  return v >= lim[1] ? "critical" : v >= lim[0] ? "warning" : "ok";
}

function renderPlan() {
  const svg = $("#plan");
  const rooms = state.overview.rooms;
  if (!state.selected && rooms.length) state.selected = rooms[0].id;
  svg.innerHTML = rooms.map((r) => {
    const L = r.layout || {};
    if (L.w == null) return "";
    const m = r.metrics;
    const cx = L.x + L.w / 2, cy = L.y + L.h / 2;
    const short = L.h < 12;
    const icons = [m.fan_pct > 30 ? "🌀" : "", m.purifier_pct > 0 ? "🫧" : "", m.light_pct > 0 ? "💡" : ""].join("");
    const line2 = `${fmt(m.co2_ppm)} ppm · ${fmt(m.occupancy)} kişi`;
    return `<g class="room st-${scaleClass(r)} ${r.id === state.selected ? "sel" : ""}" data-id="${r.id}" tabindex="0">
      <rect x="${L.x}" y="${L.y}" width="${L.w}" height="${L.h}" rx="1.2"/>
      ${short
        ? `<text class="name" x="${cx}" y="${cy + 0.9}" text-anchor="middle">${esc(r.name)} · <tspan class="val">${line2}</tspan> ${icons}</text>`
        : `<text class="name" x="${cx}" y="${cy - 2}" text-anchor="middle">${esc(r.name)}</text>
           <text class="val" x="${cx}" y="${cy + 2}" text-anchor="middle">${line2}</text>
           <text class="icons" x="${cx}" y="${cy + 6}" text-anchor="middle">${icons}</text>`}
    </g>`;
  }).join("");
  svg.querySelectorAll(".room").forEach((g) => {
    const pick = () => { state.selected = g.dataset.id; state.series = []; renderPlan(); renderDetail(true); renderFeed(); };
    g.addEventListener("click", pick);
    g.addEventListener("keydown", (e) => (e.key === "Enter" || e.key === " ") && pick());
  });
}
$("#plan-metric").addEventListener("change", (e) => { state.planMetric = e.target.value; renderPlan(); });

// ---------------- Oda detayı ----------------
let lastSeriesFetch = 0;
async function renderDetail(force = false) {
  const r = state.overview.rooms.find((x) => x.id === state.selected);
  const el = $("#room-detail");
  if (!r) { el.innerHTML = `<div class="empty">Bir oda seçin</div>`; return; }
  const m = r.metrics;
  const canControl = ["admin", "operator"].includes(auth.user.role);
  // Kaydırıcı sürüklenirken yeniden çizme
  if (el.contains(document.activeElement) && document.activeElement.type === "range") return;
  el.innerHTML = `
    <div class="detail-title">
      <div><h2>${esc(r.name)}</h2><div class="small muted">${TYPE_TR[r.type]} · öncelik ${r.priority} · ${fmt(r.volume_m3)} m³</div></div>
      <span class="badge ${r.status}">${STATUS_TR[r.status]}</span>
    </div>
    <div class="metrics">
      <div class="metric"><div class="label">CO2</div><div class="value ${statusOf(m.co2_ppm, r.thresholds.co2_warn ?? warnDefault(r, "co2"), r.thresholds.co2_crit ?? critDefault(r, "co2"))}">${fmt(m.co2_ppm)} <small class="muted">ppm</small></div></div>
      <div class="metric"><div class="label">PM2.5</div><div class="value">${fmt(m.pm25, 1)} <small class="muted">µg/m³</small></div></div>
      <div class="metric"><div class="label">Doluluk</div><div class="value">${fmt(m.occupancy)} <small class="muted">kişi</small></div></div>
      <div class="metric"><div class="label">Sıcaklık / nem</div><div class="value">${fmt(m.temp_c, 1)}° <small class="muted">%${fmt(m.humidity)}</small></div></div>
    </div>
    <div class="small muted">CO2 · son 60 dk</div>
    <svg class="spark" id="spark" viewBox="0 0 100 40" preserveAspectRatio="none"></svg>
    <div class="actuators">
      ${["fan", "purifier", "light"].map((a) => `
        <div class="act"><span>${ACT_TR[a]}</span>
          <input type="range" min="0" max="100" step="5" value="${m[a + "_pct"] ?? 0}" data-act="${a}" ${canControl ? "" : "disabled"}>
          <span class="num">%${fmt(m[a + "_pct"])}</span></div>`).join("")}
    </div>
    <div class="small muted" style="margin-top:8px">${canControl ? "Kaydırıcı elle müdahale komutu gönderir; agent sonraki turda yeniden karar verebilir." : "Elle müdahale için operatör yetkisi gerekir."}
      ${r.updated_at ? `· Son veri ${timeOf(r.updated_at)}` : ""}</div>`;
  el.querySelectorAll("input[type=range]").forEach((inp) => {
    inp.addEventListener("input", () => (inp.nextElementSibling.textContent = `%${inp.value}`));
    inp.addEventListener("change", async () => {
      try {
        await api.post(`/rooms/${r.id}/commands`, { actuator: inp.dataset.act, value: Number(inp.value), reason: "Panelden elle müdahale" });
        toast(`${ACT_TR[inp.dataset.act]} → %${inp.value} komutu gönderildi`);
        inp.blur();
      } catch (e) { toast(e.message); }
    });
  });
  if (force || Date.now() - lastSeriesFetch > 10000 || !state.series.length) {
    lastSeriesFetch = Date.now();
    try { state.series = await api.get(`/rooms/${r.id}/series?metric=co2_ppm&minutes=60`); } catch { /* sessiz */ }
  }
  drawSpark(r);
}
const warnDefault = (r) => ({ icu: 800, patient: 1000 }[r.type] ?? 1000);
const critDefault = (r) => ({ icu: 1000, patient: 1400 }[r.type] ?? 1500);
const statusOf = (v, w, c) => (v == null ? "" : v >= c ? "v-critical" : v >= w ? "v-warning" : "");

function drawSpark(r) {
  const svg = $("#spark");
  if (!svg) return;
  const pts = state.series.map((p) => p.value);
  if (pts.length < 2) { svg.innerHTML = ""; return; }
  const warn = r.thresholds.co2_warn ?? warnDefault(r);
  const lo = Math.min(400, ...pts), hi = Math.max(warn * 1.1, ...pts);
  const y = (v) => 38 - ((v - lo) / (hi - lo)) * 36;
  const step = 100 / (pts.length - 1);
  const d = pts.map((v, i) => `${i ? "L" : "M"}${(i * step).toFixed(2)},${y(v).toFixed(2)}`).join("");
  svg.innerHTML = `<path class="area" d="${d}L100,40L0,40Z"/><path class="line" d="${d}"/>
    <line class="thr" x1="0" x2="100" y1="${y(warn)}" y2="${y(warn)}"><title>Uyarı eşiği ${warn} ppm</title></line>`;
}

// ---------------- Karar akışı ----------------
function renderFeed() {
  const roomOnly = $("#feed-room-only").checked;
  const rooms = Object.fromEntries(state.overview.rooms.map((r) => [r.id, r]));
  const list = state.overview.decisions.filter((d) => !roomOnly || d.room_id === state.selected);
  $("#feed").innerHTML = list.length ? list.map((d) => {
    const fresh = !state.seenDecisions.has(d.id) && state.seenDecisions.size > 0;
    return `<li class="${d.level} ${fresh ? "fresh" : ""}">
      <span class="time">${timeOf(d.created_at)}</span>
      <span class="who ${esc(d.agent_type)}">${esc(d.agent_name || d.agent_id)}</span>
      <span class="what">${esc(d.summary)}${d.command_ids.length ? ` <span class="muted small">(${d.command_ids.length} komut)</span>` : ""}</span>
    </li>`;
  }).join("") : `<li class="muted">Henüz karar yok${roomOnly && rooms[state.selected] ? ` (${esc(rooms[state.selected].name)})` : ""}</li>`;
  state.overview.decisions.forEach((d) => state.seenDecisions.add(d.id));
  if (state.seenDecisions.size === 0) state.seenDecisions.add("_");
}
$("#feed-room-only").addEventListener("change", renderFeed);

// ---------------- Yönetim ----------------
let adminTab = "users";
document.querySelectorAll(".subtabs .tab").forEach((b) => b.addEventListener("click", () => {
  document.querySelectorAll(".subtabs .tab").forEach((x) => x.classList.toggle("active", x === b));
  adminTab = b.dataset.sub; renderAdmin(adminTab);
}));

const secretBox = (title, value, note) => `<div class="secret"><b>${esc(title)}</b><br><code>${esc(value)}</code><div class="small">${esc(note)}</div></div>`;

async function renderAdmin(tab, notice = "") {
  const body = $("#admin-body");
  try {
    if (tab === "users") {
      const users = await api.get("/users");
      body.innerHTML = `<div class="card">${notice}
        <div class="card-head"><h2>Kullanıcılar</h2><span class="small muted">Açık kayıt yoktur; hesapları yönetici açar, kimlikler sunucuda üretilir.</span></div>
        <form class="form-row" id="new-user">
          <label>Kullanıcı adı<input name="username" required pattern="[a-zA-Z0-9_.\\-]{3,40}"></label>
          <label>Ad soyad<input name="full_name"></label>
          <label>Rol<select name="role"><option value="viewer">İzleyici</option><option value="operator">Operatör</option><option value="admin">Yönetici</option></select></label>
          <button class="btn primary">Kullanıcı ekle</button></form>
        <div class="overflow"><table class="list"><tr><th>Kimlik</th><th>Kullanıcı</th><th>Ad</th><th>Rol</th><th>Durum</th><th></th></tr>
        ${users.map((u) => `<tr><td class="muted small">${esc(u.id)}</td><td>${esc(u.username)}</td><td>${esc(u.full_name)}</td>
          <td><select data-role="${u.id}">${["viewer", "operator", "admin"].map((r) => `<option value="${r}" ${u.role === r ? "selected" : ""}>${{ viewer: "İzleyici", operator: "Operatör", admin: "Yönetici" }[r]}</option>`).join("")}</select></td>
          <td>${u.active ? "Aktif" : "Pasif"}</td>
          <td><button class="btn small" data-toggle="${u.id}" data-active="${u.active}">${u.active ? "Pasifleştir" : "Aktifleştir"}</button></td></tr>`).join("")}
        </table></div></div>`;
      $("#new-user").addEventListener("submit", async (e) => {
        e.preventDefault();
        const f = Object.fromEntries(new FormData(e.target));
        try {
          const u = await api.post("/users", f);
          renderAdmin("users", secretBox(`${u.username} oluşturuldu (${u.id})`, u.temporary_password, "Geçici parola sadece şimdi gösteriliyor. Kullanıcıya iletin, ilk girişte değiştirsin."));
        } catch (err) { toast(err.message); }
      });
      body.querySelectorAll("[data-role]").forEach((s) => s.addEventListener("change", async () => {
        try { await api.patch(`/users/${s.dataset.role}`, { role: s.value }); toast("Rol güncellendi"); } catch (err) { toast(err.message); renderAdmin("users"); }
      }));
      body.querySelectorAll("[data-toggle]").forEach((b) => b.addEventListener("click", async () => {
        try { await api.patch(`/users/${b.dataset.toggle}`, { active: b.dataset.active !== "true" }); renderAdmin("users"); } catch (err) { toast(err.message); }
      }));
    } else if (tab === "devices") {
      const [devices, rooms] = await Promise.all([api.get("/devices"), api.get("/rooms")]);
      const roomName = Object.fromEntries(rooms.map((r) => [r.id, r.name]));
      body.innerHTML = `<div class="card">${notice}
        <div class="card-head"><h2>Cihazlar</h2><span class="small muted">Gerçek ESP32 bir odaya atanınca simülatör o odayı otomatik bırakır.</span></div>
        <form class="form-row" id="new-device">
          <label>Cihaz adı<input name="name" required placeholder="ESP32-Oda-1"></label>
          <label>Oda<select name="room_id">${rooms.map((r) => `<option value="${r.id}">${esc(r.name)}</option>`).join("")}</select></label>
          <button class="btn primary">ESP32 kaydet</button></form>
        <div class="overflow"><table class="list"><tr><th>Kimlik</th><th>Ad</th><th>Oda</th><th>Tür</th><th>Son görülme</th><th></th></tr>
        ${devices.map((d) => `<tr><td class="muted small">${esc(d.id)}</td><td>${esc(d.name)}</td><td>${esc(roomName[d.room_id] || "–")}</td>
          <td>${d.kind === "physical" ? "Fiziksel" : "Simüle"}${d.active ? "" : " (pasif)"}</td><td>${d.last_seen ? timeOf(d.last_seen) : "–"}</td>
          <td>${d.kind === "physical" ? `<button class="btn small" data-rotate="${d.id}">Yeni anahtar</button>` : ""}</td></tr>`).join("")}
        </table></div></div>`;
      $("#new-device").addEventListener("submit", async (e) => {
        e.preventDefault();
        const f = Object.fromEntries(new FormData(e.target));
        try {
          const d = await api.post("/devices", { ...f, kind: "physical", capabilities: { sensors: ["co2_ppm", "pm25", "occupancy", "temp_c", "humidity"], actuators: ["fan", "purifier", "light"] } });
          renderAdmin("devices", secretBox(`${d.name} kaydedildi (${d.id})`, d.api_key, "Bu anahtar sadece şimdi gösteriliyor. ESP32 yazılımına X-Device-Key olarak girin."));
        } catch (err) { toast(err.message); }
      });
      body.querySelectorAll("[data-rotate]").forEach((b) => b.addEventListener("click", async () => {
        const d = await api.post(`/devices/${b.dataset.rotate}/rotate-key`);
        renderAdmin("devices", secretBox(`${d.name} için yeni anahtar`, d.api_key, "Eski anahtar artık çalışmaz."));
      }));
    } else {
      const agents = await api.get("/agents");
      body.innerHTML = `<div class="card">${notice}
        <div class="card-head"><h2>Agent'lar</h2><span class="small muted">Dış agent'lar anahtarla durumu okur ve karar gönderir (docs/API.md).</span></div>
        <form class="form-row" id="new-agent">
          <label>Ad<input name="name" required placeholder="Oda agent'ı v1"></label>
          <label>Tür<select name="agent_type"><option value="room">Oda</option><option value="energy">Enerji</option><option value="coordinator">Koordinatör</option><option value="custom">Özel</option></select></label>
          <label>Açıklama<input name="description"></label>
          <button class="btn primary">Agent kaydet</button></form>
        <div class="overflow"><table class="list"><tr><th>Kimlik</th><th>Ad</th><th>Tür</th><th>Kaynak</th><th>Son görülme</th><th></th></tr>
        ${agents.map((a) => `<tr><td class="muted small">${esc(a.id)}</td><td>${esc(a.name)}</td><td>${AGENT_TYPE_TR[a.agent_type] || esc(a.agent_type)}</td>
          <td>${a.builtin ? "Yerleşik (eşik)" : "Dış"}</td><td>${a.last_seen ? timeOf(a.last_seen) : "–"}</td>
          <td><button class="btn small" data-agent="${a.id}" data-active="${a.active}">${a.active ? "Durdur" : "Başlat"}</button></td></tr>`).join("")}
        </table></div></div>`;
      $("#new-agent").addEventListener("submit", async (e) => {
        e.preventDefault();
        try {
          const a = await api.post("/agents", Object.fromEntries(new FormData(e.target)));
          renderAdmin("agents", secretBox(`${a.name} kaydedildi (${a.id})`, a.api_key, "Bu anahtar sadece şimdi gösteriliyor. Agent koduna X-Agent-Key olarak girin."));
        } catch (err) { toast(err.message); }
      });
      body.querySelectorAll("[data-agent]").forEach((b) => b.addEventListener("click", async () => {
        await api.patch(`/agents/${b.dataset.agent}`, { active: b.dataset.active !== "true" });
        renderAdmin("agents");
      }));
    }
  } catch (err) {
    body.innerHTML = `<div class="card error">${esc(err.message)}</div>`;
  }
}

// ---------------- Başlat ----------------
if (auth.token) {
  api.get("/auth/me").then((u) => { auth.user = u; start(); }).catch(showLogin);
} else showLogin();
