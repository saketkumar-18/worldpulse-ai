// WorldPulse AI frontend. No build step. All server data is inserted with textContent (no innerHTML).
"use strict";
const $ = (id) => document.getElementById(id);
const state = { page: 1, pageSize: 20 };

const PAGES = [
  ["Home / Daily Briefing", "#/", null],
  ["My Feed", "#/feed", null],
  ["World News", "#/category/world", "world"],
  ["India News", "#/category/india", "india"],
  ["Politics & Government", "#/category/politics", "politics"],
  ["Business & Economy", "#/category/business", "business"],
  ["Technology & AI", "#/category/technology", "technology"],
  ["Science & Research", "#/category/science", "science"],
  ["Health", "#/category/health", "health"],
  ["Sports", "#/category/sports", "sports"],
  ["Education & Careers", "#/category/education", "education"],
  ["Climate & Environment", "#/category/environment", "environment"],
  ["Entertainment & Culture", "#/category/entertainment", "entertainment"],
  ["State & Regional", "#/category/regional", "regional"],
  ["Breaking News", "#/breaking", null],
  ["Trending Topics", "#/trending", null],
  ["Newspaper Library", "#/newspapers", null],
  ["Publisher Directory", "#/publishers", null],
  ["Saved Articles", "#/saved", null],
  ["Reading History", "#/history", null],
  ["Preferences", "#/preferences", null],
  ["Notifications", "#/notifications", null],
  ["API & Integration Status", "#/status", null],
];

const AUTH_ROUTES = new Set(["#/feed", "#/saved", "#/history", "#/preferences", "#/notifications"]);
let currentUser = null;

async function api(path, opts = {}) {
  const init = { credentials: "same-origin", headers: { Accept: "application/json" } };
  if (opts.body !== undefined) {
    init.method = opts.method || "PUT";
    init.headers["Content-Type"] = "application/json";
    init.body = JSON.stringify(opts.body);
  } else if (opts.method) init.method = opts.method;
  const res = await fetch(path, init);
  if (res.status === 204) return null;
  let data = null;
  try { data = await res.json(); } catch (_) { /* non-JSON error bodies */ }
  if (!res.ok) {
    const detail = data && typeof data.detail === "string" ? data.detail : `Request failed (${res.status})`;
    const err = new Error(detail);
    err.status = res.status;
    throw err;
  }
  return data;
}

async function loadMe() {
  try { currentUser = await api("/api/me"); } catch (_) { currentUser = null; }
  renderAccountBox();
}

function renderAccountBox() {
  const box = $("account");
  box.replaceChildren();
  if (currentUser) {
    box.append(el("span", { class: "who" }, currentUser.display_name || currentUser.email));
    const out = el("button", { type: "button", class: "linkbtn" }, "Sign out");
    out.onclick = async () => { await api("/api/auth/logout", { method: "POST" }).catch(() => {}); currentUser = null; renderAccountBox(); location.hash = "#/"; };
    box.append(out);
  } else {
    const a = el("a", { href: "#/signin", class: "linkbtn" }, "Sign in");
    box.append(a);
  }
}

function saveButton(item) {
  if (!currentUser) return null;
  const btn = el("button", { type: "button", class: "linkbtn" }, item.saved ? "Saved" : "Save");
  btn.onclick = async () => {
    try {
      if (item.saved) await api(`/api/me/saved/${item.id}`, { method: "DELETE" });
      else await api(`/api/me/saved/${item.id}`, { method: "PUT" });
      item.saved = !item.saved;
      btn.textContent = item.saved ? "Saved" : "Save";
    } catch (e) { btn.textContent = e.message; }
  };
  return btn;
}

function markRead(item) {
  if (!currentUser) return;
  api(`/api/me/history/${item.id}`, { method: "POST" }).catch(() => {});
}

function el(tag, attrs = {}, ...children) {
  const node = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (k === "class") node.className = v;
    else if (k === "onclick") node.addEventListener("click", v);
    else if (k === "href") {
      if (v.startsWith("#/")) node.setAttribute("href", v);            // in-app route
      else if (/^https?:\/\//.test(v)) { node.href = v; node.rel = "noopener noreferrer nofollow"; node.target = "_blank"; }
    }
    else node.setAttribute(k, v);
  }
  for (const c of children) node.append(c instanceof Node ? c : document.createTextNode(c ?? ""));
  return node;
}

