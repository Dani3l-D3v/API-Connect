const state = {
  page: "dashboard",
  dashboard: null,
  integrations: [],
  selectedProvider: null,
  logs: [],
  logTotal: 0,
  logOffset: 0,
  pageSize: 50,
  outcome: "",
  providerFilter: "",
  query: "",
  toastTimer: null,
};

const pageTitles = {
  dashboard: "Painel",
  integrations: "Integrações",
  monitoring: "Monitoramento",
  logs: "Histórico",
};

const outcomeLabels = {
  success: "200 OK",
  upstream_error: "Erro",
  rate_limited: "Limite",
  disabled: "Desativada",
  invalid_request: "Inválida",
};

const $ = (selector) => document.querySelector(selector);
const make = (tag, className, text) => {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = text;
  return node;
};

async function api(url, options = {}) {
  let response;
  try {
    response = await fetch(url, {
      ...options,
      headers: { Accept: "application/json", ...options.headers },
    });
  } catch {
    throw new Error("Não foi possível conectar à API. Verifique se o servidor está em execução.");
  }
  if (!response.ok) {
    const body = await response.json().catch(() => null);
    throw new Error(typeof body?.detail === "string" ? body.detail : `A API retornou HTTP ${response.status}.`);
  }
  return response.status === 204 ? undefined : response.json();
}

function showToast(message, isError = false) {
  const toast = $("#toast");
  toast.textContent = message;
  toast.classList.toggle("error", isError);
  toast.classList.add("show");
  clearTimeout(state.toastTimer);
  state.toastTimer = setTimeout(() => toast.classList.remove("show"), 3600);
}

function setPage(page) {
  if (!pageTitles[page]) return;
  state.page = page;
  document.querySelectorAll(".page").forEach((item) => item.classList.remove("page-active"));
  $(`#page-${page}`).classList.add("page-active");
  document.querySelectorAll(".nav-item").forEach((item) => {
    item.classList.toggle("active", item.dataset.page === page);
  });
  $("#breadcrumb-title").textContent = pageTitles[page];
  $("#sidebar").classList.remove("open");
  $("#sidebar-backdrop").classList.remove("visible");
  if (page === "logs") void loadLogs();
  if (page === "monitoring") void loadDashboard().catch((error) => showToast(error.message, true));
  window.scrollTo({ top: 0, behavior: "smooth" });
}

function formatTime(value) {
  if (!value) return "—";
  const date = new Date(value);
  return Number.isNaN(date.getTime())
    ? "—"
    : date.toLocaleTimeString("pt-BR", { hour: "2-digit", minute: "2-digit", second: "2-digit" });
}

function formatDate(value) {
  if (!value) return "Sem chamadas";
  const date = new Date(value);
  return Number.isNaN(date.getTime())
    ? "Sem chamadas"
    : date.toLocaleString("pt-BR", { day: "2-digit", month: "short", hour: "2-digit", minute: "2-digit" });
}

function providerById(id) {
  return state.integrations.find((provider) => provider.id === id);
}

function providerMark(provider) {
  return ({ jsonplaceholder: "{ }", "open-meteo": "☼", github: "GH" })[provider] || "API";
}

function renderLogRow(item, compact = false) {
  const row = make("tr");
  row.dataset.logId = item.id;
  const columns = compact
    ? [formatTime(item.created_at), item.method, item.provider, item.endpoint, item, `${item.duration_ms} ms`]
    : [formatTime(item.created_at), item.provider, item.operation, item.method, item, `${item.duration_ms} ms`];
  columns.forEach((value, index) => {
    const cell = make("td");
    if (typeof value === "object" && value !== null) {
      const badge = make("span", `outcome-pill ${value.outcome === "success" ? "" : "error"}`, outcomeLabels[value.outcome] || value.outcome);
      cell.append(badge);
    } else if (index === (compact ? 1 : 3)) {
      const method = make("span", `method-pill ${String(value).toLowerCase()}`, value);
      cell.append(method);
    } else if (index === (compact ? 3 : 2)) {
      const endpoint = make("span", compact ? "endpoint-cell" : "operation-cell", value);
      cell.append(endpoint);
    } else {
      cell.textContent = value;
      if (index === 0) cell.className = "log-time-cell";
    }
    row.append(cell);
  });
  if (!compact) {
    const action = make("td");
    action.append(make("span", "log-detail-button", "↗"));
    row.append(action);
  }
  return row;
}

