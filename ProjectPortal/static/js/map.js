/* Cart map: everyone can browse and open projects; admins can edit the layout. */
(() => {
    "use strict";
    const $ = id => document.getElementById(id);
    const { el, api } = ProjectDetail;
    const canvas = $("mapCanvas");
    const GRID = 10;

    const wrap = $("mapWrap"), sizer = $("mapSizer"), layout = $("mapLayout");
    const state = { data: null, editing: false, cartId: null, projectId: null, zoom: null };
    const ZMIN = 0.15, ZMAX = 2;

    // ---------- zoom + pan ----------
    function saveZoom() { try { localStorage.setItem("kp-map-zoom", String(state.zoom)); } catch (e) { /* storage off */ } }
    function savedZoom() { try { return parseFloat(localStorage.getItem("kp-map-zoom")) || null; } catch (e) { return null; } }
    function fitZoom() { return clamp((wrap.clientWidth - 24) / state.data.width, ZMIN, 1); }

    function applyZoom(z, anchor) {
        const old = state.zoom || z;
        state.zoom = clamp(Math.round(z * 100) / 100, ZMIN, ZMAX);
        const { width: W, height: H } = state.data;
        // keep the point under the cursor (or the view centre) in place while zooming
        const r = wrap.getBoundingClientRect();
        const ax = anchor ? anchor.x - r.left : wrap.clientWidth / 2;
        const ay = anchor ? anchor.y - r.top : wrap.clientHeight / 2;
        const ux = (wrap.scrollLeft + ax) / old, uy = (wrap.scrollTop + ay) / old;
        canvas.style.width = W + "px"; canvas.style.height = H + "px";
        canvas.style.transform = `scale(${state.zoom})`;
        sizer.style.width = W * state.zoom + "px"; sizer.style.height = H * state.zoom + "px";
        wrap.scrollLeft = ux * state.zoom - ax; wrap.scrollTop = uy * state.zoom - ay;
        $("zoomLevel").textContent = Math.round(state.zoom * 100) + "%";
        // overview mode: when zoomed out, carts show big names instead of unreadable chips
        canvas.classList.toggle("overview", state.zoom < 0.5);
        canvas.style.setProperty("--inv", String(1 / state.zoom));
        saveZoom();
    }

    $("zoomIn").addEventListener("click", () => applyZoom(state.zoom * 1.25));
    $("zoomOut").addEventListener("click", () => applyZoom(state.zoom / 1.25));
    $("zoomFit").addEventListener("click", () => { applyZoom(fitZoom()); wrap.scrollTo(0, 0); });
    wrap.addEventListener("wheel", e => {
        if (!e.ctrlKey && !e.metaKey) return;              // plain wheel scrolls; Ctrl/⌘ + wheel zooms
        e.preventDefault();
        applyZoom(state.zoom * (e.deltaY < 0 ? 1.1 : 1 / 1.1), { x: e.clientX, y: e.clientY });
    }, { passive: false });

    // drag on empty floor to pan (in both modes)
    wrap.addEventListener("pointerdown", e => {
        if (e.button !== 0 || e.target.closest(".cart") || !e.target.closest(".map-canvas")) return;
        const start = { x: e.clientX, y: e.clientY, l: wrap.scrollLeft, t: wrap.scrollTop };
        wrap.setPointerCapture(e.pointerId);
        wrap.classList.add("panning");
        const move = ev => { wrap.scrollLeft = start.l - (ev.clientX - start.x); wrap.scrollTop = start.t - (ev.clientY - start.y); };
        const up = () => {
            wrap.classList.remove("panning");
            wrap.removeEventListener("pointermove", move); wrap.removeEventListener("pointerup", up); wrap.removeEventListener("pointercancel", up);
        };
        wrap.addEventListener("pointermove", move); wrap.addEventListener("pointerup", up); wrap.addEventListener("pointercancel", up);
    });

    // give the map the full width when wanted
    function setPanel(visible) {
        layout.classList.toggle("panel-hidden", !visible);
        $("panelToggle").textContent = visible ? "Hide details" : "Show details";
        $("panelToggle").setAttribute("aria-pressed", String(!visible));
    }
    $("panelToggle").addEventListener("click", () => setPanel(layout.classList.contains("panel-hidden")));
    const clamp = (v, lo, hi) => Math.min(Math.max(v, lo), hi);
    const snap = v => Math.round(v / GRID) * GRID;
    const pct = (v, total) => (v / total * 100) + "%";

    // ---------- loading + drawing ----------
    function load() {
        return api("/api/map").then(data => {
            state.data = data;
            applyZoom(state.zoom || savedZoom() || fitZoom());
            if ($("mapW")) { $("mapW").value = data.width; $("mapH").value = data.height; }
            draw();
            if (state.editing && state.cartId) openEditor(state.cartId);
            fillProjectOptions();
        }).catch(e => { canvas.replaceChildren(el("div", "error map-empty", "Could not load the map: " + e.message)); });
    }

    function place(node, c) {
        const { width: W, height: H } = state.data;
        node.style.left = pct(c.x, W); node.style.top = pct(c.y, H);
        node.style.width = pct(c.w, W); node.style.height = pct(c.h, H);
    }

    function draw() {
        const { carts } = state.data;
        // project numbers can repeat (different REQs): show the REQ on those chips so they can be told apart
        const counts = new Map();
        state.data.projects.forEach(p => counts.set(p.project, (counts.get(p.project) || 0) + 1));
        canvas.classList.toggle("editing", state.editing);
        const frag = document.createDocumentFragment();

        if (!carts.length) {
            frag.append(el("div", "map-empty", state.data.can_edit
                ? "No carts yet. Click “Edit layout”, then “Add cart”."
                : "No carts have been set up yet."));
        }

        carts.forEach(c => {
            const worst = c.projects.some(p => p.status === "bad") ? "bad"
                        : c.projects.some(p => p.status === "warn") ? "warn" : (c.projects.length ? "ok" : "");
            const node = el("div", "cart" + (worst ? ` cart-${worst}` : "") + (state.editing && c.id === state.cartId ? " selected-cart" : ""));
            node.dataset.id = c.id;
            place(node, c);
            if (state.editing) {
                node.tabIndex = 0;
                node.setAttribute("aria-label", `${c.name}. Drag to move, arrow keys to nudge.`);
            }

            const head = el("div", "cart-head");
            head.append(el("span", "cart-name", c.name));
            const summary = c.projects.length
                ? `${c.projects.length} · ${c.avg}%` + (c.overdue ? ` · ${c.overdue} late` : "")
                : "empty";
            head.append(el("span", "cart-summary" + (c.overdue ? " text-bad" : ""), summary));
            if (c.notes) head.title = c.notes;
            node.append(head);

            const body = el("div", "cart-body");
            c.projects.forEach(p => {
                const chip = el("button", `chip status-${p.status}` + (String(p.id) === state.projectId ? " selected" : ""));
                chip.type = "button";
                chip.dataset.pid = p.id;
                chip.dataset.search = (p.project + " " + p.req).toLowerCase();
                chip.title = `${p.project} · ${p.req || "No REQ"} · ${p.completion.toFixed(1)}%` +
                             (p.overdue ? ` · ${p.overdue} overdue` : "") + (p.kit ? ` · kit ${p.kit}` : "");
                chip.append(el("span", "", counts.get(p.project) > 1 ? `${p.project} · ${p.req || "no REQ"}` : p.project));
                if (p.overdue) chip.append(el("span", "chip-late", "!"));
                if (p.kit) chip.append(el("span", "chip-kit", p.kit === "sent" ? "sent" : "kit"));
                body.append(chip);
            });
            node.append(body);

            if (state.editing) {
                const handle = el("div", "cart-resize");
                handle.setAttribute("aria-hidden", "true");
                node.append(handle);
            }
            frag.append(node);
        });

        canvas.replaceChildren(frag);
        applySearch();
        $("mapHint").textContent = state.editing
            ? "Editing: drag carts to move them, drag a corner to resize, click a cart to rename it or assign projects. Drag empty floor to pan."
            : (carts.length ? `${carts.length} carts. Click a cart to zoom in, a project for details · drag empty floor to pan · Ctrl + scroll to zoom.` : "");
    }

    // ---------- viewing ----------
    function projectById(id) {
        return state.data.projects.find(p => String(p.id) === String(id));
    }

    function openProject(id) {
        const p = projectById(id);
        if (!p) return;
        state.projectId = String(id);
        setPanel(true);
        canvas.querySelectorAll(".chip").forEach(c => c.classList.toggle("selected", c.dataset.pid === state.projectId));
        ProjectDetail.show(p);
    }

    ProjectDetail.onKitChange = () => load();

    canvas.addEventListener("click", e => {
        if (state.editing) return;
        const chip = e.target.closest(".chip");
        if (chip) { openProject(chip.dataset.pid); return; }
        const node = e.target.closest(".cart");
        if (node && canvas.classList.contains("overview")) zoomToCart(+node.dataset.id);
    });

    function zoomToCart(id) {
        const c = state.data.carts.find(x => x.id === id);
        if (!c) return;
        applyZoom(1);
        wrap.scrollLeft = (c.x + c.w / 2) - wrap.clientWidth / 2;
        wrap.scrollTop = (c.y + c.h / 2) - wrap.clientHeight / 2;
    }

    function applySearch() {
        const q = $("mapSearch").value.trim().toLowerCase();
        canvas.classList.toggle("searching", !!q);
        canvas.querySelectorAll(".cart").forEach(cart => {
            let hit = false;
            cart.querySelectorAll(".chip").forEach(chip => {
                const m = !!q && chip.dataset.search.includes(q);
                chip.classList.toggle("match", m);
                hit = hit || m;
            });
            if (q && cart.querySelector(".cart-name").textContent.toLowerCase().includes(q)) hit = true;
            cart.classList.toggle("has-match", hit);
        });
    }
    $("mapSearch").addEventListener("input", applySearch);
    $("mapSearch").addEventListener("keydown", e => {
        if (e.key !== "Enter") return;
        const first = canvas.querySelector(".chip.match") || canvas.querySelector(".cart.has-match");
        if (!first) return;
        first.scrollIntoView({ block: "center", inline: "center", behavior: "smooth" });
        if (first.classList.contains("chip") && !state.editing) openProject(first.dataset.pid);
    });

    // ---------- editing (admins only; the server enforces this too) ----------
    const editToggle = $("editToggle");
    if (editToggle) {
        editToggle.addEventListener("click", () => {
            state.editing = !state.editing;
            editToggle.textContent = state.editing ? "Done editing" : "Edit layout";
            editToggle.setAttribute("aria-pressed", String(state.editing));
            $("editTools").hidden = !state.editing;
            $("cartEditor").hidden = true;
            $("detailPanel").hidden = false;
            state.cartId = null;
            draw();
        });

        $("addCart").addEventListener("click", () => {
            api("/api/map/carts", { method: "POST", body: "{}" })
                .then(r => { state.cartId = r.id; return load(); })
                .then(() => { const n = canvas.querySelector(`.cart[data-id="${state.cartId}"]`); if (n) n.scrollIntoView({ block: "center", inline: "center" }); })
                .catch(e => alert(e.message));
        });

        $("bulkAdd").addEventListener("click", () => {
            const body = { count: +$("bulkCount").value, prefix: $("bulkPrefix").value, start: +$("bulkStart").value };
            if (!confirm(`Add ${body.count} carts named “${body.prefix} ${body.start}” to “${body.prefix} ${body.start + body.count - 1}”?`)) return;
            api("/api/map/carts/bulk", { method: "POST", body: JSON.stringify(body) })
                .then(() => { $("bulkStart").value = body.start + body.count; return load(); })
                .catch(e => alert(e.message));
        });

        $("arrange").addEventListener("click", () => {
            if (!confirm("Line up every cart in rows, sorted by name? Current positions will be replaced.")) return;
            api("/api/map/arrange", { method: "POST" }).then(load).then(() => wrap.scrollTo(0, 0)).catch(e => alert(e.message));
        });

        $("resizeMap").addEventListener("click", () => {
            api("/api/map/size", { method: "POST", body: JSON.stringify({ width: +$("mapW").value, height: +$("mapH").value }) })
                .then(r => { if (r.note) alert(r.note); return load(); })
                .catch(e => alert(e.message));
        });

        // drag to move / drag corner to resize
        canvas.addEventListener("pointerdown", e => {
            const node = e.target.closest(".cart");
            if (!state.editing || !node || e.button !== 0) return;
            const cart = state.data.carts.find(c => c.id === +node.dataset.id);
            const resizing = e.target.classList.contains("cart-resize");
            const scale = state.data.width / canvas.getBoundingClientRect().width;
            const start = { px: e.clientX, py: e.clientY, x: cart.x, y: cart.y, w: cart.w, h: cart.h };
            let moved = false;
            node.setPointerCapture(e.pointerId);
            node.classList.add("dragging");

            const onMove = ev => {
                const dx = (ev.clientX - start.px) * scale, dy = (ev.clientY - start.py) * scale;
                if (Math.abs(dx) + Math.abs(dy) > 3) moved = true;
                const { width: W, height: H } = state.data;
                if (resizing) {
                    cart.w = clamp(snap(start.w + dx), 120, Math.min(600, W - cart.x));
                    cart.h = clamp(snap(start.h + dy), 80, Math.min(500, H - cart.y));
                } else {
                    cart.x = clamp(snap(start.x + dx), 0, W - cart.w);
                    cart.y = clamp(snap(start.y + dy), 0, H - cart.h);
                }
                place(node, cart);
            };
            const onUp = () => {
                node.removeEventListener("pointermove", onMove);
                node.removeEventListener("pointerup", onUp);
                node.removeEventListener("pointercancel", onUp);
                node.classList.remove("dragging");
                if (moved) saveBox(cart);
                else selectCart(cart.id);
            };
            node.addEventListener("pointermove", onMove);
            node.addEventListener("pointerup", onUp);
            node.addEventListener("pointercancel", onUp);
        });

        // keyboard: Enter opens the editor, arrows nudge (Shift = bigger steps)
        canvas.addEventListener("keydown", e => {
            const node = e.target.closest(".cart");
            if (!state.editing || !node) return;
            const cart = state.data.carts.find(c => c.id === +node.dataset.id);
            if (e.key === "Enter") { selectCart(cart.id); return; }
            const step = e.shiftKey ? 50 : GRID;
            const moves = { ArrowLeft: [-step, 0], ArrowRight: [step, 0], ArrowUp: [0, -step], ArrowDown: [0, step] };
            if (!moves[e.key]) return;
            e.preventDefault();
            cart.x = clamp(cart.x + moves[e.key][0], 0, state.data.width - cart.w);
            cart.y = clamp(cart.y + moves[e.key][1], 0, state.data.height - cart.h);
            place(node, cart);
            clearTimeout(node._t);
            node._t = setTimeout(() => saveBox(cart), 400);
        });

        $("cartSave").addEventListener("click", () => {
            api(`/api/map/carts/${state.cartId}`, { method: "POST",
                body: JSON.stringify({ name: $("cartName").value, notes: $("cartNotes").value }) })
                .then(() => { $("cartSaved").textContent = "Saved"; setTimeout(() => $("cartSaved").textContent = "", 1500); return load(); })
                .catch(e => alert(e.message));
        });

        $("assignBtn").addEventListener("click", assignFromInput);
        $("assignInput").addEventListener("keydown", e => { if (e.key === "Enter") assignFromInput(); });

        $("cartDelete").addEventListener("click", () => {
            const cart = state.data.carts.find(c => c.id === state.cartId);
            if (!cart || !confirm(`Delete ${cart.name}? Its ${cart.projects.length} project(s) become unassigned.`)) return;
            api(`/api/map/carts/${cart.id}/delete`, { method: "POST" })
                .then(() => { state.cartId = null; $("cartEditor").hidden = true; $("detailPanel").hidden = false; return load(); })
                .catch(e => alert(e.message));
        });
    }

    function saveBox(cart) {
        api(`/api/map/carts/${cart.id}`, { method: "POST", body: JSON.stringify({ x: cart.x, y: cart.y, w: cart.w, h: cart.h }) })
            .then(box => Object.assign(cart, box))
            .catch(e => { alert("Could not save position: " + e.message); load(); });
    }

    function selectCart(id) {
        state.cartId = id;
        canvas.querySelectorAll(".cart").forEach(n => n.classList.toggle("selected-cart", +n.dataset.id === id));
        openEditor(id);
    }

    function openEditor(id) {
        const cart = state.data.carts.find(c => c.id === id);
        if (!cart) return;
        $("detailPanel").hidden = true;
        $("cartEditor").hidden = false;
        $("cartName").value = cart.name;
        $("cartNotes").value = cart.notes;
        $("assignMsg").textContent = "";

        const list = $("cartProjects");
        if (!cart.projects.length) {
            list.replaceChildren(el("div", "muted", "No projects yet. Add one below."));
            return;
        }
        const frag = document.createDocumentFragment();
        cart.projects.forEach(p => {
            const row = el("div", "cart-project-row");
            row.append(el("span", `chip status-${p.status} chip-static`, p.project),
                       el("span", "muted", `${p.req || "No REQ"} · ${p.completion.toFixed(1)}%`));
            const rm = el("button", "btn btn-sm btn-quiet", "Remove");
            rm.setAttribute("aria-label", `Remove ${p.project} from ${cart.name}`);
            rm.addEventListener("click", () => {
                api("/api/map/unassign", { method: "POST", body: JSON.stringify({ project_id: p.id }) })
                    .then(load).catch(e => alert(e.message));
            });
            row.append(rm);
            frag.append(row);
        });
        list.replaceChildren(frag);
    }

    // project picker: labels are unique even when project numbers repeat
    let optionMap = new Map();
    function fillProjectOptions() {
        const dl = $("projectOptions");
        if (!dl) return;
        const seen = new Map();
        state.data.projects.forEach(p => {
            const base = `${p.project} · ${p.req || "No REQ"}`;
            seen.set(base, (seen.get(base) || 0) + 1);
        });
        optionMap = new Map();
        const frag = document.createDocumentFragment();
        state.data.projects.forEach(p => {
            let label = `${p.project} · ${p.req || "No REQ"}`;
            if (seen.get(label) > 1) label += ` (#${p.id})`;
            if (p.cart) label += ` · on ${p.cart}`;
            optionMap.set(label, p.id);
            const o = document.createElement("option"); o.value = label; frag.append(o);
        });
        dl.replaceChildren(frag);
    }

    function assignFromInput() {
        const input = $("assignInput");
        const text = input.value.trim();
        let pid = optionMap.get(text);
        if (!pid && text) {                      // allow typing just a project number or REQ if it's unambiguous
            const q = text.toLowerCase();
            const hits = state.data.projects.filter(p => p.project.toLowerCase() === q || p.req.toLowerCase() === q);
            if (hits.length === 1) pid = hits[0].id;
            else if (hits.length > 1) { $("assignMsg").textContent = "More than one project matches. Pick one from the list."; return; }
        }
        if (!pid) { $("assignMsg").textContent = "Pick a project from the list."; return; }
        api(`/api/map/carts/${state.cartId}/assign`, { method: "POST", body: JSON.stringify({ project_id: pid }) })
            .then(r => {
                input.value = "";
                $("assignMsg").textContent = r.moved_from ? `Moved from ${r.moved_from}.` : "";
                return load();
            })
            .catch(e => { $("assignMsg").textContent = e.message; });
    }

    // ---------- start ----------
    load().then(() => {
        const pre = new URLSearchParams(location.search).get("project");
        if (pre && projectById(pre)) {
            openProject(pre);
            const chip = canvas.querySelector(`.chip[data-pid="${CSS.escape(pre)}"]`);
            if (chip) chip.scrollIntoView({ block: "center", inline: "center" });
        }
    });
})();