function fmtTime(iso) {
  if (!iso) return "time unknown";
  const d = new Date(iso);
  return d.toLocaleString("en-IN", { timeZone: "Asia/Kolkata", day: "2-digit", month: "short", hour: "2-digit", minute: "2-digit" }) + " IST";
}

function renderNav(active) {
  const nav = $("nav");
  nav.replaceChildren();
  for (const [label, href] of PAGES) {
    const a = el("a", { href, class: href === active ? "active" : "" }, label);
    nav.append(a);
  }
}

function storyCard(item, lang) {
  const s = item.summary || {};
  const useHi = lang === "hi" && s.headline_hi;
  const head = useHi ? s.headline_hi : (s.headline_en || item.title);
  const body = useHi ? s.detail_hi : s.detail_en;
  const badges = [];
  if (item.is_breaking) badges.push(el("span", { class: "badge breaking" }, "Breaking"));
  if (s.generator === "llm") badges.push(el("span", { class: "badge ai" }, "AI summary"));
  if (s.generator === "extractive_fallback") badges.push(el("span", { class: "badge ai" }, "Excerpt"));
  if (s.needs_review) badges.push(el("span", { class: "badge review" }, "Review"));
  if (item.publisher_count > 1) badges.push(el("span", { class: "badge" }, `${item.publisher_count} publishers`));
  const link = el("a", { href: item.url, onclick: () => markRead(item) }, "Read original at " + item.publisher);
  const save = saveButton(item);
  return el("article", { class: "card" },
    el("div", { class: "meta" }, ...badges),
    el("h3", {}, head),
    body ? el("p", { class: "summary" }, body) : el("p", { class: "summary" }, "Summary not yet generated. Open the original article."),
    el("div", { class: "meta" }, item.publisher, " · ", fmtTime(item.published_at)),
    el("div", { class: "meta" }, link, save ? " · " : "", save || ""),
  );
}

function pageHeader(title, note) {
  const children = [el("h1", {}, title)];
  if (note) children.push(el("p", { class: "dateline" }, note));
  return children;
}

function setPager(hasNext, onChange) {
  const p = $("pager");
  p.replaceChildren();
  const prev = el("button", { type: "button" }, "Previous");
  const next = el("button", { type: "button" }, "Next");
  prev.disabled = state.page <= 1;
  next.disabled = !hasNext;
  prev.onclick = () => { state.page -= 1; onChange(); };
  next.onclick = () => { state.page += 1; onChange(); };
  p.append(prev, el("span", {}, `Page ${state.page}`), next);
}

function currentFilters() {
  return { q: $("q").value.trim(), lang: $("lang").value, since: $("since").value };
}

function newsQuery(extra) {
  const f = currentFilters();
  const params = new URLSearchParams({ since_hours: f.since, page: state.page, page_size: state.pageSize, ...extra });
  if (f.lang) params.set("language", f.lang);
  if (f.q) params.set("q", f.q);
  return params.toString();
}

async function renderCategory(slug, label) {
  const view = $("view");
  view.replaceChildren(...pageHeader(label, "Showing stories from the last selected window, newest first."));
  try {
    const data = await api("/api/news?" + newsQuery({ category: slug, sort: "newest" }));
    view.append(renderListing(data, data.items.length ? "" : "No stories in this window. Try a longer date range."));
    setPager((data.page * data.page_size) < data.total, () => renderCategory(slug, label));
  } catch (e) { view.append(el("p", { class: "err" }, e.message)); }
}

function renderListing(data, emptyMsg) {
  if (!data.items.length) return el("div", { class: "empty" }, emptyMsg || "No stories found.");
  const grid = el("div", { class: "grid" });
  for (const item of data.items) grid.append(storyCard(item, $("lang").value));
  return grid;
}