function statusFromProvider(provider) {
  if (!provider.enabled) return { text: "Desativada", offline: true };
  if (provider.requests_24h === 0) return { text: "Aguardando", offline: false };
  if (provider.successful_24h < provider.requests_24h) return { text: "Verificar", offline: true };
  return { text: "Operacional", offline: false };
}

function renderProviderSummary() {
  const container = $("#provider-summary");
  container.replaceChildren();
  state.dashboard?.providers.forEach((provider) => {
    const line = make("div", "provider-summary");
    line.append(make("span", "summary-icon", providerMark(provider.id)));
    const copy = make("span", "summary-copy");
    copy.append(make("strong", "", provider.name));
    copy.append(make("small", "", `${provider.requests_24h} chamadas · limite ${provider.rate_limit_per_minute}/min`));
    const status = statusFromProvider(provider);
    const badge = make("span", `summary-state ${status.offline ? "offline" : ""}`);
    badge.append(make("i"), document.createTextNode(status.text));
    line.append(copy, badge);
    container.append(line);
  });
}

function drawMiniChart(data = []) {
  const svg = $("#mini-chart");
  svg.replaceChildren();
  const width = 220;
  const height = 54;
  const max = Math.max(1, ...data.map((item) => item.count));
  const points = data.map((item, index) => ({
    x: (index * (width - 4)) / Math.max(data.length - 1, 1) + 2,
    y: height - 5 - (item.count / max) * (height - 13),
  }));
  const line = points.map((point, index) => `${index ? "L" : "M"}${point.x},${point.y}`).join(" ");
  const area = `${line} L${width - 2},${height} L2,${height} Z`;
  const defs = document.createElementNS("http://www.w3.org/2000/svg", "defs");
  defs.innerHTML = '<linearGradient id="mini-fill" x1="0" x2="0" y1="0" y2="1"><stop offset="0%" stop-color="#55b99b" stop-opacity=".24"/><stop offset="100%" stop-color="#55b99b" stop-opacity="0"/></linearGradient>';
  svg.append(defs);
  if (points.length) {
    const fill = document.createElementNS("http://www.w3.org/2000/svg", "path");
    fill.setAttribute("d", area);
    fill.setAttribute("fill", "url(#mini-fill)");
    const stroke = document.createElementNS("http://www.w3.org/2000/svg", "path");
    stroke.setAttribute("d", line);
    stroke.setAttribute("fill", "none");
    stroke.setAttribute("stroke", "#58b99b");
    stroke.setAttribute("stroke-width", "2");
    stroke.setAttribute("stroke-linecap", "round");
    svg.append(fill, stroke);
  }
}

