// Phone client: mic + camera to the server, agent audio back, live inventory.
// Two frame streams: ~1 fps small frames to the live agent (conversation context)
// and ~2 fps high-res frames to the measuring pipeline (evidence + vision).
const $ = (s) => document.querySelector(s);
const HIRES_MS = 500, HIRES_W = 1280, LIVE_MS = 1000, LIVE_W = 640;
// Barge-in (see bargein.js): while the agent speaks, the model hears silence from this page, so
// echo and small noises cannot cut it off. Sustained speech opens the gate: the agent's volume dips
// at once and the last ~0.4 s of mic audio is sent so the user's first words are not lost. If the
// model confirms, the rest of the reply is dropped; if not, the volume comes back (no stop-start).
// Confirm window: measured gate-open -> model "interrupted" ≈ 0.8 s; 2 s leaves margin.
const BARGE_CONFIRM_MS = 2000, PREROLL_CHUNKS = 10, DUCK_LEVEL = 0.12;
let sweepId, ws, audioCtx, playCtx, playGain, micStream, camStream, processor, source, nextPlay = 0;
let playing = new Set(), uploading = false, timers = [];
let barge = new BargeIn(), bargeTimer = null, preroll = [];
let currency = "INR";

function money(amount) {
  try { return new Intl.NumberFormat(undefined, { style: "currency", currency, maximumFractionDigits: 0 }).format(amount); }
  catch { return `${Math.round(amount).toLocaleString()} ${currency}`; }
}
let unit = "A", shelf = 0, wall = 0, lastSpeaker = null, lastLine = null;
const canvas = document.createElement("canvas");

let failed = false;
function setStatus(t) { $("#status").textContent = t; }
function showError(t) { failed = true; $("#error").textContent = t; }
function showWarning(t) { $("#warning").textContent = t; }
function esc(s) { return String(s ?? "").replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c])); }

// Check the setup before letting anyone start: key, models, secure connection for the camera.
async function checkSetup() {
  if (!window.isSecureContext) {
    $("#banner").textContent = "The camera only works over a secure connection. On a phone, start the app with START_PHONE and open the https:// address it shows.";
    setStatus("Not ready");
    return;
  }
  let h;
  try { h = await (await fetch("/api/health")).json(); }
  catch { $("#banner").textContent = "Cannot reach the app. Is the black START window still open?"; setTimeout(checkSetup, 3000); return; }
  $("#models").textContent = `${h.live_model} · ${h.vision_model}`;
  $("#setModels").textContent = `${h.live_model} · ${h.vision_model}`;
  setLocaleText(h.country, h.currency);
  // On the computer, offer the phone address; on the phone itself (https) there is nothing to switch to.
  if (h.phone_url && location.protocol !== "https:") {
    $("#phoneUrl").textContent = h.phone_url; $("#phoneUrl").href = h.phone_url;
    $("#phoneQr").src = "/api/phone-qr.svg";
    $("#phone").hidden = false;
    $("#usePhone").hidden = false;
  }
  if (h.key.state === "checking") { setStatus("Checking your Google API key…"); setTimeout(checkSetup, 1500); return; }
  if (h.key.state === "missing" || h.key.state === "invalid") {
    $("#banner").textContent = h.key.detail + " Close the black START window and double-click the start script again; it will ask for the key.";
    setStatus("Not ready");
    return;
  }
  if (h.key.state === "unknown") showWarning("Could not confirm the API key: " + h.key.detail);
  $("#banner").textContent = "";
  $("#start").disabled = false;
  setStatus("Ready when you are");
}

function stopCapture() {
  timers.forEach(clearInterval); timers = [];
  processor?.disconnect(); source?.disconnect();
  micStream?.getTracks().forEach((t) => t.stop());
  document.body.classList.remove("live");
  $("#talk").textContent = "";
  $("#sideState").textContent = "Call ended";
}

const COUNTRY_NAMES = { IN: "India", US: "United States", GB: "United Kingdom", DE: "Germany", AU: "Australia" };
function setLocaleText(country, cur) {
  currency = cur;
  ["#locale", "#profileLocale2", "#setLocale"].forEach((s) => ($(s).textContent = `${country} · ${cur}`));
  $("#profileLocale").textContent = `${COUNTRY_NAMES[country] || country} · ${cur}`;
}

