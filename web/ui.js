// Dashboard behaviour around the call: navigation, menus, notifications, activity log,
// KPI cards, shelf chart and search. app.js (the call itself) reports events through UI.*.
// Display only: every figure shown here comes from the server; the packet stays authoritative.
const UI = (() => {
  const $ = (s) => document.querySelector(s);
  const $$ = (s) => [...document.querySelectorAll(s)];
  const esc = (s) => String(s ?? "").replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
  const icon = (name) => `<svg class="ic" aria-hidden="true"><use href="#i-${name}"/></svg>`;

  let started = null;
  const activity = [], notes = [];
  let unread = 0;
  const history = [];                 // [{t, v}] replacement value over time, for the sparkline and trend
  const seenBooks = new Map(), seenItems = new Set(), seenWalls = new Set();
  let chartMode = "value", lastBooks = [], fmt = (v) => String(Math.round(v));

  function clock() {
    const s = started ? Math.max(0, Math.round((Date.now() - started) / 1000)) : 0;
    return `${String(Math.floor(s / 60)).padStart(2, "0")}:${String(s % 60).padStart(2, "0")}`;
  }

  // ---- Activity table ------------------------------------------------------------
  const WHO = { Agent: ["bot", "agent"], You: ["user", "you"], System: ["cpu", "system"] };
  function log(who, action, status = "completed") {
    activity.unshift({ who, action, status, time: clock() });
    if (activity.length > 80) activity.pop();
    $("#activity").innerHTML = activity.map((a) => `<tr>
      <td><span class="who ${WHO[a.who][1]}" title="${a.who}">${icon(WHO[a.who][0])}<span class="name">${a.who}</span></span></td>
      <td>${esc(a.action)}</td>
      <td><span class="status ${a.status}">${a.status[0].toUpperCase() + a.status.slice(1)}</span></td>
      <td class="num sub">${a.time}</td></tr>`).join("");
  }

  // ---- Notifications ----------------------------------------------------------------
  function alert(kind, text) {
    notes.unshift({ kind, text, time: clock() });
    if (notes.length > 25) notes.pop();
    unread += 1;
    $("#notes").innerHTML = notes.map((n) => `<li><span class="status ${n.kind}">${{ completed: "Done", pending: "Tip", failed: "Problem" }[n.kind]}</span>
      <span>${esc(n.text)}</span><span class="cap">${n.time}</span></li>`).join("");
    $("#bellCount").textContent = unread > 9 ? "9+" : unread;
    $("#bellCount").hidden = $("#bellPanel").hidden === false;
  }

  // ---- KPIs, sparkline, chart -----------------------------------------------------
  function metrics(m, money) {
    fmt = money;
    const books = m.books;
    const value = books.reduce((s, b) => s + (b.price || 0), 0);
    const unreadable = books.filter((b) => b.status === "unidentified").length;
    $("#cBooks").textContent = m.counts.books;
    $("#cIdent").textContent = m.counts.identified;
    $("#cItems").textContent = m.counts.items;
    $("#cWalls").textContent = m.counts.walls;
    $("#kUnid").textContent = unreadable;
    $("#kRate").textContent = m.counts.books ? `${Math.round(100 * m.counts.identified / m.counts.books)}%` : "0%";
    $("#kValue").textContent = money(value);

    const now = Date.now();
    if (!history.length || history[history.length - 1].v !== value) history.push({ t: now, v: value });
    if (history.length > 40) history.shift();
    const ref = [...history].reverse().find((p) => now - p.t >= 60000) || history[0];
    const delta = value - ref.v;
    $("#kTrend").className = `trend ${delta > 0 ? "up" : "flat"}`;
    $("#kTrend use").setAttribute("href", delta > 0 ? "#i-up" : "#i-minus");
    $("#kTrendText").textContent = delta > 0 ? `+${money(delta)} last minute` : "no change yet";
    spark();

    // New lines -> activity log (once each).
    books.forEach((b) => {
      const key = `${b.shelf}-${b.position}`, prev = seenBooks.get(key) || {};
      if (b.status === "identified" && prev.status !== "identified")
        log("System", `Identified “${b.title}” on ${b.shelf} · ${b.position}`, "completed");
      else if (b.status === "unidentified" && !prev.status)
        log("System", `Spine ${b.shelf} · ${b.position} unreadable, logged as unidentified`, "failed");
      if (b.price != null && prev.price == null && b.title)
        log("System", `Priced “${b.title}” at ${money(b.price)} from a shop listing`, "completed");
      seenBooks.set(key, { status: b.status, price: b.price });
    });
    m.items.forEach((i) => { if (!seenItems.has(i.id)) { seenItems.add(i.id); log("System", `Found ${i.description} (${i.category})`, "completed"); } });
    m.walls.forEach((w) => { if (!seenWalls.has(w.label)) { seenWalls.add(w.label);
      log("System", `Measured wall ${w.label}: ${w.width_m.toFixed(2)} × ${w.height_m.toFixed(2)} m`, "completed"); } });

    lastBooks = books;
    chart();
  }

  function spark() {
    const pts = history.map((p) => p.v);
    if (pts.length < 2) { $("#kSpark").innerHTML = ""; return; }
    const max = Math.max(...pts) || 1;
    const xy = pts.map((v, i) => [(i / (pts.length - 1)) * 100, 36 - (v / max) * 32]);
    const line = xy.map(([x, y]) => `${x.toFixed(1)},${y.toFixed(1)}`).join(" ");
    $("#kSpark").innerHTML = `<defs><linearGradient id="sg" x1="0" y1="0" x2="0" y2="1">
      <stop offset="0" stop-color="#10B981" stop-opacity=".35"/><stop offset="1" stop-color="#10B981" stop-opacity="0"/></linearGradient></defs>
      <polygon points="0,40 ${line} 100,40" fill="url(#sg)"/>
      <polyline points="${line}" fill="none" stroke="#10B981" stroke-width="2" vector-effect="non-scaling-stroke" stroke-linejoin="round"/>`;
  }

  function chart() {
    const shelves = new Map();
    lastBooks.forEach((b) => {
      const s = shelves.get(b.shelf) || { value: 0, books: 0 };
      s.value += b.price || 0; s.books += 1;
      shelves.set(b.shelf, s);
    });
    const rows = [...shelves.entries()].sort((a, b) => a[0].localeCompare(b[0], undefined, { numeric: true }));
    if (!rows.length) { $("#chart").innerHTML = `<p class="chart-empty">Bars appear as shelves are read and priced.</p>`; return; }
    const max = Math.max(...rows.map(([, s]) => s[chartMode])) || 1;
    $("#chart").innerHTML = rows.map(([shelf, s]) => {
      const label = `${shelf}: ${fmt(s.value)} · ${s.books} book${s.books === 1 ? "" : "s"}`;
      return `<button type="button" class="bar" style="--h:${Math.max(3, (s[chartMode] / max) * 100)}%" aria-label="${esc(label)}">
        <span class="tip">${esc(label)}</span><span class="x">${esc(shelf)}</span></button>`;
    }).join("");
  }

  // ---- Search (books table + item list) ----------------------------------------------
  function applyFilter() {
    const q = $("#search").value.trim().toLowerCase();
    $$("#books tr[data-text], #items li[data-text]").forEach((el) => { el.hidden = !!q && !el.dataset.text.includes(q); });
  }

  // ---- Navigation, menus, theme ----------------------------------------------------------
  function setNav(open) {
    document.body.classList.toggle("nav-open", open);
    $("#menuBtn").setAttribute("aria-expanded", open);
    if (open) $(".nav")?.focus();
  }

  function toggle(btn, panel, force) {
    const open = force ?? panel.hidden;
    $$(".panel").forEach((p) => { p.hidden = true; });
    $$("[aria-controls$='Panel']").forEach((b) => b.setAttribute("aria-expanded", "false"));
    panel.hidden = !open;
    btn.setAttribute("aria-expanded", open);
    if (open && panel.id === "bellPanel") { unread = 0; $("#bellCount").hidden = true; }
  }

  function setTheme(light) {
    document.documentElement.dataset.theme = light ? "light" : "dark";
    $("#themeLabel").textContent = light ? "Dark mode" : "Light mode";
    try { localStorage.setItem("theme", light ? "light" : "dark"); } catch {}
  }

  function init() {
    try { setTheme(localStorage.getItem("theme") === "light"); } catch { setTheme(false); }
    $("#menuBtn").onclick = () => setNav(!document.body.classList.contains("nav-open"));
    $("#scrim").onclick = () => setNav(false);
    $$(".nav, [data-close]").forEach((a) => a.addEventListener("click", () => { setNav(false); toggle($("#profileBtn"), $("#profilePanel"), false); }));
    $("#bellBtn").onclick = (e) => { e.stopPropagation(); toggle($("#bellBtn"), $("#bellPanel")); };
    $("#profileBtn").onclick = (e) => { e.stopPropagation(); toggle($("#profileBtn"), $("#profilePanel")); };
    $$(".panel").forEach((p) => p.addEventListener("click", (e) => e.stopPropagation()));
    document.addEventListener("click", () => toggle($("#bellBtn"), $("#bellPanel"), false));
    document.addEventListener("keydown", (e) => {
      if (e.key !== "Escape") return;
      setNav(false);
      const open = $$(".panel").find((p) => !p.hidden);
      if (open) { toggle($(`[aria-controls='${open.id}']`), open, false); $(`[aria-controls='${open.id}']`).focus(); }
    });
    $("#themeBtn").onclick = () => setTheme(document.documentElement.dataset.theme !== "light");
    $("#newSweep").onclick = () => location.reload();
    $("#search").addEventListener("input", applyFilter);
    $("#search").addEventListener("keydown", (e) => { if (e.key === "Enter") $("#inventory").scrollIntoView({ behavior: "smooth" }); });
    [["#chartValue", "value"], ["#chartBooks", "books"]].forEach(([sel, mode]) => {
      $(sel).onclick = () => {
        chartMode = mode;
        $("#chartValue").setAttribute("aria-pressed", mode === "value");
        $("#chartBooks").setAttribute("aria-pressed", mode === "books");
        chart();
      };
    });
    // Highlight the section in view.
    const links = new Map($$(".nav").map((a) => [a.getAttribute("href").slice(1), a]));
    const spy = new IntersectionObserver((entries) => {
      entries.filter((e) => e.isIntersecting).forEach((e) => {
        links.forEach((a) => { a.classList.remove("active"); a.removeAttribute("aria-current"); });
        const a = links.get(e.target.id);
        if (a) { a.classList.add("active"); a.setAttribute("aria-current", "page"); }
      });
    }, { rootMargin: "-40% 0px -55% 0px" });
    links.forEach((_, id) => { const el = document.getElementById(id); if (el) spy.observe(el); });
  }

  init();
  return { log, alert, metrics, applyFilter, startClock: () => { started = Date.now(); } };
})();
