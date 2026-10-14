(function (KPG) {
  "use strict";

  const panel = document.getElementById("detail");

  function appendText(tag, className, text, parent) {
    const element = document.createElement(tag);
    if (className) element.className = className;
    element.textContent = text;
    (parent || panel).append(element);
    return element;
  }

  function isLong(value) {
    return typeof value === "object" && value !== null;
  }

  function metadata(node) {
    const record = {...(node.record || {})};
    delete record.text;
    delete record.evidence;
    delete record.chunk_ids;
    delete record.claim_ids;
    const section = document.createElement("section");
    section.className = "detail-section";
    appendText("h3", "", "Record metadata", section);
    const list = document.createElement("dl");
    list.className = "metadata";
    Object.entries(record).forEach(([key, value]) => {
      const term = document.createElement("dt"); term.textContent = key;
      const detail = document.createElement("dd");
      if (isLong(value)) {
        const pre = document.createElement("pre"); pre.textContent = JSON.stringify(value, null, 2); detail.append(pre);
      } else detail.textContent = String(value ?? "");
      list.append(term, detail);
    });
    section.append(list);
    panel.append(section);
  }

  function related(index, node, edges, label, onSelect) {
    if (!edges.length) return;
    const section = document.createElement("section"); section.className = "detail-section";
    appendText("h3", "", label, section);
    const list = document.createElement("div"); list.className = "related";
    edges.forEach(edge => {
      const nextId = edge.source === node.id ? edge.target : edge.source;
      const other = index.nodes.get(nextId); if (!other) return;
      const button = document.createElement("button");
      button.type = "button";
      button.textContent = `${edge.relation} · ${other.group}: ${other.label}`;
      button.addEventListener("click", () => onSelect(other.id));
      list.append(button);
    });
    section.append(list); panel.append(section);
  }

  KPG.renderDetail = function (index, node, onSelect, onExplore) {
    panel.replaceChildren();
    if (!node) {
      appendText("h2", "", "Choose a record");
      appendText("p", "muted", "Select a topic, claim, or provenance node to inspect its saved details.");
      return;
    }
    appendText("h2", "", node.label);
    appendText("p", "muted", node.meta);
    if (node.detail) appendText("p", "detail-text", node.detail);
    const actions = document.createElement("div"); actions.className = "detail-actions";
    if (["claim", "candidate"].includes(node.group)) {
      const explore = document.createElement("button"); explore.type = "button"; explore.textContent = "Explore provenance";
      explore.addEventListener("click", () => onExplore("claim", node.id)); actions.append(explore);
    } else if (["chunk", "element", "source"].includes(node.group)) {
      const explore = document.createElement("button"); explore.type = "button"; explore.textContent = "Explore local graph";
      explore.addEventListener("click", () => onExplore("claim", node.id)); actions.append(explore);
    }
    if (node.group === "document") {
      const explore = document.createElement("button"); explore.type = "button"; explore.textContent = "Explore derived knowledge";
      explore.addEventListener("click", () => onExplore("document", node.id)); actions.append(explore);
    }
    if (actions.childElementCount) panel.append(actions);

    if (node.group === "source" && /^https?:\/\//.test(node.record.uri || "")) {
      const link = document.createElement("a"); link.href = node.record.uri; link.target = "_blank"; link.rel = "noreferrer"; link.textContent = "Open publisher source"; panel.append(link);
    }
    if (node.record.location) {
      const location = document.createElement("section"); location.className = "detail-section";
      appendText("h3", "", "Source location", location);
      appendText("p", "", JSON.stringify(node.record.location), location);
      panel.append(location);
    }
    if (Array.isArray(node.record.evidence) && node.record.evidence.length) {
      const evidence = document.createElement("section"); evidence.className = "detail-section";
      appendText("h3", "", "Evidence references", evidence);
      node.record.evidence.forEach((item, position) => {
        const row = document.createElement("p"); row.className = "detail-text";
        const element = index.nodes.get(item.element_id);
        const location = element?.record?.location ? ` · ${JSON.stringify(element.record.location)}` : "";
        row.textContent = `${position + 1}. ${item.element_id || "element unavailable"}${location}${item.span ? ` · span ${item.span.start}–${item.span.end}` : ""}`;
        evidence.append(row);
      });
      panel.append(evidence);
    }
    metadata(node);
    const allEdges = [...(index.incoming.get(node.id) || []), ...(index.outgoing.get(node.id) || [])];
    related(index, node, allEdges, "Connected records", onSelect);
    const code = document.createElement("code"); code.textContent = node.id; panel.append(code);
  };
})(window.KPG = window.KPG || {});
