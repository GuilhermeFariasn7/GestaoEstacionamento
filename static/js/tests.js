/* =========================================================
 * Página de Testes
 * ========================================================= */

const api = () =>
    localStorage.getItem('smartparking:roiApiUrl') || 'http://127.0.0.1:5001';

let job = null;
let testRunning = false;

const envSelectEl = document.getElementById('envSelect');
const envInfoTextEl = document.getElementById('envInfoText');
const sourceModeEl = document.getElementById('sourceMode');
const sourceUploadGroupEl = document.getElementById('sourceUploadGroup');
const sourcePathGroupEl = document.getElementById('sourcePathGroup');
const sourceVideoFileEl = document.getElementById('sourceVideoFile');
const sourcePathInputEl = document.getElementById('sourcePathInput');
const confidenceEl = document.getElementById('confidence');
const confidenceValueEl = document.getElementById('confidenceValue');
const frameStepEl = document.getElementById('frameStep');
const stabilizeEl = document.getElementById('stabilize');
const startTestEl = document.getElementById('startTest');
const resultCardEl = document.getElementById('resultCard');
const statusTextEl = document.getElementById('statusText');
const progressTextEl = document.getElementById('progressText');
const progressBarEl = document.getElementById('progressBar');
const resultTextEl = document.getElementById('resultText');
const summaryTableEl = document.getElementById('summaryTable');
const actionsEl = document.getElementById('actions');
const toggleConfigBtn = document.getElementById('toggleConfigBtn');
const configFrameEl = document.getElementById('configFrame');
const configModalEl = document.getElementById('configModal');
const toggleHistoryBtn = document.getElementById('toggleHistoryBtn');
const refreshHistoryBtn = document.getElementById('refreshHistoryBtn');
const historyModalEl = document.getElementById('historyModal');
const historyTableWrapEl = document.getElementById('historyTableWrap');

const configModal = (configModalEl && window.bootstrap)
    ? new bootstrap.Modal(configModalEl)
    : null;

const historyModal = (historyModalEl && window.bootstrap)
    ? new bootstrap.Modal(historyModalEl)
    : null;

/* ---------- utilidades ---------- */

async function req(path, options = {}) {
    const response = await fetch(api() + path, options);
    if (!response.ok) {
        const data = await response.json().catch(() => ({}));
        throw new Error(data.error || 'Erro ao comunicar com a API.');
    }
    return response;
}

