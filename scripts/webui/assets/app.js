const state = {
  bootstrap: null,
  sessions: [],
  selectedSessionId: "",
  turns: [],
  sessionSearch: "",
  transferView: null,
  transferGroup: "",
  selectedTransferIds: new Set(),
};

const elements = {
  heroSubtitle: document.getElementById("heroSubtitle"),
  phaseBadge: document.getElementById("phaseBadge"),
  activeAccountBadge: document.getElementById("activeAccountBadge"),
  workdirInput: document.getElementById("workdirInput"),
  recentWorkdirs: document.getElementById("recentWorkdirs"),
  loadWorkdirButton: document.getElementById("loadWorkdirButton"),
  refreshAllButton: document.getElementById("refreshAllButton"),
  codexHomeValue: document.getElementById("codexHomeValue"),
  targetProfileValue: document.getElementById("targetProfileValue"),
  accountsRootValue: document.getElementById("accountsRootValue"),
  flash: document.getElementById("flash"),
  accountsNotice: document.getElementById("accountsNotice"),
  accountsList: document.getElementById("accountsList"),
  transferCountBadge: document.getElementById("transferCountBadge"),
  transferGroups: document.getElementById("transferGroups"),
  transferTargetSelect: document.getElementById("transferTargetSelect"),
  assignSelectedButton: document.getElementById("assignSelectedButton"),
  copySelectedButton: document.getElementById("copySelectedButton"),
  transferSelectionHint: document.getElementById("transferSelectionHint"),
  transferList: document.getElementById("transferList"),
  sessionsCountBadge: document.getElementById("sessionsCountBadge"),
  sessionSearchInput: document.getElementById("sessionSearchInput"),
  sessionsList: document.getElementById("sessionsList"),
  turnsCountBadge: document.getElementById("turnsCountBadge"),
  turnsNotice: document.getElementById("turnsNotice"),
  turnsList: document.getElementById("turnsList"),
  emptyStateTemplate: document.getElementById("emptyStateTemplate"),
};

async function apiFetch(path, options = {}) {
  const response = await fetch(path, {
    headers: {
      "Content-Type": "application/json",
      ...(options.headers || {}),
    },
    ...options,
  });

  let payload = null;
  try {
    payload = await response.json();
  } catch (error) {
    throw new Error(`Invalid server response for ${path}.`);
  }

  if (!response.ok || !payload.ok) {
    throw new Error(payload.error || `Request failed for ${path}.`);
  }
  return payload.data;
}

function buildQuery(params) {
  const query = new URLSearchParams();
  Object.entries(params).forEach(([key, value]) => {
    if (value !== undefined && value !== null && String(value).trim()) {
      query.set(key, value);
    }
  });
  const text = query.toString();
  return text ? `?${text}` : "";
}

function showFlash(message, type = "info") {
  elements.flash.textContent = message;
  elements.flash.className = `flash visible ${type === "error" ? "error" : ""}`.trim();
  window.clearTimeout(showFlash.timer);
  showFlash.timer = window.setTimeout(() => {
    elements.flash.className = "flash";
  }, 4200);
}

function createEmptyState(message) {
  const node = elements.emptyStateTemplate.content.firstElementChild.cloneNode(true);
  node.querySelector("p").textContent = message;
  return node;
}

function setBusy(button, busy, label) {
  if (!button) {
    return;
  }
  if (busy) {
    button.dataset.originalText = button.textContent;
    button.textContent = label;
    button.disabled = true;
  } else {
    button.textContent = button.dataset.originalText || button.textContent;
    button.disabled = false;
  }
}

async function refreshBootstrap(workdir) {
  const data = await apiFetch(`/api/bootstrap${buildQuery({ workdir })}`);
  state.bootstrap = data;
  renderBootstrap();
  return data;
}

