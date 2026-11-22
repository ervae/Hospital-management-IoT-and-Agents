// Yatak yönetimi paneli. Derleme gerektirmez; sunucu bu dosyayı doğrudan verir.
import { api, auth } from "./api.js";

const $ = (s, el = document) => el.querySelector(s);
const esc = (v) => String(v ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const fmt = (v, d = 0) => (v == null || Number.isNaN(v) ? "–" : Number(v).toLocaleString("tr-TR", { maximumFractionDigits: d, minimumFractionDigits: d }));
const timeOf = (iso) => new Date(iso).toLocaleTimeString("tr-TR", { hour: "2-digit", minute: "2-digit", second: "2-digit" });

const COLS = ["occupied", "vacated", "cleaning", "ready"];
const STATE_TR = { occupied: "Dolu", vacated: "Temizlik bekliyor", cleaning: "Temizleniyor", ready: "Hazır", unknown: "Bilinmiyor" };
const SOURCE_TR = { inference: "sensörlerden çıkarıldı", external: "dış sınıflandırıcı", manual: "elle girildi", none: "henüz yok" };
const SEX_TR = { F: "Kadın", M: "Erkek", any: "Fark etmez" };
const URG_TR = { 1: "Çok acil", 2: "Acil", 3: "Orta", 4: "Düşük", 5: "Çok düşük" };
const AGENT_TYPE_TR = { coordinator: "Koordinatör", ed: "Acil Servis", ward: "Servis", cleaning: "Temizlik/Taburcu", single: "Tek ajan (LLM)", custom: "Özel", baseline: "Temel (kural)" };
const REC_TR = { pending: "Onay bekliyor", approved: "Onaylandı", rejected: "Reddedildi", blocked: "Kısıt ihlali", superseded: "Geçersiz" };

const state = { ov: null, selected: null, ward: "", showHis: false, seen: new Set(), timer: null, busy: false };
const canAct = () => ["admin", "operator"].includes(auth.user?.role);
// Süreleri simülasyon dakikasına çevir (simülatör hızlandırılmışsa)
const simMin = (realMin) => (realMin == null ? null : realMin * (state.ov?.kpis.time_scale || 1));
const dur = (min) => {
  if (min == null) return "–";
  if (min < 60) return `${Math.round(min)} dk`;
  if (min < 48 * 60) return `${Math.floor(min / 60)} sa ${Math.round(min % 60)} dk`;
  return `${Math.round(min / 1440)} gün`;
};
const short = (min) => (min == null ? "" : min < 60 ? `${Math.round(min)} dk` : min < 48 * 60 ? `${Math.round(min / 60)} sa` : `${Math.round(min / 1440)} g`);
const since = (iso) => (iso ? simMin((Date.now() - new Date(iso).getTime()) / 60000) : null);

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
  clearTimeout(toast._t); toast._t = setTimeout(() => (t.hidden = true), 2800);
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
  document.querySelectorAll(".operator-only").forEach((el) => (el.hidden = !canAct()));
  await refresh();
  clearInterval(state.timer);
  state.timer = setInterval(refresh, 3000);
}

async function refresh() {
  if (state.busy) return;
  try {
    state.ov = await api.get("/overview");
    $("#conn").className = "dot ok";
    renderKpis(); renderLanes(); renderRecs(); renderQueue(); renderFeed();
    if (!$("#drawer").hidden && state.selected) renderDrawer();
  } catch (e) {
    if (e.status !== 401) $("#conn").className = "dot bad";
  }
}

