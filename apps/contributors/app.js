"use strict";

const filters = document.querySelector("#filters");
const detail = document.querySelector("#case-detail");
const feedback = document.querySelector("#feedback");
const loginForm = document.querySelector("#login-form");
const sourceForm = document.querySelector("#source-form");
const reviewForm = document.querySelector("#review-form");
const typologyForm = document.querySelector("#typology-form");
const withdrawForm = document.querySelector("#withdraw-form");
const pages = ["cases", "sources", "typologies", "history", "faq"];
const details = new Map();
const dateFormat = new Intl.DateTimeFormat("fr-FR", {dateStyle: "medium", timeStyle: "short"});
const kinds = {synthetic: "Pilote synthétique", source_backed: "Règles sourcées"};
const answers = {entailed: "Conclusion établie", unknown: "Information insuffisante", conflict: "Informations contradictoires"};
const languages = {fr: "FR", en: "EN", both: "FR et EN", other: "Autre"};
const pending = "Enregistré · traitement non lancé";
let catalog;
let contributions;
let user;
let visibleCase;
let reviewTarget;
let currentLanguage = "fr";
let currentPage = "cases";
let renderSequence = 0;
let sessionSequence = 0;

const escape = (value) => String(value ?? "").replace(/[&<>"']/g, (character) => ({
  "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
})[character]);
const normalize = (value) => value.normalize("NFD").replace(/\p{Diacritic}/gu, "").toLowerCase();
const formattedDate = (date) => dateFormat.format(new Date(date));
const scenarioCount = (count) => `${count} scénario${count > 1 ? "s" : ""}`;

function route(params = {}) {
  const query = new URLSearchParams(Object.entries(params).filter(([, value]) => value)).toString();
  return query ? "/?" + query : "/";
}

function externalLink(url, label) {
  try {
    if (!["http:", "https:"].includes(new URL(url).protocol)) return "";
  } catch {
    return "";
  }
  return `<a href="${escape(url)}" target="_blank" rel="noopener noreferrer">${label}</a>`;
}

async function jsonRequest(url, data) {
  const session = sessionSequence;
  const response = await fetch(url, data === undefined ? {} : {
    method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify(data),
  });
  const result = await response.json();
  if (response.status === 401 && url !== "/api/login" && user && session === sessionSequence) {
    showSignedOut("Votre session a expiré. Connectez-vous à nouveau.");
  }
  if (!response.ok) throw new Error(result.error || "Une erreur est survenue.");
  return result;
}

function announce(message) {
  feedback.hidden = !message;
  feedback.textContent = message || "";
  if (message) feedback.focus();
}

function navigate(url, options = {}) {
  history[options.replace ? "replaceState" : "pushState"](null, "", url);
  if (!options.keepScroll) window.scrollTo(0, 0);
  announce(options.message);
  render(options.focusCase);
}

function filterValues() {
  return Object.fromEntries(new FormData(filters));
}

function updateFilters() {
  navigate(route(filterValues()), {replace: true, keepScroll: true});
}

function caseURL(id, lang = currentLanguage) {
  return route({...filterValues(), cas: id, langue: lang === "en" ? "en" : ""});
}

