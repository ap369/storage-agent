const log = document.getElementById("log");
const composer = document.getElementById("composer");
const contentInput = document.getElementById("content");
const mcpStatusEl = document.getElementById("mcp-status");

let conversationId = null;

function appendLine(text, className) {
  const line = document.createElement("div");
  line.className = className;
  line.textContent = text;
  log.appendChild(line);
  log.scrollTop = log.scrollHeight;
}

let thinkingEl = null;

function showThinking() {
  if (thinkingEl) return;
  thinkingEl = document.createElement("div");
  thinkingEl.className = "thinking";
  thinkingEl.textContent = "thinking…";
  log.appendChild(thinkingEl);
  log.scrollTop = log.scrollHeight;
}

function hideThinking() {
  if (!thinkingEl) return;
  thinkingEl.remove();
  thinkingEl = null;
}

function getToken() {
  let token = localStorage.getItem("storage-agent-token");
  if (!token) {
    token = window.prompt("API token:") || "";
    localStorage.setItem("storage-agent-token", token);
  }
  return token;
}

function renderMcpStatus(servers) {
  mcpStatusEl.replaceChildren();
  if (!servers.length) {
    mcpStatusEl.textContent = "No MCP servers configured";
    return;
  }
  for (const server of servers) {
    const badge = document.createElement("span");
    badge.className = `mcp-badge ${server.connected ? "connected" : "disconnected"}`;
    badge.textContent = server.connected
      ? `${server.name} (${server.tools.length} tools)`
      : `${server.name} (disconnected)`;
    mcpStatusEl.appendChild(badge);
  }
}

async function loadMcpStatus() {
  try {
    const response = await fetch("/mcp/status", {
      headers: { Authorization: `Bearer ${getToken()}` },
    });
    if (!response.ok) throw new Error(`status ${response.status}`);
    renderMcpStatus(await response.json());
  } catch (err) {
    mcpStatusEl.textContent = "MCP status unavailable";
  }
}

loadMcpStatus();

const protocol = window.location.protocol === "https:" ? "wss:" : "ws:";
const socket = new WebSocket(`${protocol}//${window.location.host}/ws/chat`);

socket.addEventListener("open", () => {
  socket.send(JSON.stringify({ token: getToken() }));
});

socket.addEventListener("message", (event) => {
  const data = JSON.parse(event.data);
  hideThinking();

  if (data.type === "tool_call") {
    appendLine(`→ ${data.name}(${JSON.stringify(data.arguments)})`, "tool-call");
  } else if (data.type === "tool_result") {
    appendLine(`← ${data.name}: ${data.result}`, "tool-result");
    showThinking(); // waiting on the model again after this tool result
  } else if (data.type === "final") {
    conversationId = data.conversation_id;
    appendLine(data.content, "assistant");
  } else if (data.type === "error") {
    appendLine(`Error: ${data.message}`, "error");
  }
});

socket.addEventListener("close", () => {
  hideThinking();
  appendLine("(disconnected)", "error");
});

composer.addEventListener("submit", (event) => {
  event.preventDefault();
  const content = contentInput.value.trim();
  if (!content) return;

  appendLine(content, "user");
  showThinking();
  socket.send(
    JSON.stringify({ type: "message", conversation_id: conversationId, content })
  );
  contentInput.value = "";
});