// ---------------- Göstergeler ----------------
function renderKpis() {
  const k = state.ov.kpis;
  const occPct = k.total_rooms ? (100 * k.occupied) / k.total_rooms : null;
  $("#kpis").innerHTML = `
    <div class="kpi ${k.ed_waiting ? "alert" : ""}"><div class="label">Acilde yatak bekleyen</div>
      <div class="value">${k.ed_waiting} <small>hasta</small></div>
      <div class="sub">ort. ${dur(simMin(k.avg_wait_min))} · en uzun ${dur(simMin(k.max_wait_min))}</div></div>
    <div class="kpi"><div class="label">Hazır yatak</div>
      <div class="value v-ready">${k.ready_beds}</div>
      <div class="sub">${k.soon_ready} yatak yakında hazır</div></div>
    <div class="kpi ${k.hidden_free ? "hint" : ""}"><div class="label">Gizli boş yatak</div>
      <div class="value">${k.hidden_free}</div>
      <div class="sub">hazır ama HBYS'de görünmüyor</div></div>
    <div class="kpi"><div class="label">Doluluk</div>
      <div class="value">%${fmt(occPct)}</div>
      <div class="bar"><i style="width:${occPct ?? 0}%"></i></div></div>
    <div class="kpi"><div class="label">Onay bekleyen öneri</div>
      <div class="value">${k.pending_recommendations}</div>
      <div class="sub">${state.ov.agents_online} ajan çevrimiçi${k.unknown ? ` · ${k.unknown} oda veri vermiyor` : ""}</div></div>`;
  const clock = $("#sim-clock");
  clock.hidden = !k.sim_clock;
  if (k.sim_clock) clock.textContent = `Simülasyon · ${k.sim_clock}`;
}

// ---------------- Kanban kat planı ----------------
function colOf(r) {
  if (r.state !== "unknown") return r.state;
  return COLS.includes(r.his_state) ? r.his_state : "occupied";  // son bilinen (HBYS) durum
}

function card(r) {
  const tags = [];
  if (r.isolation) tags.push(`<span class="chip iso" title="İzolasyon odası">İzolasyon</span>`);
  if (r.sex !== "any") tags.push(`<span class="chip" title="${SEX_TR[r.sex]} hasta">${r.sex === "F" ? "K" : "E"}</span>`);
  if (r.hidden_free) tags.push(`<span class="chip hidden-free" title="HBYS'de: ${STATE_TR[r.his_state]}">Gizli boş</span>`);
  if (r.reserved_for) tags.push(`<span class="chip reserved">Ayrıldı</span>`);
  if (r.state === "unknown") tags.push(`<span class="chip unknown" title="Sensör verisi gelmiyor">?</span>`);
  const his = state.showHis && r.his_state !== r.state ? `<div class="his">HBYS: ${STATE_TR[r.his_state]}</div>` : "";
  const low = r.state !== "unknown" && r.confidence < 0.6;
  return `<button class="room st-${r.state} ${low ? "low" : ""} ${r.id === state.selected ? "sel" : ""}" data-id="${r.id}"
      title="${esc(r.name)} · ${STATE_TR[r.state]} · ${SOURCE_TR[r.state_source] || ""}">
    <div class="room-top"><b>${esc(r.code.split("-").pop())}</b><span class="t" title="Bu durumda ${dur(since(r.state_since))}">${short(since(r.state_since))}</span></div>
    ${tags.length ? `<div class="tags">${tags.join("")}</div>` : ""}${his}
  </button>`;
}

function renderLanes() {
  const rooms = state.ov.rooms;
  const wards = [...new Set(rooms.map((r) => r.ward))];
  const sel = $("#ward-filter");
  if (sel.options.length !== wards.length + 1) {
    sel.innerHTML = `<option value="">Tüm servisler</option>` + wards.map((w) => `<option>${esc(w)}</option>`).join("");
    sel.value = state.ward;
  }
  // FLIP: kartların eski yerini kaydet, yeniden çiz, farkı canlandır
  const before = new Map([...document.querySelectorAll("#lanes .room")].map((el) => [el.dataset.id, el.getBoundingClientRect()]));
  $("#lanes").innerHTML = wards.filter((w) => !state.ward || w === state.ward).map((w) => {
    const rs = rooms.filter((r) => r.ward === w);
    const occ = rs.filter((r) => r.state === "occupied").length;
    return `<section class="lane">
      <div class="lane-head"><b>${esc(w)}</b><span class="small muted">${rs[0]?.floor ?? ""}. kat · ${occ}/${rs.length} dolu</span></div>
      ${COLS.map((c) => `<div class="cell col-${c}" data-label="${STATE_TR[c]}">${rs.filter((r) => colOf(r) === c).map(card).join("")}</div>`).join("")}
    </section>`;
  }).join("");
  document.querySelectorAll("#lanes .room").forEach((el) => {
    const old = before.get(el.dataset.id);
    if (old) {
      const now = el.getBoundingClientRect();
      const dx = old.left - now.left, dy = old.top - now.top;
      if (Math.abs(dx) + Math.abs(dy) > 2) {
        el.animate([{ transform: `translate(${dx}px, ${dy}px)` }, { transform: "none" }],
          { duration: 650, easing: "cubic-bezier(.2,.7,.2,1)" });
        el.animate([{ boxShadow: "0 0 0 3px var(--accent)" }, { boxShadow: "0 0 0 0 transparent" }], { duration: 1600 });
      }
    }
    el.addEventListener("click", () => openRoom(el.dataset.id));
  });
}
$("#ward-filter").addEventListener("change", (e) => { state.ward = e.target.value; renderLanes(); });
$("#show-his").addEventListener("change", (e) => { state.showHis = e.target.checked; renderLanes(); });

