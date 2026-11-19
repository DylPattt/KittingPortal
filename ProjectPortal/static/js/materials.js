(() => {
    "use strict";
    const $ = id => document.getElementById(id);
    const body = $("matTable").querySelector("tbody");
    const state = { q: "", supplier: "", sort: "open", open: false, page: 1 };
    let timer = null, inflight = null;

    function el(tag, cls, text) {
        const n = document.createElement(tag);
        if (cls) n.className = cls;
        if (text !== undefined && text !== null) n.textContent = text;
        return n;
    }
    const qty = n => (Math.round((n || 0) * 100) / 100).toString();
    const money = n => (n === null || n === undefined) ? "–" : "$" + Number(n).toFixed(2);

    // keep the view in the address bar so it can be bookmarked or shared
    function readUrl() {
        const p = new URLSearchParams(location.search);
        state.q = p.get("q") || ""; state.supplier = p.get("supplier") || "";
        state.sort = p.get("sort") || "open"; state.open = p.get("open") === "1";
        state.page = parseInt(p.get("page") || "1", 10) || 1;
        $("matSearch").value = state.q; $("matSupplier").value = state.supplier;
        $("matSort").value = state.sort; $("matOpen").checked = state.open;
    }
    function params() {
        const p = new URLSearchParams();
        if (state.q) p.set("q", state.q);
        if (state.supplier) p.set("supplier", state.supplier);
        if (state.sort !== "open") p.set("sort", state.sort);
        if (state.open) p.set("open", "1");
        if (state.page > 1) p.set("page", state.page);
        return p;
    }

    function load() {
        if (inflight) inflight.abort();
        inflight = new AbortController();
        const p = params();
        history.replaceState(null, "", p.toString() ? "?" + p : location.pathname);
        $("matCount").textContent = "Loading…";

        fetch("/api/materials?" + p, { signal: inflight.signal })
            .then(r => { if (!r.ok) throw new Error("Server returned " + r.status); return r.json(); })
            .then(render)
            .catch(e => { if (e.name !== "AbortError") $("matCount").textContent = "Could not load: " + e.message; });
    }

    function render(data) {
        state.page = data.page;
        $("matCount").textContent = `${data.total.toLocaleString()} part${data.total === 1 ? "" : "s"}` +
            (state.q || state.supplier || state.open ? " match" : "");
        $("matPage").textContent = `Page ${data.page} of ${data.pages}`;
        $("matPrev").disabled = data.page <= 1;
        $("matNext").disabled = data.page >= data.pages;

        const frag = document.createDocumentFragment();
        if (!data.rows.length) {
            const tr = el("tr"); const td = el("td", "empty", "No parts match.");
            td.colSpan = 8; tr.append(td); frag.append(tr);
        }
        data.rows.forEach(r => {
            const tr = el("tr", "clickable");
            tr.tabIndex = 0;
            tr.title = "Show every project using this part";
            const sup = el("td", "", r.supplier || "–");
            if (r.vendor_id) sup.append(el("div", "muted", "Vendor ID " + r.vendor_id));
            if (r.contact) sup.append(el("div", "muted", r.contact));
            if (r.website) {
                const a = el("a", "", "website"); a.href = r.website; a.target = "_blank"; a.rel = "noopener noreferrer";
                const d = el("div"); d.append(a); sup.append(d);
            }
            const poTd = el("td");
            if (r.po) { const a = el("a", "", r.po); a.href = "/po?po=" + encodeURIComponent(r.po); poTd.append(a); } else poTd.textContent = "–";
            tr.append(el("td", "", r.part_no), el("td", "", r.description), sup, el("td", "num", money(r.price)),
                      poTd, el("td", "", r.due_date || "–"), el("td", "num", r.uses), el("td", "num", qty(r.open_total)));
            const toggle = e => { if (e.target.closest("a")) return; toggleDetail(tr, r.part_key); };
            tr.addEventListener("click", toggle);
            tr.addEventListener("keydown", e => { if (e.key === "Enter") toggle(e); });
            frag.append(tr);
        });
        body.replaceChildren(frag);
    }

    function toggleDetail(tr, partKey) {
        const next = tr.nextElementSibling;
        if (next && next.classList.contains("detail-row")) { next.remove(); return; }
        const row = el("tr", "detail-row"); const td = el("td"); td.colSpan = 8;
        td.append(el("div", "muted", "Loading…")); row.append(td); tr.after(row);

        fetch("/api/materials/detail?part=" + encodeURIComponent(partKey))
            .then(r => r.json())
            .then(lines => {
                const t = el("table", "data-table data-table-sm");
                const h = el("tr");
                ["Project", "REQ", "Qty", "Received", "Open", "Due", "PO", "Supplier", "Price"].forEach(x => h.append(el("th", "", x)));
                const thead = el("thead"); thead.append(h); t.append(thead);
                const tb = el("tbody");
                lines.forEach(l => {
                    const r = el("tr");
                    const pj = el("td");
                    if (l.project_id) { const a = el("a", "", l.project_number); a.href = "/projects?select=" + l.project_id; pj.append(a); }
                    else pj.textContent = l.project_number;
                    r.append(pj, el("td", "", l.req), el("td", "num", qty(l.qty)), el("td", "num", qty(l.received)),
                             el("td", "num" + (l.open_qty > 0 ? " text-warn" : ""), l.open_qty > 0 ? qty(l.open_qty) : "–"),
                             el("td", "", l.due_date || "–"), el("td", "", l.po || "–"), el("td", "", l.supplier || "–"),
                             el("td", "num", money(l.price)));
                    tb.append(r);
                });
                t.append(tb);
                td.replaceChildren(t);
            })
            .catch(() => td.replaceChildren(el("div", "error", "Could not load part history.")));
    }

    $("matSearch").addEventListener("input", e => {
        clearTimeout(timer);
        timer = setTimeout(() => { state.q = e.target.value.trim(); state.page = 1; load(); }, 250);
    });
    $("matSupplier").addEventListener("change", e => { state.supplier = e.target.value; state.page = 1; load(); });
    $("matSort").addEventListener("change", e => { state.sort = e.target.value; state.page = 1; load(); });
    $("matOpen").addEventListener("change", e => { state.open = e.target.checked; state.page = 1; load(); });
    $("matPrev").addEventListener("click", () => { state.page--; load(); window.scrollTo(0, 0); });
    $("matNext").addEventListener("click", () => { state.page++; load(); window.scrollTo(0, 0); });

    readUrl();
    load();
})();