async function refreshSessions() {
  const workdir = currentWorkdir();
  const data = await apiFetch(`/api/sessions${buildQuery({ workdir })}`);
  state.sessions = data.sessions;
  if (!state.selectedSessionId || !state.sessions.some((item) => item.threadId === state.selectedSessionId)) {
    state.selectedSessionId = state.sessions[0]?.threadId || "";
    state.turns = [];
  }
  renderSessions();
  if (state.selectedSessionId) {
    await loadTurns(state.selectedSessionId);
  } else {
    renderTurns();
  }
}

async function refreshTransferView() {
  const workdir = currentWorkdir();
  const data = await apiFetch(`/api/transfer-view${buildQuery({ workdir })}`);
  state.transferView = data;
  const defaultGroup = data.groups.find((group) => group.count > 0)?.name || data.groups[0]?.name || "";
  if (!state.transferGroup || !data.groups.some((group) => group.name === state.transferGroup)) {
    state.transferGroup = defaultGroup;
  }
  state.selectedTransferIds.clear();
  renderTransfer();
}

async function refreshAll(workdir) {
  const bootstrap = await refreshBootstrap(workdir);
  await Promise.all([refreshSessions(), refreshTransferView()]);
  return bootstrap;
}

function renderBootstrap() {
  const bootstrap = state.bootstrap;
  if (!bootstrap) {
    return;
  }
  elements.heroSubtitle.textContent = bootstrap.projectSubtitle;
  elements.phaseBadge.textContent = bootstrap.phase;
  elements.activeAccountBadge.textContent = bootstrap.activeAccount
    ? `Active account: ${bootstrap.activeAccount}`
    : "Active account: no exact source match";
  elements.workdirInput.value = bootstrap.workdir || "";
  elements.codexHomeValue.textContent = bootstrap.codexHome || "-";
  elements.targetProfileValue.textContent = bootstrap.codexHomeDescription || "-";
  elements.accountsRootValue.textContent = bootstrap.accountsRoot || bootstrap.accountsError || "-";
  elements.recentWorkdirs.innerHTML = "";
  (bootstrap.recentWorkdirs || []).forEach((workdir) => {
    const option = document.createElement("option");
    option.value = workdir;
    elements.recentWorkdirs.appendChild(option);
  });
  renderAccounts();
  populateTransferTargets();
}

function renderAccounts() {
  const bootstrap = state.bootstrap;
  elements.accountsList.innerHTML = "";
  if (!bootstrap) {
    elements.accountsNotice.textContent = "Bootstrap data not loaded.";
    return;
  }
  const accounts = bootstrap.accounts || [];
  if (bootstrap.accountsError) {
    elements.accountsNotice.textContent = bootstrap.accountsError;
  } else if (!accounts.length) {
    elements.accountsNotice.textContent = "No switchable account sources were found.";
  } else {
    elements.accountsNotice.textContent = "Switch the local Codex profile without leaving the browser control plane.";
  }

  if (!accounts.length) {
    elements.accountsList.appendChild(createEmptyState("No account sources are available yet."));
    return;
  }

  accounts.forEach((account) => {
    const card = document.createElement("article");
    card.className = "account-card";
    card.innerHTML = `
      <div class="account-header">
        <div>
          <div class="account-title">${escapeHtml(account.name)}</div>
          <div class="account-copy">${escapeHtml(account.description || "config.toml + auth.json")}</div>
        </div>
        <div class="account-meta">
          ${account.isActive ? '<span class="chip active">Active</span>' : ""}
          ${account.provider ? `<span class="chip">${escapeHtml(account.provider)}</span>` : '<span class="chip">provider unknown</span>'}
        </div>
      </div>
      <div class="account-copy mono">${escapeHtml(account.directory)}</div>
      <div>
        <button class="button ${account.isActive ? "" : "button-primary"}" type="button" ${account.isActive ? "disabled" : ""}>${account.isActive ? "Current profile" : "Switch to this account"}</button>
      </div>
    `;
    const button = card.querySelector("button");
    if (!account.isActive) {
      button.addEventListener("click", async () => {
        setBusy(button, true, "Switching...");
        try {
          const result = await apiFetch("/api/accounts/switch", {
            method: "POST",
            body: JSON.stringify({ account_name: account.name, restart_codex: true }),
          });
          showFlash(`Switched to ${result.accountName}.`);
          await refreshAll(currentWorkdir());
        } catch (error) {
          showFlash(error.message, "error");
        } finally {
          setBusy(button, false);
        }
      });
    }
    elements.accountsList.appendChild(card);
  });
}

