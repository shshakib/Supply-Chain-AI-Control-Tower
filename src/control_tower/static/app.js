const TRACE_NODE_LABELS = {
  request: "Question",
  access: "Access scope",
  supervisor: "Supervisor",
  shipments: "Shipment specialist",
  inventory: "Inventory specialist",
  supplier_risk: "Supplier-risk specialist",
  contracts_compliance: "Contracts and compliance specialist",
  postgresql: "PostgreSQL",
  pgvector: "RAG retrieval",
  mcp: "External-risk MCP",
  synthesis: "Final synthesis",
  answer: "Answer",
};

const SPECIALIST_NODES = new Set([
  "shipments",
  "inventory",
  "supplier_risk",
  "contracts_compliance",
]);

const ACTIVE_NODE_META = {
  request: "In progress",
  access: "Authorizing",
  supervisor: "Planning",
  shipments: "Analyzing",
  inventory: "Analyzing",
  supplier_risk: "Analyzing",
  contracts_compliance: "Retrieving",
  postgresql: "Querying",
  pgvector: "Retrieving",
  mcp: "Fetching signals",
  synthesis: "Composing",
  answer: "Preparing",
};

const FLOW_TARGETS = {
  access: ["access"],
  supervisor: ["supervisor"],
  specialists: [...SPECIALIST_NODES],
  sources: ["postgresql", "pgvector", "mcp"],
  synthesis: ["synthesis"],
  answer: ["answer"],
};

const THEME_STORAGE_KEY = "control-tower-theme";

const state = {
  personas: [],
  conversationId: null,
  busy: false,
  traceEvents: [],
  activeOperations: new Map(),
  selectedTraceEvent: null,
  agentModels: {},
};

const elements = {
  appShell: document.querySelector(".app-shell"),
  themeColor: document.querySelector('meta[name="theme-color"]'),
  themeToggle: document.querySelector("#theme-toggle"),
  runtimeStatus: document.querySelector("#runtime-status"),
  personaSelect: document.querySelector("#persona-select"),
  personaRole: document.querySelector("#persona-role"),
  personaEmail: document.querySelector("#persona-email"),
  messageStream: document.querySelector("#message-stream"),
  emptyState: document.querySelector("#empty-state"),
  evidenceContent: document.querySelector("#evidence-content"),
  eventCount: document.querySelector("#event-count"),
  databaseService: document.querySelector("#database-service"),
  databaseStatus: document.querySelector("#database-status"),
  retrievalService: document.querySelector("#retrieval-service"),
  retrievalStatus: document.querySelector("#retrieval-status"),
  mcpService: document.querySelector("#mcp-service"),
  mcpStatus: document.querySelector("#mcp-status"),
  form: document.querySelector("#chat-form"),
  input: document.querySelector("#question-input"),
  sendButton: document.querySelector("#send-button"),
  runDemo: document.querySelector("#run-demo"),
  newConversation: document.querySelector("#new-conversation"),
  tracePanel: document.querySelector("#trace-panel"),
  traceToggle: document.querySelector("#trace-toggle"),
  traceClose: document.querySelector("#trace-close"),
  traceScrim: document.querySelector("#trace-scrim"),
  traceRunState: document.querySelector(".trace-run-state"),
  traceRunLabel: document.querySelector("#trace-run-label"),
  traceRunMeta: document.querySelector("#trace-run-meta"),
  timelineList: document.querySelector("#timeline-list"),
  exchangeInspector: document.querySelector("#exchange-inspector"),
  exchangeStatus: document.querySelector("#exchange-status"),
  exchangeTitle: document.querySelector("#exchange-title"),
  exchangeMeta: document.querySelector("#exchange-meta"),
  exchangeDetails: document.querySelector("#exchange-details"),
};

function storedTheme() {
  try {
    return window.localStorage.getItem(THEME_STORAGE_KEY);
  } catch {
    return null;
  }
}

function applyTheme(theme, persist = false) {
  const resolvedTheme = theme === "dark" ? "dark" : "light";
  document.documentElement.dataset.theme = resolvedTheme;
  const darkMode = resolvedTheme === "dark";
  const nextTheme = darkMode ? "light" : "dark";
  elements.themeToggle.setAttribute("aria-pressed", String(darkMode));
  elements.themeToggle.setAttribute("aria-label", `Switch to ${nextTheme} mode`);
  elements.themeToggle.title = `Switch to ${nextTheme} mode`;
  elements.themeColor.content = darkMode ? "#0a1218" : "#111c24";
  if (!persist) return;
  try {
    window.localStorage.setItem(THEME_STORAGE_KEY, resolvedTheme);
  } catch {
    // The selected theme still applies for this page when storage is unavailable.
  }
}