async function renderHome() {
  const view = $("view");
  const today = new Date().toLocaleDateString("en-IN", { timeZone: "Asia/Kolkata", weekday: "long", day: "numeric", month: "long", year: "numeric" });
  view.replaceChildren(...pageHeader("Daily Briefing", today));
  try {
    const [top, brief, status] = await Promise.all([
      api("/api/top?limit=1"), api("/api/briefing/morning?per_section=3"), api("/api/health")]);
    view.append(el("p", { class: "dateline" }, "Last successful collection: " + (status.last_successful_collection ? fmtTime(status.last_successful_collection) : "none yet")));
    if (top.length) {
      const h = top[0], s = h.summary || {};
      view.append(el("section", { class: "hero" },
        el("div", { class: "meta" }, h.is_breaking ? el("span", { class: "badge breaking" }, "Breaking") : "", el("span", { class: "badge" }, "Top story")),
        el("h2", {}, s.headline_en || h.title),
        el("p", { class: "summary" }, s.detail_en || "Summary not yet generated. Open the original article."),
        el("div", { class: "meta" }, `${h.publisher} · ${fmtTime(h.published_at)} · `, el("a", { href: h.url }, "Original article"))));
    } else {
      view.append(el("div", { class: "empty" }, "No verified stories have been collected yet. An administrator must configure a news provider or enable RSS feeds, then run collection."));
    }
    for (const sec of brief.sections) {
      view.append(el("h2", {}, sec.label));
      view.append(el("div", { class: "grid" }, ...sec.stories.map((s) => storyCard(s, $("lang").value))));
    }
    if (!brief.sections.length) view.append(el("div", { class: "empty" }, "No briefing content yet."));
    view.append(el("p", { class: "disclaimer" }, "Summaries marked AI are machine-generated from the original article and can contain errors. Read the original article. Coverage counts are not independent verification."));
  } catch (e) { view.append(el("p", { class: "err" }, e.message)); }
}

async function renderBreaking() {
  const view = $("view");
  view.replaceChildren(...pageHeader("Breaking News", "Stories flagged as breaking in the last 6 hours, from verified provider data."));
  try { view.append(renderListing({ items: await api("/api/breaking") }, "No breaking stories right now.")); }
  catch (e) { view.append(el("p", { class: "err" }, e.message)); }
}

async function renderTrending() {
  const view = $("view");
  view.replaceChildren(...pageHeader("Trending Topics", "Events covered by at least two publishers in the last 24 hours. Coverage breadth is not proof of accuracy."));
  try {
    const rows = await api("/api/trending?hours=24");
    if (!rows.length) { view.append(el("div", { class: "empty" }, "No multi-publisher events yet.")); return; }
    const table = el("table", { class: "table" }, el("thead", {}, el("tr", {}, el("th", {}, "Event"), el("th", {}, "Publishers"), el("th", {}, "Articles"))));
    const body = el("tbody");
    for (const r of rows) body.append(el("tr", {}, el("td", {}, r.title), el("td", {}, String(r.publisher_count)), el("td", {}, String(r.article_count))));
    table.append(body);
    view.append(table);
  } catch (e) { view.append(el("p", { class: "err" }, e.message)); }
}

async function renderNewspapers() {
  const view = $("view");
  view.replaceChildren(...pageHeader("Newspaper Library", "Official e-paper links appear only after they are verified. Otherwise the official website is shown. Full editions are never copied here."));
  try {
    const rows = await api("/api/newspapers");
    const table = el("table", { class: "table" }, el("thead", {}, el("tr", {}, el("th", {}, "Newspaper"), el("th", {}, "Language"), el("th", {}, "Country"), el("th", {}, "Edition"), el("th", {}, "Open"))));
    const body = el("tbody");
    for (const p of rows) {
      const open = p.epaper_url ? el("a", { href: p.epaper_url }, "Official e-paper")
        : el("a", { href: p.website }, "Official website");
      body.append(el("tr", {}, el("td", {}, p.name), el("td", {}, p.language.toUpperCase()), el("td", {}, p.country),
        el("td", {}, p.epaper_status === "verified" ? "Verified link" : "Not verified"), el("td", {}, open)));
    }
    table.append(body);
    view.append(table);
  } catch (e) { view.append(el("p", { class: "err" }, e.message)); }
}

