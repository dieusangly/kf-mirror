(function (KPG) {
  "use strict";

  const panel = document.getElementById("detail-content");

  function appendText(tag, className, text, parent) {
    const element = document.createElement(tag);
    if (className) element.className = className;
    element.textContent = text;
    (parent || panel).append(element);
    return element;
  }

  function addSection(title, parent) {
    const section = document.createElement("section");
    section.className = "detail-section";
    appendText("h3", "", title, section);
    parent.append(section);
    return section;
  }

  function appendRelated(index, node, edges, parent, onSelect) {
    const list = document.createElement("div");
    list.className = "related";
    edges.forEach(edge => {
      const nextId = edge.source === node.id ? edge.target : edge.source;
      const other = index.nodes.get(nextId);
      if (!other) return;
      const button = document.createElement("button");
      button.type = "button";
      button.textContent = `${KPG.relationLabel(edge, node.id)} · ${KPG.groupLabels[other.group] || other.group}: ${other.label}`;
      button.addEventListener("click", () => onSelect(other.id));
      list.append(button);
    });
    parent.append(list);
  }

  function evidenceLabel(index, item, element) {
    const documentNode = index.nodes.get(item.document_id || element?.record?.document_id);
    const sourceNode = index.nodes.get(item.source_id || documentNode?.record?.source_id);
    const page = element?.record?.location?.page;
    return [...new Set([sourceNode?.label, documentNode?.label, page ? `Page ${page}` : ""].filter(Boolean))].join(" · ");
  }

  function evidenceExcerpt(index, item, element, claim) {
    if (element?.record?.text?.trim()) {
      const text = element.record.text;
      return item.span ? Array.from(text).slice(item.span.start, item.span.end).join("") : text.trim();
    }
    const chunk = (claim.chunk_ids || []).map(id => index.nodes.get(id)).find(candidate =>
      candidate?.record?.evidence?.some(reference => reference.element_id === item.element_id));
    return chunk?.record?.text?.trim() || "No extracted evidence text is stored for this location.";
  }

  function renderEvidence(index, node) {
    const records = Array.isArray(node.record.evidence) ? node.record.evidence : [];
    if (!records.length) return;
    const section = addSection(node.group === "summary" ? "Source provenance" : "Evidence", panel);
    if (node.group === "summary") appendText("p", "muted", "Inherited source passages for draft synthesis. Follow input summaries and clusters to inspect how it was formed.", section);
    records.forEach((item, position) => {
      const element = index.nodes.get(item.element_id);
      const excerpt = appendText("blockquote", "evidence-excerpt", evidenceExcerpt(index, item, element, node.record), section);
      excerpt.setAttribute("aria-label", `Evidence excerpt ${position + 1}`);
      const caption = evidenceLabel(index, item, element);
      if (caption) appendText("p", "evidence-location", caption, section);
    });
  }

  function renderLocation(record) {
    const location = record.location;
    if (!location || typeof location !== "object") return;
    const parts = [location.page ? `Page ${location.page}` : "", location.label, location.format]
      .filter(Boolean);
    if (parts.length) {
      const section = addSection("Source location", panel);
      appendText("p", "", parts.join(" · "), section);
    }
  }

  function renderTechnicalMetadata(node, parent) {
    const record = {...(node.record || {})};
    delete record.text;
    delete record.evidence;
    const list = document.createElement("dl");
    list.className = "metadata";
    Object.entries({id: node.id, ...record}).forEach(([key, value]) => {
      const term = document.createElement("dt");
      term.textContent = key;
      const detail = document.createElement("dd");
      if (typeof value === "object" && value !== null) {
        const pre = document.createElement("pre");
        pre.textContent = JSON.stringify(value, null, 2);
        detail.append(pre);
      } else detail.textContent = String(value ?? "");
      list.append(term, detail);
    });
    parent.append(list);
  }

  KPG.renderDetail = function (index, node, onSelect, onExplore) {
    panel.replaceChildren();
    if (!node) {
      appendText("p", "eyebrow", "KNOWLEDGE RECORD");
      appendText("h2", "", "Choose a record");
      appendText("p", "muted", "Select a topic, claim, or provenance node to inspect its saved details.");
      return;
    }

    appendText("p", "eyebrow", KPG.groupLabels[node.group] || node.group);
    appendText("h2", "", node.label);
    if (node.detail) appendText("p", "detail-text", node.detail);
    renderEvidence(index, node);
    renderLocation(node.record || {});

    const actions = document.createElement("div");
    actions.className = "detail-actions";
    if (["claim", "candidate", "summary"].includes(node.group)) {
      const explore = document.createElement("button");
      explore.type = "button";
      explore.textContent = "Explore provenance";
      explore.addEventListener("click", () => onExplore("claim", node.id));
      actions.append(explore);
    } else if (["summary_cluster", "semantic_cluster", "structural_chunk", "chunk", "element", "source"].includes(node.group)) {
      const explore = document.createElement("button");
      explore.type = "button";
      explore.textContent = "Explore local graph";
      explore.addEventListener("click", () => onExplore("claim", node.id));
      actions.append(explore);
    }
    if (node.group === "document") {
      const explore = document.createElement("button");
      explore.type = "button";
      explore.textContent = "Explore derived knowledge";
      explore.addEventListener("click", () => onExplore("document", node.id));
      actions.append(explore);
    }
    if (node.group === "source" && /^https?:\/\//.test(node.record.uri || "")) {
      const link = document.createElement("a");
      link.href = node.record.uri;
      link.target = "_blank";
      link.rel = "noopener noreferrer";
      link.textContent = "Open publisher source";
      actions.append(link);
    }
    if (actions.childElementCount) panel.append(actions);

    const allEdges = [...(index.incoming.get(node.id) || []), ...(index.outgoing.get(node.id) || [])];
    if (allEdges.length) {
      const connected = document.createElement("details");
      connected.className = "detail-section disclosure";
      const summary = document.createElement("summary");
      summary.textContent = `Connected records (${allEdges.length})`;
      connected.append(summary);
      connected.addEventListener("toggle", () => {
        if (connected.open && !connected.dataset.loaded) {
          appendRelated(index, node, allEdges, connected, onSelect);
          connected.dataset.loaded = "true";
        }
      });
      panel.append(connected);
    }

    const technical = document.createElement("details");
    technical.className = "detail-section disclosure";
    const technicalSummary = document.createElement("summary");
    technicalSummary.textContent = "Technical metadata";
    technical.append(technicalSummary);
    technical.addEventListener("toggle", () => {
      if (technical.open && !technical.dataset.loaded) {
        renderTechnicalMetadata(node, technical);
        technical.dataset.loaded = "true";
      }
    });
    panel.append(technical);
  };
})(window.KPG = window.KPG || {});