function initializeTheme() {
  const systemPreference = window.matchMedia("(prefers-color-scheme: dark)");
  const initialTheme = document.documentElement.dataset.theme
    || storedTheme()
    || (systemPreference.matches ? "dark" : "light");
  applyTheme(initialTheme);
  elements.themeToggle.addEventListener("click", () => {
    const nextTheme = document.documentElement.dataset.theme === "dark" ? "light" : "dark";
    applyTheme(nextTheme, true);
  });
  systemPreference.addEventListener("change", (event) => {
    if (!storedTheme()) applyTheme(event.matches ? "dark" : "light");
  });
}

async function request(path, options = {}) {
  const response = await fetch(path, {
    headers: { "Content-Type": "application/json" },
    ...options,
  });
  const payload = await response.json();
  if (!response.ok) {
    throw new Error(payload.detail || "Request failed");
  }
  return payload;
}

async function streamRequest(path, body, eventDelay = 20) {
  const response = await fetch(path, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!response.ok) {
    const payload = await response.json();
    throw new Error(payload.detail || "Request failed");
  }
  if (!response.body) {
    throw new Error("Streaming is unavailable in this browser.");
  }

  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  let finalResult = null;
  let streamError = null;

  while (true) {
    const { value, done } = await reader.read();
    buffer += decoder.decode(value || new Uint8Array(), { stream: !done });
    buffer = buffer.replaceAll("\r\n", "\n");

    let boundary = buffer.indexOf("\n\n");
    while (boundary >= 0) {
      const frame = buffer.slice(0, boundary);
      buffer = buffer.slice(boundary + 2);
      const parsed = parseSseFrame(frame);
      if (parsed?.event === "trace") {
        handleTraceEvent(parsed.payload);
        if (eventDelay) await delay(eventDelay);
      } else if (parsed?.event === "result") {
        finalResult = parsed.payload;
      } else if (parsed?.event === "error") {
        streamError = new Error(parsed.payload.detail || "Request failed");
      }
      boundary = buffer.indexOf("\n\n");
    }
    if (done) break;
  }

  if (streamError) throw streamError;
  if (!finalResult) throw new Error("The run ended without a result.");
  return finalResult;
}

function parseSseFrame(frame) {
  if (!frame || frame.startsWith(":")) return null;
  let event = "message";
  const data = [];
  for (const line of frame.split("\n")) {
    if (line.startsWith("event: ")) event = line.slice(7);
    if (line.startsWith("data: ")) data.push(line.slice(6));
  }
  if (!data.length) return null;
  return { event, payload: JSON.parse(data.join("\n")) };
}

function delay(milliseconds) {
  return new Promise((resolve) => window.setTimeout(resolve, milliseconds));
}

function selectedPersona() {
  return state.personas.find((persona) => persona.email === elements.personaSelect.value);
}

function updatePersona() {
  const persona = selectedPersona();
  if (!persona) return;
  elements.personaRole.textContent = persona.role.replaceAll("_", " ");
  elements.personaEmail.textContent = persona.email;
  state.conversationId = null;
  clearMessages();
}

function clearMessages() {
  elements.messageStream.replaceChildren(elements.emptyState);
  elements.emptyState.hidden = false;
  elements.evidenceContent.innerHTML = "<p>Tool calls and citations will appear here.</p>";
  resetTrace();
}

function addMessage(role, content, type = "") {
  elements.emptyState.hidden = true;
  const container = document.createElement("article");
  container.className = `message ${role} ${type}`.trim();
  const label = document.createElement("div");
  label.className = "message-label";
  label.textContent = role === "user" ? "You" : "Control Tower";
  const body = document.createElement("div");
  body.className = "message-body";
  body.textContent = content;
  container.append(label, body);
  elements.messageStream.append(container);
  elements.messageStream.scrollTop = elements.messageStream.scrollHeight;
}

