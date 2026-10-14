(function (KPG) {
  "use strict";

  KPG.groups = ["topic", "claim", "candidate", "chunk", "element", "document", "source"];
  KPG.groupLabels = {topic: "Topics", claim: "Claims", candidate: "Candidates", chunk: "Chunks", element: "Elements", document: "Documents", source: "Sources"};
  KPG.pageSize = 24;

  KPG.createIndex = function (graph) {
    const nodes = new Map(graph.nodes.map(node => [node.id, node]));
    const outgoing = new Map();
    const incoming = new Map();
    const neighbors = new Map();
    const push = (map, key, value) => {
      if (!map.has(key)) map.set(key, []);
      map.get(key).push(value);
    };
    graph.edges.forEach(edge => {
      push(outgoing, edge.source, edge);
      push(incoming, edge.target, edge);
      push(neighbors, edge.source, edge.target);
      push(neighbors, edge.target, edge.source);
    });
    const claimDocuments = new Map();
    graph.nodes.filter(node => node.group === "claim" || node.group === "candidate").forEach(claim => {
      const found = new Set();
      const queue = [{id: claim.id, depth: 0}];
      const seen = new Set([claim.id]);
      while (queue.length) {
        const current = queue.shift();
        if (current.depth >= 4) continue;
        (outgoing.get(current.id) || []).forEach(edge => {
          const target = nodes.get(edge.target);
          if (!target || seen.has(target.id)) return;
          seen.add(target.id);
          if (target.group === "document") found.add(target.id);
          else if (["chunk", "element"].includes(target.group)) queue.push({id: target.id, depth: current.depth + 1});
        });
      }
      claimDocuments.set(claim.id, found);
    });
    return {graph, nodes, outgoing, incoming, neighbors, claimDocuments};
  };

  KPG.sortedNodes = function (index, group) {
    return index.graph.nodes.filter(node => node.group === group).sort((a, b) => a.label.localeCompare(b.label) || a.id.localeCompare(b.id));
  };

  KPG.reviewStatuses = function (node) {
    const record = node.record || {};
    const statuses = [record.review_status, record.grounding_review_status, record.classification_review_status].filter(Boolean);
    return statuses.length ? statuses : ["unspecified"];
  };

  KPG.matchesFilters = function (index, node, filters) {
    const record = node.record || {};
    if (filters.type && node.group !== filters.type) return false;
    if (filters.build && record.build_id && record.build_id !== filters.build) return false;
    const reviews = KPG.reviewStatuses(node);
    if (filters.review && !reviews.includes("unspecified") && !reviews.includes(filters.review)) return false;
    if (filters.document && node.group !== "document" && node.group !== "source") {
      if (["claim", "candidate"].includes(node.group) && !index.claimDocuments.get(node.id)?.has(filters.document)) return false;
      if (node.group === "chunk" && record.document_id && record.document_id !== filters.document) return false;
      if (node.group === "element" && record.document_id && record.document_id !== filters.document) return false;
    }
    return true;
  };

  KPG.localNeighborhood = function (index, rootId) {
    const visible = new Set();
    const queue = [rootId];
    visible.add(rootId);
    while (queue.length) {
      const id = queue.shift();
      const nextIds = (index.outgoing.get(id) || []).map(edge => edge.target);
      nextIds.push(...(index.incoming.get(id) || []).filter(edge => edge.relation === "groups").map(edge => edge.source));
      nextIds.forEach(nextId => {
        const next = index.nodes.get(nextId);
        if (!next || visible.has(nextId)) return;
        if (!["chunk", "element", "document", "source", "topic"].includes(next.group)) return;
        visible.add(nextId);
        queue.push(nextId);
      });
    }
    return visible;
  };

  KPG.computeView = function (index, state) {
    const {mode, topic, document, selectedId, filters} = state;
    if (mode === "topic") {
      const topics = KPG.sortedNodes(index, "topic").filter(node => (!topic || node.id === topic) && KPG.matchesFilters(index, node, filters));
      const topicIds = new Set(topics.map(node => node.id));
      const claims = KPG.sortedNodes(index, "claim").filter(node => topicIds.has(node.record.topic_id) && KPG.matchesFilters(index, node, filters));
      const pageCount = Math.max(1, Math.ceil(claims.length / KPG.pageSize));
      const page = Math.min(state.page, pageCount - 1);
      const shownClaims = claims.slice(page * KPG.pageSize, (page + 1) * KPG.pageSize);
      const visible = new Set([...topics.map(node => node.id), ...shownClaims.map(node => node.id)]);
      return {visible, page, pageCount, availableClaims: claims.length};
    }
    if (mode === "full") {
      const visible = new Set(index.graph.nodes.filter(node => KPG.matchesFilters(index, node, filters)).map(node => node.id));
      return {visible, needsFullConfirmation: visible.size > 500};
    }
    if (mode === "claim") {
      const root = index.nodes.get(selectedId);
      if (!root) return {visible: new Set(), needsClaim: true};
      const visible = ["claim", "candidate"].includes(root.group)
        ? KPG.localNeighborhood(index, root.id)
        : new Set([root.id, ...(index.neighbors.get(root.id) || [])]);
      return {visible: new Set([...visible].filter(id => KPG.matchesFilters(index, index.nodes.get(id), filters))), root};
    }
    if (mode === "document") {
      const documentId = document || (index.nodes.get(selectedId)?.group === "document" ? selectedId : "");
      if (!documentId) return {visible: new Set(), needsDocument: true};
      const claims = index.graph.nodes.filter(node => ["claim", "candidate"].includes(node.group)
        && index.claimDocuments.get(node.id)?.has(documentId)
        && (!topic || node.record.topic_id === topic)
        && KPG.matchesFilters(index, node, filters))
        .sort((a, b) => a.label.localeCompare(b.label) || a.id.localeCompare(b.id));
      const pageCount = Math.max(1, Math.ceil(claims.length / KPG.pageSize));
      const page = Math.min(state.page, pageCount - 1);
      const shownClaims = claims.slice(page * KPG.pageSize, (page + 1) * KPG.pageSize);
      const topicIds = new Set(shownClaims.map(node => node.record.topic_id).filter(Boolean));
      const topics = KPG.sortedNodes(index, "topic").filter(node => topicIds.has(node.id) && KPG.matchesFilters(index, node, filters));
      const visible = new Set([...shownClaims.map(node => node.id), ...topics.map(node => node.id)]);
      if (KPG.matchesFilters(index, index.nodes.get(documentId), filters)) visible.add(documentId);
      (index.outgoing.get(documentId) || []).forEach(edge => {
        const child = index.nodes.get(edge.target);
        if (child?.group === "source" && KPG.matchesFilters(index, child, filters)) visible.add(child.id);
      });
      return {visible, page, pageCount, availableClaims: claims.length, root: index.nodes.get(documentId)};
    }
    return {visible: new Set()};
  };
})(window.KPG = window.KPG || {});