// One line per agent tool call for the activity table: [text, status].
function toolActivity(m) {
  const a = m.args || {}, r = m.result || {};
  const status = r.ok === false ? (m.name === "end_sweep" ? "pending" : "failed") : "completed";
  const text = {
    set_locale: () => r.ok === false ? "Tried to set the valuation before you confirmed it (refused)" : `Set valuation to ${r.country} · ${r.currency}`,
    start_wall: () => `Started wall ${r.label}`,
    start_shelf: () => `Started shelf ${r.label}`,
    start_front_view: () => `Started front view ${r.label}`,
    start_room_view: () => "Started the room view",
    record_fact: () => `Noted “${String(a.kind).replace(/_/g, " ")}” for ${a.target || "the last book"}`,
    add_customer_correction: () => r.ok === false ? `Could not match your correction (“${a.target}”)` : `Recorded your correction for ${a.target}`,
    build_customer_packet: () => "Built the customer-advised packet",
    end_sweep: () => r.ok === false ? "Asked about shelves that may have been missed" : "Ended the sweep, building the claim packet",
  }[m.name];
  return text ? [text(), status] : null;
}

const STEP_NAMES = { wall: "Wall", shelf: "Shelf", front: "Front view", room: "Room view" };
function showSegment(kind, label) {
  $("#segment").textContent = kind === "room" || kind === "front" && !label ? STEP_NAMES[kind]
    : `${STEP_NAMES[kind] || kind} ${label}`;
}

async function start() {
  $("#start").disabled = true;
  failed = false; $("#error").textContent = ""; showWarning("");
  setStatus("Starting…");
  try {
    camStream = await navigator.mediaDevices.getUserMedia({ video: { facingMode: "environment", width: { ideal: 1920 }, height: { ideal: 1080 } } });
    micStream = await navigator.mediaDevices.getUserMedia({ audio: { echoCancellation: true, noiseSuppression: true, autoGainControl: true } });
  } catch (e) {
    showError(e.name === "NotAllowedError" ? "Camera or microphone permission was refused. Allow both for this site (lock icon in the address bar) and press Start again."
      : `Camera or microphone not available: ${e.message}`);
    $("#start").disabled = false;
    return;
  }
  $("#preview").srcObject = camStream;
  $("#stage").classList.add("on");
  UI.startClock();
  UI.log("You", "Started the sweep", "completed");
  const r = await fetch("/api/sweeps", { method: "POST", headers: { "content-type": "application/json" },
    body: JSON.stringify({ device: navigator.userAgent.slice(0, 120) }) });
  sweepId = (await r.json()).id;
  audioCtx = new AudioContext();               // microphone
  // Agent voice (24 kHz from the model): through the avatar's audio graph so its lips follow it
  // (avatar.js), or straight out if the avatar is not up within a few seconds.
  const face = await Promise.race([window.AgentAvatar?.ready, new Promise((r) => setTimeout(r, 4000))]);
  playCtx = face?.ctx || new AudioContext({ sampleRate: 24000 });
  playGain?.disconnect();
  playGain = playCtx.createGain();                   // volume ducking for barge-in
  playGain.connect(face?.input || playCtx.destination);
  nextPlay = 0;                                      // times from a previous call's context do not apply
  await audioCtx.resume(); await playCtx.resume();
  ws = new WebSocket(`${location.protocol === "https:" ? "wss" : "ws"}://${location.host}/ws/${sweepId}`);
  ws.onmessage = (e) => handle(JSON.parse(e.data));
  ws.onclose = () => {
    stopCapture();
    if (!failed && !$("#packet").innerHTML) setStatus("Call ended.");
    $("#start").disabled = false;
  };
  ws.onopen = () => {
    source = audioCtx.createMediaStreamSource(micStream);
    processor = audioCtx.createScriptProcessor(2048, 1, 1);  // ~43 ms chunks: quick barge-in
    processor.onaudioprocess = (ev) => {
      ev.outputBuffer.getChannelData(0).fill(0);
      if (ws.readyState !== 1) return;
      const input = ev.inputBuffer.getChannelData(0);
      const pcm = resample(input, audioCtx.sampleRate, 16000);
      const event = barge.update(rmsOf(input), input.length / audioCtx.sampleRate * 1000, agentSpeaking());
      if (!barge.micOpen) {
        // Guarded: remember recent audio, send silence so the model cannot hear the agent's echo.
        preroll.push(pcm); if (preroll.length > PREROLL_CHUNKS) preroll.shift();
        ws.send(JSON.stringify({ type: "audio", data: b64(new Int16Array(pcm.length).buffer) }));
        return;
      }
      if (event === "barge") {
        localBargeIn();
        preroll.forEach((p) => ws.send(JSON.stringify({ type: "audio", data: b64(p.buffer) })));
      }
      preroll = [];
      ws.send(JSON.stringify({ type: "audio", data: b64(pcm.buffer) }));
    };
    source.connect(processor); processor.connect(audioCtx.destination);
    setStatus("Connecting to the voice agent…");
  };
}

