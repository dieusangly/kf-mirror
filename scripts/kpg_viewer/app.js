(function (KPG) {
  "use strict";

  const index = KPG.createIndex(window.graphData);
  const el = id => document.getElementById(id);
  const controls = {
    mode: el("view-mode"), topic: el("topic-filter"), document: el("document-filter"),
    build: el("build-filter"), review: el("review-filter"), type: el("type-filter"),
  };
  const pageNode = el("pager");
  const viewTitle = el("view-title");
  const summary = el("view-summary");
  const visibleCount = el("visible-count");
  const mainLayout = el("main-layout");
  const detailToggle = el("detail-toggle");
  const state = {mode: "topic", topic: "", document: "", selectedId: null, page: 0, fullConfirmed: false};

  function addOptions(select, firstLabel, values) {
    select.add(new Option(firstLabel, ""));
    values.forEach(([label, value]) => select.add(new Option(label, value)));
  }

  function optionsFromRecords(getter) {
    return [...new Set(index.graph.nodes.map(getter).filter(Boolean))].sort((a, b) => String(a).localeCompare(String(b)));
  }

  function setUpControls() {
    addOptions(controls.topic, "All topics", KPG.sortedNodes(index, "topic").map(node => [node.label, node.id]));
    addOptions(controls.document, "All documents", KPG.sortedNodes(index, "document").map(node => [node.label, node.id]));
    addOptions(controls.build, "All builds", optionsFromRecords(node => node.record?.build_id).map(value => [value, value]));
    const reviewValues = [...new Set(index.graph.nodes.flatMap(node => KPG.reviewStatuses(node)))].sort((a, b) => a.localeCompare(b));
    addOptions(controls.review, "All review states", reviewValues.map(value => [value, value]));
    addOptions(controls.type, "All node types", KPG.groups.map(group => [KPG.groupLabels[group], group]));
  }

  function currentFilters() {
    return {build: controls.build.value, review: controls.review.value, type: controls.type.value, document: controls.document.value};
  }

  function onSelect(id) {
    state.selectedId = id;
    const node = index.nodes.get(id);
    if (node?.group === "topic") {
      state.mode = "topic"; controls.mode.value = "topic"; controls.topic.value = node.id;
      state.page = 0;
    }
    if (node?.group === "document") {
      state.mode = "document"; controls.mode.value = "document"; controls.document.value = node.id;
    }
    KPG.renderDetail(index, node, onSelect, onExplore);
    render();
  }

  function onExplore(mode, id) {
    state.mode = mode;
    state.selectedId = id;
    controls.mode.value = mode;
    if (mode === "document") controls.document.value = id;
    render();
  }

  function modeDescription(view) {
    if (state.mode === "summaries") return `${view.availableSummaries.toLocaleString()} summaries · page ${view.page + 1} of ${view.pageCount}`;
    if (state.mode === "topic") return `${view.availableClaims.toLocaleString()} matching claims · page ${view.page + 1} of ${view.pageCount}`;
    if (state.mode === "claim" && ["summary", "summary_cluster"].includes(view.root?.group)) {
      return "Higher-level summaries are on the left; follow their inputs to the right.";
    }
    if (state.mode === "claim") return view.root
      ? (["claim", "candidate"].includes(view.root.group) ? "Source provenance for the selected record." : `Local graph around ${view.root.group}`)
      : "Select a claim in Topics and claims, then use Explore provenance.";
    if (state.mode === "document") return view.root
      ? `${view.availableClaims.toLocaleString()} knowledge records · page ${view.page + 1} of ${view.pageCount} · connected to ${view.root.label}`
      : "Choose a document to inspect its claims and summaries.";
    return "All graph records matching the active filters.";
  }

  function renderPager(view) {
    pageNode.replaceChildren();
    if (!["topic", "summaries", "document"].includes(state.mode) || view.pageCount < 2) return;
    const previous = document.createElement("button"); previous.type = "button"; previous.textContent = "Previous"; previous.disabled = view.page === 0;
    previous.addEventListener("click", () => { state.page -= 1; render(); });
    const pageInput = document.createElement("input");
    pageInput.type = "number"; pageInput.min = "1"; pageInput.max = String(view.pageCount); pageInput.step = "1";
    pageInput.value = String(view.page + 1); pageInput.setAttribute("aria-label", "Go to page");
    pageInput.addEventListener("change", () => {
      const requestedPage = Number.parseInt(pageInput.value, 10);
      state.page = Number.isFinite(requestedPage)
        ? Math.min(Math.max(requestedPage, 1), view.pageCount) - 1
        : view.page;
      render();
    });
    const next = document.createElement("button"); next.type = "button"; next.textContent = "Next"; next.disabled = view.page + 1 >= view.pageCount;
    next.addEventListener("click", () => { state.page += 1; render(); });
    pageNode.append(previous, pageInput, document.createTextNode(`of ${view.pageCount}`), next);
  }

  function drawEmpty(message, title) {
    const svg = el("graph");
    svg.replaceChildren(); svg.setAttribute("viewBox", "0 0 760 250"); svg.setAttribute("width", "760"); svg.setAttribute("height", "250");
    const rect = KPG.svgElement("rect", {x: 0, y: 0, width: 760, height: 250, fill: "transparent"}); svg.append(rect);
    const text = KPG.svgElement("text", {x: 24, y: 92, class: "node-label"}); text.textContent = title; svg.append(text);
    const note = KPG.svgElement("text", {x: 24, y: 122, class: "node-kind"}); note.textContent = message; svg.append(note);
  }

  function render() {
    state.topic = controls.topic.value;
    state.document = controls.document.value;
    const view = KPG.computeView(index, {mode: state.mode, topic: state.topic, document: state.document,
      selectedId: state.selectedId, filters: currentFilters(), page: state.page});
    viewTitle.textContent = {topic: "Topics and claims", summaries: "Summaries", claim: view.root?.group === "summary" ? "Summary provenance" : view.root && !["claim", "candidate"].includes(view.root.group) ? "Local graph" : "Claim provenance", document: "Document-derived knowledge", full: "Full graph / debug"}[state.mode];
    summary.textContent = modeDescription(view);
    visibleCount.textContent = `${view.visible.size.toLocaleString()} nodes in view`;
    renderPager(view);
    if (view.needsClaim) drawEmpty("Select a claim in Topics and claims to load its local provenance.", "No claim selected");
    else if (view.needsDocument) drawEmpty("Select a document in the filter above to inspect related knowledge.", "No document selected");
    else if (view.needsFullConfirmation && !state.fullConfirmed) {
      drawEmpty("This view can render a large graph. Select Render full graph to continue.", `${view.visible.size.toLocaleString()} records match the filters.`);
      const button = document.createElement("button"); button.type = "button"; button.textContent = "Render full graph";
      button.addEventListener("click", () => { state.fullConfirmed = true; render(); });
      pageNode.replaceChildren(button);
    } else KPG.renderGraph(index, view, state, onSelect);
    if (state.mode === "full" && !view.needsFullConfirmation) state.fullConfirmed = true;
  }

  controls.mode.addEventListener("change", () => { state.mode = controls.mode.value; state.page = 0; state.fullConfirmed = false; render(); });
  detailToggle.addEventListener("click", () => {
    const expanded = detailToggle.getAttribute("aria-expanded") === "true";
    detailToggle.setAttribute("aria-expanded", String(!expanded));
    detailToggle.textContent = expanded ? "Show details" : "Hide details";
    mainLayout.classList.toggle("details-collapsed", expanded);
  });
  [controls.topic, controls.document, controls.build, controls.review, controls.type].forEach(control => control.addEventListener("change", () => {
    state.page = 0;
    state.fullConfirmed = false;
    if (control === controls.document && state.mode === "document" && control.value) state.selectedId = control.value;
    render();
  }));
  let resizeFrame = 0;
  window.addEventListener("resize", () => {
    if (state.mode === "full") return;
    window.cancelAnimationFrame(resizeFrame);
    resizeFrame = window.requestAnimationFrame(() => {
      resizeFrame = 0;
      render();
    });
  });
  setUpControls();
  render();
})(window.KPG = window.KPG || {});
