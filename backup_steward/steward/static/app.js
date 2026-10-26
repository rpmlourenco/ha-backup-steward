/* No remote assets, framework, analytics or embedded credentials. */
const $ = (id) => document.getElementById(id);
let current = null,
  config = null,
  pending = null;
const labels = {
  idle: "Tudo pronto",
  running: "Backup em curso",
  success: "Proteção em dia",
  failed: "Precisa da tua atenção",
  interrupted: "Execução interrompida",
  cancelled: "Cancelado",
  destination_mounting: "A ligar e montar o destino",
  destination_mounted: "Destino montado manualmente",
  destination_off: "Destino desligado em segurança",
};
const phases = {
  pending: "A aguardar",
  delivered: "Entregue",
  success: "Concluído",
  failed: "Por confirmar",
  skipped: "Não solicitado",
  disabled_in_test: "Sem apagar · teste",
};
const operationLabels = {
  backup: "Fazer backup",
  backup_only: "Backup sem checksum integral",
  cancel: "Cancelar execução",
  backup_checksum: "Backup com checksum integral",
  checksum: "Verificar integridade",
  recover: "Concluir fecho seguro",
  destination_mount: "Ligar e montar o destino",
  destination_shutdown: "Desmontar e desligar o destino",
};
function stamp(value) {
  return value
    ? new Intl.DateTimeFormat("pt-PT", {
        day: "numeric",
        month: "short",
        hour: "2-digit",
        minute: "2-digit",
        timeZone: current?.timezone || "Europe/Madrid",
      }).format(new Date(value))
    : "Ainda sem execuções";
}
function toast(message) {
  $("toast").textContent = message;
  $("toast").hidden = false;
  setTimeout(() => ($("toast").hidden = true), 4500);
}
async function api(path, method = "GET", body) {
  const result = await fetch(`v1/${path}`, {
    method,
    headers: { "Content-Type": "application/json", "X-Steward-UI": "1" },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  let data;
  try {
    data = await result.json();
  } catch {
    throw Error("Não foi possível contactar a app.");
  }
  if (!result.ok) throw Error(data.error || "A operação não foi aceite.");
  return data;
}
async function refresh() {
  try {
    current = await api("status");
    render();
  } catch (e) {
    $("status-label").textContent = "Ligação indisponível";
    $("hero-title").textContent = "Não conseguimos contactar a app.";
    $("hero-description").textContent =
      "O estado anterior não confirma que um backup terminou. Volta a verificar dentro de momentos.";
    $("run").disabled = true;
    $("verify").disabled = true;
    $("backup-verification").disabled = true;
    $("cancel-run").disabled = true;
    $("destination-mount").disabled =
      $("destination-shutdown").disabled =
      $("recover").disabled =
        true;
  }
}
function render() {
  const s = current.state;
  const recovery = s.recovery_required ?? (s.needs_cleanup && !current.busy);
  const manual = s.state === "destination_mounted" && !current.busy;
  $("version").textContent = `v${current.version}`;
  $("mode").textContent = {
    observe: "Modo de observação",
    test: "Modo de teste",
    production: "Produção",
  }[current.mode];
  $("status-label").textContent =
    current.mode === "observe"
      ? "Só observação"
      : current.busy
        ? s.state === "destination_mounting"
          ? labels.destination_mounting
          : s.state === "recovering"
            ? s.destination_manual
              ? "Fecho do destino em curso"
              : "Recuperação em curso"
            : s.mode === "checksum" ? "Verificação em curso" : "Backup em curso"
        : labels[s.state] || s.state;
  $("status-badge").dataset.tone =
    current.mode === "observe" ? "observe" : s.state;
  $("hero-title").textContent = recovery
    ? "Vamos fechar o disco em segurança."
    : current.busy
      ? "Estamos a cuidar dos teus dados."
      : manual
        ? "O destino está disponível."
        : s.state === "success"
          ? "Tudo guardado. Podes respirar."
          : current.mode === "observe"
            ? "Conhece a tua próxima rede de segurança."
            : "Pronto para a próxima cópia.";
  $("hero-description").textContent = recovery
    ? "A execução precisa de recuperação. A tomada só será desligada depois de o fecho seguro ser confirmado."
    : manual
      ? "O disco está montado manualmente. Quando terminares, usa Desmontar e desligar para o fechar em segurança."
      : current.mode === "observe"
        ? "Explora as definições e prepara os discos. Neste modo, a app não inicia cópias nem altera a tomada."
        : current.busy
          ? "Podes acompanhar as etapas aqui. A app continua a trabalhar se reiniciares o Home Assistant."
          : "As cópias são verificadas antes de o disco offline ser desmontado e desligado.";
  if (!recovery && !manual && s.mode === "checksum" && ["running", "success"].includes(s.state)) {
    if (s.state === "success") $("status-label").textContent = s.verification_source ? "Área verificada" : "Integridade confirmada";
    $("hero-title").textContent = current.busy ? "Estamos a verificar os teus dados." : "Verificação concluída.";
    $("hero-description").textContent = `Área: ${s.verification_source || "Todas as origens"}. ${s.verification_source ? "Esta verificação parcial não substitui a verificação integral agendada." : "Verificação integral das origens configuradas."}`;
  }
  $("cancel-run").hidden = !current.busy || (!current.can_cancel && !(s.state === "running" && s.cancel_requested));
  $("cancel-run").disabled = !current.can_cancel;
  $("cancel-run").textContent = s.cancel_requested ? "A cancelar…" : "Cancelar execução";
  if (current.busy && s.cancel_requested && ["running", "cancelled"].includes(s.state)) {
    $("status-label").textContent = "A cancelar em segurança";
    $("hero-title").textContent = "Estamos a terminar em segurança.";
    $("hero-description").textContent = "Se o backup nativo estiver ativo, aguardamos a sua conclusão. Depois fechamos o disco em segurança.";
  } else if (s.state === "cancelled" && !recovery && !current.busy) {
    $("hero-title").textContent = "Execução cancelada.";
    $("hero-description").textContent = s.safe_to_power_off ? "O destino ficou fechado em segurança. Consulta as etapas concluídas antes do cancelamento." : "O cancelamento terminou antes de operar no destino.";
  }
  $("banner").hidden =
    current.busy ||
    (!recovery && s.state !== "failed" && s.retention !== "failed");
  $("banner").textContent = recovery
    ? "O disco pode continuar ligado. Usa o fecho seguro; não desligues a tomada diretamente."
    : s.retention === "failed"
      ? "A cópia L2 foi entregue, mas a retenção precisa de atenção."
      : "A execução não terminou com sucesso. Consulta a atividade e confirma o estado do destino.";
  $("run").disabled = $("verify").disabled =
    current.busy || current.mode === "observe" || s.needs_cleanup;
  $("backup-verification").disabled = $("run").disabled;
  $("recover").hidden = !recovery;
  $("recover").disabled = current.busy || current.mode === "observe";
  $("destination-mount").disabled =
    current.busy || current.mode === "observe" || !!s.needs_cleanup;
  $("destination-shutdown").disabled =
    current.busy || current.mode === "observe" || !s.needs_cleanup;
  $("destination-status").textContent =
    `${current.destination_mount || "Destino"} · ${manual ? "Ligado e montado manualmente" : s.state === "destination_mounting" ? "A ligar e montar" : s.needs_cleanup ? "Fecho seguro pendente" : s.state === "destination_off" ? "Desligado em segurança" : "Disponível para montagem manual"}`;
  $("next-run").textContent = current.schedule_enabled
    ? stamp(current.next_run)
    : "Agenda desativada";
  $("schedule-note").textContent = current.schedule_enabled
    ? `Todos os dias, ${current.schedule_time} · ${current.timezone}`
    : "Ativa quando terminares os testes";
  $("last-success").textContent = stamp(s.last_success);
  $("checksum-frequency").textContent = {
    monthly: "Mensal",
    weekly: "Semanal",
    disabled: "A teu pedido",
  }[current.checksum_frequency];
  $("last-checksum").textContent = s.last_checksum
    ? `Último: ${stamp(s.last_checksum)}`
    : "Ainda não realizada";
  $("run-id").textContent = s.run_id || "";
  for (const key of ["l2", "l3", "retention"]) {
    const val = s[key];
    $(`${key}-state`).textContent = phases[val] || "A aguardar";
    document
      .querySelector(`[data-step="${key}"]`)
      .classList.toggle("complete", ["delivered", "success"].includes(val));
  }
  $("cleanup-state").textContent = manual
    ? "Montado manualmente"
    : s.needs_cleanup
      ? "Por confirmar"
      : s.state === "cancelled" && !s.safe_to_power_off
        ? "Não utilizado"
        : ["success", "cancelled", "destination_off"].includes(s.state)
        ? "Disco desligado"
        : "A aguardar";
  document
    .querySelector('[data-step="cleanup"]')
    .classList.toggle(
      "complete",
      ["success", "cancelled", "destination_off"].includes(s.state) && !s.needs_cleanup && (s.state !== "cancelled" || s.safe_to_power_off),
    );
  $("retention-note").textContent =
    `Conserva ${current.daily_retention_days} backups L2. Com uma cópia diária, isso representa cerca de ${current.daily_retention_days} dias de recuperação. O histórico L3 tem uma retenção própria.`;
  const integrity = s.integrity;
  $("integrity-report").hidden = !integrity;
  if (integrity) {
    const status = {running: "Verificação em curso", passed: "Verificação concluída", failed: "Verificação concluída com erros", aborted: "Verificação incompleta"}[integrity.status] || integrity.status;
    $("integrity-summary").textContent = `${status} · ${s.verification_source || "Todas as áreas"} · ${integrity.error_count} erro(s) globais · limite ${integrity.error_limit}${integrity.limit_reached ? " — limite atingido" : ""}`;
    const body = $("integrity-areas"); body.replaceChildren();
    for (const area of integrity.areas || []) {
      const row = document.createElement("tr");
      const statusLabel = {pending: "Não verificado", running: "Em curso", success: "Íntegro", failed: "Com erros", stopped: "Interrompido"}[area.status] || area.status;
      for (const value of [area.kind === "l2_sha256" ? `Arquivos L2 → ${area.destination}` : `${area.source} → ${area.destination}`, {size: "Tamanho", l2_sha256: "SHA-256 L2", sha256: "SHA-256 integral"}[area.kind] || area.kind, area.status === "success" && area.kind === "size" ? "Tamanhos iguais" : statusLabel, area.errors]) {
        const cell = document.createElement("td"); cell.textContent = value; row.append(cell);
      }
      body.append(row);
    }
    const samples = $("integrity-samples"); samples.replaceChildren();
    for (const sample of integrity.samples || []) {
      const item = document.createElement("li"); item.textContent = `${sample.source}: ${sample.path || "Verificação indisponível"} — ${{different: "Conteúdo diferente", missing_on_destination: "Falta no destino", missing_on_source: "Falta na origem", read_error: "Erro de leitura", unavailable: "Origem ou destino ausente", operation_error: "Falha na verificação"}[sample.category] || "Erro de integridade"}`; samples.append(item);
    }
  }
  const list = $("events");
  list.replaceChildren();
  for (const event of (s.events || []).slice(-7).reverse()) {
    const li = document.createElement("li");
    li.textContent = translate(event.stage, event.message);
    list.append(li);
  }
  if (!list.children.length) {
    const li = document.createElement("li");
    li.className = "empty";
    li.textContent = "As etapas da próxima execução vão aparecer aqui.";
    list.append(li);
  }
}
function translate(stage, fallback) {
  return (
    {
      l2_create: "A criar o backup nativo encriptado.",
      l2_download: "A transferir o L2 para o disco de origem.",
      l3_copy: "A copiar os dados para o disco offline.",
      checksum: "A comparar a integridade de todos os ficheiros.",
      safe_remove: "A sincronizar e a desmontar o destino em segurança.",
      recovery_required: "O fecho do disco ainda não foi confirmado.",
      notification_pending: "A notificação ao Home Assistant está pendente.",
      temporary_retained:
        "A cópia L2 foi entregue; a cópia temporária interna foi conservada.",
      schedule_blocked: "A execução agendada não pôde começar.",
    }[stage] || fallback
  );
}
function confirmOperation(mode) {
  if (mode === "backup") mode = $("backup-verification").value;
  pending = mode;
  $("verification-scope").hidden = $("verification-scope-note").hidden = mode !== "checksum";
  if (mode === "checksum") {
    const select = $("verification-source");
    select.replaceChildren();
    const all = document.createElement("option");
    all.value = ""; all.textContent = "Todas as áreas"; select.append(all);
    for (const source of current.verification_sources || []) {
      const option = document.createElement("option");
      option.value = source;
      const name = source.split("/").filter(Boolean).pop();
      option.textContent = `${name.charAt(0).toUpperCase() + name.slice(1)} (${source})`;
      select.append(option);
    }
  }
  $("confirm-title").textContent = operationLabels[mode];
  $("cancel-confirm").textContent = mode === "cancel" ? "Voltar" : "Cancelar";
  $("accept-confirm").textContent = mode === "cancel" ? "Cancelar execução" : "Continuar";
  $("confirm-description").textContent =
    mode === "cancel"
      ? "A app vai parar a cópia ou verificação e fechar o disco em segurança. Se o Home Assistant ainda estiver a criar o backup nativo, terá de aguardar que termine. O cancelamento não desfaz ficheiros já copiados ou retenção já aplicada."
      : mode === "destination_mount"
      ? "A app vai ligar a tomada e montar o destino. Não será feito nenhum backup. O disco fica ligado até pedires o fecho seguro."
      : mode === "destination_shutdown"
        ? "A app vai sincronizar e desmontar o destino. Só desligará a tomada depois de confirmar o fecho seguro. Fecha primeiro os ficheiros e aplicações que estejam a usar o destino."
        : mode === "recover"
          ? "A app vai verificar o trabalho anterior e tentar concluir o fecho seguro do disco."
          : mode === "backup_only"
            ? "A app vai criar o L2 e copiar os dados para L3 sem checksum integral. Mantém a confirmação de tamanhos, o SHA-256 dos arquivos L2 e o fecho seguro. A retenção só se aplica em produção."
          : mode === "backup_checksum"
            ? "A app vai criar o L2, copiar os dados para L3 e executar SHA-256 integral de todas as áreas antes do fecho seguro. Pode demorar várias horas."
          : current.mode === "test"
            ? "A execução usará a área de teste separada e não aplicará retenção. Vai ligar o destino e criar ou verificar cópias reais."
            : mode === "checksum"
              ? "Escolhe todas as áreas ou apenas uma. A app verifica os ficheiros dessa seleção, liga e fecha o destino em segurança. Pode demorar várias horas. Não cria uma nova cópia."
              : "A app vai criar o L2, copiar os dados para L3 e fechar o disco em segurança. A política de retenção configurada será aplicada.";
  $("confirm").showModal();
}
$("run").onclick = () => confirmOperation("backup");
$("verify").onclick = () => confirmOperation("checksum");
$("cancel-run").onclick = () => confirmOperation("cancel");
$("recover").onclick = () => confirmOperation("recover");
$("destination-mount").onclick = () => confirmOperation("destination_mount");
$("destination-shutdown").onclick = () =>
  confirmOperation("destination_shutdown");
$("cancel-confirm").onclick = () => $("confirm").close();
$("accept-confirm").onclick = async () => {
  const mode = pending;
  const source = mode === "checksum" ? $("verification-source").value : "";
  $("confirm").close();
  try {
    await api(
      mode.startsWith("destination_")
        ? "destination"
        : ["recover", "cancel"].includes(mode)
          ? mode
          : "run",
      "POST",
      mode.startsWith("destination_")
        ? { action: mode.replace("destination_", "") }
        : ["recover", "cancel"].includes(mode)
          ? {}
          : source ? { mode, source } : { mode },
    );
    toast("Pedido aceite. Podes acompanhar a execução aqui.");
    await refresh();
  } catch (e) {
    toast(e.message);
  }
};
$("settings-toggle").onclick = async () => {
  try {
    config = await api("config");
    const form = $("config-form");
    for (const [key, val] of Object.entries(config)) {
      const input = form.elements.namedItem(key);
      if (!input) continue;
      if (input.type === "checkbox") input.checked = val;
      else input.value = Array.isArray(val) ? val.join("\n") : val;
    }
    form.elements.encryption_password.value = "";
    $("form-error").textContent = "";
    $("pairing").hidden = true;
    $("pair-token").value = "";
    $("settings").showModal();
  } catch (e) {
    toast(e.message);
  }
};
$("close-settings").onclick = () => $("settings").close();
$("config-form").onsubmit = async (event) => {
  event.preventDefault();
  const form = event.target;
  const value = { ...config };
  for (const input of form.elements) {
    if (!input.name) continue;
    value[input.name] =
      input.type === "checkbox"
        ? input.checked
        : input.type === "number" ||
            ["checksum_weekday", "checksum_month_week"].includes(input.name)
          ? Number(input.value)
          : input.value;
  }
  value.additional_sources = value.additional_sources
    .split("\n")
    .map((x) => x.trim())
    .filter(Boolean);
  try {
    await api("config", "PUT", value);
    $("settings").close();
    toast("Definições guardadas.");
    await refresh();
  } catch (e) {
    $("form-error").textContent = e.message;
  }
};
$("pair").onclick = async () => {
  try {
    const result = await api("pair", "POST", {});
    $("pair-token").value = result.api_token;
    $("pairing").hidden = false;
  } catch (e) {
    toast(e.message);
  }
};
$("copy-token").onclick = async () => {
  try {
    await navigator.clipboard.writeText($("pair-token").value);
    toast("Chave copiada.");
  } catch {
    toast("Não foi possível copiar. Seleciona a chave manualmente.");
  }
};
refresh();
setInterval(refresh, 5000);