function onReady(model) {
  // Frames only start flowing once the voice agent is really connected.
  timers.push(setInterval(sendHires, HIRES_MS), setInterval(sendLive, LIVE_MS));
  document.querySelectorAll("[data-seg], #end, #wide").forEach((b) => (b.disabled = false));
  document.body.classList.add("live");
  setStatus("Live: talk to the agent. It will guide you shelf by shelf.");
  $("#sideState").textContent = "Live call";
  UI.log("System", `Voice agent connected (${model})`, "completed");
  showTalk();
}

function grab(width, quality) {
  const v = $("#preview");
  if (!v.videoWidth) return null;
  canvas.width = width; canvas.height = Math.round(v.videoHeight * width / v.videoWidth);
  canvas.getContext("2d").drawImage(v, 0, 0, canvas.width, canvas.height);
  return canvas.toDataURL("image/jpeg", quality);
}

async function sendHires() {
  if (uploading || !sweepId) return;
  const url = grab(Math.min(HIRES_W, $("#preview").videoWidth || HIRES_W), 0.88);
  if (!url) return;
  uploading = true;
  try {
    const blob = await (await fetch(url)).blob();
    await fetch(`/api/sweeps/${sweepId}/frames`, { method: "POST", body: blob, headers: { "content-type": "image/jpeg" } });
  } finally { uploading = false; }
}

function sendLive() {
  if (!ws || ws.readyState !== 1) return;
  const url = grab(LIVE_W, 0.6);
  if (url) ws.send(JSON.stringify({ type: "video", data: url.split(",")[1] }));
}

async function widest() {
  // Some Android phones expose the ultra-wide lens as zoom < 1. Helps fit a whole wall in frame.
  const track = camStream?.getVideoTracks()[0];
  const caps = track?.getCapabilities?.() || {};
  if (caps.zoom) { await track.applyConstraints({ advanced: [{ zoom: caps.zoom.min }] }); setStatus(`Zoom ${caps.zoom.min}`); }
  else setStatus("This camera does not expose zoom");
}

async function segment(kind) {
  let label = kind;
  if (kind === "wall") label = String(++wall);
  if (kind === "unit") { unit = String.fromCharCode(unit.charCodeAt(0) + (shelf ? 1 : 0)); shelf = 0; kind = "shelf"; }
  if (kind === "shelf") label = `${unit}${++shelf}`;
  if (kind === "front") label = "";  // server numbers them F1, F2, ...
  const seg = await (await fetch(`/api/sweeps/${sweepId}/segment`, { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify({ kind, label }) })).json();
  showSegment(seg.kind, seg.label);
  UI.log("You", `Pressed ${STEP_NAMES[seg.kind] || seg.kind}${seg.label && seg.kind !== "room" ? " " + seg.label : ""}`, "completed");
}