async function renderPublishers() {
  const view = $("view");
  view.replaceChildren(...pageHeader("Publisher Directory", "Publishers in the configured registry and their RSS status."));
  try {
    const rows = await api("/api/publishers");
    const table = el("table", { class: "table" }, el("thead", {}, el("tr", {}, el("th", {}, "Publisher"), el("th", {}, "Country"), el("th", {}, "Language"), el("th", {}, "RSS"), el("th", {}, "Website"))));
    const body = el("tbody");
    for (const p of rows) body.append(el("tr", {}, el("td", {}, p.name), el("td", {}, p.country), el("td", {}, p.language.toUpperCase()), el("td", {}, p.rss_status), el("td", {}, el("a", { href: p.website }, p.domain))));
    table.append(body);
    view.append(table);
  } catch (e) { view.append(el("p", { class: "err" }, e.message)); }
}

async function renderStatus() {
  const view = $("view");
  view.replaceChildren(...pageHeader("Integration Status", "Public, secret-free status. Credentials are never shown."));
  try {
    const rows = await api("/api/status");
    if (!rows.length) { view.append(el("div", { class: "empty" }, "No providers configured yet.")); return; }
    const table = el("table", { class: "table" }, el("thead", {}, el("tr", {}, el("th", {}, "Provider"), el("th", {}, "Kind"), el("th", {}, "Enabled"), el("th", {}, "Last success"), el("th", {}, "Articles"))));
    const body = el("tbody");
    for (const r of rows) body.append(el("tr", {}, el("td", {}, r.name), el("td", {}, r.kind), el("td", {}, r.enabled ? "yes" : "no"), el("td", {}, r.last_success_at ? fmtTime(r.last_success_at) : "never"), el("td", {}, String(r.articles_imported))));
    table.append(body);
    view.append(table);
  } catch (e) { view.append(el("p", { class: "err" }, e.message)); }
}

async function renderSearch() {
  const view = $("view");
  const f = currentFilters();
  view.replaceChildren(...pageHeader("Search Results", f.q ? `Query: "${f.q}"` : "Enter keywords above. Results are sorted newest first."));
  if (!f.q) return;
  try {
    const data = await api("/api/news?" + newsQuery({ sort: "newest" }));
    view.append(renderListing(data, "No matching stories in this date range."));
    setPager((data.page * data.page_size) < data.total, renderSearch);
  } catch (e) { view.append(el("p", { class: "err" }, e.message)); }
}

function renderSignIn(mode = "signin") {
  const view = $("view");
  const isRegister = mode === "register";
  view.replaceChildren(...pageHeader(isRegister ? "Create account" : "Sign in",
    "Accounts are optional. Browsing news never requires sign-in."));
  const err = el("p", { class: "err", role: "alert" });
  const email = el("input", { type: "email", autocomplete: "email", required: "", maxlength: "254", id: "authEmail" });
  const pw = el("input", { type: "password", autocomplete: isRegister ? "new-password" : "current-password", required: "", maxlength: "128", id: "authPw" });
  const name = el("input", { type: "text", maxlength: "80", id: "authName", autocomplete: "name" });
  const submit = el("button", { type: "submit", class: "primary" }, isRegister ? "Create account" : "Sign in");
  const form = el("form", { class: "authform" },
    el("label", {}, "Email", email),
    ...(isRegister ? [el("label", {}, "Display name (optional)", name)] : []),
    el("label", {}, isRegister ? "Password (10+ characters, letters and digits)" : "Password", pw),
    submit, err);
  form.onsubmit = async (e) => {
    e.preventDefault();
    err.textContent = "";
    submit.disabled = true;
    try {
      const body = isRegister
        ? { email: email.value, password: pw.value, display_name: name.value || null,
            timezone: Intl.DateTimeFormat().resolvedOptions().timeZone || "Asia/Kolkata" }
        : { email: email.value, password: pw.value };
      currentUser = await api(isRegister ? "/api/auth/register" : "/api/auth/login", { method: "POST", body });
      renderAccountBox();
      location.hash = "#/feed";
    } catch (ex) { err.textContent = ex.message; }
    finally { submit.disabled = false; pw.value = ""; }
  };
  const switchLink = el("a", { href: isRegister ? "#/signin" : "#/register" },
    isRegister ? "Already have an account? Sign in" : "New here? Create an account");
  view.append(form, el("p", { class: "dateline" }, switchLink));
}