function resetTrace() {
  state.traceEvents = [];
  state.activeOperations = new Map();
  state.selectedTraceEvent = null;
  elements.eventCount.textContent = "0";
  elements.timelineList.innerHTML = "<p>No execution events yet.</p>";
  setRunState("ready", "Ready", "No active run");
  for (const panel of document.querySelectorAll(".trace-view")) panel.scrollTop = 0;

  for (const node of document.querySelectorAll(".trace-node")) {
    node.classList.remove("active", "completed", "failed", "skipped", "selected");
    node.dataset.state = "idle";
    const meta = node.querySelector(".node-meta");
    if (!node.dataset.defaultMeta) node.dataset.defaultMeta = meta.textContent;
    meta.textContent = node.dataset.defaultMeta;
  }
  for (const connector of document.querySelectorAll(".trace-connector")) {
    connector.classList.remove("active", "completed", "failed");
  }

  elements.exchangeStatus.textContent = "Idle";
  elements.exchangeTitle.textContent = "No exchange selected";
  elements.exchangeMeta.textContent = "Run a query to populate the trace.";
  elements.exchangeDetails.replaceChildren();
}

function beginTrace(question) {
  resetTrace();
  setRunState("running", "Run in progress", question);
  switchTraceView("map");
  openTracePanel();
}

function handleTraceEvent(event) {
  state.traceEvents.push(event);
  elements.eventCount.textContent = String(state.traceEvents.length);
  updateTraceNode(event);
  updateTraceFlow();
  updateSupervisorPhase();
  appendTimelineEvent(event);
  selectTraceEvent(event);

  if (event.node === "request" && event.status === "completed") {
    setRunState("completed", "Run completed", `${state.traceEvents.length} events`);
  } else if (event.node === "request" && event.status === "failed") {
    setRunState("failed", "Run failed", event.label);
  }
}

function updateTraceNode(event) {
  const node = document.querySelector(`.trace-node[data-node="${event.node}"]`);
  if (!node) return;

  if (!state.activeOperations.has(event.node)) {
    state.activeOperations.set(event.node, new Set());
  }
  const active = state.activeOperations.get(event.node);
  if (event.status === "started") active.add(event.operation_id);
  if (["completed", "failed"].includes(event.status)) active.delete(event.operation_id);

  let nodeState = node.dataset.state || "idle";
  if (event.status === "failed") {
    nodeState = "failed";
  } else if (active.size) {
    nodeState = "active";
  } else if (event.status === "completed") {
    nodeState = "completed";
  } else if (event.status === "skipped" && nodeState === "idle") {
    nodeState = "skipped";
  }

  node.dataset.state = nodeState;
  node.classList.remove("active", "completed", "failed", "skipped");
  if (nodeState !== "idle") node.classList.add(nodeState);
  const meta = node.querySelector(".node-meta");
  if (nodeState === "active") meta.textContent = ACTIVE_NODE_META[event.node] || "Working";
  if (nodeState === "completed") meta.textContent = formatDuration(event.duration_ms) || "Complete";
  if (nodeState === "failed") meta.textContent = "Failed";
  if (nodeState === "skipped") meta.textContent = "Skipped";
}

function updateSupervisorPhase() {
  const supervisor = document.querySelector('.trace-node[data-node="supervisor"]');
  if (!supervisor || supervisor.dataset.state !== "active") return;

  const meta = supervisor.querySelector(".node-meta");
  const synthesis = document.querySelector('.trace-node[data-node="synthesis"]');
  if (synthesis?.dataset.state === "active") {
    meta.textContent = "Synthesizing";
    return;
  }

  const specialistStates = [...SPECIALIST_NODES].map((nodeName) =>
    document.querySelector(`.trace-node[data-node="${nodeName}"]`)?.dataset.state || "idle"
  );
  const activeCount = specialistStates.filter((nodeState) => nodeState === "active").length;
  if (activeCount) {
    meta.textContent = `Coordinating ${activeCount} ${activeCount === 1 ? "agent" : "agents"}`;
    return;
  }
  if (specialistStates.some((nodeState) => nodeState === "completed")) {
    meta.textContent = "Reviewing evidence";
    return;
  }
  meta.textContent = "Planning";
}

function updateTraceFlow() {
  for (const connector of document.querySelectorAll(".trace-connector")) {
    const targets = FLOW_TARGETS[connector.dataset.flow] || [];
    const states = targets.map((target) =>
      document.querySelector(`.trace-node[data-node="${target}"]`)?.dataset.state || "idle"
    );
    const active = states.some((nodeState) => nodeState === "active");
    const failed = states.some((nodeState) => nodeState === "failed");
    const reached = states.some((nodeState) =>
      ["completed", "failed", "skipped"].includes(nodeState)
    );
    connector.classList.toggle("active", active);
    connector.classList.toggle("failed", !active && failed);
    connector.classList.toggle("completed", !active && !failed && reached);
  }
}

