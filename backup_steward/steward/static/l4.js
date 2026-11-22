/* Direct mirror setup. Public keys only; never render a private key. */
(() => {
  const el = id => document.getElementById(`l4-${id}`);
  let policy, status, local, ready = false, pending = false, testRequestPending = false, localTestError = null;
  const controls = ["enabled", "settings", "test", "run", "cancel", "save", "pair", "copy", "yes", "import-button"];
  const formatBytes = value => Number.isFinite(value) ? `${new Intl.NumberFormat("pt-PT",{maximumFractionDigits:2}).format(value / 1024**3)} GiB` : "—";
  const formatTime = value => value ? new Date(value).toLocaleString("pt-PT",{timeZone:local.timezone}) : "—";
  function renderTransfer() {
    el("transfer").hidden = !status.started_at;
    const active = status.busy && !status.testing && status.status === "running";
    const terminal = {success:"complete", failed:"failed", interrupted:"interrupted"};
    const phase = terminal[status.status] || status.phase;
    const phases = {preflight:"A validar as origens", planning:"A comparar alterações", transferring:"A enviar dados", verifying:"A confirmar o recetor", complete:"Espelho concluído", failed:"Sincronização falhou", interrupted:"Sincronização interrompida"};
    el("stage").textContent = `${phases[phase] || "A aguardar"}${status.source ? " · " + status.source : ""}`;
    el("source-count").textContent = Number.isFinite(status.completed_sources) && Number.isFinite(status.sources_total) ? `${status.completed_sources}/${status.sources_total} origens concluídas${active && status.source_index ? " · atual " + status.source_index : ""}` : "";
    if (phase === "complete") el("bar").value = 100;
    else if (!active || (phase === "transferring" && Number.isFinite(status.percent))) el("bar").value = status.percent ?? 0;
    else el("bar").removeAttribute("value");
    el("bytes").textContent = formatBytes(status.bytes);
    el("files").textContent = status.files_transferred ?? "—";
    el("speed").textContent = status.speed || "—";
    el("eta").textContent = active && phase === "transferring" ? status.eta || "A calcular" : "—";
    const end = active ? Date.now() : new Date(status.finished_at || (status.status === "success" ? status.last_success : null) || "").getTime();
    const elapsed = Math.max(0,Math.floor((end-new Date(status.started_at).getTime())/1000));
    el("elapsed").textContent = Number.isFinite(elapsed) ? `${Math.floor(elapsed/3600)}h ${Math.floor(elapsed%3600/60)}m ${elapsed%60}s` : "—";
    el("updated").textContent = formatTime(status.updated_at);
    const events = Array.isArray(status.events) ? status.events.slice(-40) : [];
    el("log-panel").hidden = !events.length;
    el("events").replaceChildren();
    for (const event of events) {
      const item = document.createElement("li"), time = document.createElement("time"), text = document.createElement("span");
      time.textContent = formatTime(event.time); text.textContent = event.message;
      item.append(time,text); el("events").append(item);
    }
  }
  function lock() { ready = false; controls.forEach(id => el(id).disabled = true); }
  function render() {
    const testing = testRequestPending || status.testing === true || status.connection === "testing";
    const idle = !local.busy && !local.state.needs_cleanup && !status.busy && !testing;
    el("enabled").checked = policy.enabled;
    el("enabled").disabled = !ready || testing || (!idle && !status.busy);
    el("settings").disabled = el("save").disabled = el("pair").disabled = !ready || !idle;
    el("import-button").disabled = !ready || !idle;
    el("test").disabled = !ready || !idle || !policy.sources.length || !policy.host_key || !policy.host;
    el("test").textContent = testing ? "A testar…" : "Testar ligação";
    el("run").disabled = el("yes").disabled = !ready || !idle || !policy.enabled || local.mode !== "production";
    el("cancel").hidden = !status.busy || testing;
    el("cancel").disabled = !ready || !status.busy || testing;
    el("copy").disabled = !ready || !el("pairing").value;
    el("destination").textContent = policy.host ? `${policy.label} · ${policy.host}:${policy.port}` : "Por configurar";
    el("last").textContent = status.last_success ? new Date(status.last_success).toLocaleString("pt-PT", {timeZone:local.timezone}) : "Ainda sem envios";
    el("progress").textContent = status.busy && status.speed ? `${status.source || ""} · ${status.percent || 0}% · ${status.speed}` : "—";
    el("summary").textContent = !policy.enabled ? "L4 desativado. A cópia remota existente mantém-se." : status.status === "success" ? "Espelho atualizado." : status.message || "Espelho ativo. Testa a ligação antes do primeiro envio.";
    renderTransfer();
    const connection = testing ? "testing" : localTestError ? "failed" : status.connection;
    const connectionLabels = {testing:"A testar ligação", success:"Ligação confirmada", failed:"Teste falhou", interrupted:"Teste interrompido"};
    el("connection").hidden = !connectionLabels[connection] || (status.busy && !testing);
    el("connection").dataset.state = connection || "pending";
    if (!el("connection").hidden) {
      const when = !testing && !localTestError && status.tested_at ? ` · ${new Date(status.tested_at).toLocaleString("pt-PT", {timeZone:local.timezone})}` : "";
      const defaultDetails = {success:"SSH, chaves e destino confirmados no último teste.", failed:"Repete o teste para obter o erro detalhado.", interrupted:"Repete o teste para confirmar a ligação."};
      const detail = testing ? "A verificar SSH, chaves e destino. Nenhum ficheiro será enviado." : localTestError || status.connection_message || defaultDetails[connection] || "";
      el("connection").textContent = `${connectionLabels[connection]}${when}. ${detail}`;
    }
  }
  async function refreshMirror() {
    if (pending) return;
    pending = true; lock();
    try {
      const [p, s] = await Promise.all([api("l4/config"), api("l4/status")]);
      if (!statusReady || !current) throw Error("A aguardar estado local");
      const l = current;
      if (typeof s.busy !== "boolean" || typeof s.enabled !== "boolean" || typeof l.busy !== "boolean" || typeof l.state?.needs_cleanup !== "boolean" || !Array.isArray(p.sources)) throw Error("Estado incompleto");
      policy = p; status = s; local = l; ready = true; render();
    } catch { lock(); el("summary").textContent = "Ligação indisponível. As ações ficam bloqueadas até confirmar o estado."; }
    finally { pending = false; }
  }
  async function action(name) {
    if (!ready) return;
    try { await api(`l4/${name}`, "POST", {}); await refreshMirror(); }
    catch(e) { toast(e.message); await refreshMirror(); }
  }
  async function testConnection() {
    if (!ready || el("test").disabled || testRequestPending) return;
    testRequestPending = true;
    localTestError = null;
    render();
    try {
      const result = await api("l4/test", "POST", {});
      if (result?.connection !== "success") throw Error("O teste não devolveu uma confirmação válida. Repete para confirmar a ligação.");
      status = {...status, ...result};
      toast("Ligação confirmada. Nenhum ficheiro foi enviado.");
    } catch(e) {
      localTestError = e.message;
      status = {...status, testing:false, connection:"failed", tested_at:null, connection_message:e.message};
      toast(e.message);
    } finally {
      testRequestPending = false;
      render();
      await refreshMirror();
    }
  }
  el("enabled").onchange = async () => {
    if (!ready) return;
    const next = {...policy, enabled: el("enabled").checked};
    lock();
    try { await api("l4/config", "PUT", next); }
    catch(e) { toast(e.message); }
    await refreshMirror();
  };
  el("settings").onclick = async () => {
    if (!ready) return;
    try {
      const config = await api("config");
      const form = el("form");
      for (const [key, value] of Object.entries(policy)) {
        const input = form.elements.namedItem(key);
        if (!input) continue;
        if (input.type === "checkbox") input.checked = value; else input.value = value;
      }
      el("sources").replaceChildren();
      for (const path of [config.source_mount, ...config.additional_sources]) {
        const saved = policy.sources.find(s => s.source === path);
        const card = document.createElement("div"); card.className = "mirror-source"; card.dataset.path = path;
        const label = document.createElement("label"); label.className = "check";
        const check = document.createElement("input"); check.type = "checkbox"; check.checked = !!saved; check.className = "source-selected";
        label.append(check, document.createTextNode(path)); card.append(label);
        const grid = document.createElement("div"); grid.className = "form-grid";
        for (const [name, title, value] of [["name", "Nome no recetor", saved?.name || path.split("/").pop().toLowerCase()], ["uuid", "UUID do filesystem", saved?.uuid || (path === config.source_mount ? config.source_uuid : "")]]) {
          const field = document.createElement("label"); field.textContent = title;
          const input = document.createElement("input"); input.dataset.field = name; input.value = value; field.append(input); grid.append(field);
        }
        const detect = document.createElement("button"); detect.type = "button"; detect.className = "secondary"; detect.textContent = "Detetar UUID";
        detect.onclick = async () => {
          if (!ready) return;
          try {
            const result = await api("l4/storage"); const found = result.sources.find(item => item.source === path);
            if (!found) throw Error("Não foi possível confirmar um filesystem ext4 para esta origem. Confirma a montagem e o UUID manualmente.");
            card.querySelector('[data-field="uuid"]').value = found.uuid;
          } catch(e) { el("error").textContent = e.message; }
        };
        card.append(grid, detect); el("sources").append(card);
      }
      el("pairing").value = ""; el("pairing").hidden = el("copy").hidden = true; el("error").textContent = "";
      el("dialog").showModal();
    } catch(e) { toast(e.message); }
  };
  el("form").onsubmit = async event => {
    event.preventDefault(); if (!ready) return;
    const next = {...policy};
    for (const input of event.target.elements) if (input.name) next[input.name] = input.type === "checkbox" ? input.checked : input.type === "number" ? Number(input.value) : input.value.trim();
    next.sources = [...el("sources").children].filter(card => card.querySelector(".source-selected").checked).map(card => ({source:card.dataset.path, name:card.querySelector('[data-field="name"]').value.trim(), uuid:card.querySelector('[data-field="uuid"]').value.trim()}));
    try { await api("l4/config", "PUT", next); el("error").textContent = "Definições guardadas. Podes preparar as chaves e testar a ligação."; await refreshMirror(); }
    catch(e) { el("error").textContent = e.message; }
  };
  el("import-button").onclick = () => {
    try {
      const data = JSON.parse(el("import").value);
      if (data.protocol !== "steward-mirror-v1" || typeof data.host_key !== "string" || !Number.isInteger(data.port)) throw Error();
      el("form").elements.destination_uuid.value = data.uuid || ""; el("form").elements.host_key.value = data.host_key; el("form").elements.port.value = data.port;
      el("error").textContent = "Identidade preenchida. Confirma o endereço e guarda as definições.";
    } catch { el("error").textContent = "Cola o JSON de identidade exportado pela app Mirror."; }
  };
  el("pair").onclick = async () => {
    if (!ready) return;
    try { el("pairing").value = JSON.stringify(await api("l4/pair", "POST", {}), null, 2); el("pairing").hidden = el("copy").hidden = false; render(); }
    catch(e) { el("error").textContent = e.message; }
  };
  el("copy").onclick = async () => { try { await navigator.clipboard.writeText(el("pairing").value); toast("Emparelhamento público copiado."); } catch { el("pairing").select(); } };
  el("close").onclick = () => el("dialog").close();
  el("test").onclick = testConnection;
  el("cancel").onclick = () => action("cancel");
  el("run").onclick = () => { if (ready && !el("run").disabled) el("confirm").showModal(); };
  el("no").onclick = () => el("confirm").close();
  el("yes").onclick = () => { if (ready && !el("yes").disabled) { el("confirm").close(); action("run"); } };
  refreshMirror(); setInterval(refreshMirror, 5000);
})();