async function renderFeed() {
  const view = $("view");
  view.replaceChildren(...pageHeader("My Feed", "Stories matching your preferred languages, categories, countries and follows."));
  try {
    const data = await api("/api/me/feed?" + new URLSearchParams({ page: state.page, page_size: state.pageSize }));
    view.append(renderListing(data, "Nothing matches your preferences yet. Set categories in Preferences or follow a topic."));
    setPager((data.page * data.page_size) < data.total, renderFeed);
  } catch (e) { view.append(el("p", { class: "err" }, e.message)); }
}

async function renderSaved() {
  const view = $("view");
  view.replaceChildren(...pageHeader("Saved Articles", "Stories you bookmarked."));
  try {
    const data = await api("/api/me/saved?" + new URLSearchParams({ page: state.page, page_size: state.pageSize }));
    data.items.forEach((i) => (i.saved = true));
    view.append(renderListing(data, "You have not saved any stories yet."));
    setPager((data.page * data.page_size) < data.total, renderSaved);
  } catch (e) { view.append(el("p", { class: "err" }, e.message)); }
}

async function renderHistory() {
  const view = $("view");
  view.replaceChildren(...pageHeader("Reading History", "Stories you opened from this account."));
  try {
    const data = await api("/api/me/history?" + new URLSearchParams({ page: state.page, page_size: state.pageSize }));
    view.append(renderListing(data, "No reading history yet."));
    setPager((data.page * data.page_size) < data.total, renderHistory);
  } catch (e) { view.append(el("p", { class: "err" }, e.message)); }
}

async function renderPreferences() {
  const view = $("view");
  view.replaceChildren(...pageHeader("Preferences", "Used for My Feed and the personal briefing."));
  let p;
  try { p = await api("/api/me/preferences"); } catch (e) { view.append(el("p", { class: "err" }, e.message)); return; }
  const cats = await api("/api/categories");
  const langEn = el("input", { type: "checkbox", value: "en" });
  const langHi = el("input", { type: "checkbox", value: "hi" });
  langEn.checked = p.languages.includes("en");
  langHi.checked = p.languages.includes("hi");
  const catBoxes = cats.map((c) => {
    const cb = el("input", { type: "checkbox", value: c.slug });
    cb.checked = p.categories.includes(c.slug);
    return [c.slug, cb, el("label", { class: "inline" }, cb, " " + c.label)];
  });
  const country = el("input", { type: "text", value: p.countries.join(", "), maxlength: "40", placeholder: "IN, GB, US" });
  const perSection = el("input", { type: "number", min: "1", max: "10", value: String(p.stories_per_section) });
  const err = el("p", { class: "err", role: "alert" });
  const ok = el("p", { class: "dateline" });
  const save = el("button", { type: "submit", class: "primary" }, "Save preferences");
  const form = el("form", { class: "authform" },
    el("fieldset", {}, el("legend", {}, "Languages"), el("label", { class: "inline" }, langEn, " English"), el("label", { class: "inline" }, langHi, " Hindi")),
    el("fieldset", {}, el("legend", {}, "Categories (checked ones are prioritised in the order listed)"), ...catBoxes.map((x) => x[2])),
    el("label", {}, "Countries (2-letter codes, comma separated)", country),
    el("label", {}, "Stories per section in briefing", perSection),
    save, err, ok);
  form.onsubmit = async (e) => {
    e.preventDefault();
    err.textContent = ""; ok.textContent = "";
    const languages = [langEn, langHi].filter((x) => x.checked).map((x) => x.value);
    const categories = catBoxes.filter(([, cb]) => cb.checked).map(([slug]) => slug);
    const countries = country.value.split(",").map((s) => s.trim()).filter(Boolean);
    try {
      await api("/api/me/preferences", { method: "PUT", body: { languages, categories, countries, stories_per_section: Number(perSection.value) } });
      ok.textContent = "Saved.";
    } catch (ex) { err.textContent = ex.message; }
  };
  view.append(form);
}