function currentWorkdir() {
  return elements.workdirInput.value.trim() || state.bootstrap?.workdir || "";
}

function filteredSessions() {
  const query = state.sessionSearch.trim().toLowerCase();
  if (!query) {
    return state.sessions;
  }
  return state.sessions.filter((session) => {
    return [session.title, session.preview, session.threadId]
      .join(" ")
      .toLowerCase()
      .includes(query);
  });
}

function renderSessions() {
  const sessions = filteredSessions();
  elements.sessionsCountBadge.textContent = `${sessions.length} session${sessions.length === 1 ? "" : "s"}`;
  elements.sessionsList.innerHTML = "";
  if (!sessions.length) {
    elements.sessionsList.appendChild(createEmptyState("No conversations matched this workspace or filter."));
    return;
  }
  sessions.forEach((session) => {
    const card = document.createElement("article");
    card.className = `session-card ${session.threadId === state.selectedSessionId ? "selected" : ""}`;
    card.innerHTML = `
      <div class="session-header">
        <div>
          <div class="session-title">${escapeHtml(session.title || session.threadId)}</div>
          <div class="session-copy">${escapeHtml(session.preview || "No preview available.")}</div>
        </div>
        <div class="session-meta">
          <span class="chip">${escapeHtml(session.updatedLabel)}</span>
          ${session.forkedFromId ? `<span class="chip">fork of ${escapeHtml(session.forkedFromId)}</span>` : ""}
        </div>
      </div>
      <div class="session-copy mono">${escapeHtml(session.threadId)}</div>
    `;
    card.addEventListener("click", async () => {
      state.selectedSessionId = session.threadId;
      renderSessions();
      await loadTurns(session.threadId);
    });
    elements.sessionsList.appendChild(card);
  });
}

async function loadTurns(threadId) {
  if (!threadId) {
    state.turns = [];
    renderTurns();
    return;
  }
  elements.turnsNotice.textContent = "Loading user turns...";
  try {
    const data = await apiFetch(`/api/session-turns${buildQuery({ workdir: currentWorkdir(), thread_id: threadId })}`);
    state.turns = data.turns || [];
    elements.turnsNotice.textContent = data.appServerError
      ? `App-server fallback notice: ${data.appServerError}`
      : "Select a user turn to create a forked conversation from that point.";
  } catch (error) {
    state.turns = [];
    elements.turnsNotice.textContent = error.message;
  }
  renderTurns();
}

function renderTurns() {
  elements.turnsCountBadge.textContent = `${state.turns.length} turn${state.turns.length === 1 ? "" : "s"}`;
  elements.turnsList.innerHTML = "";
  if (!state.selectedSessionId) {
    elements.turnsList.appendChild(createEmptyState("Choose a conversation from the session list first."));
    return;
  }
  if (!state.turns.length) {
    elements.turnsList.appendChild(createEmptyState("No user turns were found for this conversation."));
    return;
  }
  state.turns.forEach((turn) => {
    const card = document.createElement("article");
    card.className = "turn-card";
    card.innerHTML = `
      <div class="session-header">
        <div>
          <div class="session-title">Turn ${turn.index}</div>
          <div class="turn-copy">${escapeHtml(turn.text || turn.preview || "No text available.")}</div>
        </div>
        <div class="turn-meta">
          <span class="chip">${escapeHtml(turn.source)}</span>
        </div>
      </div>
      <div class="session-copy mono">${escapeHtml(turn.turnId)}</div>
      <div class="compact-toolbar">
        <button class="button button-primary" type="button">Fork from this turn</button>
      </div>
    `;
    const button = card.querySelector("button");
    button.addEventListener("click", async () => {
      setBusy(button, true, "Forking...");
      try {
        const result = await apiFetch("/api/fork", {
          method: "POST",
          body: JSON.stringify({
            workdir: currentWorkdir(),
            thread_id: state.selectedSessionId,
            turn_id: turn.turnId,
            restart_codex: true,
          }),
        });
        showFlash(`Fork created: ${result.result.forked_thread_id}`);
        await refreshSessions();
      } catch (error) {
        showFlash(error.message, "error");
      } finally {
        setBusy(button, false);
      }
    });
    elements.turnsList.appendChild(card);
  });
}