function appendTimelineEvent(event) {
  if (state.traceEvents.length === 1) elements.timelineList.replaceChildren();
  const item = document.createElement("button");
  item.type = "button";
  item.className = `timeline-event ${event.status}`;
  item.dataset.sequence = String(event.sequence);

  const marker = document.createElement("span");
  marker.className = "timeline-marker";
  const copy = document.createElement("span");
  copy.className = "timeline-copy";
  const title = document.createElement("strong");
  title.textContent = event.label;
  const meta = document.createElement("span");
  meta.textContent = `${TRACE_NODE_LABELS[event.node] || event.node} | ${formatTime(event.occurred_at)}`;
  copy.append(title, meta);
  const duration = document.createElement("span");
  duration.className = "timeline-duration";
  duration.textContent = formatDuration(event.duration_ms);
  item.append(marker, copy, duration);
  item.addEventListener("click", () => {
    switchTraceView("map");
    selectTraceEvent(event, true);
  });
  elements.timelineList.append(item);
}

function selectTraceEvent(event, reveal = false) {
  state.selectedTraceEvent = event;
  for (const node of document.querySelectorAll(".trace-node")) {
    node.classList.toggle("selected", node.dataset.node === event.node);
  }
  const completedEvent = event.status === "completed"
    ? event
    : [...state.traceEvents].reverse().find((item) =>
      item.node === event.node && item.status === "completed"
    );
  const displayEvent = completedEvent || event;
  elements.exchangeStatus.textContent = displayEvent.status;
  elements.exchangeTitle.textContent = SPECIALIST_NODES.has(event.node)
    ? `${TRACE_NODE_LABELS[event.node]} exchange`
    : event.label;
  const source = event.source ? sourceLabel(event.source) : "Application";
  const model = state.agentModels[event.node];
  elements.exchangeMeta.textContent = [
    TRACE_NODE_LABELS[event.node] || event.node,
    source,
    model,
    formatTime(event.occurred_at),
  ].filter(Boolean).join(" | ");
  elements.exchangeDetails.replaceChildren();

  const startedEvent = state.traceEvents.find((item) =>
    item.operation_id === event.operation_id && item.status === "started"
  );
  if (SPECIALIST_NODES.has(event.node)) {
    const routeEvent = [...state.traceEvents].reverse().find((item) =>
      item.event_type === "routing"
      && item.details?.specialist === event.node
      && item.sequence <= displayEvent.sequence
    );
    renderSpecialistExchange(routeEvent, completedEvent, startedEvent);
    if (reveal) revealExchangeInspector();
    return;
  }

  const details = {
    ...(startedEvent?.details || {}),
    ...(event.details || {}),
    ...(event.parent_node ? { called_by: TRACE_NODE_LABELS[event.parent_node] } : {}),
    ...(event.duration_ms !== null && event.duration_ms !== undefined
      ? { duration: `${event.duration_ms} ms` }
      : {}),
  };
  for (const [key, value] of Object.entries(details)) {
    const row = document.createElement("div");
    row.className = "exchange-field";
    const term = document.createElement("span");
    term.className = "exchange-field-label";
    term.textContent = humanizeKey(key);
    const description = document.createElement("span");
    description.className = "exchange-field-value";
    description.textContent = formatDetail(value);
    row.append(term, description);
    elements.exchangeDetails.append(row);
  }
  if (reveal) revealExchangeInspector();
}

function revealExchangeInspector() {
  window.requestAnimationFrame(() => {
    const reducedMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    elements.exchangeInspector.scrollIntoView({
      behavior: reducedMotion ? "auto" : "smooth",
      block: "start",
    });
  });
}

