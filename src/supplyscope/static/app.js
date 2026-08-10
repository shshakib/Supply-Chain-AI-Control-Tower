const state = {
  personas: [],
  conversationId: null,
  busy: false,
};

const elements = {
  runtimeStatus: document.querySelector("#runtime-status"),
  personaSelect: document.querySelector("#persona-select"),
  personaRole: document.querySelector("#persona-role"),
  personaEmail: document.querySelector("#persona-email"),
  messageStream: document.querySelector("#message-stream"),
  emptyState: document.querySelector("#empty-state"),
  evidenceContent: document.querySelector("#evidence-content"),
  eventCount: document.querySelector("#event-count"),
  form: document.querySelector("#chat-form"),
  input: document.querySelector("#question-input"),
  sendButton: document.querySelector("#send-button"),
  runDemo: document.querySelector("#run-demo"),
  newConversation: document.querySelector("#new-conversation"),
};

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
  elements.eventCount.textContent = "0";
}

function addMessage(role, content, type = "") {
  elements.emptyState.hidden = true;
  const container = document.createElement("article");
  container.className = `message ${role} ${type}`.trim();
  const label = document.createElement("div");
  label.className = "message-label";
  label.textContent = role === "user" ? "You" : "SupplyScope";
  const body = document.createElement("div");
  body.className = "message-body";
  body.textContent = content;
  container.append(label, body);
  elements.messageStream.append(container);
  elements.messageStream.scrollTop = elements.messageStream.scrollHeight;
}

function renderEvidence(events = [], citations = []) {
  elements.evidenceContent.replaceChildren();
  elements.eventCount.textContent = String(events.length);
  if (!events.length && !citations.length) {
    elements.evidenceContent.innerHTML = "<p>No specialist activity was returned.</p>";
    return;
  }

  for (const event of events) {
    const item = document.createElement("div");
    item.className = "evidence-item";
    const title = document.createElement("strong");
    title.textContent = event.tool || event.specialist || "Specialist result";
    const specialist = document.createElement("span");
    specialist.textContent = event.specialist || "local workflow";
    const detail = document.createElement("code");
    detail.textContent = event.result_count !== undefined
      ? `${event.result_count} records`
      : event.summary || "completed";
    item.append(title, specialist, detail);
    elements.evidenceContent.append(item);
  }

  if (citations.length) {
    const citationBlock = document.createElement("div");
    citationBlock.className = "citation-list";
    const title = document.createElement("strong");
    title.textContent = "Citations";
    citationBlock.append(title);
    for (const citation of citations) {
      const code = document.createElement("code");
      code.textContent = citation;
      citationBlock.append(code);
    }
    elements.evidenceContent.append(citationBlock);
  }
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
  setBusy(true);
  try {
    const payload = await request("/api/chat", {
      method: "POST",
      body: JSON.stringify({
        question,
        user_email: persona.email,
        conversation_id: state.conversationId,
      }),
    });
    state.conversationId = payload.conversation_id;
    addMessage("assistant", payload.output.answer);
    renderEvidence(payload.tool_events, payload.output.citations);
  } catch (error) {
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
  setBusy(true);
  try {
    const payload = await request("/api/demo", {
      method: "POST",
      body: JSON.stringify({ user_email: persona.email, question }),
    });
    addMessage("assistant", payload.output.answer);
    renderEvidence(payload.tool_events, payload.output.citations);
  } catch (error) {
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

    elements.runtimeStatus.className = health.openai_configured
      ? "runtime-status ready"
      : "runtime-status warning";
    elements.runtimeStatus.lastElementChild.textContent = health.openai_configured
      ? `${health.database} | LLM ready`
      : `${health.database} | API key needed`;
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

for (const button of document.querySelectorAll(".prompt-option")) {
  button.addEventListener("click", () => {
    elements.input.value = button.textContent.trim();
    elements.input.focus();
  });
}

initialize();