function drawWideChart(data = []) {
  const container = $("#wide-chart");
  container.replaceChildren();
  if (!data.some((item) => item.count)) {
    container.append(make("div", "chart-empty", "As chamadas aparecerão aqui conforme você usar as integrações."));
    return;
  }
  const width = 900;
  const height = 145;
  const left = 25;
  const top = 8;
  const bottom = 23;
  const plotHeight = height - top - bottom;
  const plotWidth = width - left - 8;
  const max = Math.max(1, ...data.map((item) => item.count));
  const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
  svg.setAttribute("viewBox", `0 0 ${width} ${height}`);
  svg.setAttribute("role", "img");
  svg.setAttribute("aria-label", "Requisições por hora nas últimas 24 horas");
  [0, 1, 2, 3].forEach((step) => {
    const y = top + (plotHeight * step) / 3;
    const grid = document.createElementNS("http://www.w3.org/2000/svg", "line");
    grid.setAttribute("x1", String(left));
    grid.setAttribute("x2", String(width - 5));
    grid.setAttribute("y1", String(y));
    grid.setAttribute("y2", String(y));
    grid.setAttribute("class", "chart-grid-line");
    svg.append(grid);
  });
  data.forEach((item, index) => {
    const slot = plotWidth / data.length;
    const barWidth = Math.max(4, slot * 0.56);
    const barHeight = (item.count / max) * plotHeight;
    const bar = document.createElementNS("http://www.w3.org/2000/svg", "rect");
    bar.setAttribute("x", String(left + index * slot + (slot - barWidth) / 2));
    bar.setAttribute("y", String(top + plotHeight - barHeight));
    bar.setAttribute("width", String(barWidth));
    bar.setAttribute("height", String(Math.max(1, barHeight)));
    bar.setAttribute("rx", "3");
    bar.setAttribute("class", "chart-bar");
    bar.setAttribute("aria-label", `${item.hour}: ${item.count} chamadas`);
    const title = document.createElementNS("http://www.w3.org/2000/svg", "title");
    title.textContent = `${item.hour}: ${item.count} chamadas`;
    bar.append(title);
    svg.append(bar);
    if (index % 3 === 0) {
      const label = document.createElementNS("http://www.w3.org/2000/svg", "text");
      label.setAttribute("x", String(left + index * slot + slot / 2));
      label.setAttribute("y", String(height - 5));
      label.setAttribute("text-anchor", "middle");
      label.setAttribute("class", "chart-axis-label");
      label.textContent = item.hour;
      svg.append(label);
    }
  });
  container.append(svg);
}

function renderDashboard() {
  const data = state.dashboard;
  if (!data) return;
  $("#metric-today").textContent = data.requests_today;
  $("#metric-success").textContent = `${data.success_rate}%`;
  $("#metric-latency").innerHTML = `${data.average_latency_ms} <small>ms</small>`;
  const active = data.providers.filter((provider) => provider.enabled).length;
  $("#metric-providers").textContent = `${active}/${data.providers.length}`;
  $("#health-cpu").textContent = `${data.success_rate}%`;
  $("#health-cpu").parentElement.classList.toggle("unhealthy", data.failed_24h > 0);
  $("#health-label").textContent = data.failed_24h ? `${data.failed_24h} chamada(s) com atenção` : "Serviços respondendo";
  $("#memory-count").textContent = data.requests_24h;
  $("#health-latency").innerHTML = `${data.average_latency_ms} <small>ms</small>`;
  $("#nav-log-count").textContent = data.requests_24h;
  drawMiniChart(data.hourly_requests);
  renderProviderSummary();
  const recent = $("#recent-log-rows");
  recent.replaceChildren();
  if (!data.recent_logs.length) {
    const row = make("tr");
    const cell = make("td", "table-placeholder", "Nenhuma chamada registrada até agora.");
    cell.colSpan = 6;
    row.append(cell);
    recent.append(row);
  } else {
    data.recent_logs.slice(0, 6).forEach((item) => recent.append(renderLogRow(item, true)));
  }
}

function renderIntegrationCards() {
  const grid = $("#integration-grid");
  grid.replaceChildren();
  if (!state.selectedProvider && state.integrations.length) {
    state.selectedProvider = state.integrations[0].id;
  }
  state.integrations.forEach((provider) => {
    const card = make("article", `panel integration-card ${state.selectedProvider === provider.id ? "selected" : ""}`);
    card.dataset.provider = provider.id;
    const top = make("div", "integration-card-top");
    const name = make("div", "integration-name");
    name.append(make("span", "summary-icon", providerMark(provider.id)));
    const copy = make("span");
    copy.append(make("strong", "", provider.name), make("small", "", `${provider.category} · REST`));
    name.append(copy);
    const badge = make("span", `enabled-badge ${provider.enabled ? "" : "offline"}`);
    badge.append(make("i"), document.createTextNode(provider.enabled ? "Ativa" : "Pausada"));
    top.append(name, badge);
    card.append(top, make("p", "", provider.description));
    const footer = make("div", "integration-card-footer");
    footer.append(make("span", "", `${provider.operations.length} operações disponíveis`));
    footer.append(make("strong", "", `${provider.rate_limit_per_minute} chamadas/min`));
    const actions = make("div", "integration-card-actions");
    const configure = make("button", "secondary-button", "Configurar");
    configure.type = "button";
    configure.dataset.selectProvider = provider.id;
    const toggle = make("button", "quiet-button", provider.enabled ? "Pausar" : "Ativar");
    toggle.type = "button";
    toggle.dataset.toggleProvider = provider.id;
    actions.append(configure, toggle);
    card.append(footer, actions);
    grid.append(card);
  });
  populateProviderFilter();
  if (state.selectedProvider && providerById(state.selectedProvider)) renderIntegrationDetail();
}