// ---------------- Öneriler ----------------
const reqById = () => Object.fromEntries(state.ov.requests.map((r) => [r.id, r]));
const roomById = () => Object.fromEntries(state.ov.rooms.map((r) => [r.id, r]));

function reqLine(q) {
  if (!q) return `<span class="muted">Talep kapandı</span>`;
  return `<b>${esc(q.patient_ref || q.id)}</b> · ${esc(q.ward)} · <span class="urg u${q.urgency}">${URG_TR[q.urgency]}</span>
    ${q.sex !== "any" ? ` · ${SEX_TR[q.sex]}` : ""}${q.isolation ? ` · <span class="chip iso">İzolasyon</span>` : ""}`;
}

function renderRecs() {
  const reqs = reqById(), rooms = roomById();
  const recs = state.ov.recommendations;
  const pending = recs.filter((r) => r.status === "pending")
    .sort((a, b) => (reqs[a.request_id]?.urgency ?? 9) - (reqs[b.request_id]?.urgency ?? 9));
  const recent = recs.filter((r) => r.status !== "pending" && r.status !== "superseded").slice(0, 4);
  $("#rec-count").textContent = pending.length ? `${pending.length} onay bekliyor` : "";
  const item = (rec) => {
    const q = reqs[rec.request_id] || rec.request;
    const cands = rec.candidates.map((c) => {
      const room = rooms[c.room_id];
      const ok = !c.violations.length;
      return `<li class="${ok ? "ok" : "bad"} ${rec.chosen_room_id === c.room_id ? "chosen" : ""}">
        <div><b>${esc(c.room_code || c.room_id)}</b> <span class="small muted">${esc(room?.ward || "")}</span>
          ${c.reason ? `<div class="small">${esc(c.reason)}</div>` : ""}
          ${c.violations.map((v) => `<div class="small v-bad">✕ ${esc(v)}</div>`).join("")}</div>
        ${rec.status === "pending" && ok && canAct() ? `<button class="btn small primary" data-approve="${rec.id}" data-room="${c.room_id}">Onayla</button>` : ""}
      </li>`;
    }).join("");
    const wait = q ? ` · ${dur(simMin(q.waiting_min))} ${q.status === "waiting" ? "bekliyor" : "bekledi"}` : "";
    return `<article class="rec st-${rec.status} ${state.seen.size && !state.seen.has(rec.id) ? "fresh" : ""}">
      <div class="rec-head"><div>${reqLine(q)}<span class="small muted">${wait}</span></div>
        <span class="badge ${rec.status}">${REC_TR[rec.status]}</span></div>
      <div class="small muted">${esc(rec.agent_name || rec.agent_id)} · ${timeOf(rec.created_at)}</div>
      ${rec.rationale ? `<p class="why">${esc(rec.rationale)}</p>` : ""}
      <ol class="cands">${cands}</ol>
      ${rec.status === "pending" && canAct() ? `<div class="rec-actions"><button class="btn small" data-reject="${rec.id}">Reddet</button></div>` : ""}
      ${rec.decision_note ? `<div class="small muted">Not: ${esc(rec.decision_note)}</div>` : ""}
    </article>`;
  };
  $("#recs").innerHTML = pending.length || recent.length
    ? pending.map(item).join("") + (recent.length ? `<div class="small muted sep">Son kararlar</div>` + recent.map(item).join("") : "")
    : `<div class="empty">Henüz öneri yok</div>`;
  recs.forEach((r) => state.seen.add(r.id));
  if (!state.seen.size) state.seen.add("_");
  $("#recs").querySelectorAll("[data-approve]").forEach((b) => b.addEventListener("click", () => decide(b.dataset.approve, "approve", { room_id: b.dataset.room })));
  $("#recs").querySelectorAll("[data-reject]").forEach((b) => b.addEventListener("click", () => decide(b.dataset.reject, "reject", {})));
}

