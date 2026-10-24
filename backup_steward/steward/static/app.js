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
  backup_checksum: "Backup com checksum integral",
  checksum: "Verificar integridade",
  recover: "Concluir fecho seguro",
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
  }
}
function render() {
  const s = current.state;
  const recovery = s.recovery_required ?? (s.needs_cleanup && !current.busy);
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
        ? s.state === "recovering"
          ? "Recuperação em curso"
          : "Backup em curso"
        : labels[s.state] || s.state;
  $("status-badge").dataset.tone =
    current.mode === "observe" ? "observe" : s.state;
  $("hero-title").textContent = recovery
    ? "Vamos fechar o disco em segurança."
    : current.busy
      ? "Estamos a cuidar dos teus dados."
      : s.state === "success"
        ? "Tudo guardado. Podes respirar."
        : current.mode === "observe"
          ? "Conhece a tua próxima rede de segurança."
          : "Pronto para a próxima cópia.";
  $("hero-description").textContent = recovery
    ? "A execução precisa de recuperação. A tomada só será desligada depois de o fecho seguro ser confirmado."
    : current.mode === "observe"
      ? "Explora as definições e prepara os discos. Neste modo, a app não inicia cópias nem altera a tomada."
      : current.busy
        ? "Podes acompanhar as etapas aqui. A app continua a trabalhar se reiniciares o Home Assistant."
        : "As cópias são verificadas antes de o disco offline ser desmontado e desligado.";
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
  $("recover").hidden = !recovery;
  $("recover").disabled = current.busy || current.mode === "observe";
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
  $("cleanup-state").textContent = s.needs_cleanup
    ? "Por confirmar"
    : s.state === "success"
      ? "Disco desligado"
      : "A aguardar";
  document
    .querySelector('[data-step="cleanup"]')
    .classList.toggle("complete", s.state === "success" && !s.needs_cleanup);
  $("retention-note").textContent =
    `Conserva ${current.daily_retention_days} backups L2. Com uma cópia diária, isso representa cerca de ${current.daily_retention_days} dias de recuperação. O histórico L3 tem uma retenção própria.`;
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
  pending = mode;
  $("confirm-title").textContent = operationLabels[mode];
  $("confirm-description").textContent =
    mode === "recover"
      ? "A app vai verificar o trabalho anterior e tentar concluir o fecho seguro do disco."
      : current.mode === "test"
        ? "A execução usará a área de teste separada e não aplicará retenção. Vai ligar o destino e criar ou verificar cópias reais."
        : mode === "checksum"
          ? "Esta verificação lê as origens e o espelho por completo. Pode demorar várias horas. Não cria uma nova cópia."
          : "A app vai criar o L2, copiar os dados para L3 e fechar o disco em segurança. A política de retenção configurada será aplicada.";
  $("confirm").showModal();
}
$("run").onclick = () => confirmOperation("backup");
$("verify").onclick = () => confirmOperation("checksum");
$("recover").onclick = () => confirmOperation("recover");
$("cancel-confirm").onclick = () => $("confirm").close();
$("accept-confirm").onclick = async () => {
  const mode = pending;
  $("confirm").close();
  try {
    await api(
      mode === "recover" ? "recover" : "run",
      "POST",
      mode === "recover" ? {} : { mode },
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