function handle(m) {
  if (m.type === "audio") play(m.data);
  else if (m.type === "interrupted") confirmBargeIn();
  else if (m.type === "transcript") transcript(m.speaker, m.text);
  else if (m.type === "coach") {
    $("#coach").textContent = m.text;
    setTimeout(() => { if ($("#coach").textContent === m.text) $("#coach").textContent = ""; }, 8000);
    UI.alert("pending", m.text);
    UI.log("System", m.text, "pending");
  }
  else if (m.type === "frame") {
    const q = m.quality, label = { blurry: "Blurry", glare: "Glare", dark: "Too dark" };
    $("#quality").textContent = q.ok ? "Sharp" : q.problems.map((p) => label[p] || p).join(" · ");
    $("#quality").dataset.state = q.ok ? "ok" : "bad";
    $("#quality").title = `blur ${q.blur} · glare ${(q.glare * 100).toFixed(1)}% · brightness ${q.brightness}`;
    $("#cQueue").textContent = m.queue;
  }
  else if (m.type === "inventory") inventory(m);
  else if (m.type === "tool") {
    if (m.result?.kind) showSegment(m.result.kind, m.result.label);
    if (m.name === "start_shelf" && m.result?.label) { unit = m.result.label[0]; shelf = parseInt(m.result.label.slice(1)) || shelf; }
    if (m.name === "start_wall") wall = parseInt(m.result?.label) || wall;
    if (m.name === "set_locale" && m.result?.ok) setLocaleText(m.result.country, m.result.currency);
    const row = toolActivity(m);
    if (row) UI.log("Agent", ...row);
  }
  else if (m.type === "status") setStatus(m.text);
  else if (m.type === "packet") {
    setStatus("Sweep complete. Your claim packet is ready below.");
    document.body.classList.add("done");
    loadPacketLines(m.json);
    $("#packetEmpty").hidden = true;
    $("#corrFieldset").disabled = false;
    $("#packet").innerHTML = `<div><b>Verified claim packet</b> <span class="status completed">Ready in ${Math.round(m.time_to_packet_s)}s</span></div>
      <p>${esc(m.summary.replace(/^\[PACKET READY\]\s*/, ""))}</p>
      <div class="actions"><a href="${m.url}" target="_blank">Open report</a><a href="${m.json}" download>Download JSON</a></div>`;
    UI.log("System", "Built the verified claim packet", "completed");
    UI.alert("completed", "Your claim packet is ready.");
  }
  else if (m.type === "corrections") {
    renderCorrections(m.corrections);
    const c = m.corrections[m.corrections.length - 1];
    if (c) UI.log("You", `Correction for ${c.target === "new" ? "a missed book" : c.target}: “${c.comment}”`, "pending");
  }
  else if (m.type === "customer_packet") {
    $("#packetEmpty").hidden = true;
    $("#customerPacket").innerHTML = `<div><b>Customer-advised packet</b> <span class="status pending">Version ${m.version} · stated by you</span></div>
      <p>${esc(m.summary.replace(/^\[CUSTOMER PACKET READY\]\s*/, ""))}</p>
      <div class="actions"><a href="${m.url}" target="_blank">Open report</a><a href="${m.json}" download>Download JSON</a></div>`;
    UI.log("System", `Built the customer-advised packet (version ${m.version})`, "completed");
    UI.alert("completed", "Customer-advised packet is ready.");
  }
  else if (m.type === "warning") { showWarning(m.text); UI.alert("failed", m.text); UI.log("System", m.text, "failed"); }
  else if (m.type === "error") { showError(m.message); setStatus("Stopped"); stopCapture(); UI.alert("failed", m.message); UI.log("System", m.message, "failed"); }
  else if (m.type === "ready") onReady(m.model);
}

// ---- Customer corrections -> customer-advised packet ----
async function loadPacketLines(url) {
  const p = await (await fetch(url)).json();
  const opt = (v, t) => `<option value="${esc(v)}">${esc(t)}</option>`;
  $("#cTarget").innerHTML = `<option value="" disabled selected>Choose…</option>`
    + p.books.map((b) => opt(b.id, `${b.id} · ${b.title || (b.spine_text_read.title_text ? "“" + b.spine_text_read.title_text + "”" : "unidentified")}`)).join("")
    + p.items.map((i) => opt(i.id, `${i.id} · ${i.description}`)).join("")
    + opt("new", "A book that was missed");
}

function renderCorrections(list) {
  $("#corrList").innerHTML = list.map((c) => {
    const parts = [c.title && `title “${esc(c.title)}”`, c.author && `author ${esc(c.author)}`,
      c.replacement_cost != null && `new ${money(c.replacement_cost)}`, c.used_value != null && `used ${money(c.used_value)}`].filter(Boolean);
    return `<li><b>${c.target === "new" ? "Missed book" : esc(c.target)}</b> · ${parts.join(" · ") || "comment"}<div class="sub">“${esc(c.comment)}”</div></li>`;
  }).join("");
  $("#buildCustomer").disabled = !list.length;
}