function populateTransferTargets() {
  const accounts = state.transferView?.profiles || state.bootstrap?.accounts || [];
  const current = elements.transferTargetSelect.value;
  elements.transferTargetSelect.innerHTML = "";
  if (!accounts.length) {
    const option = document.createElement("option");
    option.value = "";
    option.textContent = "No accounts available";
    elements.transferTargetSelect.appendChild(option);
    return;
  }
  accounts.forEach((account) => {
    const option = document.createElement("option");
    option.value = account.name;
    option.textContent = account.provider ? `${account.name} (${account.provider})` : `${account.name} (provider unknown)`;
    elements.transferTargetSelect.appendChild(option);
  });
  const preferred = accounts.find((item) => item.name === current) || accounts[0];
  elements.transferTargetSelect.value = preferred.name;
}

function visibleTransferConversations() {
  const conversations = state.transferView?.conversations || [];
  if (!state.transferGroup) {
    return conversations;
  }
  return conversations.filter((item) => item.assignedAccount === state.transferGroup);
}

function renderTransfer() {
  const transferView = state.transferView;
  elements.transferGroups.innerHTML = "";
  elements.transferList.innerHTML = "";
  if (!transferView) {
    elements.transferSelectionHint.textContent = "Transfer view has not been loaded yet.";
    elements.transferList.appendChild(createEmptyState("Load a workspace to see transfer groups."));
    return;
  }

  elements.transferCountBadge.textContent = `${transferView.count} thread${transferView.count === 1 ? "" : "s"}`;
  transferView.groups.forEach((group) => {
    const button = document.createElement("button");
    button.type = "button";
    button.className = `group-tab ${group.name === state.transferGroup ? "active" : ""}`;
    button.textContent = `${group.name} · ${group.count}`;
    button.addEventListener("click", () => {
      state.transferGroup = group.name;
      state.selectedTransferIds.clear();
      renderTransfer();
    });
    elements.transferGroups.appendChild(button);
  });

  const visible = visibleTransferConversations();
  const selectedCount = state.selectedTransferIds.size;
  elements.transferSelectionHint.textContent = selectedCount
    ? `${selectedCount} conversation(s) selected from ${state.transferGroup}.`
    : `Viewing ${state.transferGroup || "all"}. Select conversations from one source group at a time.`;

  if (!visible.length) {
    elements.transferList.appendChild(createEmptyState("No conversations are available in this transfer group."));
  } else {
    visible.forEach((conversation) => {
      const card = document.createElement("label");
      const checked = state.selectedTransferIds.has(conversation.threadId);
      card.className = `transfer-card ${checked ? "selected" : ""}`;
      card.innerHTML = `
        <div class="selection-row">
          <input type="checkbox" ${checked ? "checked" : ""}>
          <div class="grow">
            <div class="transfer-header">
              <div>
                <div class="transfer-title">${escapeHtml(conversation.title || conversation.threadId)}</div>
                <div class="transfer-copy">${escapeHtml(conversation.preview || "No preview available.")}</div>
              </div>
              <div class="transfer-meta">
                <span class="chip">${escapeHtml(conversation.modelProvider || "provider unknown")}</span>
                <span class="chip">${escapeHtml(conversation.assignmentSource)}</span>
              </div>
            </div>
            <div class="session-copy mono">${escapeHtml(conversation.threadId)}</div>
            <div class="session-copy">${escapeHtml(conversation.updatedLabel)}</div>
          </div>
        </div>
      `;
      const checkbox = card.querySelector("input");
      checkbox.addEventListener("change", () => {
        if (checkbox.checked) {
          state.selectedTransferIds.add(conversation.threadId);
        } else {
          state.selectedTransferIds.delete(conversation.threadId);
        }
        renderTransfer();
      });
      elements.transferList.appendChild(card);
    });
  }

  const hasSelection = state.selectedTransferIds.size > 0;
  elements.assignSelectedButton.disabled = !hasSelection || !elements.transferTargetSelect.value;
  elements.copySelectedButton.disabled = !hasSelection || !elements.transferTargetSelect.value || state.transferGroup === "Unassigned";
}