function revealContribution() {
  if (!/^#contribution-\d+$/.test(location.hash)) return false;
  const target = document.getElementById(currentPage).querySelector(location.hash);
  if (!target) return false;
  target.focus({preventScroll: true});
  target.scrollIntoView({block: "center"});
  return true;
}

function contributionName(kind, entry) {
  if (kind === "source") return entry.title;
  if (kind === "review") return `${entry.case} · ${entry.outcome}`;
  return `${entry.code} — ${entry.label}`;
}

function withdrawButton(kind, entry) {
  if (!entry.can_edit) return "";
  const name = escape(contributionName(kind, entry));
  return `<button type="button" class="contribution-action" data-withdraw="${entry.id}"
    data-target-name="${name}" aria-label="Retirer · ${name}">Retirer</button>`;
}

function reviewState(reviews) {
  if (reviews.some((review) => review.outcome !== "Confirmer la référence")) return "correction";
  return reviews.length ? "recorded" : "none";
}

async function renderCatalog(params, sequence, focusCase) {
  for (const control of filters.elements) {
    const value = params.get(control.name) || "";
    control.value = value;
    if (control.tagName === "SELECT" && control.selectedIndex === -1) control.selectedIndex = 0;
  }
  const {q, family, category, kind, review} = filterValues();
  const searchTerms = normalize(q.trim()).split(/\s+/);
  const byCase = new Map();
  for (const entry of contributions.review) {
    if (!byCase.has(entry.case)) byCase.set(entry.case, []);
    byCase.get(entry.case).push(entry);
  }
  const matching = catalog.cases.filter((item) => {
    const search = normalize([item.id, ...Object.values(item.variants).flatMap(({title, question}) => [title, question])].join(" "));
    return (!family || item.family === family) && (!category || item.category === category)
      && (!kind || item.kind === kind) && (!review || reviewState(byCase.get(item.id) || []) === review)
      && searchTerms.every((term) => search.includes(term));
  });
  const requestedCase = matching.find((item) => item.id === params.get("cas"));
  const selected = requestedCase || matching[0];
  document.querySelector("#cases").classList.toggle("has-selection", Boolean(requestedCase));
  document.querySelector("#back-to-list").href = route(filterValues());
  document.querySelector("#case-count").textContent = scenarioCount(matching.length);
  document.querySelector("#case-links").innerHTML = matching.map((item) => `
    <li><a href="${escape(caseURL(item.id))}" data-route data-case-route${item === selected ? ' aria-current="true"' : ""}>
      <span><strong>${escape(item.id)}</strong><span class="row-title" lang="${currentLanguage}">
        ${escape(item.variants[currentLanguage].title)}</span><span class="case-meta">${kinds[item.kind]}</span></span>
      <span class="case-meta">${escape(item.category)}</span>
    </a></li>`).join("");

  if (!selected) {
    visibleCase = undefined;
    detail.innerHTML = '<p class="muted">Aucun scénario ne correspond aux filtres.</p>';
    return;
  }
  if (!details.has(selected.id)) {
    visibleCase = undefined;
    detail.innerHTML = '<p class="muted" role="status">Chargement de la fiche…</p>';
  }
  try {
    const scenario = details.get(selected.id) || await jsonRequest("/api/cases/" + encodeURIComponent(selected.id));
    if (sequence !== renderSequence) return;
    details.set(selected.id, scenario);
    visibleCase = scenario;
    renderDetail(visibleCase, byCase.get(selected.id) || []);
    if (!revealContribution() && focusCase) detail.querySelector("h2").focus({preventScroll: true});
  } catch {
    if (sequence === renderSequence) detail.innerHTML = '<p role="alert">La fiche du scénario est indisponible. Rechargez la page.</p>';
  }
}

function lines(values, label, lang) {
  if (!values.length) return "";
  return `<p><strong>${label}</strong></p><ul lang="${lang}">${values.map((text) => `<li>${escape(text)}</li>`).join("")}</ul>`;
}

function renderDetail(item, reviews) {
  const lang = currentLanguage;
  const variant = item.variants[lang];
  const clauses = variant.clauses.map((clause) => `
    <li id="evidence-${escape(clause.id)}" tabindex="-1"><p lang="${lang}">${escape(clause.text)}</p>
      <span class="evidence-id">${escape(clause.id)}</span>
      ${(clause.passage_ids || []).map((key) => {
        const passage = variant.passages[key];
        return `<details class="passage">
          <summary>${escape(passage.title)} · ${escape(passage.locator)}</summary>
          <div class="passage-body">
            <p class="muted">${escape(passage.id)} · ${escape(passage.version)}</p>
            ${passage.excerpts.map((excerpt) => `<blockquote lang="fr">${escape(excerpt)}</blockquote>`).join("")}
            ${externalLink(passage.official_url, "Ouvrir la source officielle")}
          </div></details>`;
      }).join("")}
    </li>`).join("");
  const steps = variant.steps.map((step) => `
    <li id="evidence-${escape(step.id)}" tabindex="-1">
      <p lang="${lang}">${escape(step.statement)}</p><div class="reference-links evidence-id">
      ${step.source_ids.map((id) => `<a href="#evidence-${escape(id)}" data-evidence="${escape(id)}">${escape(id)}</a>`).join("")}
    </div></li>`).join("");
  const reviewHistory = reviews.map((entry) => `
    <li id="contribution-${entry.id}" tabindex="-1"><strong>${escape(entry.outcome)}</strong>
      <p class="muted">${escape(entry.section)} · ${languages[entry.language]} · v${escape(entry.version)} ·
        ${formattedDate(entry.created_at)}${entry.reference !== item.reference ? " · Référence antérieure" : ""}</p>
      <p>${escape(entry.comment)}</p>${withdrawButton("review", entry)}</li>`).join("");
  detail.innerHTML = `
    <div class="detail-header">
      <span class="case-id" title="${escape(catalog.families[item.family])} · ${escape(catalog.categories[item.category])}">
        ${escape(item.id)} · ${escape(item.family)} · ${escape(item.category)}
      </span>
      <div class="languages" role="group" aria-label="Langue du scénario">
        ${["fr", "en"].map((language) => `<button type="button" data-language="${language}"
          aria-pressed="${lang === language}">${languages[language]}</button>`).join("")}
      </div>
    </div>
    <h2 lang="${lang}" tabindex="-1">${escape(variant.title)}</h2>
    <p class="muted">${kinds[item.kind]} · v${escape(item.version)} · Référence validée</p>
    <div class="detail-body">
      <section><h3>Question</h3><p lang="${lang}">${escape(variant.question)}</p></section>
      <section><h3>Périmètre et faits du scénario</h3>
        <p id="evidence-${escape(variant.scope.id)}" tabindex="-1" lang="${lang}">${escape(variant.scope.text)}</p>
        <ul>${variant.facts.map((fact) => `<li id="evidence-${escape(fact.id)}" tabindex="-1">
          <span lang="${lang}">${escape(fact.text)}</span> <span class="evidence-id">${escape(fact.id)}</span>
        </li>`).join("")}</ul>
        <p class="muted">Faits du scénario fictifs.</p>
      </section>
      <section><h3>Règles applicables</h3>
        ${clauses ? `<ul>${clauses}</ul>` : '<p class="muted">Ce scénario utilise directement les faits fournis.</p>'}
      </section>
      <section><h3>Réponse de référence</h3><p><strong>${escape(answers[variant.status])}</strong></p>
        ${lines(variant.conclusions, "Conclusions", lang)}
        ${lines(variant.missing, "Prérequis manquants", lang)}
        ${lines(variant.withheld, "Conclusions en suspens", lang)}
        ${steps ? `<p><strong>Raisonnement</strong></p><ol>${steps}</ol>` : ""}
      </section>
      <section><h3>Avis de relecture</h3>
        ${reviewHistory ? `<ul class="review-list">${reviewHistory}</ul>` : '<p class="muted">Aucun avis pour le moment.</p>'}
        <button type="button" class="primary" data-review>Relire ce scénario</button>
      </section>
    </div>`;
}

function caseCitations(ids) {
  if (!ids.length) return '<span class="muted">Aucun</span>';
  return `<div class="case-citations">${Array.from(new Set(ids), (id) =>
    `<a href="${escape(route({cas: id}))}" data-route data-case-route>${escape(id)}</a>`).join("")}</div>`;
}

function sourceTable(titleId, entries) {
  return `<div class="table-surface"><table class="source-table" role="table" aria-labelledby="${titleId}">
    <thead role="rowgroup"><tr role="row">
      <th scope="col">N°</th><th scope="col">Source</th><th scope="col">Scénarios liés</th>
    </tr></thead>
    <tbody role="rowgroup">${entries.map(({source, cases, id}, index) => `
      <tr role="row"${id ? ` id="contribution-${id}" tabindex="-1"` : ""}>
        <td class="source-number" role="cell">${index + 1}</td>
        <th scope="row" role="rowheader">${source}</th>
        <td class="source-cases" role="cell" data-label="Scénarios liés">${caseCitations(cases)}</td>
      </tr>`).join("")}</tbody>
  </table></div>`;
}

function renderSources() {
  const submitted = contributions.source.map((item) => ({
    id: item.id,
    source: `<p><strong>${escape(item.title)}</strong></p>
      <p class="muted">${escape([item.issuer, languages[item.language], item.version, item.locator].filter(Boolean).join(" · "))}</p>
      <p class="muted">${pending} · ${formattedDate(item.created_at)}</p>
      <p>${item.mode === "file" ? `<a href="/documents/${item.id}" download>Télécharger ${escape(item.filename)}</a>`
        : externalLink(item.url, "Ouvrir le lien enregistré")}</p>${withdrawButton("source", item)}`,
    cases: catalog.sources.filter((source) => source.url === item.url
      && (!item.version || source.version === item.version)).flatMap((source) => source.cases),
  }));
  const registered = catalog.sources.map((item) => ({
    source: `<p><strong>${escape(item.title)}</strong></p>
      <p class="muted">${escape(item.version)} · ${escape(item.language.toUpperCase())}</p>
      <p>${externalLink(item.url, "Ouvrir la source officielle")}</p>`,
    cases: item.cases,
  }));
  document.querySelector("#contributed-sources").innerHTML = submitted.length
    ? sourceTable("contributed-sources-title", submitted)
    : '<p class="empty">Aucun document déposé ni URL ajoutée pour le moment.</p>';
  document.querySelector("#corpus-sources").innerHTML = sourceTable("corpus-sources-title", registered);
  revealContribution();
}

function renderTypologies() {
  const categoryEntries = Object.entries(catalog.categories);
  const counts = new Map();
  for (const {family, category} of catalog.cases) {
    const key = family + "/" + category;
    counts.set(key, (counts.get(key) || 0) + 1);
  }
  document.querySelector("#coverage").innerHTML = `<table class="coverage-table">
    <thead><tr><th scope="col">Famille</th>${categoryEntries.map(([code, label]) =>
      `<th scope="col">${escape(code)}<small>${escape(label)}</small></th>`).join("")}</tr></thead>
    <tbody>${Object.entries(catalog.families).map(([family, label]) => `
      <tr><th scope="row">${escape(family)}<small>${escape(label)}</small></th>
        ${categoryEntries.map(([category]) => {
          const count = counts.get(family + "/" + category) || 0;
          return `<td><a href="${escape(route({family, category}))}" data-route
            aria-label="${escape(family)} × ${escape(category)} · ${scenarioCount(count)}">${count}</a></td>`;
        }).join("")}
      </tr>`).join("")}</tbody>
    </table>`;
  const proposals = contributions.typology.map((item) => `
    <article class="record" id="contribution-${item.id}" tabindex="-1">
      <h3>${escape(item.code)} — ${escape(item.label)}</h3>
      <p class="muted">${item.type === "family" ? "Famille" : "Catégorie"} · Proposition à relire · ${formattedDate(item.created_at)}</p>
      <p>${escape(item.definition)}</p>${item.example ? `<p><strong>Exemple</strong> ${escape(item.example)}</p>` : ""}
      ${withdrawButton("typology", item)}
    </article>`).join("");
  document.querySelector("#proposals").innerHTML = proposals
    ? `<div class="record-block">${proposals}</div>` : '<p class="empty">Aucune proposition pour le moment.</p>';
  revealContribution();
}

function historyAction(kind, action, entry) {
  if (action === "withdrawn") {
    if (kind === "review") return "Retrait d’un avis de relecture";
    if (kind === "typology") return "Retrait d’une proposition de typologie";
    return entry.mode === "file" ? "Retrait d’un document" : "Retrait d’une URL";
  }
  if (action === "restored") return "Rétablissement";
  if (kind === "review") return "Avis de relecture";
  if (kind === "typology") return "Proposition de typologie";
  return entry.mode === "file" ? "Document déposé" : "URL ajoutée";
}

function withdrawnDetails(kind, entry) {
  const description = kind === "source"
    ? [[entry.issuer, languages[entry.language], entry.version, entry.locator].filter(Boolean).join(" · "),
      entry.filename || entry.url]
    : kind === "review" ? [entry.section, entry.comment] : [entry.definition, entry.example];
  return `<details class="history-detail"><summary>Voir la contribution</summary>
    ${description.filter(Boolean).map((text) => `<p>${escape(text)}</p>`).join("")}
  </details>`;
}

function historyRow({kind, action, occurred_at, reason, username, active, can_restore, contribution: entry}) {
  const name = escape(contributionName(kind, entry));
  const anchor = active ? `#contribution-${entry.id}` : "";
  const url = kind === "review" ? route({cas: entry.case, langue: entry.language === "en" ? "en" : ""})
    : route({page: kind === "source" ? "sources" : "typologies"});
  const link = active || kind === "review" ? `<a href="${escape(url + anchor)}" data-route>
    ${active ? "Voir la contribution" : "Voir le scénario"}</a>` : "";
  return `<tr role="row">
    <td role="cell" class="muted"><time datetime="${escape(occurred_at)}">${formattedDate(occurred_at)}</time></td>
    <th scope="row" role="rowheader">${historyAction(kind, action, entry)}</th>
    <td role="cell" class="history-user" data-label="Nom d’utilisateur">${escape(username || "Non renseigné")}</td>
    <td role="cell">
      <p><strong>${name}</strong></p>
      ${kind === "review" ? `<p class="muted">Version relue · v${escape(entry.version)} · ${languages[entry.language]}</p>` : ""}
      ${reason ? `<p>${escape(reason)}</p>` : ""}
      <div class="history-links">${link}${can_restore ? `<button type="button" class="contribution-action"
        data-restore="${entry.id}" aria-label="Rétablir · ${name}">Rétablir</button>` : ""}</div>
      ${active ? "" : withdrawnDetails(kind, entry)}
    </td>
  </tr>`;
}

async function renderHistory(sequence) {
  const container = document.querySelector("#history-entries");
  container.innerHTML = '<p class="muted" role="status">Chargement de l’historique…</p>';
  try {
    const events = await jsonRequest("/api/history");
    if (sequence !== renderSequence) return;
    contributions = {source: [], review: [], typology: []};
    for (const {action, active, kind, contribution} of events) {
      if (action === "added" && active) contributions[kind].push(contribution);
    }
    for (const list of Object.values(contributions)) list.sort((left, right) => right.id - left.id);
    container.innerHTML = events.length ? `
      <div class="table-surface"><table class="history-table" role="table" aria-labelledby="history-title">
        <thead role="rowgroup"><tr role="row"><th scope="col">Date et heure</th><th scope="col">Action</th><th scope="col">Nom d’utilisateur</th><th scope="col">Contribution</th></tr></thead>
        <tbody role="rowgroup">${events.map(historyRow).join("")}</tbody>
      </table></div>` : '<p class="empty">Aucune contribution enregistrée pour le moment.</p>';
  } catch {
    if (sequence === renderSequence) container.innerHTML = '<p class="empty" role="alert">L’historique est indisponible. Rechargez la page.</p>';
  }
}

function render(focusCase = false) {
  if (!user || !catalog || !contributions) return;
  const sequence = ++renderSequence;
  const params = new URLSearchParams(location.search);
  currentPage = pages.includes(params.get("page")) ? params.get("page") : "cases";
  currentLanguage = params.get("langue") === "en" ? "en" : "fr";
  document.querySelectorAll("[data-page]").forEach((link) => {
    if (link.dataset.page === currentPage) link.setAttribute("aria-current", "page");
    else link.removeAttribute("aria-current");
  });
  pages.forEach((page) => { document.getElementById(page).hidden = page !== currentPage; });
  if (currentPage === "cases") renderCatalog(params, sequence, focusCase);
  if (currentPage === "sources") renderSources();
  if (currentPage === "typologies") renderTypologies();
  if (currentPage === "history") renderHistory(sequence);
}

function openForm(form) {
  form.reset();
  form.querySelector(".form-error").hidden = true;
  if (form === sourceForm) updateSourceMode();
  if (form === typologyForm) updateTypologyType();
  form.closest("dialog").showModal();
}

function updateSourceMode() {
  const file = sourceForm.elements.mode.value === "file";
  document.querySelector("#file-field").hidden = !file;
  document.querySelector("#url-field").hidden = file;
  sourceForm.elements.file.disabled = !file;
  sourceForm.elements.file.required = file;
  sourceForm.elements.url.disabled = file;
  sourceForm.elements.url.required = !file;
  sourceForm.querySelector('[type="submit"]').textContent = file ? "Enregistrer le dépôt" : "Enregistrer la source";
}

function updateTypologyType() {
  const family = typologyForm.elements.type.value === "family";
  document.querySelector("#definition-label").textContent = family ? "Définition et périmètre métier" : "Définition et comportement attendu";
  document.querySelector("#example-label").textContent = family ? "Exemple (facultatif)" : "Exemple";
  typologyForm.elements.example.required = !family;
}

function readFile(file) {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(reader.result.split(",", 2)[1]);
    reader.onerror = () => reject(new Error("Impossible de lire le document."));
    reader.readAsDataURL(file);
  });
}