$("#corrForm").onsubmit = async (e) => {
  e.preventDefault();
  $("#corrMsg").textContent = "";
  const body = { target: $("#cTarget").value, title: $("#cTitle").value, author: $("#cAuthor").value,
    replacement_cost: $("#cNew").value, used_value: $("#cUsed").value, comment: $("#cComment").value };
  const r = await (await fetch(`/api/sweeps/${sweepId}/corrections`, { method: "POST",
    headers: { "content-type": "application/json" }, body: JSON.stringify(body) })).json();
  if (!r.ok) { $("#corrMsg").textContent = r.error; return; }
  e.target.reset();
};

$("#buildCustomer").onclick = async () => {
  $("#buildCustomer").disabled = true;
  const r = await (await fetch(`/api/sweeps/${sweepId}/customer-packet`, { method: "POST" })).json();
  if (!r.ok) $("#corrMsg").textContent = r.error;
  $("#buildCustomer").disabled = false;
};

$("#say").onsubmit = (e) => {
  e.preventDefault();
  const text = $("#sayText").value.trim();
  if (!text) return;
  if (ws?.readyState !== 1) { showWarning("The voice call has ended. Use the corrections form instead."); return; }
  ws.send(JSON.stringify({ type: "text", text }));
  transcript("you", text); lastLine = null;
  $("#sayText").value = "";
};

const BOOK_STATUS = { identified: ["completed", "Identified"], unidentified: ["failed", "Unidentified"], reading: ["pending", "Reading"] };

function inventory(m) {
  $("#warnings").textContent = m.warnings.join(" · ");
  $("#books").innerHTML = m.books.length ? m.books.map((b) => {
    const [cls, label] = BOOK_STATUS[b.status] || ["pending", b.status];
    const name = b.title ? `<div class="title">${esc(b.title)}</div><div class="sub">${esc(b.author)}</div>`
      : `<div class="sub">${b.read ? "Spine reads “" + esc(b.read) + "” (no match, not guessed)" : "Spine not legible"}</div>`;
    return `<tr data-text="${esc(`${b.title} ${b.author} ${b.read} ${b.shelf}`.toLowerCase())}">
      <td>${name}<div class="sub show-sm">Shelf ${esc(b.shelf)} · ${b.position}</div></td><td class="sub hide-sm">${esc(b.shelf)} · ${b.position}</td>
      <td><span class="status ${cls}">${label}</span></td>
      <td class="num sub hide-sm">${b.h != null ? `${b.h} × ${b.t}` : "—"}</td>
      <td class="num">${b.price != null ? `<b>${money(b.price)}</b>` : `<span class="sub">—</span>`}</td></tr>`;
  }).join("") : `<tr class="empty"><td colspan="5">Books appear here as the camera reads their spines.</td></tr>`;
  $("#items").innerHTML = m.items.length ? m.items.map((i) => `<li data-text="${esc(`${i.description} ${i.category} ${i.brand_model}`.toLowerCase())}">
      <div><div class="title">${esc(i.description)}</div><div class="sub">${esc(i.category)}${i.brand_model ? " · " + esc(i.brand_model) : ""}</div></div>
      <span class="sub">${esc(i.id.replace("item-", "#"))}</span></li>`).join("") : `<li class="empty-li">Nothing yet.</li>`;
  $("#walls").innerHTML = m.walls.length ? m.walls.map((w) => `<li><span class="title">Wall ${esc(w.label)}</span>
      <span class="sub">${w.width_m.toFixed(2)} × ${w.height_m.toFixed(2)} m</span></li>`).join("") : `<li class="empty-li">No wall measured yet.</li>`;
  UI.metrics(m, money);
  UI.applyFilter();
}

function transcript(speaker, text) {
  if (speaker !== lastSpeaker || !lastLine) {
    lastLine = document.createElement("p");
    lastLine.className = speaker;
    lastLine.innerHTML = `<b>${speaker}:</b> <span></span>`;
    $("#transcript").appendChild(lastLine);
    lastSpeaker = speaker;
  }
  lastLine.querySelector("span").textContent += text;
  $("#transcript").scrollTop = 1e9;
}

