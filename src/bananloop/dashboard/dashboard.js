(() => {
  "use strict";

  let bearerToken = "";
  let selectedCampaignId = "";
  let campaignCursor = null;
  let researchCursor = null;
  const campaignSummaries = new Map();
  let eventCursor = 0;
  let polling = false;
  let refreshQueued = false;
  let selectionGeneration = 0;
  let pollTimer = null;
  let hasSuccessfulRead = false;
  const seenEventIds = new Set();
  const byId = (id) => document.getElementById(id);

  function setNotice(message, kind = "") {
    const node = byId("notice");
    node.textContent = message;
    node.dataset.kind = kind;
  }

  function setConnection(message, state = "") {
    const node = byId("connection");
    node.textContent = message;
    node.dataset.state = state;
  }

  async function api(path, options = {}) {
    const headers = new Headers(options.headers || {});
    if (bearerToken) headers.set("Authorization", `Bearer ${bearerToken}`);
    if (options.method && options.method !== "GET") {
      headers.set("X-BananLoop-CSRF", "1");
    }
    const response = await fetch(path, {
      ...options,
      headers,
      cache: "no-store",
      credentials: "same-origin",
    });
    if (!response.ok) {
      let details = {};
      try { details = await response.json(); } catch (_) { /* safe generic fallback */ }
      const error = new Error(details.error || `HTTP ${response.status}`);
      error.status = response.status;
      error.details = details;
      throw error;
    }
    return response.json();
  }

  function addOption(select, campaign) {
    const option = document.createElement("option");
    option.value = campaign.campaign_id;
    const label = [campaign.active ? "● " : "", campaign.campaign_id, campaign.status,
      campaign.profile || "legacy"].filter(Boolean).join(" · ");
    option.textContent = label;
    select.append(option);
  }

  async function loadCampaignPage(reset = false) {
    const select = byId("campaign-select");
    if (reset) {
      select.replaceChildren();
      campaignCursor = null;
      campaignSummaries.clear();
    }
    const query = new URLSearchParams({ limit: "100" });
    if (campaignCursor) query.set("cursor", campaignCursor);
    const page = await api(`/v1/campaigns?${query}`);
    for (const campaign of page.campaigns) {
      campaignSummaries.set(campaign.campaign_id, campaign);
      addOption(select, campaign);
    }
    campaignCursor = page.next_cursor;
    byId("load-more").hidden = !campaignCursor;
    select.disabled = select.options.length === 0;
    if (reset && page.campaigns.length) {
      const active = page.campaigns.find((item) => item.active) || page.campaigns[0];
      select.value = active.campaign_id;
      await selectCampaign(active.campaign_id);
    }
    return page;
  }

  function summaryCard(label, value, mono = false) {
    const wrapper = document.createElement("div");
    wrapper.className = "summary-card";
    const dt = document.createElement("dt");
    dt.textContent = label;
    const dd = document.createElement("dd");
    dd.textContent = value === null || value === undefined || value === "" ? "Unavailable" : String(value);
    if (mono) dd.classList.add("mono");
    wrapper.append(dt, dd);
    return wrapper;
  }

  function setSummaryMessage(message) {
    const title = document.createElement("dt");
    title.className = "sr-only";
    title.textContent = "Campaign summary";
    const body = document.createElement("dd");
    body.className = "empty";
    body.textContent = message;
    byId("summary-grid").replaceChildren(title, body);
  }

  function renderCampaignSummary(campaign, snapshot, graph) {
    const grid = byId("summary-grid");
    grid.replaceChildren();
    const fields = [
      ["Status", graph?.status || snapshot?.status || campaign.status], ["Mode", campaign.mode],
      ["Schema", campaign.schema_version], ["Profile", campaign.integrity_valid === true ? campaign.profile : "Unverified"],
      ["Campaign", campaign.campaign_id, true], ["Flag hash", campaign.integrity_valid === true ? campaign.flag_hash : null, true],
      ["Approved spec hash", campaign.approved_spec_hash, true],
      ["Integrity", campaign.integrity_valid === null ? "Not initialized" : (campaign.integrity_valid ? "Verified" : "Mismatch")],
      ["Active phase", snapshot?.engine_stage || graph?.nodes?.find((node) => node.status === "RUNNING")?.phase || "Unavailable"],
    ];
    for (const [label, value, mono] of fields) grid.append(summaryCard(label, value, mono));
    byId("as-of").textContent = snapshot?.as_of ? `Snapshot ${snapshot.as_of}`
      : graph?.as_of ? `Graph ${graph.as_of}` : `Index ${campaign.updated_at}`;
    byId("budget-summary").replaceChildren();
    const budget = snapshot?.budget;
    if (budget && typeof budget === "object") {
      for (const [name, value] of Object.entries(budget)) {
        if (value === null || typeof value === "object") continue;
        const chip = document.createElement("span");
        chip.className = "budget-chip";
        chip.textContent = `${name.replaceAll("_", " ")}: ${value}`;
        byId("budget-summary").append(chip);
      }
    }
    const controlsAllowed = campaign.schema_version === 1;
    for (const button of document.querySelectorAll("[data-command]")) button.disabled = !controlsAllowed;
    byId("control-help").textContent = controlsAllowed
      ? "A recorded command is operator intent only. The campaign supervisor must act before state changes."
      : "These legacy control routes do not apply to graph campaigns. Graph state is shown from its canonical API projection.";
  }

  function renderObservationChips(target, entries, emptyLabel) {
    target.replaceChildren();
    if (!entries.length) {
      const item = document.createElement("span");
      item.className = "muted";
      item.textContent = emptyLabel;
      target.append(item);
      return;
    }
    for (const [name, value] of entries) {
      const chip = document.createElement("span");
      chip.className = "budget-chip";
      chip.textContent = `${name.replaceAll("_", " ")}: ${value}`;
      target.append(chip);
    }
  }

  function renderObservability(value) {
    byId("observability-capture").textContent = `${value.source} · captured ${value.as_of} · campaign cursor ${value.event_cursor ?? "Unavailable"}`;
    const scheduler = value.scheduler || {};
    const schedulerEntries = scheduler.available
      ? [["planning reason", scheduler.reason], ["eligible candidates", scheduler.candidate_count],
        ["blocked nodes", scheduler.blocked_node_count],
        ["worker event cursor", scheduler.worker_event_cursor ?? "Unavailable"],
        ["source", `${scheduler.source} · ${scheduler.as_of}`]]
      : [];
    renderObservationChips(byId("scheduler-summary"), schedulerEntries,
      "Scheduler state unavailable; no wait reason is inferred.");
    const queue = value.queue || {};
    const queueEntries = [];
    if (queue.available && queue.states) {
      for (const [state, amount] of Object.entries(queue.states)) queueEntries.push([state, amount]);
      for (const [name, amount] of Object.entries(queue.leases || {})) queueEntries.push([`lease_${name}`, amount]);
      queueEntries.push(["worker event cursor", queue.worker_event_cursor ?? "Unavailable"]);
      queueEntries.push(["transition_window", queue.transition_window || "bounded recent event window"]);
      for (const [name, amount] of Object.entries(queue.transitions || {})) {
        if (amount > 0 && ["LEASE_EXPIRED_PRE_DISPATCH", "JOB_UNKNOWN", "UNKNOWN_RECONCILED"].includes(name)) {
          queueEntries.push([name, amount]);
        }
      }
      renderObservationChips(byId("queue-summary"), queueEntries, "No queue records.");
    } else {
      renderObservationChips(byId("queue-summary"), [], "Queue state unavailable; it is not equivalent to an empty queue.");
    }
    const dimensions = value.budget?.available ? Object.entries(value.budget.dimensions || {}) : [];
    const budgetEntries = [];
    for (const [resource, values] of dimensions) {
      budgetEntries.push([`${resource} cap/spent/reserved/unknown/available`,
        `${values.cap}/${values.spent}/${values.reserved}/${values.unknown}/${values.available}`]);
    }
    renderObservationChips(byId("budget-dimensions"), budgetEntries,
      "Per-dimension budget records unavailable; no zero budget is inferred.");
  }

  async function refreshObservability(campaignId, generation) {
    try {
      const value = await api(`/v1/campaigns/${encodeURIComponent(campaignId)}/observability`);
      if (generation !== selectionGeneration || campaignId !== selectedCampaignId) return;
      renderObservability(value);
    } catch (error) {
      if (generation !== selectionGeneration || campaignId !== selectedCampaignId) return;
      byId("observability-capture").textContent = `Observability unavailable: ${error.message}`;
      renderObservationChips(byId("scheduler-summary"), [], "Scheduler state unavailable.");
      renderObservationChips(byId("queue-summary"), [], "Queue state unavailable.");
      renderObservationChips(byId("budget-dimensions"), [], "Budget state unavailable.");
    }
  }

  function renderLifecycle(value) {
    const phaseTarget = byId("phase-state");
    phaseTarget.replaceChildren();
    if (!value.available || !value.phase_state) {
      phaseTarget.append(Object.assign(document.createElement("span"), {
        className: "muted", textContent: "Optimization phase records are unavailable for this campaign.",
      }));
    } else {
      for (const [phase, statuses] of Object.entries(value.phase_state)) {
        const total = Object.values(statuses).reduce((sum, count) => sum + count, 0);
        if (!total) continue;
        const chip = document.createElement("span");
        chip.className = "budget-chip";
        chip.textContent = `${phase}: ${Object.entries(statuses).filter(([, count]) => count > 0)
          .map(([status, count]) => `${status} ${count}`).join(" · ")}`;
        phaseTarget.append(chip);
      }
    }
    const target = byId("lifecycle-items");
    target.replaceChildren();
    for (const proposal of value.proposals || []) {
      target.append(researchCard(
        `Pivot ${proposal.proposal_id} · ${proposal.status}`,
        proposal.rationale
          ? `${proposal.current_architecture} → ${proposal.proposed_architecture}. ${proposal.rationale} Expected: ${proposal.expected_effect}. Scope items: ${proposal.changed_scope_count}. Resources: ${JSON.stringify(proposal.resources || {})}`
          : "Persisted proposal identity failed validation; inspect the source records through the reviewed Core interface.",
        proposal.classification || "PERSISTED PROPOSAL",
      ));
    }
    for (const decision of value.decisions || []) {
      target.append(researchCard(
        `Owner decision · ${decision.proposal_id} · ${decision.status}`,
        `${decision.decision}${decision.rejection_action ? ` · ${decision.rejection_action}` : ""} · ${decision.created_at}`,
        "PERSISTED OWNER DECISION",
      ));
    }
    for (const child of value.children || []) {
      target.append(researchCard(
        `Child campaign · ${child.child_id || "Reserved"} · ${child.state}`,
        `Spec hash ${child.child_spec_hash} · updated ${child.updated_at}. Child initialization and activation are tracked by saga state.`,
        "PERSISTED HANDOFF STATE",
      ));
    }
    for (const receipt of value.verifier_receipts || []) {
      target.append(researchCard(
        `Evaluator verification · ${receipt.decision}`,
        `Receipt ${receipt.receipt_digest.slice(0, 16)} · protocol ${receipt.protocol_digest.slice(0, 16)} · approval ${receipt.approval_digest ? receipt.approval_digest.slice(0, 16) : "none"}`,
        "CORE VERIFIER RECEIPT",
      ));
    }
    for (const receipt of value.integration_receipts || []) {
      target.append(researchCard(
        `Integration verification · ${receipt.decision}`,
        `Receipt ${receipt.receipt_digest.slice(0, 16)} · protocol ${receipt.protocol_digest.slice(0, 16)} · candidate ${receipt.candidate_snapshot_id.slice(0, 12)}`,
        "CORE INTEGRATION RECEIPT",
      ));
    }
    const evaluatorState = value.evaluator_state;
    if (evaluatorState) {
      const verifier = evaluatorState.verifier || {};
      const integration = evaluatorState.integration || {};
      target.append(researchCard(
        "Evaluator lifecycle totals",
        `Verifier approvals/rejections ${verifier.APPROVE ?? 0}/${verifier.REJECT ?? 0}; integration passes/failures ${integration.PASS ?? 0}/${integration.FAIL ?? 0}; run receipts ${evaluatorState.run_receipts ?? 0}. Scores are not shown.`,
        "PERSISTED RECEIPT COUNTS",
      ));
    }
    for (const handoff of value.protocol_handoffs || []) {
      target.append(researchCard(
        `Frozen evaluator handoff · ${handoff.parent_campaign_id} → ${handoff.child_campaign_id}`,
        `${handoff.protocol_id} v${handoff.protocol_version} · protocol ${handoff.protocol_digest.slice(0, 16)} · receipt ${handoff.receipt_digest.slice(0, 16)}`,
        "PERSISTED PROTOCOL HANDOFF",
      ));
    }
    target.append(researchCard(
      "Evaluator run receipts",
      value.evaluator_run_receipt_count === null ? "Run receipt count unavailable." : `${value.evaluator_run_receipt_count} durable run receipts; evaluation scores are not shown in this view.`,
      "CORE RECEIPT COUNT",
    ));
    if (Object.values(value.truncated || {}).some(Boolean)) {
      target.append(researchCard("Receipt list is bounded", "More than 100 records exist for one or more sections.", "TRUNCATED VIEW"));
    }
    if (!target.children.length) {
      target.append(Object.assign(document.createElement("p"), {
        className: "empty", textContent: "No pivot, child, or evaluator verification receipts are persisted yet.",
      }));
    }
    byId("lifecycle-source").textContent = `${value.source} · ${value.as_of} · cursor ${value.event_cursor ?? "Unavailable"}`;
  }

  async function refreshLifecycle(campaignId, generation) {
    try {
      const value = await api(`/v1/campaigns/${encodeURIComponent(campaignId)}/lifecycle`);
      if (generation !== selectionGeneration || campaignId !== selectedCampaignId) return;
      renderLifecycle(value);
    } catch (error) {
      if (generation !== selectionGeneration || campaignId !== selectedCampaignId) return;
      byId("lifecycle-source").textContent = "Unavailable";
      byId("lifecycle-items").replaceChildren(Object.assign(document.createElement("p"), {
        className: "empty", textContent: `Lifecycle receipts unavailable: ${error.message}`,
      }));
    }
  }

  function appendListItems(target, values, emptyLabel) {
    target.replaceChildren();
    if (!values.length) {
      const item = document.createElement("li");
      item.className = "empty";
      item.textContent = emptyLabel;
      target.append(item);
      return;
    }
    for (const value of values) {
      const item = document.createElement("li");
      item.textContent = value;
      target.append(item);
    }
  }

  function renderGraph(graph) {
    byId("graph-panel").hidden = false;
    byId("graph-revision").textContent = `Revision ${graph.graph_revision}`;
    const nodeList = byId("graph-nodes");
    nodeList.replaceChildren();
    for (const node of graph.nodes || []) {
      const card = document.createElement("article");
      card.className = "node-card";
      const title = document.createElement("div");
      title.className = "node-title";
      title.textContent = node.node_id;
      const meta = document.createElement("div");
      meta.className = "node-meta";
      meta.textContent = `${node.phase} · ${node.status} · rev ${node.revision}`;
      const readiness = document.createElement("div");
      readiness.className = node.ready ? "node-ready" : "node-blockers";
      readiness.textContent = node.ready ? "Ready by Core" : `Blocked: ${(node.blockers || []).join(", ")}`;
      const evidence = document.createElement("div");
      evidence.className = "node-meta";
      evidence.textContent = `Opaque completion refs: ${(node.completion_evidence_refs || []).length}`;
      card.append(title, meta, readiness, evidence);
      nodeList.append(card);
    }
    appendListItems(byId("graph-edges"), (graph.edges || []).map((edge) =>
      `${edge.source_id} — ${edge.type} → ${edge.target_id}`), "No dependency edges.");
    appendListItems(byId("graph-groups"), (graph.groups || []).map((group) =>
      `${group.kind} ${group.group_id}: ${group.member_node_ids.join(", ")} → ${group.owner_node_id}`),
    "No readiness groups.");
  }

  function researchCard(title, details, label) {
    const card = document.createElement("article");
    card.className = "research-card";
    const heading = document.createElement("h3");
    heading.textContent = title;
    const badge = document.createElement("span");
    badge.className = "research-label";
    badge.textContent = label;
    const text = document.createElement("p");
    text.textContent = details;
    card.append(heading, badge, text);
    return card;
  }

  async function loadResearchScopes(campaignId = selectedCampaignId, reset = true,
                                   generation = selectionGeneration) {
    const select = byId("research-scope");
    const previous = select.value;
    if (reset) researchCursor = null;
    const query = new URLSearchParams({ limit: "50" });
    if (researchCursor) query.set("cursor", researchCursor);
    const result = await api(`/v1/campaigns/${encodeURIComponent(campaignId)}/research?${query}`);
    if (generation !== selectionGeneration || campaignId !== selectedCampaignId) return;
    if (reset) select.replaceChildren();
    for (const scope of result.scopes || []) {
      const option = document.createElement("option");
      option.value = `${scope.source_snapshot_id}|${scope.protocol_digest}`;
      option.textContent = `${scope.source_snapshot_id.slice(0, 12)} · ${scope.protocol_digest.slice(0, 12)} · cursor ${scope.event_cursor}`;
      option.dataset.snapshot = scope.source_snapshot_id;
      option.dataset.protocol = scope.protocol_digest;
      select.append(option);
    }
    researchCursor = result.next_cursor;
    byId("research-load-more").hidden = !researchCursor;
    select.disabled = select.options.length === 0;
    byId("research-source").textContent = result.source || "PERSISTED";
    if (!select.options.length) {
      select.add(new Option("No PUBLIC_DEV scopes found", ""));
      byId("research-findings").replaceChildren();
      byId("research-findings").append(Object.assign(document.createElement("p"), {
        className: "empty", textContent: result.next_cursor
          ? "More scopes are available through the documented paginated API."
          : "No persisted PUBLIC_DEV knowledge scope exists for this campaign.",
      }));
      return;
    }
    select.value = [...select.options].some((option) => option.value === previous)
      ? previous : select.options[0].value;
    await loadResearchProjection(campaignId, generation);
  }

  async function loadResearchProjection(campaignId = selectedCampaignId,
                                        generation = selectionGeneration) {
    const option = byId("research-scope").selectedOptions[0];
    if (!option || !option.dataset.snapshot) return;
    const query = new URLSearchParams({
      source_snapshot_id: option.dataset.snapshot,
      protocol_digest: option.dataset.protocol,
    });
    const result = await api(`/v1/campaigns/${encodeURIComponent(campaignId)}/research?${query}`);
    const currentScope = byId("research-scope").selectedOptions[0];
    if (generation !== selectionGeneration || campaignId !== selectedCampaignId
        || !currentScope || currentScope.dataset.snapshot !== option.dataset.snapshot
        || currentScope.dataset.protocol !== option.dataset.protocol) return;
    if (generation !== selectionGeneration || campaignId !== selectedCampaignId) return;
    const target = byId("research-findings");
    target.replaceChildren();
    const contradictionByEvidence = new Map();
    for (const item of result.contradictions || []) {
      for (const id of [...item.supporting_evidence_ids, ...item.refuting_evidence_ids]) {
        contradictionByEvidence.set(id, item.contradiction_id);
      }
    }
    for (const item of result.evidence || []) {
      const contradictionId = contradictionByEvidence.get(item.evidence_id);
      target.append(researchCard(
        `${item.claim_key} · ${item.stance}`,
        `${item.statement} | ${item.campaign_id}/${item.node_id} · ${item.kind} · confidence ${item.confidence}`
          + (contradictionId ? ` · Contradiction ${contradictionId.slice(0, 12)}` : ""),
        contradictionId ? "CONTRADICTED" : (item.kind === "CAUSAL_INFERENCE"
          ? "UNVERIFIED INFERENCE" : "UNVERIFIED ROLE FINDING"),
      ));
    }
    for (const item of result.causal_memory || []) {
      target.append(researchCard(
        `Causal memory · ${item.claim_key}`,
        `${(item.statements || []).join("; ")} · ${item.status}`,
        item.status === "CONTRADICTED" ? "CONTRADICTED" : "UNVERIFIED INFERENCE",
      ));
    }
    for (const item of result.contradictions || []) {
      target.append(researchCard(
        `Contradiction · ${item.claim_key}`,
        `Supporting evidence: ${item.supporting_evidence_ids.length}; refuting evidence: ${item.refuting_evidence_ids.length}.`,
        "CONTRADICTED",
      ));
    }
    for (const item of result.structural_memory || []) {
      target.append(researchCard(
        `Intervention · ${item.intervention_type} / ${item.algorithmic_layer}`,
        `Evidence: ${item.evidence_ids.length} · support ${item.support_count} · refutation ${item.refutation_count}`,
        "UNVERIFIED STRUCTURAL MEMORY",
      ));
    }
    if (!target.children.length) {
      target.append(Object.assign(document.createElement("p"), {
        className: "empty", textContent: "This PUBLIC_DEV scope has no projected findings.",
      }));
    }
    byId("research-source").textContent = `${result.source} · ${result.as_of}`;
  }

  function appendEvents(events) {
    const list = byId("event-list");
    const empty = list.querySelector(".empty");
    if (empty) list.replaceChildren();
    for (const event of events) {
      if (seenEventIds.has(event.seq)) continue;
      seenEventIds.add(event.seq);
      const item = document.createElement("li");
      item.className = "event-item";
      const seq = document.createElement("span");
      seq.className = "event-seq";
      seq.textContent = `#${event.seq}`;
      const type = document.createElement("span");
      type.className = "event-type";
      type.textContent = event.type;
      const time = document.createElement("time");
      time.className = "event-time";
      time.dateTime = event.created_at;
      time.textContent = event.created_at;
      const payload = document.createElement("span");
      payload.className = "event-payload";
      payload.textContent = JSON.stringify(event.payload || {});
      item.append(seq, type, time, payload);
      list.prepend(item);
    }
    while (list.children.length > 100) list.lastElementChild.remove();
  }

  async function refreshEvents(campaignId, generation) {
    const base = `/v1/campaigns/${encodeURIComponent(campaignId)}/events`;
    let cursor = eventCursor;
    let page;
    try {
      page = await api(`${base}?after=${cursor}&limit=100`);
    } catch (error) {
      if (error.status === 410) {
        await api(`/v1/campaigns/${encodeURIComponent(campaignId)}/snapshot`);
        if (generation !== selectionGeneration || campaignId !== selectedCampaignId) return;
        cursor = Number(error.details.cursor_floor || 0);
        setNotice("Some older events are outside retention. Snapshot refreshed; continuing from the retained range.");
        page = await api(`${base}?after=${cursor}&limit=100`);
      } else if (error.status === 409) {
        await api(`/v1/campaigns/${encodeURIComponent(campaignId)}/snapshot`);
        if (generation !== selectionGeneration || campaignId !== selectedCampaignId) return;
        cursor = Number(error.details.current_cursor || 0);
        eventCursor = cursor;
        setNotice("Event cursor was ahead of the campaign. Snapshot refreshed and cursor reset.");
        byId("event-cursor").textContent = `Cursor ${cursor}`;
        return;
      } else {
        throw error;
      }
    }
    if (generation !== selectionGeneration || campaignId !== selectedCampaignId) return;
    appendEvents(page.events || []);
    eventCursor = Number(page.cursor || eventCursor);
    byId("event-cursor").textContent = `Cursor ${eventCursor} · floor ${page.cursor_floor}`;
  }

  async function refreshSelected() {
    if (!selectedCampaignId) return;
    if (polling) { refreshQueued = true; return; }
    polling = true;
    const campaignId = selectedCampaignId;
    const generation = selectionGeneration;
    const isCurrent = () => generation === selectionGeneration && campaignId === selectedCampaignId;
    setConnection(hasSuccessfulRead ? "Refreshing" : "Loading campaign", hasSuccessfulRead ? "stale" : "loading");
    try {
      const selected = campaignSummaries.get(campaignId);
      if (!selected) throw new Error("Selected campaign is no longer in the campaign index.");
      let snapshot = null;
      try {
        snapshot = await api(`/v1/campaigns/${encodeURIComponent(campaignId)}/snapshot`);
      } catch (_) { /* V2 graph campaigns use their own projection. */ }
      if (!isCurrent()) return;
      let graph = null;
      try {
        graph = await api(`/v1/campaigns/${encodeURIComponent(campaignId)}/graph`);
        if (!isCurrent()) return;
        renderGraph(graph);
      } catch (error) {
        if (!isCurrent()) return;
        if (error.status === 404 || error.status === 409) byId("graph-panel").hidden = true;
        else throw error;
      }
      if (!isCurrent()) return;
      renderCampaignSummary(selected, snapshot, graph);
      await refreshObservability(campaignId, generation);
      await refreshLifecycle(campaignId, generation);
      try {
        await loadResearchScopes(campaignId, true, generation);
      } catch (error) {
        if (!isCurrent()) return;
        byId("research-source").textContent = "Unavailable";
        byId("research-findings").replaceChildren(Object.assign(document.createElement("p"), {
          className: "empty", textContent: `Research knowledge unavailable: ${error.message}`,
        }));
      }
      await refreshEvents(campaignId, generation);
      if (!isCurrent()) return;
      hasSuccessfulRead = true;
      setConnection("Connected", "connected");
      if (!byId("notice").textContent) setNotice(selected.integrity_valid === false
        ? "Campaign spec hash mismatch. Profile and feature flags are withheld."
        : "State is read from durable campaign records.");
    } catch (error) {
      if (!isCurrent()) return;
      if (error.status === 401 || error.status === 403) {
        byId("auth-panel").hidden = false;
        setConnection(error.status === 403 ? "Unauthorized" : "Authentication required", "error");
        setNotice(error.status === 403
          ? "The API denied this request. Check campaign access and local API authorization."
          : "The API rejected this request. Enter the local bearer token or clear the current token.", "error");
      } else if (error.status >= 500) {
        setConnection(hasSuccessfulRead ? "Stale · server error" : "Server error", "error");
        setNotice(`The API returned a server error: ${error.message}`, "error");
      } else if (!error.status) {
        setConnection(hasSuccessfulRead ? "Stale · disconnected" : "Disconnected", "error");
        setNotice("The local API connection was lost. Campaign state is not inferred from the transport failure.", "error");
      } else {
        setConnection(hasSuccessfulRead ? "Stale · reconnecting" : "Unavailable", hasSuccessfulRead ? "stale" : "error");
        setNotice(`Campaign data is unavailable: ${error.message}`, "error");
      }
    } finally {
      polling = false;
      if (refreshQueued) {
        refreshQueued = false;
        queueMicrotask(() => refreshSelected());
      }
    }
  }

  async function selectCampaign(campaignId) {
    selectionGeneration += 1;
    selectedCampaignId = campaignId;
    hasSuccessfulRead = false;
    setNotice("");
    eventCursor = 0;
    seenEventIds.clear();
    byId("event-list").replaceChildren();
    setSummaryMessage(campaignId ? "Loading selected campaign…" : "Choose a campaign to see its safe summary.");
    byId("observability-capture").textContent = "Loading persisted observations…";
    byId("queue-summary").replaceChildren();
    byId("budget-dimensions").replaceChildren();
    byId("graph-panel").hidden = true;
    byId("graph-nodes").replaceChildren();
    byId("graph-edges").replaceChildren();
    byId("graph-groups").replaceChildren();
    byId("research-scope").replaceChildren();
    byId("research-scope").disabled = true;
    byId("research-findings").replaceChildren(Object.assign(document.createElement("p"), {
      className: "empty", textContent: "Loading PUBLIC_DEV research projection…",
    }));
    byId("research-source").textContent = "Loading";
    byId("research-load-more").hidden = true;
    byId("lifecycle-source").textContent = "Loading";
    byId("lifecycle-items").replaceChildren(Object.assign(document.createElement("p"), {
      className: "empty", textContent: "Loading persisted lifecycle receipts…",
    }));
    byId("phase-state").replaceChildren();
    for (const button of document.querySelectorAll("[data-command]")) button.disabled = true;
    byId("refresh").disabled = !campaignId;
    byId("control-result").textContent = "";
    if (campaignId) await refreshSelected();
  }

  async function submitControl(command) {
    if (!selectedCampaignId) return;
    const campaignId = selectedCampaignId;
    const generation = selectionGeneration;
    const requestId = crypto.randomUUID();
    try {
      const receipt = await api(`/v1/campaigns/${encodeURIComponent(campaignId)}/commands/${command}`, {
        method: "POST",
        headers: { "Content-Type": "application/json", "Idempotency-Key": requestId },
        body: JSON.stringify({ request_id: requestId }),
      });
      if (generation !== selectionGeneration || campaignId !== selectedCampaignId) return;
      byId("control-result").textContent = `Intent ${receipt.status || "recorded"} with receipt ${receipt.command_id || receipt.request_id || requestId}. Campaign execution has not been confirmed.`;
    } catch (error) {
      if (generation !== selectionGeneration || campaignId !== selectedCampaignId) return;
      byId("control-result").textContent = `Command was not confirmed: ${error.message}`;
    }
  }

  byId("token-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    bearerToken = byId("token-input").value;
    byId("token-input").value = "";
    byId("auth-panel").hidden = true;
    byId("overview-title").focus();
    byId("clear-token").hidden = !bearerToken;
    setNotice("");
    try {
      await loadCampaignPage(true);
      setConnection("Connected", "connected");
      if (pollTimer) clearInterval(pollTimer);
      pollTimer = setInterval(refreshSelected, 5000);
      const campaignSelect = byId("campaign-select");
      (campaignSelect.disabled ? byId("overview-title") : campaignSelect).focus();
    } catch (error) {
      byId("auth-panel").hidden = false;
      bearerToken = "";
      setConnection(error.status === 401 ? "Authentication required" : "Unavailable", "error");
      setNotice(error.status === 401 ? "The token was not accepted." : error.message, "error");
      byId("token-input").focus();
    }
  });

  byId("campaign-select").addEventListener("change", (event) => selectCampaign(event.target.value));
  byId("research-load-more").addEventListener("click", () => loadResearchScopes(selectedCampaignId, false).catch((error) => {
    setNotice(`Research scopes unavailable: ${error.message}`, "error");
  }));
  byId("research-scope").addEventListener("change", () => loadResearchProjection(selectedCampaignId).catch((error) => {
    byId("research-source").textContent = "Unavailable";
    setNotice(`Research projection unavailable: ${error.message}`, "error");
  }));
  byId("load-more").addEventListener("click", async () => {
    try { await loadCampaignPage(false); }
    catch (error) { setNotice(`Campaign index unavailable: ${error.message}`, "error"); }
  });
  byId("refresh").addEventListener("click", refreshSelected);
  byId("clear-token").addEventListener("click", () => {
    selectionGeneration += 1;
    bearerToken = "";
    selectedCampaignId = "";
    if (pollTimer) clearInterval(pollTimer);
    byId("auth-panel").hidden = false;
    byId("clear-token").hidden = true;
    byId("token-input").focus();
    byId("campaign-select").replaceChildren();
    byId("campaign-select").disabled = true;
    campaignSummaries.clear();
    researchCursor = null;
    byId("refresh").disabled = true;
    setSummaryMessage("Choose a campaign to see its safe summary.");
    byId("budget-summary").replaceChildren();
    byId("observability-capture").textContent = "Persisted state has not been loaded.";
    byId("queue-summary").replaceChildren();
    byId("budget-dimensions").replaceChildren();
    byId("graph-panel").hidden = true;
    byId("graph-nodes").replaceChildren();
    byId("graph-edges").replaceChildren();
    byId("graph-groups").replaceChildren();
    byId("research-scope").replaceChildren();
    byId("research-scope").disabled = true;
    byId("research-findings").replaceChildren();
    byId("research-source").textContent = "Not loaded";
    byId("research-load-more").hidden = true;
    byId("lifecycle-source").textContent = "Not loaded";
    byId("lifecycle-items").replaceChildren();
    byId("phase-state").replaceChildren();
    byId("event-list").replaceChildren();
    byId("control-result").textContent = "";
    setConnection("Token cleared", "");
    hasSuccessfulRead = false;
    setNotice("Credentials are cleared from memory.");
  });
  for (const button of document.querySelectorAll("[data-command]")) {
    button.addEventListener("click", () => submitControl(button.dataset.command));
  }

  if (!window.isSecureContext && location.hostname !== "localhost" && location.hostname !== "127.0.0.1") {
    setNotice("Dashboard requires a same-origin loopback connection.", "error");
  }
  setConnection("Ready to connect");
})();