function renderSpecialistExchange(routeEvent, completedEvent, startedEvent) {
  const delegatedTask = routeEvent?.details?.delegated_task
    || "The delegated task is not available yet.";
  appendExchangeSection("Input from supervisor", delegatedTask, "exchange-task");

  if (!completedEvent) {
    appendExchangeSection(
      "Specialist response",
      "Waiting for the specialist's structured response.",
      "exchange-pending",
    );
    return;
  }

  const details = completedEvent.details || {};
  appendExchangeSection(
    "Specialist response",
    details.summary || "The specialist completed without a public summary.",
    "exchange-response",
  );

  const evidence = Array.isArray(details.evidence) ? details.evidence : [];
  if (evidence.length) {
    const section = document.createElement("section");
    section.className = "exchange-section";
    const heading = document.createElement("div");
    heading.className = "exchange-section-heading";
    const label = document.createElement("span");
    label.textContent = "Evidence returned";
    const count = document.createElement("span");
    count.className = "exchange-section-count";
    count.textContent = String(evidence.length);
    heading.append(label, count);
    section.append(heading);

    for (const item of evidence) {
      const evidenceItem = document.createElement("div");
      evidenceItem.className = "exchange-evidence-item";
      const claim = document.createElement("p");
      claim.textContent = item.claim || "Evidence claim not supplied.";
      const reference = document.createElement("code");
      reference.textContent = item.reference || "No reference";
      evidenceItem.append(claim, reference);
      section.append(evidenceItem);
    }
    elements.exchangeDetails.append(section);
  }

  const limitations = Array.isArray(details.limitations) ? details.limitations : [];
  if (limitations.length) {
    const section = document.createElement("section");
    section.className = "exchange-section exchange-limitations";
    const heading = document.createElement("div");
    heading.className = "exchange-section-heading";
    const label = document.createElement("span");
    label.textContent = "Limitations";
    const count = document.createElement("span");
    count.className = "exchange-section-count";
    count.textContent = String(limitations.length);
    heading.append(label, count);
    const list = document.createElement("ul");
    for (const limitation of limitations) {
      const item = document.createElement("li");
      item.textContent = limitation;
      list.append(item);
    }
    section.append(heading, list);
    elements.exchangeDetails.append(section);
  }

  const metadata = document.createElement("div");
  metadata.className = "exchange-metadata";
  const domain = document.createElement("span");
  domain.textContent = humanizeKey(details.domain || "specialist");
  const duration = document.createElement("span");
  duration.textContent = formatDuration(completedEvent.duration_ms)
    || formatDuration(startedEvent?.duration_ms)
    || "Completed";
  metadata.append(domain, duration);
  elements.exchangeDetails.append(metadata);
}

function appendExchangeSection(label, content, className) {
  const section = document.createElement("section");
  section.className = `exchange-section ${className}`;
  const heading = document.createElement("div");
  heading.className = "exchange-section-heading";
  heading.textContent = label;
  const body = document.createElement("p");
  body.textContent = content;
  section.append(heading, body);
  elements.exchangeDetails.append(section);
}

function setRunState(status, label, meta) {
  elements.traceRunState.className = `trace-run-state ${status}`;
  elements.traceRunLabel.textContent = label;
  elements.traceRunMeta.textContent = meta;
}

function switchTraceView(view) {
  for (const tab of document.querySelectorAll(".trace-tab")) {
    const selected = tab.dataset.view === view;
    tab.classList.toggle("active", selected);
    tab.setAttribute("aria-selected", String(selected));
  }
  for (const panel of document.querySelectorAll("[data-view-panel]")) {
    const selected = panel.dataset.viewPanel === view;
    panel.hidden = !selected;
    panel.classList.toggle("active", selected);
  }
}

function openTracePanel() {
  elements.tracePanel.classList.add("open");
  elements.appShell.classList.add("trace-open");
  elements.traceToggle.setAttribute("aria-expanded", "true");
}

function closeTracePanel() {
  elements.tracePanel.classList.remove("open");
  elements.appShell.classList.remove("trace-open");
  elements.traceToggle.setAttribute("aria-expanded", "false");
}

function renderEvidence(events = [], citations = [], integrations = {}) {
  elements.evidenceContent.replaceChildren();
  const riskMcp = integrations.external_risk_mcp;
  if (riskMcp) updateMcpStatus(riskMcp);

  if (!events.length && !citations.length) {
    elements.evidenceContent.innerHTML = "<p>No specialist evidence was returned.</p>";
    return;
  }

  if (events.length) {
    const activitySection = document.createElement("section");
    activitySection.className = "evidence-section";
    activitySection.append(evidenceSectionHeading("Tool evidence", events.length));
    for (const event of events) {
      const item = document.createElement("article");
      item.className = "evidence-item";
      const heading = document.createElement("div");
      heading.className = "evidence-item-heading";
      const title = document.createElement("strong");
      title.textContent = humanizeKey(event.tool || event.specialist || "Specialist result");
      const source = document.createElement("span");
      source.className = `evidence-source ${event.source || "local"}`;
      source.textContent = sourceLabel(event.source);
      heading.append(title, source);
      const meta = document.createElement("span");
      const specialist = TRACE_NODE_LABELS[event.specialist] || humanizeKey(
        event.specialist || "local workflow"
      );
      const result = event.result_count !== undefined
        ? `${event.result_count} ${event.result_count === 1 ? "record" : "records"}`
        : event.summary || "Completed";
      meta.textContent = `${specialist} | ${result}`;
      item.append(heading, meta);
      activitySection.append(item);
    }
    elements.evidenceContent.append(activitySection);
  }

  if (citations.length) {
    elements.evidenceContent.append(renderCitationSection(citations));
  }
}