function populateProviderFilter() {
  const select = $("#provider-filter");
  const current = select.value;
  select.replaceChildren(new Option("Todas as integrações", ""));
  state.integrations.forEach((provider) => select.add(new Option(provider.name, provider.id)));
  select.value = state.integrations.some((provider) => provider.id === current) ? current : "";
}

function selectProvider(providerId) {
  state.selectedProvider = providerId;
  renderIntegrationCards();
  $("#integration-detail").scrollIntoView({ behavior: "smooth", block: "start" });
}

function renderIntegrationDetail() {
  const provider = providerById(state.selectedProvider);
  if (!provider) return;
  $("#integration-detail").hidden = false;
  $("#detail-title").textContent = provider.name;
  $("#detail-description").textContent = provider.description;
  $("#detail-chip").textContent = provider.enabled ? `${provider.rate_limit_per_minute} req/min` : "Integração pausada";
  $("#rate-limit-control").value = provider.rate_limit_per_minute;
  $("#toggle-provider").textContent = provider.enabled ? "Pausar integração" : "Ativar integração";
  $("#execute-button").disabled = !provider.enabled;
  $("#operation-select").disabled = !provider.enabled;
  const operationSelect = $("#operation-select");
  operationSelect.replaceChildren();
  provider.operations.forEach((operation) => operationSelect.add(new Option(`${operation.method} · ${operation.name}`, operation.id)));
  renderOperationFields();
  $("#response-panel").hidden = true;
}

function renderOperationFields() {
  const provider = providerById(state.selectedProvider);
  const operation = provider?.operations.find((item) => item.id === $("#operation-select").value);
  const container = $("#operation-fields");
  container.replaceChildren();
  if (!operation) return;
  operation.fields.forEach((field) => {
    const label = make("label");
    const caption = make("span");
    caption.append(document.createTextNode(field.label));
    if (field.required) caption.append(make("span", "required-mark", " *"));
    const control = make(field.type === "textarea" ? "textarea" : "input", field.type === "textarea" ? "" : "");
    if (field.type !== "textarea") control.type = field.type === "number" ? "number" : "text";
    control.name = field.name;
    control.required = field.required;
    control.placeholder = field.placeholder || "";
    if (field.type === "number") {
      control.min = field.min;
      control.max = field.max;
      if (Number.isInteger(field.min) && Number.isInteger(field.max)) control.step = "1";
      else control.step = "any";
    }
    if (field.default !== undefined) control.value = field.default;
    const helpText = field.type === "number"
      ? `Entre ${field.min} e ${field.max}.`
      : `Até ${field.maxLength} caracteres.`;
    label.append(caption, control, make("small", "field-help", helpText));
    container.append(label);
  });
  $("#response-panel").hidden = true;
}