async function decide(id, kind, body) {
  state.busy = true;
  try {
    const r = await api.post(`/recommendations/${id}/${kind}`, body);
    toast(kind === "approve" ? `Onaylandı: ${r.candidates.find((c) => c.room_id === r.chosen_room_id)?.room_code} ayrıldı` : "Öneri reddedildi");
  } catch (e) { toast(e.message); }
  state.busy = false;
  refresh();
}

// ---------------- Bekleyenler ----------------
function renderQueue() {
  const pendingFor = new Set(state.ov.recommendations.filter((r) => r.status === "pending").map((r) => r.request_id));
  const list = state.ov.requests;
  $("#queue").innerHTML = list.length ? list.map((q) => `
    <li><div>${reqLine(q)}</div>
      <div class="small muted">${q.origin === "ed" ? "Acil" : q.origin === "elective" ? "Planlı" : "Nakil"} · ${dur(simMin(q.waiting_min))} bekliyor
        ${pendingFor.has(q.id) ? ` · <span class="v-ready">öneri var</span>` : ""}</div></li>`).join("")
    : `<li class="empty">Yatak bekleyen hasta yok</li>`;
}

$("#new-req").addEventListener("click", () => {
  const wards = [...new Set(state.ov.rooms.map((r) => r.ward))];
  state.selected = null;
  $("#drawer-body").innerHTML = `<h2 id="drawer-title">Yeni yatış talebi</h2>
    <p class="small muted">Gerçek hasta kimliği girmeyin; kısa bir sentetik kod yeterli.</p>
    <form id="req-form" class="stack">
      <label>Hasta kodu<input name="patient_ref" placeholder="H-2001" maxlength="40"></label>
      <label>Servis<select name="ward">${wards.map((w) => `<option>${esc(w)}</option>`).join("")}</select></label>
      <label>Cinsiyet<select name="sex"><option value="F">Kadın</option><option value="M">Erkek</option><option value="any">Belirtilmedi</option></select></label>
      <label>Aciliyet<select name="urgency">${[1, 2, 3, 4, 5].map((u) => `<option value="${u}" ${u === 3 ? "selected" : ""}>${u} · ${URG_TR[u]}</option>`).join("")}</select></label>
      <label class="row"><input type="checkbox" name="isolation"> İzolasyon gerekiyor</label>
      <label class="row"><input type="checkbox" name="allow_overflow"> Başka servise yatabilir</label>
      <button class="btn primary">Talebi aç</button>
    </form>`;
  $("#drawer").hidden = false;
  $("#req-form").addEventListener("submit", async (e) => {
    e.preventDefault();
    const f = new FormData(e.target);
    try {
      await api.post("/requests", { patient_ref: f.get("patient_ref"), ward: f.get("ward"), sex: f.get("sex"),
        urgency: Number(f.get("urgency")), isolation: f.has("isolation"), allow_overflow: f.has("allow_overflow"), origin: "ed" });
      toast("Talep açıldı"); closeDrawer(); refresh();
    } catch (err) { toast(err.message); }
  });
});

