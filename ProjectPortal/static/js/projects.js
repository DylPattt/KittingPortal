/* Projects tab: list search / filter / sort. The detail panel lives in project_detail.js. */
(() => {
    "use strict";
    const $ = id => document.getElementById(id);
    const list = $("projectList");
    const cards = [...list.querySelectorAll(".project-card")];

    function applyListView() {
        const q = $("projectSearch").value.trim().toLowerCase();
        const f = $("statusFilter").value;
        cards.forEach(c => {
            const okSearch = c.dataset.search.includes(q);
            const okFilter = !f || (f === "overdue" ? +c.dataset.overdue > 0 : c.dataset.status === f);
            c.hidden = !(okSearch && okFilter);
        });
    }
    function applySort() {
        const key = $("projectSort").value;
        const num = (c, k) => parseFloat(c.dataset[k]) || 0;
        const sorters = {
            completion: (a, b) => num(a, "completion") - num(b, "completion"),
            remaining: (a, b) => num(b, "remaining") - num(a, "remaining"),
            overdue: (a, b) => num(b, "overdue") - num(a, "overdue"),
            name: (a, b) => a.dataset.project.localeCompare(b.dataset.project, undefined, { numeric: true }),
        };
        [...cards].sort(sorters[key]).forEach(c => list.append(c));
    }

    function selectCard(card, scroll) {
        cards.forEach(c => c.classList.toggle("selected-card", c === card));
        if (scroll) card.scrollIntoView({ block: "center" });
        ProjectDetail.show(card.dataset);
    }

    ProjectDetail.onKitChange = (id, status) => {
        const card = cards.find(c => c.dataset.id === id);
        if (card) card.dataset.kit = status;
    };

    $("projectSearch").addEventListener("input", applyListView);
    $("statusFilter").addEventListener("change", applyListView);
    $("projectSort").addEventListener("change", applySort);
    list.addEventListener("click", e => { const c = e.target.closest(".project-card"); if (c) selectCard(c); });
    list.addEventListener("keydown", e => {
        if (e.key !== "Enter" && e.key !== " ") return;
        const c = e.target.closest(".project-card");
        if (c) { e.preventDefault(); selectCard(c); }
    });

    // deep link from Critical parts / Admin / notifications: /projects?select=ID
    const pre = new URLSearchParams(location.search).get("select");
    const target = pre && cards.find(c => c.dataset.id === pre);
    if (target) selectCard(target, true);
})();