function renderMonitoring() {
  const data = state.dashboard;
  if (!data) return;
  const kpis = $("#monitoring-kpis");
  kpis.replaceChildren();
  [
    ["CHAMADAS · 24H", data.requests_24h, `${data.requests_today} hoje`],
    ["RESPOSTAS COM SUCESSO", `${data.success_rate}%`, `${data.successful_24h} chamadas`],
    ["CHAMADAS COM ERRO", data.failed_24h, "Nas últimas 24 horas"],
    ["LATÊNCIA MÉDIA", `${data.average_latency_ms} ms`, "Chamadas registradas"],
  ].forEach(([label, value, note]) => {
    const card = make("article", "panel monitor-kpi");
    card.append(make("span", "", label), make("strong", "", value), make("small", "", note));
    kpis.append(card);
  });
  const list = $("#monitoring-list");
  list.replaceChildren();
  const header = make("div", "monitoring-row monitoring-header");
  ["INTEGRAÇÃO", "STATUS", "CHAMADAS", "SUCESSO", "LIMITE / MIN"].forEach((text) => header.append(make("span", "", text)));
  list.append(header);
  data.providers.forEach((provider) => {
    const row = make("div", "monitoring-row");
    const name = make("span", "monitoring-provider");
    name.append(make("span", "summary-icon", providerMark(provider.id)), document.createTextNode(provider.name));
    const condition = statusFromProvider(provider);
    const status = make("span", `availability ${condition.offline ? "offline" : ""}`);
    status.append(make("i"), document.createTextNode(condition.text));
    const calls = make("strong", "monitoring-value", provider.requests_24h);
    const success = provider.requests_24h
      ? `${Math.round((provider.successful_24h / provider.requests_24h) * 100)}%`
      : "—";
    const successValue = make("strong", "monitoring-value", success);
    const limitColumn = make("div", "limit-column");
    const track = make("div", "limit-track");
    const usage = provider.rate_limit_per_minute
      ? Math.min(100, (provider.requests_24h / provider.rate_limit_per_minute) * 100)
      : 0;
    track.append(make("span"));
    track.firstElementChild.style.width = `${usage}%`;
    limitColumn.append(track, make("small", "limit-caption", `${provider.rate_limit_per_minute} por minuto · ${provider.last_call ? formatDate(provider.last_call) : "sem chamadas"} `));
    row.append(name, status, calls, successValue, limitColumn);
    list.append(row);
  });
  drawWideChart(data.hourly_requests);
}

function renderLogs() {
  const body = $("#all-log-rows");
  body.replaceChildren();
  if (!state.logs.length) {
    const row = make("tr");
    const cell = make("td", "table-placeholder", "Nenhum registro corresponde a este filtro.");
    cell.colSpan = 7;
    row.append(cell);
    body.append(row);
  } else {
    state.logs.forEach((item) => body.append(renderLogRow(item)));
  }
  $("#log-result-count").textContent = `${state.logTotal} ${state.logTotal === 1 ? "registro" : "registros"}`;
  const start = state.logTotal ? state.logOffset + 1 : 0;
  const end = Math.min(state.logOffset + state.pageSize, state.logTotal);
  $("#pagination-label").textContent = `Mostrando ${start}–${end} de ${state.logTotal} registros`;
  $("#previous-page").disabled = state.logOffset === 0;
  $("#next-page").disabled = state.logOffset + state.pageSize >= state.logTotal;
}

async function loadLogs() {
  const params = new URLSearchParams({
    limit: String(state.pageSize),
    offset: String(state.logOffset),
  });
  if (state.outcome) params.set("outcome", state.outcome);
  if (state.providerFilter) params.set("provider", state.providerFilter);
  if (state.query) params.set("q", state.query);
  try {
    const result = await api(`/api/logs?${params}`);
    state.logs = result.items;
    state.logTotal = result.total;
    renderLogs();
  } catch (error) {
    showToast(error.message, true);
  }
}

async function loadDashboard() {
  state.dashboard = await api("/api/dashboard");
  renderDashboard();
  renderMonitoring();
}

async function loadIntegrations() {
  state.integrations = await api("/api/integrations");
  renderIntegrationCards();
  if (state.page === "monitoring") renderMonitoring();
}

async function refreshAll() {
  try {
    await Promise.all([loadDashboard(), loadIntegrations()]);
    if (state.page === "logs") await loadLogs();
  } catch (error) {
    showToast(error.message, true);
  }
}

async function updateProvider(provider, changes) {
  try {
    await api(`/api/integrations/${provider}/settings`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(changes),
    });
    await loadIntegrations();
    await loadDashboard();
    const current = providerById(provider);
    if (current) {
      showToast(current.enabled ? "Configuração da integração atualizada." : "Integração pausada.");
    }
  } catch (error) {
    showToast(error.message, true);
  }
}

