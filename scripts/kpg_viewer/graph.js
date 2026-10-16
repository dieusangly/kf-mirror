(function (KPG) {
  "use strict";

  const svg = document.getElementById("graph");
  const cardWidth = 204;
  const cardHeight = 64;
  const colGap = 250;
  const rowGap = 78;

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

  KPG.renderGraph = function (index, view, state, onSelect) {
    const visibleNodes = [...view.visible].map(id => index.nodes.get(id)).filter(Boolean);
    const sorted = KPG.groups.flatMap(group => visibleNodes.filter(node => node.group === group).sort((a, b) => a.label.localeCompare(b.label) || a.id.localeCompare(b.id)));
    if (!sorted.length) {
      svg.replaceChildren();
      svg.setAttribute("viewBox", "0 0 760 250");
      svg.setAttribute("height", "250");
      const background = svgElement("rect", {x: 0, y: 0, width: 760, height: 250, fill: "transparent"});
      svg.append(background);
      return;
    }
    const positions = new Map();
    const grouped = Object.fromEntries(KPG.groups.map(group => [group, sorted.filter(node => node.group === group)]));
    let maxRows = 1;
    const xOffset = 20;
    KPG.groups.forEach((group, column) => {
      const list = grouped[group];
      maxRows = Math.max(maxRows, list.length);
      list.forEach((node, row) => positions.set(node.id, {x: xOffset + column * colGap, y: 50 + row * rowGap}));
    });
    const width = Math.max(760, xOffset * 2 + (KPG.groups.length - 1) * colGap + cardWidth);
    const height = Math.max(210, 58 + maxRows * rowGap);
    svg.replaceChildren();
    svg.setAttribute("viewBox", `0 0 ${width} ${height}`);
    svg.setAttribute("width", width);
    svg.setAttribute("height", height);
    KPG.groups.forEach((group, column) => {
      if (!grouped[group].length) return;
      const heading = svgElement("text", {x: xOffset + column * colGap, y: 28, class: "node-kind"});
      heading.textContent = KPG.groupLabels[group].toUpperCase();
      svg.append(heading);
    });
    index.graph.edges.forEach(edge => {
      if (!view.visible.has(edge.source) || !view.visible.has(edge.target)) return;
      const a = positions.get(edge.source), b = positions.get(edge.target);
      if (!a || !b) return;
      let d;
      if (b.x >= a.x) {
        d = `M ${a.x + cardWidth} ${a.y + cardHeight / 2} C ${a.x + cardWidth + 32} ${a.y + cardHeight / 2}, ${b.x - 32} ${b.y + cardHeight / 2}, ${b.x} ${b.y + cardHeight / 2}`;
      } else {
        d = `M ${a.x} ${a.y + cardHeight / 2} C ${a.x - 32} ${a.y + cardHeight / 2}, ${b.x + cardWidth + 32} ${b.y + cardHeight / 2}, ${b.x + cardWidth} ${b.y + cardHeight / 2}`;
      }
      svg.append(svgElement("path", {class: `edge${edge.evidence ? " evidence" : ""}`, d}));
    });
    sorted.forEach(node => {
      const point = positions.get(node.id);
      const groupLabel = node.group === "candidate" ? `candidate claim, ${node.classification_status || "status unavailable"}` : node.group.replaceAll("_", " ");
      const group = svgElement("g", {class: `node ${node.group}${state.selectedId === node.id ? " selected" : ""}`, tabindex: "0", role: "button", "aria-label": `${groupLabel}: ${node.label}`});
      group.append(svgElement("rect", {x: point.x, y: point.y, width: cardWidth, height: cardHeight}));
      const kind = svgElement("text", {x: point.x + 10, y: point.y + 15, class: "node-kind"});
      kind.textContent = node.group === "candidate"
        ? `CANDIDATE · ${(node.classification_status || "STATUS UNKNOWN").toUpperCase()}`
        : groupLabel.toUpperCase();
      group.append(kind);
      textLines(node.label, 28, 2).forEach((line, row) => {
        const label = svgElement("text", {x: point.x + 10, y: point.y + 34 + row * 14, class: "node-label"});
        label.textContent = line;
        group.append(label);
      });
      group.addEventListener("click", () => onSelect(node.id));
      group.addEventListener("keydown", event => {
        if (event.key === "Enter" || event.key === " ") { event.preventDefault(); onSelect(node.id); }
      });
      svg.append(group);
    });
  };

  KPG.svgElement = svgElement;
})(window.KPG = window.KPG || {});