function onSubmit(form, url, kind, afterSave, prepare = (data) => data) {
  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    const errorBox = form.querySelector(".form-error");
    const button = form.querySelector('[type="submit"]');
    const dialog = form.closest("dialog");
    if (button.disabled) return;
    const session = sessionSequence;
    errorBox.hidden = true;
    button.disabled = true;
    form.setAttribute("aria-busy", "true");
    try {
      const fields = await prepare(Object.fromEntries(new FormData(form)));
      if (session !== sessionSequence) return;
      const result = await jsonRequest(url, fields);
      if (session !== sessionSequence) return;
      if (kind) contributions[kind].unshift(result);
      dialog.close();
      afterSave(result);
    } catch (error) {
      if (session !== sessionSequence) return;
      errorBox.textContent = error.message;
      errorBox.hidden = false;
    } finally {
      button.disabled = false;
      form.removeAttribute("aria-busy");
    }
  });
}

function applyContributionState({kind, active, contribution}) {
  const remaining = contributions[kind].filter((item) => item.id !== contribution.id);
  if (active) {
    remaining.push(contribution);
    remaining.sort((left, right) => right.id - left.id);
  }
  contributions[kind] = remaining;
  announce(active ? "Contribution rétablie." : "Contribution retirée. Vous pouvez la rétablir dans Historique.");
  render();
}