async function handleAssignSelected() {
  const threadIds = Array.from(state.selectedTransferIds);
  if (!threadIds.length) {
    showFlash("Select at least one conversation first.", "error");
    return;
  }
  setBusy(elements.assignSelectedButton, true, "Assigning...");
  try {
    const result = await apiFetch("/api/transfer/assign", {
      method: "POST",
      body: JSON.stringify({
        workdir: currentWorkdir(),
        account_name: elements.transferTargetSelect.value,
        thread_ids: threadIds,
      }),
    });
    state.transferView = result.transferView;
    state.selectedTransferIds.clear();
    if (state.transferGroup === "Unassigned") {
      state.transferGroup = result.targetAccount;
    }
    renderTransfer();
    showFlash(`Assigned ${result.assignedCount} conversation(s) to ${result.targetAccount}.`);
  } catch (error) {
    showFlash(error.message, "error");
  } finally {
    setBusy(elements.assignSelectedButton, false);
  }
}

async function handleCopySelected() {
  const threadIds = Array.from(state.selectedTransferIds);
  if (!threadIds.length) {
    showFlash("Select at least one conversation first.", "error");
    return;
  }
  setBusy(elements.copySelectedButton, true, "Copying...");
  try {
    const result = await apiFetch("/api/transfer/copy", {
      method: "POST",
      body: JSON.stringify({
        workdir: currentWorkdir(),
        target_account: elements.transferTargetSelect.value,
        thread_ids: threadIds,
        restart_codex: true,
      }),
    });
    state.transferView = result.transferView;
    state.selectedTransferIds.clear();
    renderTransfer();
    showFlash(`Copied ${result.importedCount} conversation(s) to ${result.targetAccount}.`);
    await refreshSessions();
  } catch (error) {
    showFlash(error.message, "error");
  } finally {
    setBusy(elements.copySelectedButton, false);
  }
}

function escapeHtml(value) {
  return String(value)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#39;");
}

function wireEvents() {
  elements.loadWorkdirButton.addEventListener("click", async () => {
    setBusy(elements.loadWorkdirButton, true, "Loading...");
    try {
      await refreshAll(currentWorkdir());
      showFlash("Workspace refreshed.");
    } catch (error) {
      showFlash(error.message, "error");
    } finally {
      setBusy(elements.loadWorkdirButton, false);
    }
  });

  elements.refreshAllButton.addEventListener("click", async () => {
    setBusy(elements.refreshAllButton, true, "Refreshing...");
    try {
      await refreshAll(currentWorkdir());
      showFlash("All views refreshed.");
    } catch (error) {
      showFlash(error.message, "error");
    } finally {
      setBusy(elements.refreshAllButton, false);
    }
  });

  elements.workdirInput.addEventListener("keydown", async (event) => {
    if (event.key !== "Enter") {
      return;
    }
    event.preventDefault();
    try {
      await refreshAll(currentWorkdir());
      showFlash("Workspace refreshed.");
    } catch (error) {
      showFlash(error.message, "error");
    }
  });

  elements.sessionSearchInput.addEventListener("input", (event) => {
    state.sessionSearch = event.target.value;
    renderSessions();
  });

  elements.assignSelectedButton.addEventListener("click", handleAssignSelected);
  elements.copySelectedButton.addEventListener("click", handleCopySelected);
}

async function bootstrap() {
  wireEvents();
  try {
    await refreshAll();
    showFlash("Web UI ready.");
  } catch (error) {
    showFlash(error.message, "error");
  }
}

bootstrap();
