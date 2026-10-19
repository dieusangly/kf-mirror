(function (KPG) {
  "use strict";

  KPG.groups = ["topic", "claim", "candidate", "summary", "summary_cluster", "semantic_cluster", "structural_chunk", "chunk", "element", "document", "source"];
  KPG.groupLabels = {topic: "Topics", claim: "Claims", candidate: "Candidate claims", summary: "Summaries", summary_cluster: "Summary clusters", semantic_cluster: "Semantic clusters", structural_chunk: "Structural chunks", chunk: "Original chunks", element: "Elements", document: "Documents", source: "Sources"};
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
      (claim.record?.evidence || []).forEach(item => { if (item.document_id) found.add(item.document_id); });
      (claim.record?.chunk_ids || []).forEach(id => {
        const chunk = nodes.get(id);
        if (chunk?.record?.document_id) found.add(chunk.record.document_id);
      });
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
      if (["chunk", "element", "structural_chunk"].includes(node.group)
        && record.document_id && record.document_id !== filters.document) return false;
      if (["semantic_cluster", "summary"].includes(node.group) && Array.isArray(record.document_ids) && !record.document_ids.includes(filters.document)) return false;
      if (node.group === "summary_cluster" && !record.summary_ids.some(id => index.nodes.get(id)?.record.document_ids.includes(filters.document))) return false;
    }
    return true;
  };

  KPG.localNeighborhood = function (index, rootId) {
    const visible = new Set();
    const root = index.nodes.get(rootId);
    const include = id => {
      if (id && index.nodes.has(id)) visible.add(id);
    };
    if (root && ["claim", "candidate"].includes(root.group)) {
      const record = root.record || {};
      const originalChunkIds = new Set(record.chunk_ids || []);
      include(rootId);

      (index.incoming.get(rootId) || []).forEach(edge => {
        if (edge.relation === "groups" || edge.relation === "classified as") include(edge.source);
      });
      (index.outgoing.get(rootId) || []).forEach(edge => {
        const target = index.nodes.get(edge.target);
        if (["semantic_cluster", "chunk", "element"].includes(target?.group)) include(target.id);
        if (edge.relation === "classified as") include(edge.target);
      });

      const evidence = Array.isArray(record.evidence) ? record.evidence : [];
      evidence.forEach(item => {
        include(item.element_id);
        include(item.document_id);
        include(item.source_id);
      });

      [...visible].forEach(id => {
        if (index.nodes.get(id)?.group !== "semantic_cluster") return;
        (index.outgoing.get(id) || []).forEach(edge => {
          const structural = index.nodes.get(edge.target);
          const chunkIds = structural?.record?.original_chunk_ids || [];
          if (structural?.group === "structural_chunk" && chunkIds.some(chunkId => originalChunkIds.has(chunkId))) {
            include(structural.id);
            chunkIds.filter(chunkId => originalChunkIds.has(chunkId)).forEach(include);
          }
        });
      });

      originalChunkIds.forEach(include);
      [...visible].forEach(id => {
        const node = index.nodes.get(id);
        if (node?.group === "chunk" || node?.group === "element") {
          (index.outgoing.get(id) || []).forEach(edge => {
            const target = index.nodes.get(edge.target);
            if (target?.group === "element" || target?.group === "document" || target?.group === "source") include(target.id);
          });
        }
      });
      [...visible].forEach(id => {
        const node = index.nodes.get(id);
        if (node?.group !== "document") return;
        (index.outgoing.get(id) || []).forEach(edge => {
          if (index.nodes.get(edge.target)?.group === "source") include(edge.target);
        });
      });
      return visible;
    }

    const queue = [rootId];
    const summaryProvenance = ["summary", "summary_cluster"].includes(root?.group);
    visible.add(rootId);
    for (let position = 0; position < queue.length; position += 1) {
      const id = queue[position];
      const nextIds = (index.outgoing.get(id) || []).map(edge => edge.target);
      if (!summaryProvenance) nextIds.push(...(index.incoming.get(id) || []).filter(edge => edge.relation === "groups").map(edge => edge.source));
      nextIds.forEach(nextId => {
        const next = index.nodes.get(nextId);
        if (!next || visible.has(nextId)) return;
        if (!["summary", "summary_cluster", "semantic_cluster", "structural_chunk", "chunk", "element", "document", "source", "topic"].includes(next.group)) return;
        visible.add(nextId);
        queue.push(nextId);
      });
    }
    return visible;
  };

  KPG.computeView = function (index, state) {
    const {mode, topic, document, selectedId, filters} = state;
    if (mode === "summaries") {
      const summaries = KPG.sortedNodes(index, "summary").filter(node => KPG.matchesFilters(index, node, filters))
        .sort((a, b) => b.record.level - a.record.level || a.label.localeCompare(b.label) || a.id.localeCompare(b.id));
      const pageCount = Math.max(1, Math.ceil(summaries.length / KPG.pageSize));
      const page = Math.min(state.page, pageCount - 1);
      return {visible: new Set(summaries.slice(page * KPG.pageSize, (page + 1) * KPG.pageSize).map(node => node.id)),
        page, pageCount, availableSummaries: summaries.length};
    }
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
      const visible = ["claim", "candidate", "summary", "summary_cluster"].includes(root.group)
        ? KPG.localNeighborhood(index, root.id)
        : new Set([root.id, ...(index.neighbors.get(root.id) || [])]);
      return {visible: new Set([...visible].filter(id => KPG.matchesFilters(index, index.nodes.get(id), filters))), root};
    }
    if (mode === "document") {
      const documentId = document || (index.nodes.get(selectedId)?.group === "document" ? selectedId : "");
      if (!documentId) return {visible: new Set(), needsDocument: true};
      const claims = index.graph.nodes.filter(node => ["claim", "candidate", "summary"].includes(node.group)
        && (node.group === "summary" ? node.record.document_ids.includes(documentId) : index.claimDocuments.get(node.id)?.has(documentId))
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
