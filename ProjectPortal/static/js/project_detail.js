/* Shared project detail panel (Projects tab + Map).
   Usage: ProjectDetail.show({id, project, req, completion, remaining, bom, me, url, expected, cart})
   Optional hook: ProjectDetail.onKitChange = (projectId, status) => { ... } */
window.ProjectDetail = (() => {
    "use strict";

    const csrf = document.querySelector('meta[name="csrf"]').content;
    const $ = id => document.getElementById(id);
    const matList = $("material-list");
    const state = { id: null, overdue: false, sort: "due" };
    let inflight = null;

    // Build elements with textContent so spreadsheet text can never inject HTML.
    function el(tag, cls, text) {
        const n = document.createElement(tag);
        if (cls) n.className = cls;
        if (text !== undefined && text !== null) n.textContent = text;
        return n;
    }
    const msg = (cls, text) => matList.replaceChildren(el("div", cls, text));
    const fmtQty = n => (Math.round(n * 100) / 100).toString();
    const usDate = iso => iso ? `${iso.slice(5, 7)}/${iso.slice(8, 10)}/${iso.slice(0, 4)}` : "";

    async function api(url, opts = {}) {
        const res = await fetch(url, {
            ...opts,
            headers: { "X-CSRF-Token": csrf, "Content-Type": "application/json", ...(opts.headers || {}) },
        });
        const data = await res.json().catch(() => ({}));
        if (!res.ok) throw new Error(data.error || `Server returned ${res.status}`);
        return data;
    }

    function link(text, href, external) {
        const a = el("a", "", text);
        a.href = href;
        if (external) { a.target = "_blank"; a.rel = "noopener noreferrer"; }
        return a;
    }

    function show(d) {
        state.id = String(d.id);
        $("detail-project").textContent = d.project;
        $("detail-completion").textContent = (parseFloat(d.completion) || 0).toFixed(1) + "%";
        $("detail-remaining").textContent = d.remaining;
        $("detail-bom").textContent = d.bom;

        const sub = $("detail-sub");
        sub.replaceChildren(el("span", "", d.req || "No REQ"));
        if (d.me) sub.append(el("span", "pill", d.me));
        if (d.expected) sub.append(el("span", "", "Last open part due " + usDate(d.expected)));
        if (d.cart) {
            const onMap = location.pathname === "/map";
            sub.append(onMap ? el("span", "pill", d.cart) : link("On " + d.cart, "/map?project=" + d.id));
        }
        if ((d.url || "").startsWith("https://")) sub.append(link("Open BOM file", d.url, true));
        $("shortage-tools").hidden = false;
        loadShortages();
    }

    function loadShortages() {
        if (inflight) inflight.abort();
        inflight = new AbortController();
        const qs = `overdue=${state.overdue ? 1 : 0}&sort=${state.sort}`;
        $("csvLink").href = `/api/projects/${state.id}/shortages.csv?${qs}`;
        msg("empty", "Loading shortages…");

        api(`/api/projects/${state.id}/shortages?${qs}`, { signal: inflight.signal })
            .then(data => { renderKit(data.kit); renderLines(data.lines); })
            .catch(err => { if (err.name !== "AbortError") msg("error", "Could not load shortages: " + err.message); });
    }

    // ---------- kit request ----------
    function kitChanged(status) {
        if (typeof api_.onKitChange === "function") api_.onKitChange(state.id, status);
    }

    function renderKit(kit) {
        const box = $("kit-box");
        box.hidden = false;
        box.replaceChildren(el("h3", "", "Kit request"));
        const row = el("div", "kit-row");

        if (!kit) {
            const note = el("input", "field field-sm");
            note.placeholder = "Optional note"; note.maxLength = 500; note.setAttribute("aria-label", "Kit request note");
            const btn = el("button", "btn btn-primary btn-sm", "Mark kit for request");
            btn.addEventListener("click", () => {
                btn.disabled = true;
                api(`/api/projects/${state.id}/kit-request`, { method: "POST", body: JSON.stringify({ note: note.value }) })
                    .then(d => { renderKit(d.kit); kitChanged("requested"); })
                    .catch(e => { btn.disabled = false; alert(e.message); });
            });
            row.append(note, btn);
        } else {
            row.append(el("span", "pill pill-" + (kit.status === "sent" ? "ok" : "warn"), "Kit " + kit.status));
            row.append(el("span", "muted", `by ${kit.requested_by || "unknown"} · ${kit.created_at}${kit.note ? " · " + kit.note : ""}`));
            if (kit.status === "requested" && kit.can_cancel) {
                const c = el("button", "btn btn-sm", "Cancel request");
                c.addEventListener("click", () => {
                    api(`/api/kit-requests/${kit.id}/cancel`, { method: "POST" })
                        .then(() => { renderKit(null); kitChanged(""); }).catch(e => alert(e.message));
                });
                row.append(c);
            }
        }
        box.append(row);
    }

    // ---------- shortage rows + replacement finder ----------
    function metaLine(item) {
        const meta = el("div", "material-meta", [item.material, item.material_type, item.supplier].filter(Boolean).join(" · ") || "–");
        if (item.po) meta.append(document.createTextNode(" · "), link(item.po, "/po?po=" + encodeURIComponent(item.po)));
        return meta;
    }

    function renderLines(lines) {
        if (!lines.length) return msg("empty", state.overdue ? "No overdue shortages." : "No shortages found for this project.");
        const frag = document.createDocumentFragment();

        lines.forEach(item => {
            const card = el("div", "material-card");
            const main = el("div");
            main.append(
                el("div", "material-title", item.part_no || "(no part number)"),
                el("div", "material-description", item.description),
                metaLine(item),
                el("div", "material-open", "Open qty: " + fmtQty(item.open_qty)),
                el("div", item.overdue ? "material-overdue" : "material-due",
                   "Due: " + (item.due_date || "–") + (item.overdue ? ` (${item.days_late} days late)` : "")),
            );
            card.append(main);

            if (item.part_key) {
                const aside = el("div", "also-found");
                const btn = el("button", "btn btn-sm", item.received_elsewhere
                    ? `Received on ${item.received_elsewhere} other project${item.received_elsewhere > 1 ? "s" : ""}`
                    : "Find replacement");
                const panel = el("div", "replace-panel");
                panel.hidden = true;
                btn.addEventListener("click", () => toggleReplacements(panel, item.part_key));
                aside.append(btn);
                card.append(aside, panel);
            }
            frag.append(card);
        });
        matList.replaceChildren(frag);
    }

    function toggleReplacements(panel, partKey) {
        if (!panel.hidden) { panel.hidden = true; return; }
        panel.hidden = false;
        if (panel.dataset.loaded) return;
        panel.replaceChildren(el("div", "muted", "Looking for received stock…"));
        api(`/api/projects/${state.id}/replacements?part=${encodeURIComponent(partKey)}`)
            .then(rows => {
                panel.dataset.loaded = "1";
                if (!rows.length) return panel.replaceChildren(el("div", "muted", "No other project has received this part."));
                const wrap = el("div");
                wrap.append(el("div", "replace-title", "Already received on these projects (possible stock to borrow)"));
                const table = el("table", "data-table data-table-sm");
                const head = el("tr");
                ["Project", "REQ", "Received", "Ordered", "Project done", "PO"].forEach(h => head.append(el("th", "", h)));
                const thead = el("thead"); thead.append(head); table.append(thead);
                const body = el("tbody");
                rows.forEach(r => {
                    const tr = el("tr");
                    const td = el("td"); td.append(link(r.project_number, `/projects?select=${r.project_id}`));
                    tr.append(td, el("td", "", r.req), el("td", "num", fmtQty(r.received)), el("td", "num", fmtQty(r.qty)),
                              el("td", "num", r.completion.toFixed(0) + "%"), el("td", "", r.po));
                    body.append(tr);
                });
                table.append(body); wrap.append(table);
                panel.replaceChildren(wrap);
            })
            .catch(e => panel.replaceChildren(el("div", "error", "Could not load: " + e.message)));
    }

    $("overdueOnly").addEventListener("change", e => { state.overdue = e.target.checked; if (state.id) loadShortages(); });
    $("shortageSort").addEventListener("change", e => { state.sort = e.target.value; if (state.id) loadShortages(); });

    const api_ = { show, el, api, onKitChange: null };
    return api_;
})();