// ---------------- Ajan akışı ----------------
function renderFeed() {
  const list = state.ov.decisions;
  $("#feed").innerHTML = list.length ? list.slice(0, 8).map((d) => `<li class="${d.level}">
      <span class="time">${timeOf(d.created_at)}</span>
      <span class="who">${esc(d.agent_name || d.agent_id)}</span>
      <span class="what">${esc(d.summary)}</span></li>`).join("")
    : `<li class="muted small">Ajanlar serbest metin kayıt gönderdiğinde burada görünür.</li>`;
}

// ---------------- Oda detayı ----------------
function openRoom(id) { state.selected = id; $("#drawer").hidden = false; renderDrawer(true); renderLanes(); }
function closeDrawer() { $("#drawer").hidden = true; state.selected = null; renderLanes(); }
document.querySelectorAll("[data-close]").forEach((el) => el.addEventListener("click", closeDrawer));
document.addEventListener("keydown", (e) => e.key === "Escape" && !$("#drawer").hidden && closeDrawer());

let transitions = [];
async function renderDrawer(fetchHistory = false) {
  const r = state.ov.rooms.find((x) => x.id === state.selected);
  if (!r) return;
  const body = $("#drawer-body");
  if (body.contains(document.activeElement) && ["SELECT", "INPUT"].includes(document.activeElement.tagName)) return;
  if (fetchHistory || !transitions.length || transitions[0]?.room_id !== r.id) {
    try { transitions = (await api.get(`/rooms/${r.id}/transitions?hours=48`)).reverse(); } catch { transitions = []; }
  }
  const m = r.metrics;
  const opt = (sel) => Object.entries(STATE_TR).map(([k, v]) => `<option value="${k}" ${k === sel ? "selected" : ""}>${v}</option>`).join("");
  body.innerHTML = `
    <div class="detail-title"><div><h2 id="drawer-title">${esc(r.name)}</h2>
      <div class="small muted">${esc(r.code)} · ${r.isolation ? "izolasyon odası · " : ""}${r.sex === "any" ? "karma" : SEX_TR[r.sex]} · ${r.beds} yatak</div></div>
      <span class="badge st-${r.state}">${STATE_TR[r.state]}</span></div>
    <div class="metrics">
      <div class="metric"><div class="label">IoT durumu</div><div class="value">${STATE_TR[r.state]}</div>
        <div class="small muted">${SOURCE_TR[r.state_source] || ""} · güven %${fmt(r.confidence * 100)} · ${dur(since(r.state_since))}</div></div>
      <div class="metric ${r.hidden_free ? "hint" : ""}"><div class="label">HBYS'de görünen</div><div class="value">${STATE_TR[r.his_state]}</div>
        <div class="small muted">${r.hidden_free ? "Gizli boş yatak" : r.his_updated_at ? `güncelleme ${timeOf(r.his_updated_at)}` : "kayıt yok"}</div></div>
      <div class="metric"><div class="label">Yatak yükü</div><div class="value">${fmt(m.bed_load_kg, 1)} <small class="muted">kg</small></div></div>
      <div class="metric"><div class="label">Hareket / kapı</div><div class="value">${fmt(m.motion)} <small class="muted">/ ${fmt(m.door_events ?? m.door_open)}</small></div></div>
      <div class="metric"><div class="label">Sıcaklık / nem</div><div class="value">${fmt(m.temp_c, 1)}° <small class="muted">%${fmt(m.humidity)}</small></div></div>
      <div class="metric"><div class="label">Bağlantı</div><div class="value ${r.online ? "v-ready" : "v-bad"}">${r.online ? "Veri geliyor" : "Veri yok"}</div>
        <div class="small muted">${r.updated_at ? `son ölçüm ${timeOf(r.updated_at)}` : ""}</div></div>
    </div>
    ${canAct() ? `<div class="stack">
      <div class="row"><button class="btn small" id="his-ready">HBYS'ye "hazır" gir</button>
        <span class="small muted">Hemşire/temizlik personelinin elle girişi</span></div>
      <form class="row" id="fix-state"><select name="state">${opt(r.state)}</select><button class="btn small">Durumu düzelt</button></form>
      <form class="row" id="add-label"><select name="state">${opt(r.state)}</select><input name="note" placeholder="Etiket notu (laboratuvar)"><button class="btn small">Etiketle</button></form>
    </div>` : ""}
    <h3 class="small muted">Durum geçmişi</h3>
    <ol class="history">${transitions.slice(0, 12).map((t) => `<li><span class="time">${timeOf(t.ts)}</span>
      <span class="${t.channel}">${t.channel === "his" ? "HBYS" : "IoT"}</span> ${STATE_TR[t.from_state]} → <b>${STATE_TR[t.to_state]}</b></li>`).join("") || `<li class="muted">Kayıt yok</li>`}</ol>`;
  const post = async (path, b, msg) => { try { await api.post(path, b); toast(msg); await refresh(); renderDrawer(true); } catch (e) { toast(e.message); } };
  $("#his-ready")?.addEventListener("click", () => post(`/rooms/${r.id}/his-state`, { state: "ready" }, "HBYS durumu güncellendi"));
  $("#fix-state")?.addEventListener("submit", (e) => { e.preventDefault(); post(`/rooms/${r.id}/state`, { state: new FormData(e.target).get("state") }, "Durum düzeltildi"); });
  $("#add-label")?.addEventListener("submit", (e) => { e.preventDefault(); const f = new FormData(e.target); post(`/rooms/${r.id}/labels`, { state: f.get("state"), note: f.get("note") }, "Etiket kaydedildi"); });
}

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
      const roomName = Object.fromEntries(rooms.map((r) => [r.id, `${r.code} · ${r.ward}`]));
      body.innerHTML = `<div class="card">${notice}
        <div class="card-head"><h2>Cihazlar</h2><span class="small muted">Gerçek ESP32 bir odaya atanınca simülatör o odayı otomatik bırakır ve durumu bu cihazın sensörlerinden çıkarılır.</span></div>
        <form class="form-row" id="new-device">
          <label>Cihaz adı<input name="name" required placeholder="ESP32-Oda-1"></label>
          <label>Oda<select name="room_id">${rooms.map((r) => `<option value="${r.id}">${esc(r.code)} · ${esc(r.ward)}</option>`).join("")}</select></label>
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
          const d = await api.post("/devices", { ...f, kind: "physical", capabilities: { sensors: ["bed_load_kg", "motion", "door_events", "temp_c", "humidity"], actuators: ["led"] } });
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
        <div class="card-head"><h2>Ajanlar</h2><span class="small muted">Dış ajanlar anahtarla durumu okur, yatış talebi açar ve yatak önerisi gönderir (docs/API.md).</span></div>
        <form class="form-row" id="new-agent">
          <label>Ad<input name="name" required placeholder="Koordinatör v1"></label>
          <label>Tür<select name="agent_type">${Object.entries(AGENT_TYPE_TR).filter(([k]) => k !== "baseline").map(([k, v]) => `<option value="${k}">${v}</option>`).join("")}</select></label>
          <label>Açıklama<input name="description"></label>
          <button class="btn primary">Ajan kaydet</button></form>
        <div class="overflow"><table class="list"><tr><th>Kimlik</th><th>Ad</th><th>Tür</th><th>Kaynak</th><th>Son görülme</th><th></th></tr>
        ${agents.map((a) => `<tr><td class="muted small">${esc(a.id)}</td><td>${esc(a.name)}</td><td>${AGENT_TYPE_TR[a.agent_type] || esc(a.agent_type)}</td>
          <td>${a.builtin ? "Yerleşik (kural)" : "Dış"}</td><td>${a.last_seen ? timeOf(a.last_seen) : "–"}</td>
          <td><button class="btn small" data-agent="${a.id}" data-active="${a.active}">${a.active ? "Durdur" : "Başlat"}</button></td></tr>`).join("")}
        </table></div></div>`;
      $("#new-agent").addEventListener("submit", async (e) => {
        e.preventDefault();
        try {
          const a = await api.post("/agents", Object.fromEntries(new FormData(e.target)));
          renderAdmin("agents", secretBox(`${a.name} kaydedildi (${a.id})`, a.api_key, "Bu anahtar sadece şimdi gösteriliyor. Ajan koduna X-Agent-Key olarak girin."));
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
