/* File Prepare HF - single page UI. Plain JS, no build step. */
"use strict";

const A = window.APP;
const $view = document.getElementById("view");
const isRole = (role) => ({ viewer: 0, operator: 1, admin: 2 })[A.user.role] >= ({ viewer: 0, operator: 1, admin: 2 })[role];
let pollers = [];

// ------------------------------------------------------------------ helpers
function h(tag, attrs = {}, ...children) {
  const el = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs || {})) {
    if (v === null || v === undefined || v === false) continue;
    if (k === "class") el.className = v;
    else if (k.startsWith("on")) el.addEventListener(k.slice(2), v);
    else if (k === "html") el.innerHTML = v;
    else if (v === true) el.setAttribute(k, "");
    else el.setAttribute(k, v);
  }
  for (const c of children.flat(Infinity)) {
    if (c === null || c === undefined || c === false) continue;
    el.append(c instanceof Node ? c : document.createTextNode(String(c)));
  }
  return el;
}
// replaceChildren() that skips null/false (conditional children)
// text with http(s) links turned into clickable links (opened in a new tab)
function linkify(text) {
  const out = document.createDocumentFragment();
  let last = 0;
  for (const m of String(text).matchAll(/https?:\/\/[^\s"'<>|]+[^\s"'<>|.,;:!?)\]]/g)) {
    out.append(text.slice(last, m.index), h("a", { href: m[0], target: "_blank", rel: "noopener noreferrer" }, m[0]));
    last = m.index + m[0].length;
  }
  out.append(String(text).slice(last));
  return out;
}
function fill(el, ...kids) { el.replaceChildren(...kids.flat(Infinity).filter((k) => k !== null && k !== undefined && k !== false)); }
const fmtTime = (t) => (t ? new Date(t * 1000).toLocaleString() : "—");
const badge = (text, cls) => h("span", { class: `badge ${cls ?? text}` }, text);
// ms = 0 keeps the toast until it is clicked or closed
// default durations: errors 20 s, everything else 12 s ("input needed" toasts pass 30 s)
function toast(message, kind = "", ms = kind === "error" ? 20000 : 12000) {
  const t = h("div", { class: `toast ${kind}` }, message,
    h("button", { class: "small", style: "float:right;margin-left:10px;border:none", title: "Dismiss", onclick: () => t.remove() }, "✕"));
  document.getElementById("toasts").append(t);
  if (ms) setTimeout(() => t.remove(), ms);
  return t;
}
async function api(method, url, body) {
  const opts = { method, headers: { "X-CSRF-Token": A.csrf } };
  if (body !== undefined) { opts.headers["Content-Type"] = "application/json"; opts.body = JSON.stringify(body); }
  const res = await fetch(A.base + url, opts);
  if (res.status === 401) { location.href = A.base + "/login"; throw new Error("Not logged in"); }
  const data = res.headers.get("content-type")?.includes("json") ? await res.json() : {};
  if (!res.ok) {
    const err = new Error(data.detail || `HTTP ${res.status}`);
    err.errors = data.errors || [];
    throw err;
  }
  return data;
}
function getPath(obj, path) { return path.split(".").reduce((o, k) => (o == null ? undefined : o[k]), obj); }
function setPath(obj, path, value) {
  const keys = path.split("."); let o = obj;
  keys.slice(0, -1).forEach((k) => { if (o[k] == null || typeof o[k] !== "object") o[k] = {}; o = o[k]; });
  o[keys.at(-1)] = value;
}
function every(ms, fn) { fn(); const id = setInterval(fn, ms); pollers.push(id); }
function clearPollers() { pollers.forEach(clearInterval); pollers = []; }
function modal(content) {
  const back = h("div", { class: "modal-back", onclick: (e) => { if (e.target === back) back.remove(); } }, h("div", { class: "modal" }, content));
  document.body.append(back);
  return back;
}

// ------------------------------------------------------------------ generic form renderer
// Renders sections (from /api/forms/*) as tabs. Returns { el, values(), showErrors(errors) }.
function renderForm(sections, values, { secrets = {}, readOnly = false, keyPrefix = "" } = {}) {
  const state = structuredClone(values || {});
  const inputs = [];          // {field, getter, wrap}
  const tabButtons = [], panes = [];
  const root = h("div");
  const tabs = h("div", { class: "tabs" });
  root.append(tabs);

  function evalRule(rule) {
    if (!rule) return true;
    if (rule.includes("||")) return rule.split("||").some((r) => evalRule(r.trim()));
    const m = rule.match(/^([\w.]+)\s*(==|!=)\s*(.+)$/);
    const read = (p) => getPath(state, p);
    if (m) return (String(read(m[1])) === m[3].trim()) === (m[2] === "==");
    return !!read(rule.trim());
  }
  let initialised = false;
  function refresh() {
    inputs.forEach(({ field, wrap }) => wrap.classList.toggle("hidden", !evalRule(field.visible_if)));
    sections.forEach((s, i) => {
      const visible = evalRule(s.visible_if);
      const btn = tabButtons[i];
      if (visible && btn.classList.contains("hidden") && initialised) {
        btn.classList.add("new-tab");                       // highlight a tab that just appeared
        toast(`New settings tab: ${s.label}`, "", 4000);
      }
      btn.classList.toggle("hidden", !visible);
      if (!visible) btn.classList.remove("new-tab");
      if (!visible && btn.classList.contains("active")) select(0);
    });
    initialised = true;
  }
  function select(i) {
    tabButtons[i]?.classList.remove("new-tab");
    tabButtons.forEach((b, j) => b.classList.toggle("active", i === j));
    panes.forEach((p, j) => p.classList.toggle("hidden", i !== j));
  }
  const update = (key, value) => { setPath(state, key, value); refresh(); };

  sections.forEach((section, i) => {
    const btn = h("button", { type: "button", onclick: () => select(i) }, section.label);
    tabButtons.push(btn); tabs.append(btn);
    const pane = h("div", { class: "hidden" });
    if (section.help) pane.append(h("p", { class: "muted" }, section.help));
    const grid = h("div", { class: "fields" });
    section.fields.filter((f) => !f.hidden).forEach((field) => {
      const wrap = renderField(field, getPath(state, field.key), update, secrets, readOnly, () => state);
      wrap.dataset.key = field.key;
      inputs.push({ field, wrap }); grid.append(wrap);
    });
    pane.append(grid); panes.push(pane); root.append(pane);
  });
  select(0); refresh();
  return {
    el: root,
    values: () => structuredClone(state),
    showErrors(errors) {
      root.querySelectorAll(".field.invalid").forEach((f) => { f.classList.remove("invalid"); f.querySelector(".error")?.remove(); });
      tabButtons.forEach((b) => b.querySelector(".dot")?.remove());
      for (const err of errors) {
        const key = err.field.replace(/^body\./, "");
        const wrap = [...root.querySelectorAll(".field")].find((f) => f.dataset.key && (key === f.dataset.key || key.startsWith(f.dataset.key + ".")
          || (keyPrefix && key === keyPrefix + f.dataset.key)));
        if (!wrap) continue;
        wrap.classList.add("invalid"); wrap.append(h("div", { class: "error" }, err.message));
        const idx = panes.findIndex((p) => p.contains(wrap));
        if (idx >= 0 && !tabButtons[idx].querySelector(".dot")) tabButtons[idx].append(h("span", { class: "dot" }));
      }
    },
  };
}

// copy of a field description whose keys (and nested field keys) live under ``prefix``
function withPrefix(field, prefix) {
  const copy = { ...field, key: `${prefix}.${field.name}`, visible_if: null };
  if (copy.fields) copy.fields = copy.fields.map((sub) => withPrefix(sub, copy.key));
  return copy;
}

function renderField(field, value, update, secrets, readOnly, getState) {
  const wrap = h("div", { class: "field" });
  const tip = field.help ? h("span", { class: "tip", tabindex: "0", "data-tip": field.help, "aria-label": field.help }, "i") : null;
  const label = h("span", { class: "label" }, field.label, tip);
  const help = null;   // help text is shown in the tooltip next to the label
  const dis = readOnly ? { disabled: true } : {};
  let control;
  switch (field.type) {
    case "bool":
      control = h("label", { class: "check" }, h("input", { type: "checkbox", ...dis, checked: !!value, onchange: (e) => update(field.key, e.target.checked) }), field.label, tip);
      wrap.append(control); if (help) wrap.append(help);
      return wrap;
    case "int": case "float":
      control = h("input", { type: "number", ...dis, value: value ?? "", min: field.min, max: field.max, step: field.step || (field.type === "int" ? 1 : "any"),
        oninput: (e) => update(field.key, e.target.value === "" ? null : Number(e.target.value)) });
      break;
    case "select": {
      control = h("select", { ...dis, onchange: (e) => update(field.key, field.value_type === "int" ? Number(e.target.value) : e.target.value) },
        field.choices.map((c) => h("option", { value: c, selected: String(value) === c }, c + (field.choices_help?.[c] ? ` — ${field.choices_help[c]}` : ""))));
      break;
    }
    case "secret": {
      const has = secrets[field.key] ?? secrets[field.key.replace(/^[^.]+\./, "")];
      control = h("input", { type: "password", ...dis, autocomplete: "new-password", placeholder: has ? "•••••• (unchanged)" : "not set", oninput: (e) => update(field.key, e.target.value) });
      wrap.append(label, control); if (has) wrap.append(h("span", { class: "secret-state" }, "✓ stored — leave empty to keep"));
      if (help) wrap.append(help);
      return wrap;
    }
    case "list": case "intlist": case "floatlist":
      wrap.classList.add("wide");
      control = h("textarea", { ...dis, rows: 3, oninput: (e) => {
        const lines = e.target.value.split("\n").map((s) => s.trim()).filter(Boolean);
        update(field.key, field.type === "list" ? lines : lines.map(Number));
      } }, (value || []).join("\n"));
      break;
    case "multiselect": {
      const selected = new Set(value || []);
      control = h("div", { class: "row" }, field.choices.map((c) => h("label", { class: "check" },
        h("input", { type: "checkbox", ...dis, checked: selected.has(c), onchange: (e) => { e.target.checked ? selected.add(c) : selected.delete(c); update(field.key, field.choices.filter((x) => selected.has(x))); } }), c)));
      break;
    }
    case "ordered": {
      let list = [...(value || [])];
      if (field.choices_from === "tracker_sites" && getState && getState().tracker?.sites) field.choices = Object.keys(getState().tracker.sites);
      control = h("div", { class: "ordered-list" });
      const draw = () => {
        fill(control);
        list.forEach((c, i) => control.append(h("div", { class: "ordered-item" }, h("span", { class: "muted small" }, `${i + 1}.`), h("span", { class: "name" }, c),
          h("button", { type: "button", class: "small", ...dis, disabled: readOnly || i === 0, onclick: () => { [list[i - 1], list[i]] = [list[i], list[i - 1]]; changed(); } }, "↑"),
          h("button", { type: "button", class: "small", ...dis, disabled: readOnly || i === list.length - 1, onclick: () => { [list[i + 1], list[i]] = [list[i], list[i + 1]]; changed(); } }, "↓"),
          h("button", { type: "button", class: "small danger", ...dis, onclick: () => { list.splice(i, 1); changed(); } }, "✕"))));
        const remaining = field.choices.filter((c) => !list.includes(c));
        if (remaining.length && !readOnly) control.append(h("div", { class: "row" }, remaining.map((c) => h("button", { type: "button", class: "small", onclick: () => { list.push(c); changed(); } }, `+ ${c}`))));
        if (!list.length) control.prepend(h("span", { class: "muted small" }, "none — uploads disabled"));
      };
      const changed = () => { update(field.key, [...list]); draw(); };
      draw();
      break;
    }
    case "model": {
      if (field.fields.length > 2) wrap.classList.add("wide");    // small models (selectors) share a row
      control = h("fieldset", { class: "subfields" }, h("legend", {}, field.label));
      field.fields.forEach((sub) => control.append(renderField(sub, value?.[sub.name], update, secrets, readOnly, getState)));
      wrap.append(control); if (help) wrap.append(help);
      return wrap;
    }
    case "dict_models": {
      wrap.classList.add("wide");
      let entries = structuredClone(value || {});
      control = h("div", { class: "grid" });
      const draw = () => {
        fill(control);
        Object.entries(entries).forEach(([key, item]) => {
          const body = h("div", { class: "fields" });
          field.item_fields.forEach((sub) => body.append(renderField(withPrefix(sub, `${field.key}.${key}`), item?.[sub.name],
            (k, v) => { setPath(entries, k.slice(field.key.length + 1), v); update(k, v); }, secrets, readOnly, getState)));
          control.append(h("details", { class: "dict-item", open: Object.keys(entries).length === 1 },
            h("summary", {}, h("strong", {}, key), field.addable && !readOnly
              ? h("button", { type: "button", class: "small danger", style: "margin-left:10px", onclick: (e) => { e.preventDefault();
                  if (!confirm(`Remove ${key}?`)) return; delete entries[key]; update(field.key, structuredClone(entries)); draw(); } }, "Remove") : null),
            body));
        });
        if (field.addable && !readOnly) {
          const name = h("input", { type: "text", placeholder: `new ${field.key_label}` });
          control.append(h("div", { class: "row", style: "flex-wrap:nowrap;max-width:420px" }, name, h("button", { type: "button", onclick: () => {
            const key = name.value.trim().toLowerCase();
            if (!/^[a-z0-9_-]{1,32}$/.test(key)) return toast("Use 1-32 lower-case letters, digits, '-' or '_'", "error");
            if (entries[key]) return toast(`${key} already exists`, "error");
            entries[key] = structuredClone(field.item_defaults || {}); update(field.key, structuredClone(entries)); draw();
          } }, "+ Add")));
        }
      };
      draw();
      break;
    }
    case "json":
      wrap.classList.add("wide");
      control = h("textarea", { ...dis, rows: 6, oninput: (e) => { try { update(field.key, JSON.parse(e.target.value)); e.target.style.borderColor = ""; } catch { e.target.style.borderColor = "var(--err)"; } } },
        JSON.stringify(value ?? null, null, 2));
      break;
    default:
      control = h("input", { type: "text", ...dis, value: value ?? "", oninput: (e) => update(field.key, e.target.value) });
      if (field.key.endsWith("working_directory") && !readOnly) {
        const input = control;
        control = h("div", { class: "row", style: "flex-wrap:nowrap" }, input, h("button", { type: "button", onclick: () => browseDirectory(input.value, (p) => { input.value = p; update(field.key, p); }) }, "Browse…"));
      }
  }
  wrap.append(label, control);
  if (help) wrap.append(help);
  return wrap;
}

async function browseDirectory(start, onPick) {
  const body = h("div");
  const m = modal(body);
  async function load(path) {
    let data;
    try { data = await api("GET", `/api/fs/list?path=${encodeURIComponent(path || "")}`); } catch (e) { toast(e.message, "error"); return; }
    fill(body, 
      h("h2", {}, "Select the working directory"),
      h("div", { class: "mono small" }, data.path || "(drives)"),
      h("p", { class: "muted small" }, `${data.mp4_count} .mp4 file(s) here`),
      h("div", { class: "dir-list" },
        data.parent !== null && data.parent !== undefined ? h("div", { onclick: () => load(data.parent) }, "⬆ ..") : null,
        data.dirs.map((d) => h("div", { onclick: () => load(data.path ? `${data.path}${data.path.endsWith("/") || data.path.endsWith("\\") ? "" : (data.path.includes("\\") ? "\\" : "/")}${d}` : d) }, "📁 " + d))),
      h("div", { class: "row", style: "margin-top:12px" }, h("span", { class: "spacer" }), h("button", { onclick: () => m.remove() }, "Cancel"),
        h("button", { class: "primary", disabled: !data.path, onclick: () => { onPick(data.path); m.remove(); } }, "Use this folder")));
  }
  load(start);
}

// ------------------------------------------------------------------ router
const routes = { jobs: viewJobs, job: viewJob, new: viewNewJob, edit: viewEditJob, settings: viewSettings, modules: viewModules,
  tools: viewTools, resources: viewResources, wiki: viewWiki, users: viewUsers, account: viewAccount, about: viewAbout };
function route() {
  clearPollers();
  const [name, ...rest] = (location.hash.slice(1) || "jobs").split("/");
  document.querySelectorAll("#nav a").forEach((a) => a.classList.toggle("active", a.dataset.route === (name === "job" || name === "edit" ? "jobs" : name)));
  fill($view, h("p", { class: "muted" }, "Loading…"));
  (routes[name] || viewJobs)(...rest.map(decodeURIComponent)).catch((e) => { fill($view, h("div", { class: "alert error" }, e.message)); });
}
window.addEventListener("hashchange", route);
document.getElementById("theme-toggle").addEventListener("click", (e) => {
  e.preventDefault();
  const next = document.documentElement.dataset.theme === "dark" ? "light" : "dark";
  document.documentElement.dataset.theme = next; try { localStorage.setItem("theme", next); } catch {}
});
try { const t = localStorage.getItem("theme"); if (t) document.documentElement.dataset.theme = t; } catch {}

// global watcher: sidebar question counter + toasts on job state changes and new questions
const STATE_TOAST = { completed: "ok", ready: "ok", failed: "error", blocked: "error", cancelled: "error", interrupted: "error" };
let knownJobs = null;
async function watchJobs() {
  let data;
  try { data = await api("GET", "/api/jobs"); } catch { return; }
  const n = data.jobs.reduce((s, j) => s + j.prompts, 0);
  const b = document.getElementById("nav-prompts");
  b.textContent = n ? `${n} question${n > 1 ? "s" : ""}` : ""; b.className = n ? "badge warning" : "badge hidden";
  const current = Object.fromEntries(data.jobs.map((j) => [j.id, j]));
  if (knownJobs) {
    for (const j of data.jobs) {
      const before = knownJobs[j.id];
      const link = (text) => h("a", { href: `#job/${j.id}`, onclick: (e) => e.target.closest(".toast")?.remove() }, text);
      if (j.prompts > (before?.prompts || 0)) {
        toast(h("span", {}, "Input needed: ", link(j.name), " is waiting for your answer."), "warning", 30000);
        if (document.hidden && "Notification" in window && Notification.permission === "granted")
          new Notification("File Prepare HF - input needed", { body: j.name });
      }
      if (before && before.state !== j.state)
        toast(h("span", {}, link(j.name), ` is now ${j.state}`, j.error ? ` - ${j.error}` : ""), STATE_TOAST[j.state] || "", STATE_TOAST[j.state] === "error" ? 20000 : 10000);
    }
  }
  knownJobs = current;
}
watchJobs();
setInterval(watchJobs, 4000);
document.addEventListener("click", () => { if ("Notification" in window && Notification.permission === "default") Notification.requestPermission(); }, { once: true });

// ------------------------------------------------------------------ jobs list
async function viewJobs() {
  const table = h("tbody");
  const sched = h("span", { class: "muted small" });
  fill($view, 
    h("div", { class: "topbar" }, h("h1", {}, "Jobs"), sched, isRole("operator") ? h("button", { class: "primary", onclick: () => (location.hash = "#new") }, "New job") : null),
    h("div", { class: "panel" }, h("table", {}, h("thead", {}, h("tr", {}, ["Job", "State", "Directory", "Progress", "Files", "Created", ""].map((t) => h("th", {}, t)))), table)));
  every(3000, async () => {
    const data = await api("GET", "/api/jobs");
    const s = data.scheduler;
    sched.textContent = `${s.running}/${s.max_concurrent_jobs} running · ${s.queued} queued · CPU encode: ${s.cpu_encode_running.length ? s.cpu_encode_running.join(", ") : "free"} · GPU encode: ${s.gpu_encode_running.length ? s.gpu_encode_running.join(", ") : "free"}`;
    fill(table, ...(data.jobs.length ? data.jobs.map(jobRow) : [h("tr", {}, h("td", { colspan: 7, class: "muted" }, "No jobs yet — create one with “New job”."))]));
  });
}
function jobRow(j) {
  const pct = j.progress.percent;
  return h("tr", { class: "clickable", onclick: () => (location.hash = `#job/${j.id}`) },
    h("td", {}, h("strong", {}, j.name), h("div", { class: "muted small mono" }, j.id), j.encode_label ? badge(j.encode_label, "hevc") : null),
    h("td", {}, badge(j.state), j.prompts ? h("div", {}, badge(`${j.prompts} question(s)`, "warning")) : null),
    h("td", { class: "small mono" }, j.directory),
    h("td", { style: "min-width:160px" }, ["running", "previewing", "stopping"].includes(j.state)
      ? [h("div", { class: `progress ${pct == null ? "indeterminate" : ""}` }, h("div", { style: `width:${pct || 0}%` })), h("div", { class: "small muted" }, `${j.progress.stage} ${j.progress.detail || ""}`.slice(0, 80))]
      : h("span", { class: "small muted" }, j.state_help)),
    h("td", { class: "small" }, j.plan ? `${j.files} · ✓${j.results.success} ✗${j.results.failed}` : "—"),
    h("td", { class: "small" }, fmtTime(j.created_at), h("div", { class: "muted" }, j.created_by)),
    h("td", {}, "›"));
}

// ------------------------------------------------------------------ job create / edit
async function jobEditor({ title, job }) {
  const [formDef, defaults] = await Promise.all([api("GET", "/api/forms/job"), job ? Promise.resolve(null) : api("GET", "/api/job-defaults")]);
  const values = job ? job.config : defaults;
  const form = renderForm(formDef.sections, values);
  const name = h("input", { type: "text", value: job?.name || "", placeholder: "Defaults to the folder name" });
  const saveDefaults = h("input", { type: "checkbox" });
  const status = h("div");
  async function save(preview) {
    fill(status);
    const body = { name: name.value, config: form.values(), save_as_defaults: saveDefaults.checked };
    try {
      const res = job ? await api("PUT", `/api/jobs/${job.id}`, body) : await api("POST", "/api/jobs", body);
      if (preview) await api("POST", `/api/jobs/${res.id}/preview`);
      toast(preview ? "Saved — preview started" : "Saved", "ok");
      location.hash = `#job/${res.id}`;
    } catch (e) {
      form.showErrors(e.errors || []);
      fill(status, h("div", { class: "alert error" }, e.message, e.errors?.length ? h("ul", {}, e.errors.map((x) => h("li", {}, `${x.field}: ${x.message}`))) : null));
    }
  }
  fill($view, 
    h("div", { class: "topbar" }, h("h1", {}, title), job ? h("button", { onclick: () => (location.hash = `#job/${job.id}`) }, "Back") : null),
    h("div", { class: "panel" }, h("div", { class: "fields" }, h("div", { class: "field" }, h("span", { class: "label" }, "Job name"), name)),
      h("p", { class: "muted small" }, "Tabs for thumbnails, video preview and tracker settings appear when those features are enabled. Hidden fields keep their values.")),
    h("div", { class: "panel" }, form.el), status,
    h("div", { class: "row" }, isRole("admin") ? h("label", { class: "check" }, saveDefaults, "Also save as default settings") : null, h("span", { class: "spacer" }),
      h("button", { onclick: () => save(false) }, "Save"), h("button", { class: "primary", onclick: () => save(true) }, "Save & preview")));
}
function viewNewJob() { return jobEditor({ title: "New job" }); }
async function viewEditJob(id) { const job = await api("GET", `/api/jobs/${id}`); return jobEditor({ title: `Edit: ${job.name}`, job }); }

// ------------------------------------------------------------------ job detail
async function viewJob(id) {
  const header = h("div", { class: "topbar" });
  const prompts = h("div");
  const overview = h("div", { class: "panel" });
  const planPanel = h("div");
  const performerPanel = h("div");
  let performersLoadedFor = null;
  const logBox = h("div", { class: "log" });
  let logNext = 0, lastPromptIds = "", lastPlanAt = null, lastResults = "";
  const settingsPanel = h("details", { class: "panel job-settings" }, h("summary", {}, h("strong", {}, "Settings used by this job")));
  let settingsShownFor = null;
  fill($view, header, prompts, overview, settingsPanel, planPanel, performerPanel, h("div", { class: "panel" }, h("h2", {}, "Log"), logBox));
  const jobFormDef = api("GET", "/api/forms/job");

  async function act(action, confirmText) {
    if (confirmText && !confirm(confirmText)) return;
    try { await api("POST", `/api/jobs/${id}/${action}`); toast(`${action} requested`, "ok", 4000); refresh(); } catch (e) { toast(e.message, "error"); }
  }
  async function refresh() {
    const job = await api("GET", `/api/jobs/${id}`);
    const s = job.summary, st = job.state;
    const configKey = JSON.stringify(job.config);
    if (configKey !== settingsShownFor) {       // read-only copy of the job form; only the tabs that apply are shown
      settingsShownFor = configKey;
      const form = renderForm((await jobFormDef).sections, job.config, { readOnly: true });
      fill(settingsPanel, settingsPanel.querySelector("summary"),
        h("p", { class: "muted small" }, "The job keeps its own copy of the settings, taken when it was created or last edited. ",
          "Use “Edit settings” to change them (a new preview is needed afterwards)."), form.el);
    }
    const can = isRole("operator");
    fill(header, h("h1", {}, job.name), badge(st), job.summary.encode_label ? badge(job.summary.encode_label, "hevc") : null,
      can && ["draft", "ready", "blocked", "completed", "failed", "cancelled", "stopped", "interrupted"].includes(st) ? h("button", { onclick: () => (location.hash = `#edit/${id}`) }, "Edit settings") : null,
      can && ["draft", "ready", "blocked", "completed", "failed", "cancelled", "stopped", "interrupted"].includes(st) ? h("button", { onclick: () => act("preview") }, "Preview") : null,
      can && st === "ready" ? h("button", { class: "primary", onclick: () => act("queue") }, "Queue / start") : null,
      can && st !== "ready" && A.requirePreview === false && ["draft", "completed", "failed", "cancelled", "stopped", "interrupted"].includes(st) && !(["stopped", "interrupted", "failed"].includes(st) && job.plan_is_current)
        ? h("button", { class: "primary", title: "Preview is not required (Settings > Application > Jobs): files are checked at the start of the run",
            onclick: () => act("start", "Start without a preview? Files are checked first; blocking issues stop the job before anything changes.") }, "Start without preview") : null,
      can && ["stopped", "interrupted", "failed"].includes(st) && job.plan_is_current ? h("button", { class: "primary", onclick: () => act("start") }, "Resume") : null,
      can && ["running", "previewing", "queued"].includes(st) ? h("button", { onclick: () => act("stop") }, st === "queued" ? "Unqueue" : "Stop after current file") : null,
      can && ["running", "previewing", "stopping", "queued"].includes(st) ? h("button", { class: "danger", onclick: () => act("cancel", "Cancel now? The current step (e.g. encoding) is aborted.") }, "Cancel") : null,
      h("button", { title: "Download this job's settings, plan, results and log tail as JSON (for bug reports)", onclick: () => { location.href = `${A.base}/api/jobs/${id}/export`; } }, "Export"),
      can ? h("button", { onclick: async () => { const r = await api("POST", `/api/jobs/${id}/duplicate`); location.hash = `#job/${r.id}`; } }, "Duplicate") : null,
      can && !["running", "previewing", "stopping", "queued"].includes(st) ? h("button", { class: "danger", onclick: async () => { if (confirm("Delete this job and its log?")) { await api("DELETE", `/api/jobs/${id}`); location.hash = "#jobs"; } } }, "Delete") : null);

    const pct = s.progress.percent;
    fill(overview, h("div", { class: "kv" },
      h("div", {}, "Status"), h("div", {}, s.state_help + (job.error ? ` — ${job.error}` : "")),
      h("div", {}, "Directory"), h("div", { class: "mono small" }, s.directory),
      h("div", {}, "Created"), h("div", {}, `${fmtTime(job.created_at)} by ${job.created_by}`),
      h("div", {}, "Started / finished"), h("div", {}, `${fmtTime(job.started_at)} / ${fmtTime(job.finished_at)}`),
      h("div", {}, "Results"), h("div", {}, `✓ ${s.results.success} succeeded · ✗ ${s.results.failed} failed · ${s.results.running} running`),
      h("div", {}, "Progress"), h("div", {}, ["running", "previewing", "stopping"].includes(st)
        ? [h("div", { class: `progress ${pct == null ? "indeterminate" : ""}` }, h("div", { style: `width:${pct || 0}%` })), h("div", { class: "small muted" }, `${s.progress.stage} — ${s.progress.detail}`)] : "—")),
      job.plan && !job.plan_is_current ? h("div", { class: "alert warning", style: "margin-top:10px" }, "Settings changed since the last preview — run a new preview before starting.") : null);

    const promptIds = job.prompts.map((p) => p.id).join(",");
    if (promptIds !== lastPromptIds) { lastPromptIds = promptIds; fill(prompts, ...job.prompts.map((p) => promptCard(id, p, refresh))); }
    const resultsKey = JSON.stringify(job.results);
    if (job.plan && (job.plan.created_at + job.plan.config_hash !== lastPlanAt || resultsKey !== lastResults)) {
      lastPlanAt = job.plan.created_at + job.plan.config_hash; lastResults = resultsKey; fill(planPanel, renderPlan(job, id, refresh));
      const o = job.config.processing.outputs;
      if ((o.performer_portraits || o.upload_template) && performersLoadedFor !== job.plan.created_at) {
        performersLoadedFor = job.plan.created_at;
        renderPerformers(id, performerPanel);
      }
    } else if (!job.plan) fill(planPanel);
    const logs = await api("GET", `/api/jobs/${id}/logs?after=${logNext}`);
    if (logs.lines.length) {
      const atBottom = logBox.scrollTop + logBox.clientHeight >= logBox.scrollHeight - 30;
      logs.lines.forEach((line) => { const lvl = (line.split("|")[1] || "").trim(); logBox.append(h("div", { class: `l-${lvl}` }, linkify(line))); });
      while (logBox.childElementCount > 3000) logBox.firstChild.remove();
      if (atBottom || logNext === 0) logBox.scrollTop = logBox.scrollHeight;
    }
    logNext = logs.next;
  }
  every(2000, refresh);
}

// list question: one field per entry, "+ Add another" (Enter in a field adds the next one), sent as a JSON array
function listAnswer(p, send) {
  const rows = h("div", { class: "list-answer" });
  const addField = (value = "", focus = false) => {
    const input = h("input", { type: "text", value, placeholder: `${p.item_label || "Entry"} ${rows.childElementCount + 1}` });
    input.addEventListener("keydown", (e) => {
      if (e.key === "Enter") { e.preventDefault(); if (input.value.trim()) addField("", true); }
    });
    const line = h("div", { class: "row", style: "flex-wrap:nowrap" }, input,
      h("button", { type: "button", class: "small", title: "Remove", onclick: () => { line.remove(); if (!rows.childElementCount) addField("", true); } }, "✕"));
    rows.append(line);
    if (focus) input.focus();
  };
  addField("", false);
  const values = () => [...rows.querySelectorAll("input")].map((i) => i.value.trim()).filter(Boolean);
  return h("div", {}, rows,
    h("div", { class: "row", style: "margin-top:8px" },
      h("button", { type: "button", onclick: () => addField("", true) }, `+ Add another ${(p.item_label || "entry").toLowerCase()}`),
      h("span", { class: "spacer" }),
      h("button", { type: "button", onclick: () => send("[]") }, "None"),
      h("button", { type: "button", class: "primary", onclick: () => send(JSON.stringify(values())) }, "Submit all")));
}

function promptCard(jobId, p, after) {
  const input = p.multiline ? h("textarea", { rows: 5 }) : h("input", { type: "text", placeholder: "Type an answer…" });
  const send = async (answer) => {
    try { await api("POST", `/api/jobs/${jobId}/prompts/${p.id}`, { answer }); toast("Answer sent", "ok", 3000); after(); } catch (e) { toast(e.message, "error"); }
  };
  if (!p.multiline) input.addEventListener("keydown", (e) => { if (e.key === "Enter") send(input.value); });
  const ctx = h("pre", {}, linkify(p.context.join("\n")));
  setTimeout(() => (ctx.scrollTop = ctx.scrollHeight), 0);
  return h("div", { class: "prompt" },
    h("div", { class: "row" }, badge("input needed", "warning"), h("strong", {}, p.title), h("span", { class: "spacer" }), h("span", { class: "muted small" }, fmtTime(p.asked_at))),
    p.context.length ? h("details", { open: true }, h("summary", { class: "small muted" }, "Recent log (options are listed here)"), ctx) : null,
    h("div", { class: "question" }, linkify(p.text)),
    isRole("operator") ? [
      p.kind === "list" ? listAnswer(p, send) : [
        p.choices.length ? h("div", { class: "row", style: "margin-bottom:8px" }, p.choices.map((c) => h("button", { onclick: () => send(c) }, c))) : null,
        h("div", { class: "row", style: "flex-wrap:nowrap" }, input, h("button", { class: "primary", onclick: () => send(input.value) }, "Send"))],
    ] : h("p", { class: "muted" }, "An operator must answer this question."));
}

// performer image picker (before the run): candidates from TPDB posters or uploads -> image host -> Performers_Images.json
const replacing = new Set();      // performers whose image is being replaced (tools shown again)
async function renderPerformers(jobId, panel) {
  let data;
  try { data = await api("GET", `/api/jobs/${jobId}/performers`); } catch (e) { fill(panel, h("div", { class: "alert error" }, e.message)); return; }
  if (!data.performers.length) { fill(panel); return; }
  const can = isRole("operator");
  const again = () => renderPerformers(jobId, panel);
  const imgUrl = (p, c) => `${A.base}/api/jobs/${jobId}/performers/image/${encodeURIComponent(p.key)}/${encodeURIComponent(c.file)}`;
  const busy = (btn, text) => { const old = btn.textContent; btn.disabled = true; btn.textContent = text; return () => { btn.disabled = false; btn.textContent = old; }; };
  const post = async (action, body, btn, text) => {
    const restore = btn ? busy(btn, text) : () => {};
    try { const r = await api("POST", `/api/jobs/${jobId}/performers/${action}`, body); return r; }
    catch (err) { toast(err.message, "error"); restore(); return null; }
  };
  const choose = async (p, c, btn) => {
    if (await post("choose", { key: p.key, file: c.file }, btn, "Uploading…")) { replacing.delete(p.key); toast(`${p.key}: image saved`, "ok"); again(); }
  };
  const row = (p) => {
    const open = !p.url || replacing.has(p.key);
    const status = p.url ? h("div", { class: "row" }, h("img", { src: p.url, class: "portrait-thumb", alt: "" }), badge("image set", "ok"))
      : badge("no image", "warning");
    const header = h("div", { class: "row" }, h("strong", {}, p.name),
      p.tpdb_id ? h("span", { class: "muted small mono", title: p.linked ? "Linked by you" : "From the scene match" }, `TPDB ${p.tpdb_id}${p.linked ? " (linked)" : ""}`)
        : badge("not on TPDB", ""),
      h("span", { class: "spacer" }), status,
      can && p.url ? h("button", { class: "small", onclick: () => { replacing.has(p.key) ? replacing.delete(p.key) : replacing.add(p.key); again(); } },
        replacing.has(p.key) ? "Keep current image" : "Replace image") : null);
    if (!open) return h("div", { class: "performer done" }, header);

    const faces = p.candidates.filter((c) => c.kind !== "poster");
    const posters = p.candidates.filter((c) => c.kind === "poster");
    const fileInput = h("input", { type: "file", accept: ".jpg,.jpeg,.png,.webp", class: "hidden", onchange: async () => {
      const fd = new FormData(); fd.append("key", p.key); fd.append("file", fileInput.files[0]);
      const res = await fetch(`${A.base}/api/jobs/${jobId}/performers/upload`, { method: "POST", headers: { "X-CSRF-Token": A.csrf }, body: fd });
      const out = await res.json();
      if (!res.ok) return toast(out.detail || "Upload failed", "error");
      toast(`${p.key}: image added - pick it below`, "ok"); again();
    } });
    const tools = can ? h("div", { class: "row", style: "margin:8px 0" },
      p.tpdb_id ? h("button", { class: "small", onclick: async (e) => {
        if (await post("candidates", { key: p.key, tpdb_id: p.tpdb_id }, e.target, "Finding faces…")) again();
      } }, posters.length ? "Find faces again" : "Find faces on TPDB") : null,
      h("button", { class: "small", title: "Link this performer to a TPDB performer by its UUID (shown on the performer's TPDB page), then find faces",
        onclick: async (e) => {
          const uuid = prompt(`TPDB performer UUID for ${p.key}`, p.linked ? p.tpdb_id : ""); if (!uuid) return;
          const r = await post("link", { key: p.key, uuid }, e.target, "Checking…");
          if (r) { toast(`${p.key} linked to TPDB performer ${r.tpdb_name}`, "ok"); again(); }
        } }, p.tpdb_id ? "Link to another TPDB performer" : "Link to TPDB performer"),
      h("button", { class: "small", onclick: () => fileInput.click() }, "Upload an image…"), fileInput,
      h("button", { class: "small", onclick: async (e) => {
        const url = prompt(`Image URL for ${p.key} (already hosted)`, ""); if (!url) return;
        if (await post("url", { key: p.key, url }, e.target, "Saving…")) { replacing.delete(p.key); toast(`${p.key}: URL saved`, "ok"); again(); }
      } }, "Paste URL"),
      p.candidates.length ? h("button", { class: "small danger", title: "Delete the downloaded posters and face crops of this performer",
        onclick: async (e) => { if (await post("discard", { key: p.key }, e.target, "Deleting…")) again(); } }, "Discard candidates") : null) : null;
    return h("div", { class: "performer" }, header, tools,
      faces.length ? h("div", { class: "candidates" }, faces.map((c) => h("figure", { class: "candidate" },
        h("img", { src: imgUrl(p, c), alt: c.file }), h("figcaption", {}, c.kind),
        can ? h("button", { class: "small primary", onclick: (e) => choose(p, c, e.target) }, "Use this face") : null))) : null,
      posters.length ? h("details", {}, h("summary", { class: "small muted" }, `${posters.length} downloaded poster(s)`),
        h("div", { class: "candidates" }, posters.map((c) => h("figure", { class: "candidate" }, h("img", { src: imgUrl(p, c), alt: c.file }),
          can ? h("button", { class: "small", onclick: (e) => choose(p, c, e.target) }, "Use as-is") : null)))) : null);
  };
  const missing = data.performers.filter((p) => !p.url).length;
  fill(panel, h("div", { class: "panel" },
    h("div", { class: "row" }, h("h2", { style: "margin:0" }, "Performer images"), h("span", { class: "spacer" }),
      missing ? badge(`${missing} without image`, "warning") : badge("all performers have an image", "ok")),
    h("p", { class: "muted small" }, `Pick an image for each performer before starting: it is uploaded to ${data.hosts.join(" → ") || "(no portrait host)"} `,
      "and saved in Resources/Performers_Images.json; the other candidates are then deleted. Performers without a TPDB id can be linked by their TPDB UUID. ",
      "Performers that already have an image are listed last, collapsed."),
    data.performers.map(row)));
}

function renderPlan(job, id, refresh) {
  const plan = job.plan, summary = job.summary.plan;
  const issueRow = (i) => h("div", { class: "issue" }, badge(i.level), h("div", {}, h("div", { class: "mono small muted" }, i.code), h("div", { class: "small" }, linkify(i.message))));
  const stepList = (f, r) => {
    const done = new Set(r?.completed_steps || []);
    return h("ul", { class: "steps" }, f.steps.map((s) => h("li", { class: [...done].some((d) => d === s || d.includes(s.split(" ")[0]) || s.includes(d)) ? "done" : "" }, s)));
  };
  const editable = isRole("operator") && !["running", "previewing", "queued", "stopping"].includes(job.state);
  const MODE_LABELS = { names: "names", title: "title", both: "names + title", tpdb_id: "TPDB id", user_input: "typed name" };
  // per-file file name mode: each option shows the name it gives (no new preview needed)
  const modeSelect = (f) => {
    const c = f.name_candidates || {};
    if (!f.match || !Object.keys(c).length) return null;
    const jobMode = job.config.processing.naming.filename_mode;
    const sel = h("select", { class: "small", style: "max-width:100%;margin-top:4px", disabled: !editable || f.custom_name,
      title: f.custom_name ? "A custom name is set - clear it with Rename to choose a name mode" : "File name mode for this file",
      onchange: async () => {
        try { await api("POST", `/api/jobs/${id}/names`, { file: f.source_name, mode: sel.value }); toast("Name mode updated", "ok", 4000); refresh(); }
        catch (e) { toast(e.message, "error"); refresh(); }
      } },
      h("option", { value: "" }, `Job default (${MODE_LABELS[jobMode] || jobMode})${c[jobMode] ? ": " + c[jobMode] : ""}`),
      ["names", "title", "both", "tpdb_id"].filter((m) => c[m] && m !== jobMode && (m !== "tpdb_id" || f.match.tpdb_id)).map((m) => h("option", { value: m }, `${MODE_LABELS[m]}: ${c[m]}`)));
    sel.value = f.name_mode && f.name_mode !== jobMode ? f.name_mode : "";
    return h("div", { class: "small" }, sel);
  };
  // per-file studio display (title, metadata, tags, template): each option shows the studio text it gives
  const STUDIO_LABELS = { site_studio: "Site(Studio)", site: "site only", studio: "studio only" };
  const studioSelect = (f) => {
    const c = f.studio_candidates || {};
    if (!f.match || new Set(Object.values(c)).size < 2) return null;
    const def = f.match.studio_mode || job.config.processing.naming.studio_mode;
    const sel = h("select", { class: "small", style: "max-width:100%;margin-top:4px", disabled: !editable, title: "Studio display for this file",
      onchange: async () => {
        try { await api("POST", `/api/jobs/${id}/names`, { file: f.source_name, studio_mode: sel.value }); toast("Studio display updated", "ok", 4000); refresh(); }
        catch (e) { toast(e.message, "error"); refresh(); }
      } },
      h("option", { value: "" }, `Studio: ${f.match.studio_mode ? "approved" : "job default"} (${STUDIO_LABELS[def] || def})${c[def] ? ": " + c[def] : ""}`),
      Object.keys(c).filter((m) => m !== def && c[m] !== c[def]).map((m) => h("option", { value: m }, `Studio: ${STUDIO_LABELS[m]}: ${c[m]}`)));
    sel.value = f.studio_mode && f.studio_mode !== def ? f.studio_mode : "";
    return h("div", { class: "small" }, sel);
  };
  const files = plan.files.map((f) => {
    const r = job.results[f.source_name];
    return h("tr", {},
      h("td", {}, h("div", { class: "mono small" }, f.source_name), f.new_file_name && f.new_file_name !== f.source_name ? h("div", { class: "mono small" }, "→ " + f.new_file_name, f.custom_name ? h("span", { style: "margin-left:6px" }, badge("custom", "info")) : null) : null,
        f.title ? h("div", { class: "small muted" }, f.title) : null, modeSelect(f), studioSelect(f)),
      h("td", {}, badge(f.status), r ? h("div", {}, badge(r.status)) : null),
      h("td", { class: "small" }, f.steps.length ? stepList(f, r) : "—", r?.error ? h("div", { style: "color:var(--err)" }, r.error) : null),
      h("td", {}, f.issues.map(issueRow)),
      h("td", {}, isRole("operator") && !["running", "previewing", "queued", "stopping"].includes(job.state)
        ? h("div", { class: "row" },
          f.match ? h("button", { class: "small", title: "Set this file's new name yourself (no new preview needed). Empty = back to the name from TPDB.", onclick: async () => {
            const name = prompt(`New name for ${f.source_name} (without .mp4, used as-is; empty = name from TPDB)`, f.new_file_name.replace(/\.mp4$/i, ""));
            if (name === null) return;
            try { await api("POST", `/api/jobs/${id}/names`, { file: f.source_name, name }); toast("Name updated", "ok", 4000); refresh(); } catch (e) { toast(e.message, "error"); }
          } }, "Rename") : null,
          h("button", { class: "small", title: "Look this file up directly by a TPDB scene UUID (from a manual search), then preview again", onclick: async () => {
            const current = job.config.processing.matching.uuid_overrides?.[f.source_name] || "";
            const uuid = prompt(`TPDB scene UUID for ${f.source_name}\n(search the scene on theporndb.net; empty = remove)`, current);
            if (uuid === null) return;
            try { await api("POST", `/api/jobs/${id}/uuid`, { file: f.source_name, uuid }); toast("UUID saved — run the preview again", "ok"); refresh(); } catch (e) { toast(e.message, "error"); }
          } }, job.config.processing.matching.uuid_overrides?.[f.source_name] ? "Change UUID" : "Set TPDB UUID"),
          h("button", { class: "small", title: "Move this file to the moved-out folder (removes it from the job), then preview again", onclick: async () => {
            if (!confirm(`Move ${f.source_name} out of the working directory?`)) return;
            try { const r = await api("POST", `/api/jobs/${id}/move-out`, { file: f.source_name }); toast(`Moved to ${r.moved_to} — run the preview again`, "ok"); refresh(); } catch (e) { toast(e.message, "error"); }
          } }, "Move out")) : null));
  });
  return h("div", { class: "panel" },
    h("div", { class: "row" }, h("h2", { style: "margin:0" }, "Preview / plan"), h("span", { class: "spacer" }),
      summary.blocking ? badge(`${summary.errors} blocking issue(s)`, "error") : badge("no blocking issues", "ok"), summary.warnings ? badge(`${summary.warnings} warning(s)`, "warning") : null,
      h("span", { class: "muted small" }, `previewed ${fmtTime(plan.created_at)}`)),
    plan.issues.length ? h("div", { style: "margin-top:10px" }, plan.issues.map(issueRow)) : null,
    isRole("operator") && !["running", "previewing", "queued", "stopping"].includes(job.state) && plan.files.some((f) => f.match) ? (() => {
      const input = h("input", { type: "text", value: job.config.processing.naming.extra_suffix || "", placeholder: "e.g. VR2Normal", style: "max-width:240px" });
      const apply = async () => {
        try { await api("POST", `/api/jobs/${id}/names`, { suffix: input.value }); toast(input.value ? `Suffix ".${input.value}" applied` : "Suffix removed", "ok", 4000); refresh(); }
        catch (e) { toast(e.message, "error"); }
      };
      input.addEventListener("keydown", (e) => { if (e.key === "Enter") apply(); });
      return h("div", { class: "row", style: "margin-top:10px" }, h("span", { class: "small" }, "Suffix for all files:"), input,
        h("button", { class: "small", onclick: apply }, "Apply"),
        h("span", { class: "muted small" }, "Added after each name from TPDB (and to the video title); files you renamed keep their name. No new preview needed."));
    })() : null,
    plan.renames.length ? h("details", {}, h("summary", {}, `${plan.renames.length} strict-mode rename(s)`), h("ul", { class: "mono small" }, plan.renames.map(([a, b]) => h("li", {}, `${a} → ${b}`)))) : null,
    h("table", { class: "plan-table", style: "margin-top:10px" },
      h("colgroup", {}, ["34%", "9%", "19%", "26%", "12%"].map((w) => h("col", { style: `width:${w}` }))),
      h("thead", {}, h("tr", {}, ["File", "Status", "Steps", "Issues", ""].map((t) => h("th", {}, t)))), h("tbody", {}, files)));
}

// ------------------------------------------------------------------ settings
const SETTINGS = [["processing", "Processing defaults"], ["thumbnails", "Thumbnails"], ["video_preview", "Video preview"], ["tracker", "Tracker sites"], ["app", "Application"], ["credentials", "Credentials"]];
async function viewSettings(name = "processing") {
  const visible = SETTINGS.filter(([n]) => n !== "credentials" || isRole("admin"));
  const data = await api("GET", `/api/settings/${name}`);
  const form = renderForm(data.sections, data.values, { secrets: data.secrets, readOnly: !isRole("admin") });
  const status = h("div");
  fill($view, 
    h("div", { class: "topbar" }, h("h1", {}, "Settings"), h("span", { class: "muted small mono" }, data.file)),
    h("div", { class: "tabs" }, visible.map(([n, label]) => h("button", { class: n === name ? "active" : "", onclick: () => (location.hash = `#settings/${n}`) }, label))),
    name === "processing" ? h("p", { class: "muted" }, "These are the defaults copied into every new job. Each job keeps its own copy, editable before its preview.") : null,
    name === "credentials" ? h("div", { class: "alert warning" }, "Secrets are never sent back to the browser. Leave a secret empty to keep the stored value.") : null,
    h("div", { class: "panel" }, form.el), status,
    isRole("admin") ? h("div", { class: "row" }, h("span", { class: "spacer" }), h("button", { class: "primary", onclick: async () => {
      fill(status);
      try {
        const res = await api("PUT", `/api/settings/${name}`, form.values());
        toast("Settings saved", "ok");
        if (res.restart_required) fill(status, h("div", { class: "alert warning" }, "Server, authentication and session settings take effect after a restart."));
      } catch (e) { form.showErrors(e.errors || []); fill(status, h("div", { class: "alert error" }, e.message, h("ul", {}, (e.errors || []).map((x) => h("li", {}, `${x.field}: ${x.message}`))))); }
    } }, "Save settings")) : h("p", { class: "muted" }, "Read only — only admins can change settings."));
}

// ------------------------------------------------------------------ modules
const MODULE_STATE = { ready: ["ready", "Configured and enabled in the default job settings", "ok"],
  off: ["off by default", "Configured - not enabled in the default job settings", ""],
  setup: ["setup needed", "Something is missing before this module can work - see the status line", "warning"] };
async function viewModules() {
  const data = await api("GET", "/api/modules");
  fill($view, h("div", { class: "topbar" }, h("h1", {}, "Modules")),
    h("p", { class: "muted" }, "Every processing step is a separate module. Enable them per job (New job → Outputs / Image hosts / Tracker) or in Settings → Processing defaults."),
    h("p", { class: "small muted" }, "ready = configured and on by default · off by default = works, enable it per job · setup needed = credentials, files or settings are missing (see the line under the description)"),
    h("div", { class: "grid cols-3" }, data.modules.map((m) => {
      const result = h("div", { class: "small" });
      return h("div", { class: "panel" }, h("div", { class: "row" }, h("strong", {}, m.label), h("span", { class: "spacer" }),
          h("span", { title: MODULE_STATE[m.state][1] }, badge(MODULE_STATE[m.state][0], MODULE_STATE[m.state][2]))),
        h("div", { class: "muted small" }, m.kind), h("p", {}, m.description), h("div", { class: "small" }, m.status),
        m.testable && isRole("operator") ? h("div", { class: "row", style: "margin-top:8px" }, h("button", { class: "small", onclick: async () => {
          result.textContent = "Testing…";
          try { const r = await api("POST", `/api/modules/${m.name}/test`); fill(result, badge(r.ok ? "ok" : "failed", r.ok ? "ok" : "error"), " ", r.message); } catch (e) { result.textContent = e.message; }
        } }, "Test"), result) : null);
    })));
}

// ------------------------------------------------------------------ tools
async function viewTools() {
  const ff = h("div", { class: "panel" }, "Checking ffmpeg…");
  const gpuPanel = h("div", { class: "panel" }, "Detecting GPU acceleration…");
  const res = h("div", { class: "panel" });
  const sessions = h("div", { class: "panel" });
  const pix = h("div", { class: "panel" });
  const upd = h("div", { class: "panel" }, "Checking version…");
  fill($view, h("div", { class: "topbar" }, h("h1", {}, "Tools")), upd, h("div", { class: "grid cols-2" }, ff, gpuPanel), sessions, res, pix);

  async function loadUpdates(refresh) {
    const r = await checkUpdates(refresh, false);
    fill(upd, h("h2", {}, "Version"),
      h("p", {}, "Installed: ", h("strong", {}, `v${r.current}`), r.channel === "off" ? " · update check is off (Settings > Updates)" :
        [` · channel: ${r.channel} (${r.branch} branch) · latest: `, h("strong", {}, r.latest ? `v${r.latest}` : "?"), " ",
          r.error ? badge("check failed", "error") : r.update_available ? badge("update available", "warning") : r.ahead ? badge("newer than channel", "") : badge("up to date", "ok")]),
      r.error ? h("p", { class: "small muted" }, r.error) : null,
      r.channel !== "off" ? h("p", { class: "small muted" }, `Checked ${fmtTime(r.checked_at)}`,
        r.retry_in ? ` · next manual check possible in ${Math.floor(r.retry_in / 60)}:${String(r.retry_in % 60).padStart(2, "0")}` : "",
        " · click the version number in the sidebar to check from any page.") : null,
      r.update_available ? h("p", {}, "Update with ", h("code", {}, `git pull origin ${r.branch}`), " (or download the branch), then restart. ", h("a", { href: r.url, target: "_blank", rel: "noopener" }, "Open on GitHub")) : null,
      r.channel !== "off" ? h("button", { class: "small", onclick: () => { fill(upd, "Checking version…"); loadUpdates(true).catch((e) => fill(upd, e.message)); } }, "Check now") : null);
  }
  loadUpdates(false).catch((e) => fill(upd, e.message));

  async function loadFfmpeg(refresh) {
    const r = await api("GET", `/api/tools/ffmpeg${refresh ? "?refresh=1" : ""}`);
    const tool = (t) => [h("div", {}, t.name), h("div", {}, t.found ? [badge(t.ok ? "ok" : "too old", t.ok ? "ok" : "error"), ` ${t.version || "?"} ${t.build_date ? "(" + t.build_date + ")" : ""}`,
      h("div", { class: "mono small muted" }, t.path || t.command)] : badge("not found", "error"))];
    fill(ff, h("div", { class: "row" }, h("h2", { style: "margin:0" }, "ffmpeg"), h("span", { class: "spacer" }), h("button", { class: "small", onclick: () => loadFfmpeg(true) }, "Re-check")),
      h("div", { class: "kv", style: "margin-top:10px" }, ...tool(r.ffmpeg), ...tool(r.ffprobe),
        h("div", {}, "Encoders"), h("div", {}, Object.entries(r.encoders).map(([e, ok]) => h("span", { style: "margin-right:6px" }, badge(e, ok ? "ok" : "error")))),
        h("div", {}, "OS"), h("div", {}, r.os_name)),
      h("h3", {}, `Update ffmpeg (${r.os_name})`),
      h("ul", {}, r.update_links.map(([label, link]) => h("li", {}, label, ": ", link.startsWith("http") ? h("a", { href: link, target: "_blank", rel: "noopener" }, link) : h("code", {}, link)))),
      h("p", { class: "muted small" }, "After updating, make sure the new ffmpeg is on PATH (or set its path in Settings → Application → Environment) and press Re-check."));
  }
  async function loadResources() {
    const r = await api("GET", "/api/tools/resources");
    let entries = r.entries;
    const table = h("tbody");
    const output = h("div", { class: "small" });
    const draw = () => fill(table, ...entries.map((e, i) => {
      const st = r.state[e.name] || {};
      const cell = (key, ph) => h("input", { type: "text", value: e[key] || "", placeholder: ph, disabled: !isRole("admin"), oninput: (ev) => (e[key] = ev.target.value) });
      return h("tr", {}, h("td", {}, cell("name", "name")), h("td", {}, cell("url", "https://github.com/user/repo/blob/main/file.json")), h("td", {}, cell("target", "Performers_Images.json")),
        h("td", {}, h("select", { disabled: !isRole("admin"), onchange: (ev) => (e.merge = ev.target.value) }, ["replace", "merge_json"].map((m) => h("option", { selected: e.merge === m }, m)))),
        h("td", {}, h("input", { type: "checkbox", checked: e.enabled, disabled: !isRole("admin"), onchange: (ev) => (e.enabled = ev.target.checked) })),
        h("td", { class: "small" }, st.synced_at ? fmtTime(st.synced_at) : "never", st.error ? h("div", { style: "color:var(--err)" }, st.error) : null),
        h("td", {}, isRole("admin") ? h("button", { class: "small danger", onclick: () => { entries.splice(i, 1); draw(); } }, "✕") : null));
    }));
    draw();
    const run = async (action) => {
      output.textContent = `${action}…`;
      try { const x = await api("POST", `/api/tools/resources/${action}`, {}); fill(output, ...x.results.map((y) => h("div", {}, badge(y.status, y.status === "error" ? "error" : "ok"), ` ${y.name} ${y.error || ""}`))); loadResources(); }
      catch (e) { output.textContent = e.message; }
    };
    fill(res, h("div", { class: "row" }, h("h2", { style: "margin:0" }, "Resource sync (GitHub)"), h("span", { class: "spacer" }),
        isRole("operator") ? [h("button", { onclick: () => run("check") }, "Check for updates"), h("button", { class: "primary", onclick: () => run("sync") }, "Sync now")] : null),
      h("p", { class: "muted small" }, "Keeps files in Resources/ (e.g. Performers_Images.json) in sync with GitHub raw files. merge_json keeps local-only entries. Private repos: add a token in Credentials → GitHub."),
      h("table", {}, h("thead", {}, h("tr", {}, ["Name", "GitHub URL", "Target (in Resources/)", "Mode", "On", "Last sync", ""].map((t) => h("th", {}, t)))), table),
      isRole("admin") ? h("div", { class: "row", style: "margin-top:8px" }, h("button", { onclick: () => { entries.push({ name: "", url: "", target: "", merge: "replace", enabled: true }); draw(); } }, "+ Add resource"),
        h("button", { onclick: async () => { try { await api("PUT", "/api/tools/resources/entries", entries); toast("Resource list saved", "ok"); loadResources(); } catch (e) { toast(e.message + (e.errors?.length ? ": " + e.errors.map((x) => x.message).join(", ") : ""), "error"); } } }, "Save list")) : null,
      output, h("p", { class: "muted small" }, "Local resource files: " + r.files.join(", ")));
  }
  async function loadSessions() {
    const r = await api("GET", "/api/tools/tracker-sessions");
    fill(sessions, h("h2", {}, "Tracker browser sessions"),
      r.sessions.length ? h("table", {}, h("tbody", {}, r.sessions.map((s) => h("tr", {}, h("td", {}, s.tracker), h("td", {}, s.job_id ? h("a", { href: `#job/${s.job_id}` }, `job ${s.job_id}`) : "terminal"), h("td", {}, badge(s.state, s.logged_in ? "ok" : "")),
        h("td", { class: "small" }, `${s.pending} pending · idle ${s.idle_seconds}s`), h("td", {}, isRole("operator") ? h("button", { class: "small", onclick: async () => { await api("POST", `/api/tools/tracker-sessions/${encodeURIComponent(s.id)}/close`); loadSessions(); } }, "Close") : null)))))
        : h("p", { class: "muted" }, "No open sessions. Each job opens its own tracker browser with its first upload, keeps it logged in between its files and closes it when the job ends."));
  }
  async function loadPixhost() {
    if (!isRole("operator")) { pix.remove(); return; }
    const r = await api("GET", "/api/tools/pixhost");
    fill(pix, h("h2", {}, "Pixhost uploads (delete links)"),
      r.uploads.length ? h("table", {}, h("tbody", {}, r.uploads.slice(-200).reverse().map((u) => h("tr", {}, h("td", { class: "small" }, u.timestamp), h("td", { class: "small mono" }, u.key),
        h("td", {}, h("a", { href: u.show_url, target: "_blank", rel: "noopener" }, "view")),
        h("td", {}, h("button", { class: "small danger", onclick: async () => { if (!confirm("Delete this image from Pixhost?")) return; const x = await api("POST", "/api/tools/pixhost/delete", { manage_url: u.manage_url }); toast(x.ok ? "Deleted" : "Delete failed", x.ok ? "ok" : "error"); loadPixhost(); } }, "Delete"))))))
        : h("p", { class: "muted" }, "No pending Pixhost uploads."));
  }
  async function loadGpu(refresh) {
    if (refresh) fill(gpuPanel, "Testing every backend (a few seconds)…");
    const r = await api("GET", `/api/tools/gpu${refresh ? "?refresh=1" : ""}`);
    fill(gpuPanel, h("div", { class: "row" }, h("h2", { style: "margin:0" }, "GPU acceleration"), h("span", { class: "spacer" }), h("button", { class: "small", onclick: () => loadGpu(true) }, "Re-detect")),
      h("p", {}, "Setting: ", h("code", {}, r.setting), " · in use: ", r.selected ? badge(r.selected, "ok") : badge("CPU only", "")),
      h("table", {}, h("thead", {}, h("tr", {}, ["Backend", "Decode", "H.264", "HEVC", "AV1"].map((t) => h("th", {}, t)))),
        h("tbody", {}, r.backends.map((b) => h("tr", {}, h("td", {}, b.label), h("td", {}, badge(b.decode ? "yes" : "no", b.decode ? "ok" : "")),
          ["h264", "hevc", "av1"].map((c) => h("td", { class: "small mono" }, b.encoders[c] ? badge(b.encoders[c], "ok") : "—")))))),
      h("p", { class: "muted small" }, "Every backend is tested by actually decoding and encoding a short clip. Choose the backend and what it is used for in Settings → Application → Hardware acceleration; select CPU or GPU and HEVC or AV1 per job in the Encoding tab."));
  }
  loadGpu(false).catch((e) => fill(gpuPanel, e.message));
  loadFfmpeg(false).catch((e) => fill(ff, e.message));
  loadResources(); loadSessions(); loadPixhost();
}

// ------------------------------------------------------------------ resources
const fmtSize = (n) => (n > 1048576 ? `${(n / 1048576).toFixed(1)} MB` : n > 1024 ? `${(n / 1024).toFixed(1)} KB` : `${n} B`);
async function viewResources(selected) {
  const data = await api("GET", "/api/resources");
  selected = selected || data.template_file;
  const viewer = h("div", { class: "panel" });
  $view.replaceChildren(h("div", { class: "topbar" }, h("h1", {}, "Resources")),
    h("p", { class: "muted" }, "Files in the Resources folder: templates, fonts, image maps and models. Click a file to preview it. ",
      "Keep them up to date from GitHub in Tools > Resource sync."),
    h("div", { class: "panel" }, h("table", {}, h("thead", {}, h("tr", {}, ["File", "Type", "Size", "Modified", ""].map((t) => h("th", {}, t)))),
      h("tbody", {}, data.files.map((f) => h("tr", { class: "clickable" + (f.name === selected ? " selected" : ""), onclick: () => (location.hash = `#resources/${encodeURIComponent(f.name)}`) },
        h("td", { class: "mono small" }, f.name), h("td", {}, badge(f.kind, "")), h("td", { class: "small" }, fmtSize(f.size)),
        h("td", { class: "small" }, fmtTime(f.modified)),
        h("td", { class: "small muted" }, f.name === data.template_file ? "default template" : f.name === data.font_file ? "default font" : "")))))),
    viewer);
  const file = data.files.find((f) => f.name === selected);
  if (!file) return;
  const url = `${A.base}/api/resources/file/${encodeURIComponent(file.name)}`;
  if (file.kind === "template") {
    const r = await api("GET", `/api/resources/template-preview/${encodeURIComponent(file.name)}`);
    const frame = h("iframe", { sandbox: "allow-popups allow-popups-to-escape-sandbox", class: "template-frame", title: "Template preview" });
    frame.srcdoc = r.html;
    fill(viewer, h("div", { class: "row" }, h("h2", { style: "margin:0" }, `Template preview: ${file.name}`), h("span", { class: "spacer" }),
        h("span", { class: "muted small" }, "Sample data; images are placeholders, icons come from BBCode_Images.json")),
      h("p", { class: "small muted" }, "Placeholders used: ", r.placeholders.join(" ")),
      frame, h("details", {}, h("summary", {}, "Filled BBCode (what is pasted into the tracker)"), h("pre", { style: "max-height:420px" }, r.bbcode)));
  } else if (file.kind === "font") {
    const family = `res_${file.name.replace(/\W/g, "_")}`;
    try { const face = new FontFace(family, `url(${url})`); await face.load(); document.fonts.add(face); } catch (e) { toast(`Cannot load font: ${e.message}`, "error"); }
    const sample = h("div", { class: "font-sample", style: `font-family:'${family}'` }, "Studio - 2025-January-01 - Sample Title - Jane Doe");
    const input = h("input", { type: "text", value: sample.textContent, oninput: (e) => (sample.textContent = e.target.value) });
    fill(viewer, h("h2", {}, `Font: ${file.name}`), h("div", { class: "field" }, h("span", { class: "label" }, "Sample text"), input),
      sample, h("div", { class: "font-sample small-sizes", style: `font-family:'${family}'` },
        ["12", "18", "24", "36"].map((px) => h("div", { style: `font-size:${px}px` }, `${px}px  ABCDEFGHIJKLMNOPQRSTUVWXYZ abcdefghijklmnopqrstuvwxyz 0123456789 00:12:34`))));
  } else if (file.kind === "image") {
    fill(viewer, h("h2", {}, file.name), h("img", { src: url, style: "max-width:100%" }));
  } else if (["json", "template", "log", "other"].includes(file.kind) && file.size < 2_000_000) {
    const text = await (await fetch(url)).text();
    let shown = text;
    if (file.kind === "json") { try { shown = JSON.stringify(JSON.parse(text), null, 2); } catch {} }
    fill(viewer, h("h2", {}, file.name), h("pre", { style: "max-height:520px" }, shown));
  } else {
    fill(viewer, h("h2", {}, file.name), h("p", { class: "muted" }, "No preview for this file type."));
  }
}

// ------------------------------------------------------------------ wiki
async function viewWiki(page) {
  const index = await api("GET", "/api/wiki");
  page = page || index.pages[0]?.page;
  const content = page ? await api("GET", `/api/wiki/${page}`) : { html: "<p>No wiki pages.</p>" };
  fill($view, h("div", { class: "topbar" }, h("h1", {}, "Wiki / Guide")),
    h("div", { class: "tabs" }, index.pages.map((p) => h("button", { class: p.page === page ? "active" : "", onclick: () => (location.hash = `#wiki/${p.page}`) }, p.title))),
    h("div", { class: "panel wiki", html: content.html }));
}

// ------------------------------------------------------------------ users & account
async function viewUsers() {
  const data = await api("GET", "/api/users");
  const u = h("input", { type: "text", placeholder: "username" }), p = h("input", { type: "password", placeholder: "password (10+ chars)" });
  const role = h("select", {}, ["operator", "viewer", "admin"].map((r) => h("option", {}, r)));
  fill($view, h("div", { class: "topbar" }, h("h1", {}, "Users")),
    h("div", { class: "panel" }, h("table", {}, h("tbody", {}, data.users.map((x) => h("tr", {}, h("td", {}, x.username), h("td", {}, badge(x.role, "")),
      h("td", {}, h("button", { class: "small", onclick: async () => { const np = prompt(`New password for ${x.username}`); if (!np) return; try { await api("POST", `/api/users/${x.username}/password`, { new_password: np }); toast("Password changed", "ok"); } catch (e) { toast(e.message, "error"); } } }, "Reset password"), " ",
        x.username !== A.user.username ? h("button", { class: "small danger", onclick: async () => { if (!confirm(`Delete ${x.username}?`)) return; try { await api("DELETE", `/api/users/${x.username}`); viewUsers(); } catch (e) { toast(e.message, "error"); } } }, "Delete") : null)))))),
    h("div", { class: "panel" }, h("h2", {}, "Add user"), h("div", { class: "row", style: "flex-wrap:nowrap" }, u, p, role,
      h("button", { class: "primary", onclick: async () => { try { await api("POST", "/api/users", { username: u.value, password: p.value, role: role.value }); toast("User added", "ok"); viewUsers(); } catch (e) { toast(e.message, "error"); } } }, "Add")),
      h("p", { class: "muted small" }, "admin: everything · operator: jobs and tools · viewer: read only")));
}
async function viewAccount() {
  const cur = h("input", { type: "password", autocomplete: "current-password" }), np = h("input", { type: "password", autocomplete: "new-password" }), np2 = h("input", { type: "password", autocomplete: "new-password" });
  fill($view, h("div", { class: "topbar" }, h("h1", {}, "Account")),
    h("div", { class: "panel", style: "max-width:460px" }, h("p", {}, `Signed in as ${A.user.username} (${A.user.role})`),
      h("div", { class: "field" }, h("span", { class: "label" }, "Current password"), cur), h("div", { class: "field" }, h("span", { class: "label" }, "New password"), np),
      h("div", { class: "field" }, h("span", { class: "label" }, "Repeat new password"), np2),
      h("button", { class: "primary", style: "margin-top:10px", onclick: async () => {
        if (np.value !== np2.value) return toast("Passwords do not match", "error");
        try { await api("POST", `/api/users/${A.user.username}/password`, { current_password: cur.value, new_password: np.value }); toast("Password changed", "ok"); cur.value = np.value = np2.value = ""; } catch (e) { toast(e.message, "error"); }
      } }, "Change password")));
}

// ------------------------------------------------------------------ about
async function viewAbout() {
  const a = await api("GET", "/api/about");
  const ext = (href, text) => h("a", { href, target: "_blank", rel: "noopener" }, text);
  const status = h("span", { class: "muted" }, "checking…");
  fill($view, h("div", { class: "topbar" }, h("h1", {}, "About")),
    h("div", { class: "panel" },
      h("h2", {}, "File Prepare HF ", h("span", { class: "muted" }, `v${a.version}`)),
      h("p", {}, "Prepares video releases for upload: TPDB matching, renaming, HEVC/AV1 encoding, covers, thumbnail sheets, ",
        "animated previews, performer images, BBCode templates, image host uploads and tracker uploads, from a web interface with a job queue."),
      h("p", {}, "Updates: ", status, " · ", h("a", { href: "#", onclick: (e) => { e.preventDefault(); manualUpdateCheck(); } }, "Check now")),
      h("div", { class: "row", style: "gap:8px;flex-wrap:wrap;margin-top:10px" },
        h("a", { class: "btn", href: a.repository_url, target: "_blank", rel: "noopener" }, "GitHub repository"),
        h("a", { class: "btn primary", href: a.new_issue_url, target: "_blank", rel: "noopener" }, "Report an issue"),
        h("a", { class: "btn", href: a.issues_url, target: "_blank", rel: "noopener" }, "Open issues"))),
    h("div", { class: "panel" }, h("h2", {}, "Links"),
      h("ul", {},
        h("li", {}, "Repository: ", ext(a.repository_url, a.repository)),
        h("li", {}, "Stable branch: ", ext(a.branches.stable, a.branches.stable.split("/tree/")[1]), " · development branch: ", ext(a.branches.dev, a.branches.dev.split("/tree/")[1])),
        h("li", {}, "Documentation: ", h("a", { href: "#wiki" }, "Wiki / Guide"), " (also docs/ in the repository)"),
        h("li", {}, "License: ", ext(a.license_url, "MIT")))),
    h("div", { class: "panel" }, h("h2", {}, "Environment"),
      h("p", { class: "small muted" }, "Included automatically when you use Report an issue (paths are not)."),
      h("table", {}, h("tbody", {}, Object.entries(a.environment).map(([k, v]) => h("tr", {}, h("td", { class: "muted", style: "width:200px" }, k), h("td", { class: "mono small" }, v)))))));
  checkUpdates(false, false).then((r) => fill(status, r.channel === "off" ? "check is off" : r.error ? badge("check failed", "error") :
    r.update_available ? [badge(`v${r.latest} available`, "warning"), ` on the ${r.channel} channel`] :
    [badge(r.ahead ? "newer than channel" : "up to date", r.ahead ? "" : "ok"), ` (${r.channel} channel, latest v${r.latest})`])).catch((e) => fill(status, e.message));
}

// ------------------------------------------------------------------ version check
// Clicking the version in the sidebar checks now (the server allows one real check per 5 minutes).
async function manualUpdateCheck() {
  let r;
  try { r = await checkUpdates(true, false); } catch (e) { return toast(`Version check failed: ${e.message}`, "error"); }
  if (r.channel === "off") return toast(h("span", {}, "The update check is off. Choose a channel in ", h("a", { href: "#settings/app" }, "Settings › Application › Updates"), "."), "", 10000);
  const wait = r.retry_in ? ` Next check possible in ${Math.floor(r.retry_in / 60)}:${String(r.retry_in % 60).padStart(2, "0")}.` : "";
  const ago = Math.round((Date.now() / 1000 - r.checked_at) / 60);
  const when = r.fresh ? "" : ` (checked ${ago < 1 ? "less than a minute" : ago + " min"} ago.${wait})`;
  if (r.error) return toast(`Version check failed: ${r.error}${when}`, "error");
  if (r.update_available) return toast(h("span", {}, `Version ${r.latest} is available on the ${r.channel} channel (you have ${r.current}).${when} `,
    h("a", { href: r.url, target: "_blank", rel: "noopener" }, "View on GitHub"), " · ", h("a", { href: "#tools" }, "Details")), "warning", 20000);
  toast(`v${r.current} is ${r.ahead ? "newer than" : "the latest on"} the ${r.channel} channel (${r.branch} branch: v${r.latest}).${when}`, "ok", 10000);
}
document.getElementById("version-check")?.addEventListener("click", (e) => { e.preventDefault(); manualUpdateCheck(); });

async function checkUpdates(refresh = false, notify = true) {
  const r = await api("GET", `/api/updates${refresh ? "?refresh=1" : ""}`);
  const b = document.getElementById("update-badge");
  if (b) { b.textContent = r.update_available ? `v${r.latest} available` : ""; b.classList.toggle("hidden", !r.update_available); }
  if (notify && r.update_available) {
    let seen = null; try { seen = sessionStorage.getItem("update-toast"); } catch (e) {}
    if (seen !== r.latest) {
      toast(h("span", {}, `Version ${r.latest} is available on the ${r.channel} channel (you have ${r.current}). `,
        h("a", { href: r.url, target: "_blank", rel: "noopener" }, "View on GitHub"), " · ", h("a", { href: "#tools" }, "Details")), "warning", 20000);
      try { sessionStorage.setItem("update-toast", r.latest); } catch (e) {}
    }
  }
  return r;
}

api("GET", "/api/me").then((me) => { A.requirePreview = me.require_preview; }).catch(() => {});
checkUpdates().catch(() => {});
// "Report an issue" opens a new GitHub issue pre-filled with the version and environment
api("GET", "/api/about").then((a) => { const l = document.getElementById("report-issue"); if (l) l.href = a.new_issue_url; }).catch(() => {});
route();