function basename(path) {
    if (!path) return '—';
    const clean = String(path).split(/[?#]/)[0];
    const parts = clean.split(/[\\/]/);
    return parts[parts.length - 1] || clean;
}

function formatDate(timestamp) {
    if (!timestamp) return '—';
    try {
        return new Date(timestamp * 1000).toLocaleString('pt-BR');
    } catch (error) {
        return '—';
    }
}

function formatPercent(value) {
    if (value === null || value === undefined) return '—';
    return Math.round(value * 100) + '%';
}

/* ---------- parâmetros ---------- */

confidenceEl.oninput = () => {
    confidenceValueEl.textContent = Number(confidenceEl.value).toFixed(2);
};

/* ---------- ambiente ---------- */

const LAST_ENV_KEY = 'smartparking:lastEnvironment';

async function loadEnvironments() {
    envSelectEl.innerHTML = '<option value="">Carregando ambientes...</option>';

    try {
        const response = await req('/api/environments');
        const names = await response.json();

        if (!Array.isArray(names) || names.length === 0) {
            envSelectEl.innerHTML = '<option value="">Nenhum ambiente configurado</option>';
            envInfoTextEl.textContent = 'Configure um ambiente primeiro em "Configurar vagas".';
            return;
        }

        const preferred = localStorage.getItem(LAST_ENV_KEY);
        envSelectEl.innerHTML = names.map(name =>
            `<option value="${name}">${name}</option>`
        ).join('');

        envSelectEl.value = names.includes(preferred) ? preferred : names[0];
        await updateEnvInfo();
    } catch (error) {
        envSelectEl.innerHTML = '<option value="">Erro ao carregar ambientes</option>';
        envInfoTextEl.textContent = error.message;
    }
}

async function updateEnvInfo() {
    const name = envSelectEl.value;
    if (!name) {
        envInfoTextEl.textContent = 'Selecione um ambiente configurado.';
        return;
    }

    localStorage.setItem(LAST_ENV_KEY, name);

    try {
        const response = await req('/api/environments/' + encodeURIComponent(name));
        const env = await response.json();
        const regionCount = Object.keys(env.regions || {}).filter(k => k !== '__detection__').length;

        if (regionCount === 0) {
            envInfoTextEl.innerHTML = '<span class="text-danger">Este ambiente ainda não tem vagas configuradas.</span>';
        } else {
            envInfoTextEl.textContent = `${regionCount} vaga(s) configurada(s). Fonte salva: ${basename(env.source)}.`;
        }
    } catch (error) {
        envInfoTextEl.textContent = 'Não foi possível carregar os detalhes deste ambiente.';
    }
}

envSelectEl.onchange = updateEnvInfo;

/* ---------- fonte de vídeo ---------- */

function updateSourceModeUI() {
    const mode = sourceModeEl.value;
    sourceUploadGroupEl.classList.toggle('d-none', mode !== 'upload');
    sourcePathGroupEl.classList.toggle('d-none', mode !== 'path');
}

sourceModeEl.onchange = updateSourceModeUI;
updateSourceModeUI();

/* ---------- modal de configuração ---------- */

toggleConfigBtn.onclick = () => {
    const name = envSelectEl.value;
    configFrameEl.src = name ? ('/roi?env=' + encodeURIComponent(name)) : '/roi';
    if (configModal) configModal.show();
};

if (configModalEl) {
    configModalEl.addEventListener('hidden.bs.modal', () => {
        configFrameEl.src = 'about:blank';
        // As vagas podem ter mudado enquanto o modal estava aberto.
        loadEnvironments();
    });
}

/* ---------- modal de histórico ---------- */

function renderHistory(jobs) {
    if (!jobs || jobs.length === 0) {
        historyTableWrapEl.innerHTML = '<p class="sp-result-text">Nenhum teste executado ainda.</p>';
        return;
    }

    const statusLabel = {
        completed: '<span class="sp-pill sp-pill--success">Concluído</span>',
        running: '<span class="sp-pill sp-pill--running">Em execução</span>',
        error: '<span class="sp-pill sp-pill--danger">Erro</span>'
    };

    const rows = jobs.map(item => {
        const badge = statusLabel[item.status] || `<span class="sp-pill">${item.status}</span>`;
        const errorTitle = item.status === 'error' && item.error ? ` title="${item.error.replace(/"/g, '&quot;')}"` : '';

        let actions = '—';
        if (item.status === 'completed') {
            actions = `<a href="/tests/${item.id}/review" class="btn btn-outline-secondary btn-sm me-1">
                            <i class="bi bi-check2-square"></i>
                        </a>
                        <a href="${api()}/api/tests/${item.id}/download" class="btn btn-outline-success btn-sm">
                            <i class="bi bi-download"></i>
                        </a>`;
        }

        return `<tr${errorTitle}>
            <td><strong>${item.environment || '—'}</strong></td>
            <td>${basename(item.source)}</td>
            <td>${formatDate(item.created_at)}</td>
            <td>${badge}</td>
            <td>${formatPercent(item.accuracy)}</td>
            <td>${formatPercent(item.precision)}</td>
            <td class="text-nowrap">${actions}</td>
        </tr>`;
    }).join('');

    historyTableWrapEl.innerHTML = `
        <table class="sp-history-table">
            <thead>
                <tr>
                    <th>Ambiente</th>
                    <th>Vídeo</th>
                    <th>Data</th>
                    <th>Status</th>
                    <th>Acurácia</th>
                    <th>Precisão</th>
                    <th>Ações</th>
                </tr>
            </thead>
            <tbody>${rows}</tbody>
        </table>`;
}

async function loadHistory() {
    historyTableWrapEl.innerHTML = '<p class="sp-result-text">Carregando histórico...</p>';
    try {
        const response = await req('/api/tests');
        const jobs = await response.json();
        renderHistory(jobs);
    } catch (error) {
        historyTableWrapEl.innerHTML = `<p class="sp-result-text text-danger">${error.message}</p>`;
    }
}

if (toggleHistoryBtn) {
    toggleHistoryBtn.onclick = () => {
        if (historyModal) historyModal.show();
        loadHistory();
    };
}

if (refreshHistoryBtn) {
    refreshHistoryBtn.onclick = loadHistory;
}

/* ---------- estado do formulário ---------- */

function setControlsDisabled(disabled) {
    [envSelectEl, sourceModeEl, sourceVideoFileEl, sourcePathInputEl, confidenceEl, frameStepEl, stabilizeEl]
        .forEach(el => { el.disabled = disabled; });
}

function setButtonRunning(running, label = 'Processando...') {
    testRunning = running;
    setControlsDisabled(running);
    startTestEl.disabled = running;
    startTestEl.innerHTML = running
        ? `<span class="spinner-border spinner-border-sm me-1"></span>${label}`
        : '<i class="bi bi-play-fill me-1"></i>Iniciar teste';
}

/* ---------- feedback ---------- */

function showRunning(message = 'Preparando teste...') {
    resultCardEl.classList.remove('d-none');
    statusTextEl.innerHTML = '<i class="bi bi-hourglass-split me-1"></i>' + message;
    progressTextEl.textContent = '0%';
    progressBarEl.style.width = '0%';
    resultTextEl.textContent = 'O vídeo está sendo processado. Aguarde a conclusão.';
    summaryTableEl.innerHTML = '';
    actionsEl.innerHTML = '';
    resultCardEl.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
}

function showError(message) {
    resultCardEl.classList.remove('d-none');
    statusTextEl.innerHTML = '<i class="bi bi-exclamation-triangle me-1"></i>Erro';
    resultTextEl.textContent = message || 'O teste não pôde ser executado.';
    actionsEl.innerHTML = '';
    progressBarEl.style.width = '0%';
    progressTextEl.textContent = '0%';
}

function renderSummary(summary) {
    if (!summary) {
        summaryTableEl.innerHTML = '';
        return;
    }

    const rows = Object.entries(summary).map(([name, count]) => {
        const total = count.ocupada + count.livre;
        const occupied = total ? Math.round(count.ocupada / total * 100) : 0;
        return `<tr>
            <td><strong>${name}</strong></td>
            <td><span class="sp-pill sp-pill--danger">${count.ocupada}</span></td>
            <td><span class="sp-pill sp-pill--success">${count.livre}</span></td>
            <td>${occupied}%</td>
        </tr>`;
    }).join('');

    summaryTableEl.innerHTML = `
        <table>
            <thead><tr><th>Vaga</th><th>Ocupada</th><th>Livre</th><th>% ocupada</th></tr></thead>
            <tbody>${rows}</tbody>
        </table>`;
}

/* ---------- início ---------- */

async function startTest() {
    if (testRunning) return;

    const environment = envSelectEl.value;
    if (!environment) {
        showError('Selecione um ambiente configurado antes de iniciar o teste.');
        return;
    }

    const mode = sourceModeEl.value;

    if (mode === 'upload' && (!sourceVideoFileEl.files || sourceVideoFileEl.files.length === 0)) {
        showError('Selecione um arquivo de vídeo para enviar.');
        return;
    }

    if (mode === 'path' && !sourcePathInputEl.value.trim()) {
        showError('Digite o caminho ou a URL do vídeo.');
        return;
    }

    setButtonRunning(true, 'Iniciando...');
    showRunning('Iniciando teste...');

    try {
        const common = {
            environment,
            confidence: Number(confidenceEl.value),
            frame_step: Number(frameStepEl.value),
            stabilize: stabilizeEl.checked
        };

        let response;

        if (mode === 'upload') {
            const formData = new FormData();
            Object.entries(common).forEach(([key, value]) => formData.append(key, value));
            formData.append('video', sourceVideoFileEl.files[0]);

            response = await req('/api/tests', {
                method: 'POST',
                body: formData
            });
        } else {
            const body = { ...common };
            if (mode === 'path') {
                body.source = sourcePathInputEl.value.trim();
            }

            response = await req('/api/tests', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(body)
            });
        }

        const data = await response.json();
        job = data.id;
        if (!job) throw new Error('A API não retornou o identificador do teste.');

        progressBarEl.style.width = '0%';
        progressTextEl.textContent = '0%';
        statusTextEl.innerHTML = '<i class="bi bi-cpu me-1"></i>Teste iniciado';
        resultTextEl.textContent = 'O YOLO começou a analisar o vídeo. Acompanhe o progresso abaixo.';
        await poll();
    } catch (error) {
        showError(error.message);
        setButtonRunning(false);
    }
}

startTestEl.onclick = startTest;

/* ---------- polling ---------- */

async function poll() {
    if (!job) return;

    try {
        const data = await (await req('/api/tests/' + job)).json();
        const progress = Number(data.progress) || 0;

        progressBarEl.style.width = progress + '%';
        progressTextEl.textContent = progress + '%';

        if (data.status === 'running') {
            statusTextEl.innerHTML = '<i class="bi bi-cpu me-1"></i>Analisando vídeo...';
            resultTextEl.textContent = `Amostras processadas: ${data.rows || 0}. O teste continua em execução.`;
            return setTimeout(poll, 1000);
        }

        if (data.status === 'completed') {
            progressBarEl.style.width = '100%';
            progressTextEl.textContent = '100%';
            statusTextEl.innerHTML = '<i class="bi bi-check-circle me-1"></i>Teste concluído';
            resultTextEl.textContent = `Foram analisadas ${data.rows || 0} amostras. Revise o gabarito para calcular acertos e erros.`;
            renderSummary(data.summary);

            actionsEl.innerHTML =
                `<a class="btn btn-primary sp-btn-primary" href="/tests/${job}/review">
                    <i class="bi bi-check2-square me-1"></i>Avaliar gabarito
                 </a>
                 <a class="btn btn-outline-success" href="${api()}/api/tests/${job}/download">
                    <i class="bi bi-download me-1"></i>Baixar CSV
                 </a>`;

            setButtonRunning(false);
            loadHistory();
            return;
        }

        showError(data.error || 'O teste terminou com erro.');
        setButtonRunning(false);
        loadHistory();
    } catch (error) {
        showError(error.message);
        setButtonRunning(false);
    }
}

/* ---------- inicialização ---------- */

loadEnvironments();
