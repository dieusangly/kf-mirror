# Unified Assistant POC design

The application presents one Assistant over two explicitly pinned, published
snapshots. Retrieval uses the full 13-document `aml-cft-pilot-13` snapshot;
reasoning uses the already active `reasoning-poc-01` snapshot and its unchanged
reference to the frozen 2,164-mapping release. Exact IDs are owned by
[`config/assistant-poc.json`](../config/assistant-poc.json). No publication,
corpus substitution, review restart or application migration is implicit.

A bounded structured planner sees partial accepted-coverage metadata from the
existing selector, resolves references using previous user questions,
and divides a submission into at most four independently answerable parts. An
independent plan assessment checks coverage, reference resolution and routing.
Definitions, summaries and documentary explanations go directly to existing
retrieval; accepted facts and logical consequences use the existing reasoning
interpreter, audits, engine, evidence admission and certificate checks. Only an
explicit symbolic coverage gap permits a fresh documentary answer. A failed
citation, certificate, scope, integrity or audit check never authorizes fallback.
Independently validated parts survive failure elsewhere. Composition joins
validated parts without another model call or new conclusions.

The application enforces part, request, token, elapsed-time and output limits.
Only original source passages enter documentary answers; generated prose also
passes a fidelity assessment. Assistant history never supplies evidence or
logical premises. Source/document filters apply unchanged to both capabilities.
Binding admission checks exact source editions, document identities and every
overlapping accepted evidence span. Returned evidence is checked again.

New owner-bound conversations and atomic, idempotent user/assistant pairs live
in additive tables. Bindings are immutable per conversation and recorded with
filters on both messages. Existing sessions/messages retain their bytes and
provenance and are available separately as read-only legacy history. Internal
capabilities never persist their own turns.

Minimum gates are functional paths, faithful evidence, intact validation and
conversation isolation. Mocked language tests are distinct from live language
verification. Coverage gaps remain limitations; no minimum deduction count,
production pass-rate or historical latency gate applies. Paid calls, application
writes and activation require new approval after the exact scope and verified
backup/restore are prepared.
