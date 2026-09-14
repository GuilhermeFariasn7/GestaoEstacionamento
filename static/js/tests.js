/* =========================================================
 * Página de Testes
 * ========================================================= */

const api = () =>
    localStorage.getItem('smartparking:roiApiUrl') || 'http://127.0.0.1:5001';

let job = null;
let testRunning = false;

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

const configModal = (configModalEl && window.bootstrap)
    ? new bootstrap.Modal(configModalEl)
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

/* ---------- parâmetros ---------- */

confidenceEl.oninput = () => {
    confidenceValueEl.textContent = Number(confidenceEl.value).toFixed(2);
};

/* ---------- modal de configuração ---------- */

toggleConfigBtn.onclick = () => {
    configFrameEl.src = '/roi';
    if (configModal) configModal.show();
};

if (configModalEl) {
    configModalEl.addEventListener('hidden.bs.modal', () => {
        configFrameEl.src = 'about:blank';
    });
}

/* ---------- estado do formulário ---------- */

function setControlsDisabled(disabled) {
    [confidenceEl, frameStepEl, stabilizeEl].forEach(el => el.disabled = disabled);
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

    setButtonRunning(true, 'Iniciando...');
    showRunning('Iniciando teste...');

    try {
        const body = {
            confidence: Number(confidenceEl.value),
            frame_step: Number(frameStepEl.value),
            stabilize: stabilizeEl.checked
        };

        const response = await req('/api/tests', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(body)
        });

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
            return;
        }

        showError(data.error || 'O teste terminou com erro.');
        setButtonRunning(false);
    } catch (error) {
        showError(error.message);
        setButtonRunning(false);
    }
}