function evidenceSectionHeading(label, count) {
  const heading = document.createElement("div");
  heading.className = "evidence-section-heading";
  const title = document.createElement("strong");
  title.textContent = label;
  const badge = document.createElement("span");
  badge.textContent = String(count);
  heading.append(title, badge);
  return heading;
}

function renderCitationSection(citations) {
  const section = document.createElement("section");
  section.className = "citation-section";
  const uniqueCitations = [...new Set(
    citations.map((citation) => String(citation).trim()).filter(Boolean)
  )];
  section.append(evidenceSectionHeading("Sources", uniqueCitations.length));

  const groups = new Map();
  for (const citation of uniqueCitations) {
    const description = describeCitation(citation);
    if (!groups.has(description.group)) {
      groups.set(description.group, {
        label: description.groupLabel,
        items: [],
      });
    }
    groups.get(description.group).items.push(description);
  }

  for (const groupName of ["operations", "external", "documents", "other"]) {
    const group = groups.get(groupName);
    if (!group) continue;
    const groupElement = document.createElement("div");
    groupElement.className = "citation-group";
    const heading = document.createElement("div");
    heading.className = "citation-group-heading";
    const label = document.createElement("span");
    label.textContent = group.label;
    const count = document.createElement("span");
    count.textContent = String(group.items.length);
    heading.append(label, count);
    groupElement.append(heading);

    for (const citation of group.items) {
      const item = document.createElement("article");
      item.className = "citation-item";
      const type = document.createElement("span");
      type.className = `citation-type ${citation.tone}`;
      type.textContent = citation.type;
      const copy = document.createElement("div");
      copy.className = "citation-copy";
      const title = document.createElement("strong");
      title.textContent = citation.title;
      const meta = document.createElement("span");
      meta.textContent = citation.meta;
      const reference = document.createElement("code");
      reference.textContent = citation.raw;
      copy.append(title, meta, reference);
      item.append(type, copy);
      groupElement.append(item);
    }
    section.append(groupElement);
  }

  return section;
}