async function renderNotifications() {
  const view = $("view");
  view.replaceChildren(...pageHeader("Notifications", "Briefings by email. Times are in your account timezone. Email delivery requires the server to have SMTP configured."));
  let n;
  try { n = await api("/api/me/notifications"); } catch (e) { view.append(el("p", { class: "err" }, e.message)); return; }
  const emailOn = el("input", { type: "checkbox" }); emailOn.checked = n.email_enabled;
  const morningOn = el("input", { type: "checkbox" }); morningOn.checked = n.morning_enabled;
  const morningAt = el("input", { type: "time", value: n.morning_time });
  const eveningOn = el("input", { type: "checkbox" }); eveningOn.checked = n.evening_enabled;
  const eveningAt = el("input", { type: "time", value: n.evening_time });
  const err = el("p", { class: "err", role: "alert" }); const ok = el("p", { class: "dateline" });
  const form = el("form", { class: "authform" },
    el("label", { class: "inline" }, emailOn, " Send me briefings by email"),
    el("label", { class: "inline" }, morningOn, " Morning briefing at ", morningAt),
    el("label", { class: "inline" }, eveningOn, " Evening briefing at ", eveningAt),
    el("button", { type: "submit", class: "primary" }, "Save"), err, ok);
  form.onsubmit = async (e) => {
    e.preventDefault(); err.textContent = ""; ok.textContent = "";
    try {
      await api("/api/me/notifications", { method: "PUT", body: {
        email_enabled: emailOn.checked, morning_enabled: morningOn.checked, morning_time: morningAt.value,
        evening_enabled: eveningOn.checked, evening_time: eveningAt.value } });
      ok.textContent = "Saved.";
    } catch (ex) { err.textContent = ex.message; }
  };
  view.append(form);
}

async function route() {
  const hash = location.hash || "#/";
  if (hash === "#/signin" || hash === "#/register") {
    renderNav("#/");
    return renderSignIn(hash === "#/register" ? "register" : "signin");
  }
  if (AUTH_ROUTES.has(hash) && !currentUser) {
    renderNav(hash);
    const view = $("view");
    view.replaceChildren(...pageHeader("Sign in required", "This page needs an account."));
    view.append(el("p", {}, el("a", { href: "#/signin" }, "Sign in"), " or ", el("a", { href: "#/register" }, "create an account"), "."));
    return;
  }
  const page = PAGES.find(([, href]) => href === hash) || PAGES.find(([, href]) => hash.startsWith(href) && href !== "#/");
  let label = page ? page[0] : "Home / Daily Briefing";
  let active = page ? page[1] : "#/";
  if (hash.startsWith("#/category/")) {
    const slug = hash.split("/")[2];
    const hit = PAGES.find(([, href]) => href === "#/category/" + slug);
    if (hit) { label = hit[0]; active = hit[1]; }
    state.page = 1;
    renderNav(active);
    return renderCategory(slug, label);
  }
  state.page = 1;
  renderNav(active);
  if (hash === "#/search") { renderNav("#/"); return renderSearch(); }
  const map = { "#/": renderHome, "#/breaking": renderBreaking, "#/trending": renderTrending,
    "#/newspapers": renderNewspapers, "#/publishers": renderPublishers, "#/status": renderStatus,
    "#/feed": renderFeed, "#/saved": renderSaved, "#/history": renderHistory,
    "#/preferences": renderPreferences, "#/notifications": renderNotifications };
  return (map[hash] || renderHome)();
}

function init() {
  loadMe();
  $("menuBtn").onclick = () => $("sidebar").classList.toggle("open");
  $("searchForm").onsubmit = (e) => {
    e.preventDefault();
    state.page = 1;
    if (location.hash === "#/search") route(); else location.hash = "#/search";
  };
  for (const id of ["lang", "since"]) $(id).onchange = () => { state.page = 1; route(); };
  window.addEventListener("hashchange", () => { $("sidebar").classList.remove("open"); route(); });
  route();
}
init();
