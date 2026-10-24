(() => {
  const envSelect = document.getElementById('envSelect');
  const startBtn = document.getElementById('evaluationStartBtn');
  const stopBtn = document.getElementById('evaluationStopBtn');
  const everyInput = document.getElementById('evaluationSampleEvery');
  const statusEl = document.getElementById('evaluationStatus');
  const messageEl = document.getElementById('evaluationMessage');
  const predictionsLink = document.getElementById('evaluationPredictionsLink');
  const truthLink = document.getElementById('evaluationGroundTruthLink');
  const apiInput = document.getElementById('apiUrl');
  if (!envSelect || !startBtn || !stopBtn) return;

  const base = () => (apiInput?.value || window.location.origin).replace(/\/$/, '');
  async function request(path, options = {}) {
    const response = await fetch(`${base()}${path}`, options);
    const body = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(body.error || `Falha HTTP ${response.status}`);
    return body;
  }
  function render(data) {
    const active = Boolean(data.active);
    statusEl.textContent = active ? 'Coletando' : (data.exists ? 'Finalizada' : 'Inativa');
    startBtn.disabled = active;
    stopBtn.disabled = !active;
    if (data.exists) {
      messageEl.textContent = `Ambiente: ${data.environment}. Amostras salvas: ${data.samples ?? 0}. Análises observadas: ${data.detections ?? 0}. ${active ? 'A coleta está ativa.' : 'A coleta foi finalizada.'}`;
      predictionsLink.style.display = 'inline';
      truthLink.style.display = 'inline';
    }
  }
  async function refresh() {
    try { render(await request('/api/evaluation/status')); } catch (e) { messageEl.textContent = e.message; }
  }
  startBtn.addEventListener('click', async () => {
    try {
      const environment = envSelect.value;
      if (!environment) throw new Error('Selecione um ambiente nas configurações primeiro.');
      const every = Math.max(1, Math.min(100, Number.parseInt(everyInput.value, 10) || 5));
      render(await request('/api/evaluation/start', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({environment, sample_every:every})}));
    } catch (e) { messageEl.textContent = e.message; }
  });
  stopBtn.addEventListener('click', async () => {
    try { render(await request('/api/evaluation/stop', {method:'POST'})); } catch (e) { messageEl.textContent = e.message; }
  });
  predictionsLink.addEventListener('click', e => { e.preventDefault(); window.location.href = `${base()}/api/evaluation/predictions.csv`; });
  truthLink.addEventListener('click', e => { e.preventDefault(); window.location.href = `${base()}/api/evaluation/gabarito.csv`; });
  refresh();
  window.setInterval(refresh, 3000);
})();