function describeCitation(raw) {
  let match = raw.match(/^tracking:([^:]+):(.+)$/i);
  if (match) {
    return citationDescription(
      "operations",
      "Operational records",
      "Tracking",
      `${match[1]} tracking event`,
      formatCitationDate(match[2]),
      raw,
      "operational",
    );
  }

  match = raw.match(/^shipment:(.+)$/i);
  if (match) {
    return citationDescription(
      "operations",
      "Operational records",
      "Shipment",
      match[1],
      "Authorized shipment record",
      raw,
      "operational",
    );
  }

  match = raw.match(/^inventory-history:([^/]+)\/([^/]+)\/(.+)$/i);
  if (match) {
    return citationDescription(
      "operations",
      "Operational records",
      "Inventory history",
      `${match[2]} at ${match[1]}`,
      formatCitationDate(match[3]),
      raw,
      "operational",
    );
  }

  match = raw.match(/^inventory:([^/]+)\/([^/]+)\/(.+)$/i);
  if (match) {
    return citationDescription(
      "operations",
      "Operational records",
      "Inventory",
      `${match[2]} at ${match[1]}`,
      formatCitationDate(match[3]),
      raw,
      "operational",
    );
  }

  match = raw.match(/^supplier:([^/]+)\s*\/\s*incident:([^:]+):(.+)$/i);
  if (match) {
    return citationDescription(
      "operations",
      "Operational records",
      "Quality incident",
      `${match[2]} at ${match[1].trim()}`,
      formatCitationDate(match[3]),
      raw,
      "operational",
    );
  }

  match = raw.match(/^supplier:(.+)$/i);
  if (match) {
    return citationDescription(
      "operations",
      "Operational records",
      "Supplier",
      match[1],
      "Supplier performance record",
      raw,
      "operational",
    );
  }

  match = raw.match(/^external-risk:(.+)$/i);
  if (match) {
    return citationDescription(
      "external",
      "External intelligence",
      "Risk signal",
      match[1],
      "Correlated MCP evidence",
      raw,
      "external",
    );
  }

  match = raw.match(/^(.+?)(?:\.md)?#chunk-(\d+)$/i);
  if (match) {
    const documentName = match[1].replace(/\.md$/i, "");
    return citationDescription(
      "documents",
      "Retrieved documents",
      "Document",
      titleFromSlug(documentName),
      `Retrieved passage ${match[2]}`,
      raw,
      "document",
    );
  }

  match = raw.match(/^(.+\.md)$/i);
  if (match) {
    return citationDescription(
      "documents",
      "Retrieved documents",
      "Document",
      titleFromSlug(match[1].replace(/\.md$/i, "")),
      "Retrieved document evidence",
      raw,
      "document",
    );
  }

  return citationDescription(
    "other",
    "Other references",
    "Reference",
    raw,
    "Stable evidence identifier",
    raw,
    "neutral",
  );
}

function citationDescription(group, groupLabel, type, title, meta, raw, tone) {
  return { group, groupLabel, type, title, meta, raw, tone };
}

function titleFromSlug(value) {
  return value
    .replaceAll("_", " ")
    .replaceAll("-", " ")
    .replace(/\s+/g, " ")
    .trim()
    .replace(/\b\w/g, (character) => character.toUpperCase())
    .replace(/\bSup (\d+)\b/g, "SUP-$1")
    .replace(/\bSs Critical (\d+)\b/g, "SS-CRITICAL-$1");
}

function formatCitationDate(value) {
  const dateOnly = /^\d{4}-\d{2}-\d{2}$/.test(value);
  const date = new Date(dateOnly ? `${value}T00:00:00Z` : value);
  if (Number.isNaN(date.getTime())) return value;
  const options = !dateOnly
    ? { dateStyle: "medium", timeStyle: "short" }
    : { dateStyle: "medium", timeZone: "UTC" };
  return new Intl.DateTimeFormat(undefined, options).format(date);
}

function sourceLabel(source) {
  return {
    application: "Application",
    openai: "OpenAI agent",
    postgresql: "PostgreSQL",
    pgvector: "RAG retrieval",
    mcp: "External MCP",
  }[source] || "Local workflow";
}

function humanizeKey(key) {
  return key.replaceAll("_", " ").replace(/^./, (character) => character.toUpperCase());
}

function formatDetail(value) {
  if (Array.isArray(value)) return value.join(", ");
  if (value && typeof value === "object") return JSON.stringify(value, null, 2);
  return String(value);
}

function formatTime(value) {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "";
  return date.toLocaleTimeString([], {
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
    fractionalSecondDigits: 3,
  });
}

function formatDuration(value) {
  if (value === null || value === undefined) return "";
  return value < 1000 ? `${Math.round(value)} ms` : `${(value / 1000).toFixed(1)} s`;
}

function setServiceState(container, textElement, status, text) {
  container.className = `service-row ${status}`;
  textElement.textContent = text;
}

function updateMcpStatus(status = {}) {
  const connectionState = status.state || "unavailable";
  if (connectionState === "connected") {
    setServiceState(elements.mcpService, elements.mcpStatus, "ready", "Connected");
  } else if (connectionState === "disabled") {
    setServiceState(elements.mcpService, elements.mcpStatus, "muted", "Disabled");
  } else {
    setServiceState(elements.mcpService, elements.mcpStatus, "warning", "Local fallback");
  }
}

function updateAgentModels(health) {
  const sharedSpecialistModel = health.specialist_model;
  const models = health.agent_models || {
    supervisor: health.supervisor_model,
    shipments: sharedSpecialistModel,
    inventory: sharedSpecialistModel,
    supplier_risk: sharedSpecialistModel,
    contracts_compliance: sharedSpecialistModel,
  };
  state.agentModels = models;
  for (const element of document.querySelectorAll("[data-agent-model]")) {
    const model = models[element.dataset.agentModel];
    element.textContent = model || "Unavailable";
    element.title = model ? `Configured model: ${model}` : "Model unavailable";
  }
}

function updateServiceStatus(health) {
  setServiceState(
    elements.databaseService,
    elements.databaseStatus,
    "ready",
    health.database,
  );

  const indexed = health.indexed_chunks || 0;
  const total = health.document_chunks || 0;
  const retrievalReady = indexed > 0;
  setServiceState(
    elements.retrievalService,
    elements.retrievalStatus,
    retrievalReady ? "ready" : "warning",
    retrievalReady ? `${indexed}/${total} indexed` : "Keyword fallback",
  );
  updateMcpStatus(health.external_risk_mcp);
  updateAgentModels(health);
}

function setBusy(busy) {
  state.busy = busy;
  elements.sendButton.disabled = busy;
  elements.input.disabled = busy;
  elements.sendButton.textContent = busy ? "Working" : "Send";
}

async function sendQuestion(question) {
  if (!question.trim() || state.busy) return;
  const persona = selectedPersona();
  addMessage("user", question);
  beginTrace(question);
  setBusy(true);
  try {
    const payload = await streamRequest("/api/chat/stream", {
      question,
      user_email: persona.email,
      conversation_id: state.conversationId,
    });
    state.conversationId = payload.conversation_id;
    addMessage("assistant", payload.output.answer);
    renderEvidence(payload.tool_events, payload.output.citations, payload.integrations);
  } catch (error) {
    setRunState("failed", "Run failed", error.message);
    addMessage("assistant", error.message, "error");
  } finally {
    setBusy(false);
    elements.input.focus();
  }
}

async function runLocalDemo() {
  if (state.busy) return;
  const persona = selectedPersona();
  const question = "Which delayed shipments could stop production, and what remedies apply?";
  addMessage("user", question);
  beginTrace(question);
  setBusy(true);
  try {
    const payload = await streamRequest(
      "/api/demo/stream",
      { user_email: persona.email, question },
      70,
    );
    addMessage("assistant", payload.output.answer);
    renderEvidence(payload.tool_events, payload.output.citations, payload.integrations);
  } catch (error) {
    setRunState("failed", "Run failed", error.message);
    addMessage("assistant", error.message, "error");
  } finally {
    setBusy(false);
  }
}

async function initialize() {
  try {
    const [health, personas] = await Promise.all([
      request("/api/health"),
      request("/api/personas"),
    ]);
    state.personas = personas;
    for (const persona of personas) {
      const option = document.createElement("option");
      option.value = persona.email;
      option.textContent = persona.display_name;
      elements.personaSelect.append(option);
    }
    const preferred = personas.find((persona) => persona.email.startsWith("noah.east"));
    if (preferred) elements.personaSelect.value = preferred.email;
    updatePersona();
    updateServiceStatus(health);

    const mcpReady = health.external_risk_mcp?.state === "connected";
    elements.runtimeStatus.className = health.openai_configured && mcpReady
      ? "runtime-status ready"
      : "runtime-status warning";
    const llmText = health.openai_configured ? "LLM ready" : "API key needed";
    const mcpText = mcpReady ? "Risk MCP online" : "Risk MCP fallback";
    elements.runtimeStatus.lastElementChild.textContent = `${llmText} | ${mcpText}`;
  } catch (error) {
    elements.runtimeStatus.className = "runtime-status warning";
    elements.runtimeStatus.lastElementChild.textContent = "Service unavailable";
    addMessage("assistant", error.message, "error");
  }
}

elements.form.addEventListener("submit", (event) => {
  event.preventDefault();
  const question = elements.input.value;
  elements.input.value = "";
  sendQuestion(question);
});

elements.input.addEventListener("keydown", (event) => {
  if (event.key === "Enter" && !event.shiftKey) {
    event.preventDefault();
    elements.form.requestSubmit();
  }
});

elements.personaSelect.addEventListener("change", updatePersona);
elements.newConversation.addEventListener("click", () => {
  state.conversationId = null;
  clearMessages();
  elements.input.focus();
});
elements.runDemo.addEventListener("click", runLocalDemo);
elements.traceToggle.addEventListener("click", openTracePanel);
elements.traceClose.addEventListener("click", closeTracePanel);
elements.traceScrim.addEventListener("click", closeTracePanel);

for (const tab of document.querySelectorAll(".trace-tab")) {
  tab.addEventListener("click", () => switchTraceView(tab.dataset.view));
}

for (const node of document.querySelectorAll(".trace-node")) {
  node.addEventListener("click", () => {
    const event = [...state.traceEvents].reverse().find((item) => item.node === node.dataset.node);
    if (event) selectTraceEvent(event, true);
  });
}

for (const button of document.querySelectorAll(".prompt-option")) {
  button.addEventListener("click", () => {
    elements.input.value = button.textContent.trim();
    elements.input.focus();
  });
}

document.addEventListener("keydown", (event) => {
  if (event.key === "Escape") closeTracePanel();
});

initializeTheme();
initialize();