function play(data) {
  const bytes = Uint8Array.from(atob(data), (c) => c.charCodeAt(0));
  const pcm = new Int16Array(bytes.buffer);
  const buf = playCtx.createBuffer(1, pcm.length, 24000);
  const ch = buf.getChannelData(0);
  for (let i = 0; i < pcm.length; i++) ch[i] = pcm[i] / 32768;
  const src = playCtx.createBufferSource();
  src.buffer = buf; src.connect(playGain);
  const at = Math.max(playCtx.currentTime, nextPlay);
  src.start(at); nextPlay = at + buf.duration;
  playing.add(src); src.onended = () => { playing.delete(src); showTalk(); };
  showTalk();
}

function agentSpeaking() { return playing.size > 0; }

function setVolume(level, seconds) {
  playGain?.gain.setTargetAtTime(level, playCtx.currentTime, seconds);
}

function showTalk() {
  if (!document.body.classList.contains("live")) return;
  const state = bargeTimer ? "paused" : playing.size ? "speaking" : "listening";
  $("#talk").dataset.state = state;
  $("#talk").textContent = { paused: "Go ahead, the agent is listening", speaking: "Agent speaking · talk to interrupt", listening: "Listening" }[state];
}

function localBargeIn() {
  // Dip the agent's voice at once; the model's own voice detection decides what happens next.
  if (!playing.size || bargeTimer) return;
  setVolume(DUCK_LEVEL, 0.03);
  ws?.readyState === 1 && ws.send(JSON.stringify({ type: "barge_in" }));
  bargeTimer = setTimeout(() => {   // not confirmed: it was not speech, bring the voice back smoothly
    bargeTimer = null;
    barge.reject();
    setVolume(1, 0.08);
    showTalk();
  }, BARGE_CONFIRM_MS);
  showTalk();
}

function confirmBargeIn() {
  // The model stopped its reply: drop the rest of it and mark it in the transcript.
  clearTimeout(bargeTimer); bargeTimer = null;
  if (lastSpeaker === "agent" && lastLine) {
    lastLine.querySelector("span").textContent += " — (interrupted)";
    lastLine = null;
  }
  stopPlayback();
}

function stopPlayback() {
  playing.forEach((s) => { try { s.stop(); } catch {} });
  playing.clear(); nextPlay = 0;
  setVolume(1, 0.01);
  showTalk();
}

function resample(input, inRate, outRate) {
  const ratio = inRate / outRate, n = Math.floor(input.length / ratio), out = new Int16Array(n);
  for (let i = 0; i < n; i++) {
    const idx = i * ratio, a = Math.floor(idx), b = Math.min(a + 1, input.length - 1), w = idx - a;
    const s = Math.max(-1, Math.min(1, input[a] * (1 - w) + input[b] * w));
    out[i] = s < 0 ? s * 0x8000 : s * 0x7fff;
  }
  return out;
}

function b64(buf) { let s = ""; const b = new Uint8Array(buf); for (let i = 0; i < b.length; i++) s += String.fromCharCode(b[i]); return btoa(s); }

$("#start").onclick = () => start().catch((e) => { setStatus(`Could not start: ${e.message}`); $("#start").disabled = false; });
$("#end").onclick = async () => {
  timers.forEach(clearInterval); timers = []; $("#end").disabled = true; setStatus("Ending sweep, building packet (up to a few minutes)…");
  const r = await fetch(`/api/sweeps/${sweepId}/finish`, { method: "POST" });
  if (!r.ok) showError("Building the packet failed. The details are in data/app.log.");
};
$("#wide").onclick = () => widest().catch((e) => setStatus(e.message));
$("#copyUrl").onclick = async () => {
  try { await navigator.clipboard.writeText($("#phoneUrl").textContent); $("#copyUrl").textContent = "Copied"; }
  catch { $("#copyUrl").textContent = "Select & copy"; }
  setTimeout(() => ($("#copyUrl").textContent = "Copy"), 1500);
};
document.querySelectorAll("[data-seg]").forEach((b) => (b.onclick = () => segment(b.dataset.seg)));
checkSetup();
