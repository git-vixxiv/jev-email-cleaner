(() => {
  "use strict";
  const DATA = JSON.parse(document.getElementById("plan-data").textContent);
  const PAGE = 200;
  const STORE_KEY = "jevclean:" + DATA.plan_id;
  const $ = (id) => document.getElementById(id);

  // ── state ──────────────────────────────────────────────
  const choices = new Map();      // id -> "keep" | "delete": only explicit choices
  try {
    const saved = JSON.parse(localStorage.getItem(STORE_KEY) || "{}");
    for (const [id, c] of Object.entries(saved)) choices.set(id, c);
  } catch (_) { /* storage unavailable: start clean */ }
  const byId = new Map(DATA.emails.map((e) => [e.id, e]));
  let tab = "delete", limit = PAGE, undoSnapshot = null, toastTimer = 0;

  const effective = (e) => choices.get(e.id) || (e.action === "review" ? null : e.action);
  const isChanged = (e) => choices.has(e.id) && choices.get(e.id) !== e.action;

  function persist() {
    try {
      const out = {};
      for (const [id, c] of choices) if (isChanged(byId.get(id))) out[id] = c;
      localStorage.setItem(STORE_KEY, JSON.stringify(out));
    } catch (_) { /* best effort */ }
  }

  // ── dom helper: text only, never innerHTML with mail content ──
  function h(tag, attrs, ...kids) {
    const el = document.createElement(tag);
    for (const [k, v] of Object.entries(attrs || {})) {
      if (v === false || v == null) continue;
      if (k === "class") el.className = v; else el.setAttribute(k, v === true ? "" : v);
    }
    for (const kid of kids.flat()) if (kid != null) el.append(kid instanceof Node ? kid : String(kid));
    return el;
  }

  // ── filtering ──────────────────────────────────────────
  function filtered() {
    const q = $("q").value.trim().toLowerCase();
    const topic = $("topic").value;
    return DATA.emails.filter((e) =>
      e.action === tab &&
      (!topic || e.topic === topic) &&
      (!q || (e.name + " " + e.from + " " + e.subject + " " + e.topic).toLowerCase().includes(q)));
  }

  // ── rows ───────────────────────────────────────────────
  function decideControl(e) {
    const wrap = h("div", { class: "decide", role: "radiogroup", "aria-label": "Decision for " + (e.subject || "message") });
    for (const value of ["keep", "delete"]) {
      const id = "d-" + e.id + "-" + value;
      wrap.append(
        h("input", { type: "radio", name: "d-" + e.id, id, value, checked: effective(e) === value, "data-id": e.id }),
        h("label", { for: id }, value === "keep" ? "Keep" : "Delete"));
    }
    return wrap;
  }

  function row(e) {
    const meta = h("div", { class: "row__meta mono" },
      h("span", null, e.topic),
      h("span", null, e.age + "d old"),
      h("span", { title: "Probability this is a disposable kind of mail" },
        h("span", { class: "meter", "aria-hidden": "true" }, h("i", { style: "width:" + Math.round(e.disposable * 100) + "%" })),
        Math.round(e.disposable * 100) + "% disposable"),
      e.unread ? h("span", null, "unread") : null,
      isChanged(e) ? h("span", { class: "flag" }, "changed") : null);
    return h("li", { class: "row" + (isChanged(e) ? " is-changed" : ""), "data-id": e.id },
      decideControl(e),
      h("div", null,
        h("div", { class: "row__who" }, e.name || e.from, e.name ? h("span", { class: "addr" }, e.from) : null),
        h("a", { class: "row__subject", href: e.url, target: "_blank", rel: "noopener noreferrer" }, e.subject || "(no subject)"),
        meta,
        e.reasons.length ? h("div", { class: "row__why" }, e.reasons.join(" · ")) : null));
  }

  function renderRows() {
    const items = filtered();
    $("shown").textContent = items.length + " shown";
    const panel = $("panel");
    panel.replaceChildren();
    if (!items.length) {
      panel.append(h("p", { class: "empty" }, $("q").value || $("topic").value
        ? "No rows match this filter. Clear it to see the full list."
        : tab === "delete" ? "Nothing is suggested for deletion. Loosen the policy thresholds in config.yaml and re-run plan."
        : tab === "review" ? "No borderline mail. Every email was confidently kept or suggested for deletion."
        : "Nothing in this bucket."));
      return;
    }
    const list = h("ol", { class: "ledger" });
    items.slice(0, limit).forEach((e) => list.append(row(e)));
    panel.append(list);
    if (items.length > limit) {
      const more = h("button", { class: "btn more", type: "button" }, "Show " + Math.min(PAGE, items.length - limit) + " more");
      more.addEventListener("click", () => { limit += PAGE; renderRows(); });
      panel.append(more);
    }
  }

  function renderSenders() {
    $("shown").textContent = "";
    const q = $("q").value.trim().toLowerCase();
    const rows = DATA.senders.filter((s) => !q || (s.name + " " + s.sender).toLowerCase().includes(q));
    const panel = $("panel");
    panel.replaceChildren();
    if (!rows.length) {
      panel.append(h("p", { class: "empty" }, "No sender sent three or more judged emails with an unsubscribe link."));
      return;
    }
    const body = h("tbody");
    for (const s of rows) {
      const del = h("button", { class: "btn", type: "button" }, "Delete all " + s.ids.length);
      del.addEventListener("click", () => bulk(s.ids, "delete", "Marked " + s.ids.length + " from " + (s.name || s.sender) + " as delete."));
      body.append(h("tr", null,
        h("td", { class: "who" }, s.name || s.sender, s.name ? h("span", null, s.sender) : null),
        h("td", { "data-label": "Emails" }, s.count),
        h("td", { "data-label": "Unread" }, Math.round(s.unread_rate * 100) + "%"),
        h("td", { "data-label": "Deletable" }, Math.round(s.delete_rate * 100) + "%"),
        h("td", { "data-label": "Act" }, h("div", { class: "acts" }, del,
          s.unsubscribe ? h("a", { class: "btn", href: s.unsubscribe, target: "_blank", rel: "noopener noreferrer" }, "Unsubscribe") : null))));
    }
    panel.append(h("table", { class: "senders" },
      h("thead", null, h("tr", null, ["Sender", "Emails", "Unread", "Deletable", ""].map((t) => h("th", { scope: "col" }, t)))),
      body));
  }

  function render() {
    $("bulk").hidden = tab === "senders";
    $("topic").hidden = tab === "senders";
    (tab === "senders" ? renderSenders : renderRows)();
    tally();
  }

  // ── tally + export state ───────────────────────────────
  function tally() {
    let del = 0, keep = 0, undecided = 0, changed = 0;
    for (const e of DATA.emails) {
      const eff = effective(e);
      if (eff === "delete") del++; else if (eff === "keep") keep++; else undecided++;
      if (isChanged(e)) changed++;
    }
    $("t-delete").textContent = del;
    $("t-keep").textContent = keep;
    $("t-review").textContent = undecided;
    $("t-changed").textContent = changed;
    $("export-n").textContent = changed;
    $("export").disabled = changed === 0;
  }

  // ── actions ────────────────────────────────────────────
  function setChoice(id, value) {
    const e = byId.get(id);
    if (e.action === value) choices.delete(id); else choices.set(id, value);
  }

  function bulk(ids, value, message) {
    undoSnapshot = ids.map((id) => [id, choices.get(id)]);
    ids.forEach((id) => setChoice(id, value));
    persist(); render();
    toast(message);
  }

  function undo() {
    if (!undoSnapshot) return;
    for (const [id, prev] of undoSnapshot) prev ? choices.set(id, prev) : choices.delete(id);
    undoSnapshot = null; persist(); render(); hideToast();
  }

  function toast(message) {
    $("toast-msg").textContent = message;
    $("toast").hidden = false;
    clearTimeout(toastTimer);
    toastTimer = setTimeout(hideToast, 6000);
  }
  function hideToast() { $("toast").hidden = true; }

  function exportDecisions() {
    const decisions = {};
    for (const e of DATA.emails) if (isChanged(e)) decisions[e.id] = choices.get(e.id);
    const blob = new Blob([JSON.stringify({ plan_id: DATA.plan_id, exported: new Date().toISOString(), decisions }, null, 1)],
      { type: "application/json" });
    const a = h("a", { href: URL.createObjectURL(blob), download: "jev-decisions-" + DATA.plan_id + ".json" });
    document.body.append(a); a.click(); a.remove();
    setTimeout(() => URL.revokeObjectURL(a.href), 1000);
  }

  // ── wiring ─────────────────────────────────────────────
  $("panel").addEventListener("change", (ev) => {
    const input = ev.target.closest("input[type=radio][data-id]");
    if (!input) return;
    setChoice(input.dataset.id, input.value);
    persist();
    const li = input.closest(".row");
    li.replaceWith(row(byId.get(input.dataset.id)));
    const again = document.getElementById(input.id);
    if (again) again.focus({ preventScroll: true });
    tally();
  });

  document.querySelectorAll(".tab").forEach((btn) => btn.addEventListener("click", () => {
    tab = btn.dataset.tab; limit = PAGE;
    document.querySelectorAll(".tab").forEach((b) => b.setAttribute("aria-selected", String(b === btn)));
    render();
  }));

  document.querySelectorAll("[data-bulk]").forEach((btn) => btn.addEventListener("click", () => {
    const ids = filtered().map((e) => e.id);
    if (ids.length) bulk(ids, btn.dataset.bulk, "Marked " + ids.length + " shown as " + btn.dataset.bulk + ".");
  }));

  let debounce = 0;
  $("q").addEventListener("input", () => { clearTimeout(debounce); debounce = setTimeout(() => { limit = PAGE; render(); }, 120); });
  $("q").addEventListener("keydown", (ev) => { if (ev.key === "Escape") { $("q").value = ""; render(); } });
  $("topic").addEventListener("change", () => { limit = PAGE; render(); });
  $("find").addEventListener("click", () => $("q").focus());
  $("export").addEventListener("click", exportDecisions);
  $("toast-undo").addEventListener("click", undo);
  document.addEventListener("keydown", (ev) => {
    const typing = /^(INPUT|SELECT|TEXTAREA)$/.test(document.activeElement.tagName) && document.activeElement.type !== "radio";
    if ((ev.key === "/" && !typing) || (ev.key.toLowerCase() === "k" && (ev.metaKey || ev.ctrlKey))) {
      ev.preventDefault(); $("q").focus(); $("q").select();
    }
  });

  // ── boot ───────────────────────────────────────────────
  $("meta").textContent = "Plan " + DATA.generated + " · " + DATA.emails.length + " judged";
  $("t-archive").textContent = DATA.archive_count;
  for (const t of ["delete", "review", "keep"]) $("n-" + t).textContent = DATA.emails.filter((e) => e.action === t).length;
  $("n-senders").textContent = DATA.senders.length;
  [...new Set(DATA.emails.map((e) => e.topic))].sort().forEach((t) => $("topic").append(h("option", { value: t }, t)));
  if (!DATA.emails.some((e) => e.action === "delete")) {
    tab = DATA.emails.some((e) => e.action === "review") ? "review" : "keep";
    document.querySelectorAll(".tab").forEach((b) => b.setAttribute("aria-selected", String(b.dataset.tab === tab)));
  }
  render();
})();