function showCallResult(value, failed = false) {
  $("#response-panel").hidden = false;
  $("#response-panel").classList.toggle("response-error", failed);
  $("#response-json").textContent = JSON.stringify(value, null, 2);
}

async function executeCall(event) {
  event.preventDefault();
  const provider = providerById(state.selectedProvider);
  const operationId = $("#operation-select").value;
  const operation = provider?.operations.find((item) => item.id === operationId);
  if (!provider || !operation) return;
  const parameters = {};
  new FormData(event.currentTarget).forEach((value, key) => {
    if (value !== "") parameters[key] = operation.fields.find((field) => field.name === key)?.type === "number" ? Number(value) : value;
  });
  const button = $("#execute-button");
  button.disabled = true;
  button.textContent = "Conectando...";
  $("#response-panel").hidden = true;
  try {
    const result = await api(`/api/integrations/${encodeURIComponent(provider.id)}/${encodeURIComponent(operationId)}`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ parameters }),
    });
    showCallResult(result);
    showToast(`${provider.name} respondeu em ${result.data ? "OK" : "concluído"}.`);
    await Promise.all([loadDashboard(), loadLogs()]);
  } catch (error) {
    showCallResult({ error: error.message }, true);
    showToast(error.message, true);
    await Promise.all([loadDashboard(), loadLogs()]);
  } finally {
    button.disabled = false;
    button.innerHTML = "<span>↗</span> Executar chamada";
  }
}

async function openLogDetails(id) {
  try {
    const item = await api(`/api/logs/${id}`);
    const provider = providerById(item.provider);
    $("#dialog-title").textContent = `${provider?.name || item.provider} · ${item.operation}`;
    const content = $("#dialog-content");
    content.replaceChildren();
    const grid = make("div", "dialog-grid");
    [
      ["Método", item.method],
      ["Status", `${item.status_code} · ${outcomeLabels[item.outcome] || item.outcome}`],
      ["Endpoint", item.endpoint],
      ["Latência", `${item.duration_ms} ms`],
      ["Horário", formatDate(item.created_at)],
    ].forEach(([label, value]) => {
      const cell = make("div", "dialog-item");
      cell.append(make("small", "", label), make("strong", "", value));
      grid.append(cell);
    });
    content.append(grid);
    if (item.error_message) {
      content.append(make("div", "dialog-section-label", "DETALHE DO ERRO"));
      content.append(make("pre", "dialog-json", item.error_message));
    }
    content.append(make("div", "dialog-section-label", "PARÂMETROS ENVIADOS"));
    content.append(make("pre", "dialog-json", JSON.stringify(item.parameters, null, 2)));
    if (item.response !== null && item.response !== undefined) {
      content.append(make("div", "dialog-section-label", "RESPOSTA"));
      content.append(make("pre", "dialog-json", JSON.stringify(item.response, null, 2)));
    }
    $("#log-dialog").showModal();
  } catch (error) {
    showToast(error.message, true);
  }
}

