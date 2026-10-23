(function (KPG) {
  "use strict";

  const svg = document.getElementById("graph");
  const canvas = svg.parentElement;
  const cardHeight = 82;
  const rowGap = 98;

  function positionColumnHeadings() {
    const headings = svg.querySelector(".column-headings");
    if (headings) headings.setAttribute("transform", `translate(0, ${canvas.scrollTop})`);
  }
  canvas.addEventListener("scroll", positionColumnHeadings, {passive: true});

  function svgElement(name, attrs) {
    const element = document.createElementNS("http://www.w3.org/2000/svg", name);
    Object.entries(attrs || {}).forEach(([key, value]) => element.setAttribute(key, value));
    return element;
  }

  function textLines(value, maxChars, maxLines) {
    const words = String(value || "").split(/\s+/).filter(Boolean);
    const lines = [""];
    words.forEach(word => {
      while (word.length > maxChars) {
        const last = lines.length - 1;
        if (lines[last].length) lines.push("");
        lines[lines.length - 1] = word.slice(0, maxChars);
        word = word.slice(maxChars);
      }
      if (!word) return;
      const last = lines.length - 1;
      if ((lines[last] + " " + word).trim().length > maxChars && lines[last]) lines.push(word);
      else lines[last] = (lines[last] + " " + word).trim();
    });
    return lines.slice(0, maxLines);
  }

  function nodeContext(node) {
    const record = node.record || {};
    if (node.group === "summary") return `Level ${record.level} · ${record.original_chunk_ids.length} original chunks · ${record.review_status}`;
    if (node.group === "summary_cluster") return `${record.summary_ids.length} summaries → level ${record.level}`;
    if (node.group === "topic") {
      const count = Array.isArray(record.claim_ids) ? record.claim_ids.length : 0;
      return `${count.toLocaleString()} claims`;
    }
    if (["claim", "candidate"].includes(node.group)) {
      const evidenceCount = Array.isArray(record.evidence) ? record.evidence.length : 0;
      const status = node.group === "candidate"
        ? node.classification_status || "status unavailable"
        : [...new Set(KPG.reviewStatuses(node))].join(" / ");
      return `${evidenceCount} evidence · ${status}`;
    }
    return "";
  }

  function applyFocus(focusId, view, broad) {
    if (!focusId) {
      svg.querySelectorAll(".node, .edge").forEach(element => element.classList.remove("is-dimmed", "is-related", "is-focused"));
      return;
    }
    const related = new Set([focusId]);
    const neighbors = view.index.neighbors.get(focusId) || [];
    neighbors.forEach(id => related.add(id));
    svg.querySelectorAll(".node").forEach(element => {
      const id = element.dataset.nodeId;
      const isRelated = related.has(id) || (broad && view.visible.has(id));
      element.classList.toggle("is-focused", id === focusId);
      element.classList.toggle("is-related", isRelated && id !== focusId);
      element.classList.toggle("is-dimmed", !isRelated);
    });
    svg.querySelectorAll(".edge").forEach(element => {
      const source = element.dataset.source;
      const target = element.dataset.target;
      const touchesFocus = source === focusId || target === focusId;
      const local = broad && view.visible.has(source) && view.visible.has(target);
      element.classList.toggle("is-focused", touchesFocus);
      element.classList.toggle("is-related", !touchesFocus && local);
      element.classList.toggle("is-dimmed", !touchesFocus && !local);
    });
  }

  KPG.renderGraph = function (index, view, state, onSelect) {
    const visibleNodes = [...view.visible].map(id => index.nodes.get(id)).filter(Boolean);
    const separateSummaryLevels = state.mode === "claim" && ["summary", "summary_cluster"].includes(view.root?.group);
    const columns = KPG.layoutColumns(visibleNodes, separateSummaryLevels);
    const sorted = columns.flatMap(column => column.nodes);
    if (!sorted.length) {
      svg.replaceChildren();
      svg.setAttribute("viewBox", "0 0 760 220");
      svg.setAttribute("width", "760");
      svg.setAttribute("height", "220");
      return;
    }

    const canvasWidth = canvas.clientWidth;
    const compact = canvasWidth < 520 && columns.length === 2;
    const availableWidth = Math.max(0, canvasWidth - (compact ? 16 : 0));
    const cardWidth = compact ? Math.max(128, Math.floor((availableWidth - 66) / 2)) : 222;
    const colGap = compact ? cardWidth + 22 : 258;
    const maxRows = Math.max(1, ...columns.map(column => column.nodes.length));
    const top = 54;
    const contentWidth = 44 + (columns.length - 1) * colGap + cardWidth;
    const minWidth = Math.min(680, availableWidth);
    const width = Math.max(minWidth, contentWidth, availableWidth);
    const xOffset = 22 + Math.max(0, (width - contentWidth) / 2);
    const height = Math.max(220, top + (maxRows - 1) * rowGap + cardHeight + 30);
    const positions = new Map();
    const centerColumns = ["claim", "document", "full"].includes(state.mode);
    columns.forEach((entry, column) => {
      const nodes = entry.nodes;
      const root = view.root && nodes.find(node => node.id === view.root.id);
      if (root) {
        const otherNodes = nodes.filter(node => node.id !== root.id);
        otherNodes.splice(Math.floor(otherNodes.length / 2), 0, root);
        entry.nodes = otherNodes;
      }
      const columnNodes = entry.nodes;
      const startY = top + (centerColumns ? ((maxRows - columnNodes.length) * rowGap) / 2 : 0);
      columnNodes.forEach((node, row) => positions.set(node.id, {
        x: xOffset + column * colGap,
        y: startY + row * rowGap,
      }));
    });

    svg.replaceChildren();
    svg.setAttribute("viewBox", `0 0 ${width} ${height}`);
    svg.setAttribute("width", String(width));
    svg.setAttribute("height", String(height));

    const headings = svgElement("g", {class: "column-headings", "pointer-events": "none"});
    headings.append(svgElement("rect", {x: 0, y: 0, width, height: 40, fill: "var(--surface)"}));
    columns.forEach((entry, column) => {
      const heading = svgElement("text", {x: xOffset + column * colGap, y: 28, class: "node-kind", "data-column": entry.key});
      heading.textContent = entry.label.toUpperCase();
      headings.append(heading);
    });

    index.graph.edges.forEach(edge => {
      if (!view.visible.has(edge.source) || !view.visible.has(edge.target)) return;
      const a = positions.get(edge.source), b = positions.get(edge.target);
      if (!a || !b) return;
      let d;
      if (b.x >= a.x) {
        d = `M ${a.x + cardWidth} ${a.y + cardHeight / 2} C ${a.x + cardWidth + 34} ${a.y + cardHeight / 2}, ${b.x - 34} ${b.y + cardHeight / 2}, ${b.x} ${b.y + cardHeight / 2}`;
      } else {
        d = `M ${a.x} ${a.y + cardHeight / 2} C ${a.x - 34} ${a.y + cardHeight / 2}, ${b.x + cardWidth + 34} ${b.y + cardHeight / 2}, ${b.x + cardWidth} ${b.y + cardHeight / 2}`;
      }
      const path = svgElement("path", {
        class: `edge${edge.evidence ? " evidence" : ""}`,
        d,
        "data-source": edge.source,
        "data-target": edge.target,
        "aria-label": edge.relation,
      });
      svg.append(path);
    });

    sorted.forEach(node => {
      const point = positions.get(node.id);
      const label = node.group === "candidate"
        ? `candidate claim, ${node.classification_status || "status unavailable"}`
        : node.group.replaceAll("_", " ");
      const card = svgElement("g", {
        class: `node ${node.group}${state.selectedId === node.id ? " selected" : ""}`,
        tabindex: "0",
        role: "button",
        "aria-label": `${label}: ${node.label}`,
        "data-node-id": node.id,
      });
      card.append(svgElement("rect", {x: point.x, y: point.y, width: cardWidth, height: cardHeight}));
      const kind = svgElement("text", {x: point.x + 11, y: point.y + 16, class: "node-kind"});
      kind.textContent = label.toUpperCase();
      card.append(kind);
      textLines(node.label, compact ? 18 : 30, 2).forEach((line, row) => {
        const text = svgElement("text", {x: point.x + 11, y: point.y + 37 + row * 14, class: "node-label"});
        text.textContent = line;
        card.append(text);
      });
      const context = nodeContext(node);
      if (context) {
        const subline = svgElement("text", {x: point.x + 11, y: point.y + 72, class: "node-context"});
        subline.textContent = context;
        card.append(subline);
      }
      card.addEventListener("click", () => onSelect(node.id));
      card.addEventListener("keydown", event => {
        if (event.key === "Enter" || event.key === " ") { event.preventDefault(); onSelect(node.id); }
      });
      card.addEventListener("pointerenter", () => applyFocus(node.id, {...view, index}, false));
      card.addEventListener("pointerleave", () => applySelectedFocus());
      card.addEventListener("focus", () => applyFocus(node.id, {...view, index}, false));
      card.addEventListener("blur", () => applySelectedFocus());
      svg.append(card);
    });
    svg.append(headings);

    function applySelectedFocus() {
      const selected = state.selectedId && view.visible.has(state.selectedId) ? state.selectedId : null;
      applyFocus(selected, {...view, index}, state.mode === "claim" && Boolean(view.root));
    }

    applySelectedFocus();
    if (["claim", "document"].includes(state.mode) && view.root) {
      const rootPoint = positions.get(view.root.id);
      if (rootPoint) {
        canvas.scrollTop = Math.max(0, rootPoint.y + cardHeight / 2 - canvas.clientHeight / 2);
      }
    }
    positionColumnHeadings();
  };

  KPG.svgElement = svgElement;
})(window.KPG = window.KPG || {});