async function restoreContribution(button) {
  const session = sessionSequence;
  button.disabled = true;
  try {
    const result = await jsonRequest("/api/contributions/state", {id: Number(button.dataset.restore), action: "restored"});
    if (session === sessionSequence) applyContributionState(result);
  } catch (error) {
    if (session === sessionSequence) announce(error.message);
  } finally {
    button.disabled = false;
  }
}

function showSignedOut(message) {
  sessionSequence++;
  renderSequence++;
  user = catalog = contributions = visibleCase = reviewTarget = undefined;
  details.clear();
  for (const page of pages) document.getElementById(page).hidden = true;
  for (const id of ["case-count", "case-links", "case-detail", "contributed-sources", "corpus-sources",
    "coverage", "proposals", "history-entries", "account-name", "review-context", "withdraw-target"]) {
    document.getElementById(id).replaceChildren();
  }
  for (const dialog of document.querySelectorAll("dialog")) {
    dialog.close();
    dialog.querySelector("form").reset();
  }
  for (const id of ["app-nav", "sign-out", "account-name", "load-status"]) {
    document.getElementById(id).hidden = true;
  }
  document.querySelector("#sign-in").hidden = false;
  document.querySelector("#signed-out").hidden = false;
  announce(message);
}

document.addEventListener("click", (event) => {
  const withdraw = event.target.closest("[data-withdraw]");
  if (withdraw) {
    openForm(withdrawForm);
    withdrawForm.dataset.contribution = withdraw.dataset.withdraw;
    document.querySelector("#withdraw-target").textContent = withdraw.dataset.targetName;
    withdrawForm.elements.reason.focus();
  }
  const restore = event.target.closest("[data-restore]");
  if (restore) restoreContribution(restore);

  const link = event.target.closest("a[data-route]");
  if (!link || event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
  event.preventDefault();
  const caseRoute = link.hasAttribute("data-case-route");
  const keepScroll = caseRoute && currentPage === "cases" && !matchMedia("(max-width: 720px)").matches;
  navigate(link.href, {keepScroll, focusCase: caseRoute});
});
window.addEventListener("popstate", () => { announce(); render(); });
filters.addEventListener("input", updateFilters);
filters.addEventListener("submit", (event) => event.preventDefault());
detail.addEventListener("click", (event) => {
  const languageButton = event.target.closest("[data-language]");
  const evidenceLink = event.target.closest("[data-evidence]");
  if (languageButton && visibleCase) {
    const language = languageButton.dataset.language;
    // Keep the list/detail state on mobile when the desktop default is translated.
    const id = new URLSearchParams(location.search).get("cas");
    navigate(caseURL(id, language), {keepScroll: true});
    detail.querySelector(`[data-language="${language}"]`)?.focus();
  }
  if (evidenceLink) {
    const target = document.getElementById("evidence-" + evidenceLink.dataset.evidence);
    if (target) {
      event.preventDefault();
      target.focus();
      target.scrollIntoView({block: "center"});
    }
  }
  if (event.target.closest("[data-review]") && visibleCase) {
    reviewTarget = visibleCase;
    openForm(reviewForm);
    document.querySelector("#review-context").textContent = `${reviewTarget.id} · v${reviewTarget.version}`;
    reviewForm.elements.language.value = currentLanguage;
  }
});

document.querySelector("#sign-in").addEventListener("click", () => {
  openForm(loginForm);
  loginForm.elements.email.focus();
});
loginForm.closest("dialog").addEventListener("close", () => loginForm.reset());
document.querySelector("#sign-out").addEventListener("click", async (event) => {
  const button = event.currentTarget;
  button.disabled = true;
  try {
    await jsonRequest("/api/logout", {});
    showSignedOut("Vous êtes déconnecté.");
  } catch {
    announce("La déconnexion est indisponible. Réessayez.");
  } finally {
    button.disabled = false;
  }
});
document.querySelector("#add-source").addEventListener("click", () => openForm(sourceForm));
document.querySelector("#add-typology").addEventListener("click", () => openForm(typologyForm));
sourceForm.addEventListener("change", (event) => { if (event.target.name === "mode") updateSourceMode(); });
typologyForm.elements.type.addEventListener("change", updateTypologyType);
for (const dialog of document.querySelectorAll("dialog")) {
  dialog.querySelectorAll("[data-close]").forEach((button) => button.addEventListener("click", () => {
    if (dialog.querySelector("form").getAttribute("aria-busy") !== "true") dialog.close();
  }));
  dialog.addEventListener("cancel", (event) => {
    if (dialog.querySelector("form").getAttribute("aria-busy") === "true") event.preventDefault();
  });
}
const fileField = document.querySelector("#file-field");
fileField.addEventListener("dragover", (event) => { event.preventDefault(); fileField.classList.add("is-dragged"); });
fileField.addEventListener("dragleave", () => fileField.classList.remove("is-dragged"));
fileField.addEventListener("drop", (event) => {
  event.preventDefault();
  fileField.classList.remove("is-dragged");
  if (!event.dataTransfer.files.length) return;
  const transfer = new DataTransfer();
  transfer.items.add(event.dataTransfer.files[0]);
  sourceForm.elements.file.files = transfer.files;
});

onSubmit(loginForm, "/api/login", null, ({user: profile}) => showSignedIn(profile));
onSubmit(sourceForm, "/api/sources", "source",
  () => navigate(route({page: "sources"}), {message: pending}),
  async ({file, ...fields}) => {
    if (fields.mode === "url") return fields;
    if (!file || !file.size || file.size > 15 * 1024 * 1024) {
      throw new Error("Sélectionnez un document non vide de 15 Mio maximum.");
    }
    return {...fields, filename: file.name, content: await readFile(file)};
  });
onSubmit(reviewForm, "/api/reviews", "review",
  () => navigate(caseURL(reviewTarget.id), {replace: true, keepScroll: true, message: "Avis de relecture enregistré."}),
  (fields) => ({...fields, case: reviewTarget.id, reference: reviewTarget.reference}));
onSubmit(typologyForm, "/api/typologies", "typology",
  () => navigate(route({page: "typologies"}), {replace: true, message: "Proposition enregistrée · À relire."}));
onSubmit(withdrawForm, "/api/contributions/state", null, applyContributionState,
  ({reason}) => ({id: Number(withdrawForm.dataset.contribution), action: "withdrawn", reason}));

function addOptions(select, entries, keepPlaceholder = false) {
  select.options.length = keepPlaceholder ? 1 : 0;
  for (const [value, label] of entries) select.add(new Option(label, value));
}

async function showSignedIn(profile) {
  const session = ++sessionSequence;
  user = profile;
  const status = document.querySelector("#load-status");
  status.textContent = "Chargement du corpus…";
  status.hidden = false;
  document.querySelector("#signed-out").hidden = true;
  document.querySelector("#sign-in").hidden = true;
  document.querySelector("#sign-out").hidden = false;
  const name = document.querySelector("#account-name");
  name.textContent = profile.name;
  name.hidden = false;
  announce();
  try {
    const loaded = await Promise.all([jsonRequest("/api/catalog"), jsonRequest("/api/contributions")]);
    if (session !== sessionSequence) return;
    [catalog, contributions] = loaded;
    addOptions(filters.elements.family, Object.entries(catalog.families).map(([code, label]) => [code, code + " — " + label]), true);
    addOptions(filters.elements.category, Object.entries(catalog.categories).map(([code, label]) => [code, code + " — " + label]), true);
    addOptions(reviewForm.elements.section, catalog.review_sections.map((label) => [label, label]));
    addOptions(reviewForm.elements.outcome, catalog.review_outcomes.map((label) => [label, label]));
    document.querySelector("#app-nav").hidden = false;
    status.hidden = true;
    render();
  } catch {
    if (session === sessionSequence) status.textContent = "Le corpus est indisponible. Rechargez la page.";
  }
}

async function start() {
  const status = document.querySelector("#load-status");
  try {
    const {user: profile} = await jsonRequest("/api/session");
    if (profile) await showSignedIn(profile);
    else showSignedOut();
  } catch {
    status.textContent = "La connexion est indisponible. Rechargez la page.";
  }
}

start();