async function init() {
  $("#today-date").textContent = new Date().toLocaleDateString("pt-BR", { day: "2-digit", month: "short", year: "numeric" });
  document.addEventListener("click", (event) => {
    const pageButton = event.target.closest("[data-page]");
    if (pageButton) setPage(pageButton.dataset.page);
  });
  document.addEventListener("keydown", (event) => {
    if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === "k") {
      event.preventDefault();
      $("#global-search").focus();
    }
    if (event.key === "Escape") {
      $("#sidebar").classList.remove("open");
      $("#sidebar-backdrop").classList.remove("visible");
    }
  });
  $("#mobile-menu").addEventListener("click", () => {
    $("#sidebar").classList.add("open");
    $("#sidebar-backdrop").classList.add("visible");
  });
  const backdrop = make("button", "sidebar-backdrop");
  backdrop.id = "sidebar-backdrop";
  backdrop.type = "button";
  backdrop.setAttribute("aria-label", "Fechar menu");
  backdrop.addEventListener("click", () => {
    $("#sidebar").classList.remove("open");
    backdrop.classList.remove("visible");
  });
  document.body.append(backdrop);
  $("#operation-select").addEventListener("change", renderOperationFields);
  $("#call-form").addEventListener("submit", executeCall);
  $("#integration-grid").addEventListener("click", (event) => {
    const toggle = event.target.closest("[data-toggle-provider]");
    if (toggle) {
      event.stopPropagation();
      const provider = providerById(toggle.dataset.toggleProvider);
      if (provider) void updateProvider(provider.id, { enabled: !provider.enabled });
      return;
    }
    const configure = event.target.closest("[data-select-provider]");
    if (configure) selectProvider(configure.dataset.selectProvider);
  });
  $("#toggle-provider").addEventListener("click", () => {
    const provider = providerById(state.selectedProvider);
    if (provider) void updateProvider(provider.id, { enabled: !provider.enabled });
  });
  $("#save-limit").addEventListener("click", () => {
    const provider = providerById(state.selectedProvider);
    const value = Number($("#rate-limit-control").value);
    if (!provider || !Number.isInteger(value) || value < 1 || value > 120) {
      showToast("O limite deve ser um número inteiro entre 1 e 120.", true);
      return;
    }
    void updateProvider(provider.id, { rate_limit_per_minute: value });
  });
  $("#provider-filter").addEventListener("change", () => {
    state.providerFilter = $("#provider-filter").value;
    state.logOffset = 0;
    void loadLogs();
  });
  document.querySelectorAll(".filter-tab").forEach((tab) => tab.addEventListener("click", () => {
    document.querySelectorAll(".filter-tab").forEach((item) => item.classList.toggle("active", item === tab));
    state.outcome = tab.dataset.outcome;
    state.logOffset = 0;
    void loadLogs();
  }));
  $("#previous-page").addEventListener("click", () => {
    state.logOffset = Math.max(0, state.logOffset - state.pageSize);
    void loadLogs();
  });
  $("#next-page").addEventListener("click", () => {
    state.logOffset += state.pageSize;
    void loadLogs();
  });
  $("#all-log-rows").addEventListener("click", (event) => {
    const row = event.target.closest("[data-log-id]");
    if (row) void openLogDetails(row.dataset.logId);
  });
  $("#recent-log-rows").addEventListener("click", (event) => {
    const row = event.target.closest("[data-log-id]");
    if (row) void openLogDetails(row.dataset.logId);
  });
  $("#global-search").addEventListener("input", () => {
    state.query = $("#global-search").value.trim();
    state.logOffset = 0;
    if (state.page !== "logs") setPage("logs");
    else void loadLogs();
  });
  $("#clear-logs").addEventListener("click", async () => {
    if (!window.confirm("Excluir todo o histórico de chamadas? Esta ação não pode ser desfeita.")) return;
    try {
      await api("/api/logs", { method: "DELETE" });
      state.logOffset = 0;
      await Promise.all([loadLogs(), loadDashboard()]);
      showToast("Histórico removido.");
    } catch (error) {
      showToast(error.message, true);
    }
  });
  $("#refresh-monitoring").addEventListener("click", () => {
    void refreshAll().then(() => showToast("Indicadores atualizados."));
  });
  $("#copy-response").addEventListener("click", async () => {
    try {
      await navigator.clipboard.writeText($("#response-json").textContent);
      showToast("JSON copiado.");
    } catch {
      showToast("Não foi possível copiar. Selecione e copie o JSON manualmente.", true);
    }
  });
  $("#close-dialog").addEventListener("click", () => $("#log-dialog").close());
  $("#log-dialog").addEventListener("click", (event) => {
    if (event.target === $("#log-dialog")) $("#log-dialog").close();
  });
  await refreshAll();
  void loadLogs();
  setInterval(() => {
    void loadDashboard().catch((error) => showToast(error.message, true));
    if (state.page === "logs") void loadLogs();
  }, 30000);
}

init().catch((error) => showToast(error.message, true));
