/* =========================================================
 * Smart Parking — Experimentos do TCC
 * Fluxo: 1. vídeo → 2. vagas → 3. execução → 4. análise
 * ========================================================= */
(() => {
'use strict';

/* ---------------------------------------------------------
 * Utilidades
 * --------------------------------------------------------- */
const API = () => localStorage.getItem('smartparking:roiApiUrl') || 'http://127.0.0.1:5001';
const $ = id => document.getElementById(id);
const IGNORE = 'ignorar';
const PAGE_SIZE = 100;

async function req(path, options = {}) {
    let response;
    try {
        response = await fetch(API() + path, options);
    } catch (error) {
        throw new Error('Não foi possível falar com o servidor. A janela do Smart Parking está aberta?');
    }
    if (!response.ok) {
        const data = await response.json().catch(() => ({}));
        throw new Error(data.error || `Erro ${response.status} no servidor.`);
    }
    return response.json();
}

function postJSON(path, body, method = 'POST') {
    return req(path, { method, headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });
}

const esc = text => String(text ?? '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
const num = (v, d = 1) => (v === null || v === undefined || v === '' || isNaN(v)) ? '—' : Number(v).toFixed(d).replace('.', ',');
const pct = v => (v === null || v === undefined) ? '—' : (v * 100).toFixed(2).replace('.', ',') + '%';
const intBR = v => Number(v || 0).toLocaleString('pt-BR');

function fmtTime(seconds) {
    if (seconds === null || seconds === undefined || !isFinite(seconds)) return '—';
    seconds = Math.max(0, Math.round(seconds));
    const h = Math.floor(seconds / 3600), m = Math.floor(seconds % 3600 / 60), s = seconds % 60;
    return (h ? h + ':' + String(m).padStart(2, '0') : String(m).padStart(2, '0')) + ':' + String(s).padStart(2, '0');
}

function fmtDuration(seconds) {
    if (!seconds) return '—';
    const m = Math.floor(seconds / 60), s = Math.round(seconds % 60);
    return m ? `${m} min ${String(s).padStart(2, '0')} s` : `${s} s`;
}

let toastTimer = null;
function toast(message, kind = '') {
    const el = $('toast');
    el.textContent = message;
    el.className = 'toast show ' + kind;
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => { el.className = 'toast ' + kind; }, kind === 'error' ? 6000 : 3000);
}

function debounce(fn, ms) {
    let t = null;
    return (...args) => { clearTimeout(t); t = setTimeout(() => fn(...args), ms); };
}

const frameUrl = (path, t = 0, maxWidth = 0) =>
    `${API()}/api/tests/video-frame?source=${encodeURIComponent(path)}&t=${t}` + (maxWidth ? `&max_width=${maxWidth}` : '');

/** Posição da imagem (object-fit: contain) dentro do palco, e prepara o canvas. */
function fitCanvas(stage, img, canvas) {
    const w = stage.clientWidth, h = stage.clientHeight, dpr = window.devicePixelRatio || 1;
    canvas.width = Math.max(1, Math.round(w * dpr));
    canvas.height = Math.max(1, Math.round(h * dpr));
    const ctx = canvas.getContext('2d');
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.clearRect(0, 0, w, h);
    if (!img.naturalWidth || !img.complete) return { ctx, ready: false };
    const scale = Math.min(w / img.naturalWidth, h / img.naturalHeight);
    return {
        ctx, ready: true, w, h, scale,
        ox: (w - img.naturalWidth * scale) / 2,
        oy: (h - img.naturalHeight * scale) / 2,
    };
}

function drawLabel(ctx, text, x, y, color, bounds) {
    ctx.font = 'bold 12px Segoe UI, Arial';
    const tw = ctx.measureText(text).width + 8;
    const tx = Math.max(bounds.ox + 2, Math.min(bounds.w - tw - 2, x));
    const ty = Math.max(bounds.oy + 15, Math.min(bounds.h - 4, y));
    ctx.fillStyle = color;
    ctx.fillRect(tx - 2, ty - 13, tw, 17);
    ctx.fillStyle = '#111827';
    ctx.fillText(text, tx + 2, ty);
}

function pathPolygon(ctx, points) {
    ctx.beginPath();
    points.forEach(([x, y], i) => i ? ctx.lineTo(x, y) : ctx.moveTo(x, y));
    ctx.closePath();
}

function pointInPolygon(x, y, points) {
    let inside = false;
    for (let i = 0, j = points.length - 1; i < points.length; j = i++) {
        const [xi, yi] = points[i], [xj, yj] = points[j];
        if (((yi > y) !== (yj > y)) && (x < (xj - xi) * (y - yi) / (yj - yi) + xi)) inside = !inside;
    }
    return inside;
}

const regionCount = env => env ? Object.keys(env.regions || {}).length : 0;
const naturalSort = (a, b) => a.localeCompare(b, 'pt-BR', { numeric: true });
// Amostras em 0, i, 2i... enquanto houver vídeo (igual ao test_runner).
const sampleCount = (duration, interval) => Math.max(1, Math.ceil(duration / interval - 1e-9));

/* ---------------------------------------------------------
 * Estado
 * --------------------------------------------------------- */
const S = {
    step: 1,
    videos: [],
    video: null,
    envs: [],
    envName: '',
    job: null,
    polling: null,
    runningJob: null,
    labelVideo: null,
};

const currentEnv = () => S.envs.find(e => e.name === S.envName) || null;

function envCheck() {
    const env = currentEnv(), video = S.video;
    if (!video) return { ok: false, kind: 'info', msg: 'Escolha um vídeo primeiro.' };
    if (!env) return { ok: false, kind: 'info', msg: 'Crie um ambiente: dê um nome e clique em “Definir vagas sobre este vídeo”.' };
    const n = regionCount(env);
    if (!n) return { ok: false, kind: 'warn', msg: 'Este ambiente ainda não tem vagas. Clique em “Definir vagas sobre este vídeo”.' };
    if (env.ref_width && (env.ref_width !== video.width || env.ref_height !== video.height)) {
        return {
            ok: false, kind: 'error', mismatch: true,
            msg: `As vagas deste ambiente foram desenhadas numa imagem de ${env.ref_width}×${env.ref_height}, mas o vídeo é ${video.width}×${video.height}. ` +
                 'Clique em “Ajustar vagas para este vídeo” e confira cada vaga antes de continuar.'
        };
    }
    let msg = `${n} vaga(s) definida(s). Confira na imagem se cada contorno está em cima da vaga real.`;
    if (env.source && env.source !== video.path) {
        msg += ` Este ambiente foi salvo com outro vídeo (${esc(env.source.split('/').pop())}); se for a mesma câmera e ângulo, pode usar.`;
    }
    if (!env.ref_width) msg += ' (Sem quadro de referência salvo para conferir a resolução.)';
    return { ok: true, kind: 'ok', msg };
}

/* ---------------------------------------------------------
 * Etapas
 * --------------------------------------------------------- */
function stepAllowed(n) {
    if (n === 1) return true;
    if (n === 2) return !!S.video;
    if (n === 3) return !!S.video && envCheck().ok;
    if (n === 4) return !!S.job;
    return false;
}

function updateStepper() {
    document.querySelectorAll('.step').forEach(btn => {
        const n = Number(btn.dataset.step);
        btn.disabled = !stepAllowed(n);
        btn.classList.toggle('active', n === S.step);
        btn.classList.remove('done');
    });
    const env = currentEnv();
    const mark = (n, done) => document.querySelector(`.step[data-step="${n}"]`).classList.toggle('done', done && n !== S.step);
    mark(1, !!S.video);
    mark(2, envCheck().ok);
    mark(3, !!S.job);
    $('sum1').textContent = S.video ? S.video.name : 'Escolha o vídeo';
    $('sum2').textContent = env ? `${env.name} · ${regionCount(env)} vaga(s)` : 'Defina as vagas';
    $('sum3').textContent = S.runningJob ? 'Em execução...' : (S.job ? 'Teste concluído' : 'Parâmetros e YOLO');
    $('btnTo2').disabled = !S.video;
    $('btnTo3').disabled = !envCheck().ok;
}

function goStep(n) {
    if (!stepAllowed(n)) return;
    S.step = n;
    document.querySelectorAll('.panel').forEach(p => p.classList.toggle('hidden', Number(p.dataset.panel) !== n));
    if (n === 2) renderEnvPanel();
    if (n === 3) renderRunPanel();
    if (n === 4) requestAnimationFrame(drawAnalysis);
    updateStepper();
    window.scrollTo({ top: 0, behavior: 'smooth' });
}

document.querySelectorAll('.step').forEach(btn => btn.addEventListener('click', () => goStep(Number(btn.dataset.step))));
document.querySelectorAll('[data-goto]').forEach(btn => btn.addEventListener('click', () => goStep(Number(btn.dataset.goto))));

/* ---------------------------------------------------------
 * 1. Vídeo
 * --------------------------------------------------------- */
async function loadVideos(selectPath = null) {
    try {
        const data = await req('/api/tests/videos');
        S.videos = data.videos || [];
        if (data.problems && data.problems.length) {
            console.warn('Vídeos que não abriram:', data.problems);
        }
    } catch (error) {
        $('videoList').innerHTML = `<div class="alert alert-error">${esc(error.message)}</div>`;
        return;
    }
    const wanted = selectPath || (S.video && S.video.path) || localStorage.getItem('exp:video');
    const found = S.videos.find(v => v.path === wanted);
    renderVideoList();
    if (found) selectVideo(found, false);
}

function renderVideoList() {
    const list = $('videoList');
    if (!S.videos.length) {
        list.innerHTML = '<div class="empty">Nenhum vídeo encontrado na pasta data/.<br>Envie um vídeo pelo botão abaixo.</div>';
        return;
    }
    list.innerHTML = S.videos.map((v, i) => `
        <button class="video-item ${S.video && S.video.path === v.path ? 'active' : ''}" data-i="${i}">
            <span class="name" title="${esc(v.path)}">${esc(v.name)}</span>
            <span class="badge">${fmtDuration(v.duration_s)}</span>
            <span class="meta">${v.width}×${v.height} · ${num(v.fps, 0)} fps · ${num(v.size_mb, 1)} MB · ${esc(v.folder || '')}</span>
        </button>`).join('');
    list.querySelectorAll('.video-item').forEach(el => el.addEventListener('click', () => selectVideo(S.videos[Number(el.dataset.i)])));
}

function selectVideo(video, render = true) {
    S.video = video;
    localStorage.setItem('exp:video', video.path);
    if (render) renderVideoList();
    else document.querySelectorAll('.video-item').forEach((el, i) => el.classList.toggle('active', S.videos[i] === video));

    const img = $('videoPreview');
    $('videoStageMsg').textContent = 'Carregando quadro...';
    $('videoStageMsg').classList.remove('hidden');
    img.onload = () => $('videoStageMsg').classList.add('hidden');
    img.onerror = () => { $('videoStageMsg').textContent = 'Não foi possível ler um quadro deste vídeo.'; };
    img.src = frameUrl(video.path, Math.min(1, (video.duration_s || 0) / 2), 960);

    $('videoInfo').innerHTML = [
        ['Duração', fmtDuration(video.duration_s)],
        ['Resolução', `${video.width}×${video.height}`],
        ['FPS', num(video.fps, 2)],
        ['Amostras a cada 5 s', video.duration_s ? intBR(sampleCount(video.duration_s, 5)) : '—'],
    ].map(([k, v]) => `<div><span>${k}</span><b>${v}</b></div>`).join('');

    // Ambiente sugerido: o que já usa este vídeo.
    const match = S.envs.find(e => e.source === video.path);
    if (match) S.envName = match.name;
    updateStepper();
}

$('btnReloadVideos').onclick = () => loadVideos();
$('btnTo2').onclick = () => goStep(2);

$('videoFile').addEventListener('change', event => {
    const file = event.target.files[0];
    event.target.value = '';
    if (!file) return;
    const bar = $('uploadProgress'), text = $('uploadText');
    bar.classList.remove('hidden');
    bar.firstElementChild.style.width = '0%';
    text.textContent = `Enviando ${file.name}...`;

    const form = new FormData();
    form.append('file', file);
    const xhr = new XMLHttpRequest();
    xhr.open('POST', API() + '/api/tests/videos');
    xhr.upload.onprogress = e => {
        if (e.lengthComputable) {
            const p = Math.round(e.loaded / e.total * 100);
            bar.firstElementChild.style.width = p + '%';
            text.textContent = `Enviando ${file.name}... ${p}%`;
        }
    };
    xhr.onload = async () => {
        let data = {};
        try { data = JSON.parse(xhr.responseText); } catch (_) { /* vazio */ }
        bar.classList.add('hidden');
        if (xhr.status !== 200) {
            text.textContent = 'Formatos: mp4, avi, mov, mkv, webm.';
            toast(data.error || 'Falha no envio do vídeo.', 'error');
            return;
        }
        text.textContent = `${data.name} enviado.`;
        toast('Vídeo enviado.', 'ok');
        await loadVideos(data.path);
    };
    xhr.onerror = () => {
        bar.classList.add('hidden');
        toast('Falha no envio. O servidor está rodando?', 'error');
    };
    xhr.send(form);
});

/* ---------------------------------------------------------
 * 2. Vagas (ambiente)
 * --------------------------------------------------------- */
async function loadEnvs(selectName = null) {
    try {
        S.envs = await req('/api/tests/environments');
    } catch (error) {
        toast(error.message, 'error');
        S.envs = [];
    }
    if (selectName) S.envName = selectName;
    if (!S.envs.some(e => e.name === S.envName)) {
        const bySource = S.video && S.envs.find(e => e.source === S.video.path);
        const last = S.envs.find(e => e.name === localStorage.getItem('exp:env'));
        S.envName = (bySource || last || S.envs[0] || {}).name || '';
    }
    renderEnvSelect();
    updateStepper();
}

function renderEnvSelect() {
    const select = $('envSelect');
    const options = S.envs.map(e =>
        `<option value="${esc(e.name)}">${esc(e.name)} — ${regionCount(e)} vaga(s)</option>`).join('');
    select.innerHTML = options + '<option value="__new__">+ Criar novo ambiente...</option>';
    select.value = S.envName || '__new__';
    $('newEnvField').classList.toggle('hidden', select.value !== '__new__');
}

$('envSelect').addEventListener('change', () => {
    const value = $('envSelect').value;
    S.envName = value === '__new__' ? '' : value;
    if (S.envName) localStorage.setItem('exp:env', S.envName);
    $('newEnvField').classList.toggle('hidden', value !== '__new__');
    if (value === '__new__') $('newEnvName').focus();
    renderEnvPanel();
});

function renderEnvPanel() {
    if (!S.video) return;
    const env = currentEnv();
    const check = envCheck();
    const kind = { ok: 'alert-ok', warn: 'alert-warn', error: 'alert-error', info: 'alert-info' }[check.kind];
    $('envStatus').innerHTML = `<div class="alert ${kind}">${check.msg}</div>`;
    $('btnEditVagas').textContent = !env || !regionCount(env)
        ? 'Definir vagas sobre este vídeo'
        : (check.mismatch ? 'Ajustar vagas para este vídeo' : 'Editar vagas');

    const img = $('envPreview');
    const src = frameUrl(S.video.path, Math.min(1, (S.video.duration_s || 0) / 2), 1600);
    img.onload = drawEnvPreview;
    if (img.dataset.src !== src) { img.dataset.src = src; img.src = src; } else drawEnvPreview();
    updateStepper();
}

function drawEnvPreview() {
    const img = $('envPreview');
    const b = fitCanvas($('envStage'), img, $('envCanvas'));
    const env = currentEnv();
    if (!b.ready || !env || !S.video) return;
    // As vagas estão em pixels do quadro de referência; a prévia pode estar reduzida.
    const refW = env.ref_width || S.video.width, refH = env.ref_height || S.video.height;
    const kx = img.naturalWidth / refW, ky = img.naturalHeight / refH;
    Object.entries(env.regions).sort(([a], [c]) => naturalSort(a, c)).forEach(([name, pts]) => {
        const screen = pts.map(([x, y]) => [b.ox + x * kx * b.scale, b.oy + y * ky * b.scale]);
        pathPolygon(b.ctx, screen);
        b.ctx.fillStyle = 'rgba(34,197,94,.18)';
        b.ctx.fill();
        b.ctx.lineWidth = 2.5;
        b.ctx.strokeStyle = '#22c55e';
        b.ctx.stroke();
        drawLabel(b.ctx, name, Math.min(...screen.map(p => p[0])) + 3, Math.min(...screen.map(p => p[1])) + 16, '#22c55e', b);
    });
}

$('btnEditVagas').onclick = () => {
    let name = S.envName;
    let isNew = false;
    if (!name) {
        name = $('newEnvName').value.trim().replace(/\s+/g, '_');
        if (!/^[\p{L}\p{N}_-]{1,60}$/u.test(name)) {
            toast('Dê um nome ao novo ambiente (letras, números, - e _).', 'error');
            $('newEnvName').focus();
            return;
        }
        if (S.envs.some(e => e.name === name)) {
            toast('Já existe um ambiente com esse nome. Escolha-o na lista.', 'error');
            return;
        }
        isNew = true;
    }
    openEditor(name, isNew);
};
$('btnTo3').onclick = () => goStep(3);

/* ---------------------------------------------------------
 * Editor de vagas (modal)
 * --------------------------------------------------------- */
const E = {
    name: '', isNew: false, regions: {}, current: [], selected: null,
    drag: null, suppressClick: false, mouse: null, t: 0, dirty: false, bounds: null, k: 1,
};

function openModal(id) { $(id).classList.add('open'); }
function closeModal(id) { $(id).classList.remove('open'); }
document.querySelectorAll('[data-close]').forEach(btn => btn.addEventListener('click', () => {
    if (btn.dataset.close === 'roiModal') return closeEditor();
    closeModal(btn.dataset.close);
}));

function openEditor(name, isNew) {
    const env = currentEnv();
    E.name = name;
    E.isNew = isNew;
    E.current = [];
    E.selected = null;
    E.dirty = false;
    E.regions = {};

    if (env && regionCount(env)) {
        // Se as vagas foram desenhadas em outra resolução, já traz convertidas.
        const sx = env.ref_width ? S.video.width / env.ref_width : 1;
        const sy = env.ref_height ? S.video.height / env.ref_height : 1;
        Object.entries(env.regions).forEach(([n, pts]) => {
            E.regions[n] = pts.map(([x, y]) => [x * sx, y * sy]);
        });
        if (sx !== 1 || sy !== 1) E.dirty = true;
    }
    const det = (env && env.detection) || {};
    $('roiProjection').value = det.projection || 'perspective';
    $('roiFov').value = det.fov || 180;
    $('roiStabilize').checked = !!det.stabilize;
    updateFovVisibility();

    const dur = S.video.duration_s || 0;
    $('roiTime').max = Math.max(0, dur - 0.1).toFixed(1);
    E.t = Math.min(1, dur / 2);
    $('roiTime').value = E.t;
    $('roiTimeText').textContent = fmtTime(E.t);
    $('roiTitle').textContent = `Vagas do ambiente “${name}”${isNew ? ' (novo)' : ''} — vídeo ${S.video.name}`;
    $('roiFootInfo').textContent = `As coordenadas ficam em pixels do vídeo (${S.video.width}×${S.video.height}). O quadro escolhido vira a imagem de referência do ambiente.`;
    openModal('roiModal');
    loadEditorFrame();
    renderEditorList();
}

function closeEditor(force = false) {
    if (!force && (E.dirty || E.current.length) && !confirm('Descartar as alterações nas vagas?')) return;
    closeModal('roiModal');
}

function updateFovVisibility() {
    const p = $('roiProjection').value;
    $('roiFov').classList.toggle('hidden', !(p === 'fisheye' || p === 'cylindrical'));
}
$('roiProjection').addEventListener('change', () => { updateFovVisibility(); E.dirty = true; });
$('roiStabilize').addEventListener('change', () => { E.dirty = true; });
$('roiFov').addEventListener('change', () => { E.dirty = true; });

function loadEditorFrame() {
    const img = $('roiImage');
    $('roiStageMsg').classList.remove('hidden');
    img.onload = () => { $('roiStageMsg').classList.add('hidden'); drawEditor(); };
    img.onerror = () => { $('roiStageMsg').textContent = 'Não foi possível ler este quadro.'; };
    img.src = frameUrl(S.video.path, E.t);
}
const reloadFrameDebounced = debounce(loadEditorFrame, 250);
$('roiTime').addEventListener('input', () => {
    E.t = Number($('roiTime').value);
    $('roiTimeText').textContent = fmtTime(E.t);
    reloadFrameDebounced();
});

function nextVagaName() {
    let max = 0;
    Object.keys(E.regions).forEach(n => {
        const m = /^vaga-(\d+)$/i.exec(n);
        if (m) max = Math.max(max, Number(m[1]));
    });
    return 'vaga-' + String(max + 1).padStart(2, '0');
}

const toScreen = ([x, y]) => [E.bounds.ox + x * E.k * E.bounds.scale, E.bounds.oy + y * E.k * E.bounds.scale];
function toVideo(sx, sy) {
    const img = $('roiImage');
    const x = (sx - E.bounds.ox) / E.bounds.scale, y = (sy - E.bounds.oy) / E.bounds.scale;
    const cx = Math.max(0, Math.min(img.naturalWidth, x)), cy = Math.max(0, Math.min(img.naturalHeight, y));
    return [cx / E.k, cy / E.k];
}

function drawEditor() {
    const img = $('roiImage');
    const b = fitCanvas($('roiStage'), img, $('roiCanvas'));
    if (!b.ready) return;
    E.bounds = b;
    E.k = img.naturalWidth / S.video.width;
    const ctx = b.ctx;

    Object.keys(E.regions).sort(naturalSort).forEach(name => {
        const screen = E.regions[name].map(toScreen);
        const sel = name === E.selected;
        pathPolygon(ctx, screen);
        ctx.fillStyle = sel ? 'rgba(59,130,246,.25)' : 'rgba(34,197,94,.16)';
        ctx.fill();
        ctx.lineWidth = sel ? 3 : 2;
        ctx.strokeStyle = sel ? '#3b82f6' : '#22c55e';
        ctx.stroke();
        screen.forEach(([x, y]) => {
            ctx.beginPath();
            ctx.arc(x, y, 4.5, 0, Math.PI * 2);
            ctx.fillStyle = '#fff';
            ctx.fill();
            ctx.lineWidth = 2;
            ctx.stroke();
        });
        drawLabel(ctx, name, Math.min(...screen.map(p => p[0])) + 3, Math.min(...screen.map(p => p[1])) + 16, sel ? '#93c5fd' : '#22c55e', b);
    });

    if (E.current.length) {
        const screen = E.current.map(toScreen);
        ctx.beginPath();
        screen.forEach(([x, y], i) => i ? ctx.lineTo(x, y) : ctx.moveTo(x, y));
        if (E.mouse) ctx.lineTo(E.mouse[0], E.mouse[1]);
        ctx.lineWidth = 2;
        ctx.setLineDash([6, 4]);
        ctx.strokeStyle = '#f59e0b';
        ctx.stroke();
        ctx.setLineDash([]);
        screen.forEach(([x, y], i) => {
            ctx.beginPath();
            ctx.arc(x, y, i === 0 ? 7 : 4.5, 0, Math.PI * 2);
            ctx.fillStyle = i === 0 ? '#f59e0b' : '#fff';
            ctx.fill();
            ctx.strokeStyle = '#f59e0b';
            ctx.stroke();
        });
    }

    $('roiHint').textContent = E.current.length
        ? `Desenhando ${nextVagaName()}: ${E.current.length} ponto(s). ` + (E.current.length >= 3 ? 'Clique no primeiro ponto (laranja) ou aperte Enter para fechar.' : 'Continue clicando nos cantos.')
        : 'Clique no primeiro canto de uma vaga para começar a desenhar.';
}

function renderEditorList() {
    const names = Object.keys(E.regions).sort(naturalSort);
    $('roiCount').textContent = names.length;
    $('roiList').innerHTML = names.length ? names.map(n => `
        <div class="vaga-item ${n === E.selected ? 'sel' : ''}" data-name="${esc(n)}">
            <span class="swatch"></span>
            <input value="${esc(n)}" data-old="${esc(n)}" maxlength="40" title="Clique para renomear">
            <button class="btn btn-sm btn-danger" data-del="${esc(n)}" title="Excluir vaga">×</button>
        </div>`).join('') : '<div class="empty" style="padding:1rem">Nenhuma vaga desenhada.</div>';
}

$('roiList').addEventListener('mouseover', e => {
    const item = e.target.closest('.vaga-item');
    const name = item ? item.dataset.name : null;
    if (name !== E.selected) { E.selected = name; drawEditor(); }
});
$('roiList').addEventListener('click', e => {
    const del = e.target.closest('[data-del]');
    if (!del) return;
    delete E.regions[del.dataset.del];
    E.selected = null;
    E.dirty = true;
    renderEditorList();
    drawEditor();
});
$('roiList').addEventListener('change', e => {
    const input = e.target.closest('input[data-old]');
    if (!input) return;
    const oldName = input.dataset.old, newName = input.value.trim();
    if (!newName || newName === '__detection__' || (newName !== oldName && E.regions[newName])) {
        toast('Nome inválido ou repetido.', 'error');
        input.value = oldName;
        return;
    }
    if (newName !== oldName) {
        E.regions[newName] = E.regions[oldName];
        delete E.regions[oldName];
        E.dirty = true;
        renderEditorList();
        drawEditor();
    }
});
$('roiList').addEventListener('keydown', e => { if (e.key === 'Enter') e.target.blur(); });

function closeCurrentPolygon() {
    // Remove pontos repetidos (duplo clique gera dois cliques no mesmo lugar).
    const pts = E.current.filter((p, i, arr) => i === 0 || Math.hypot(p[0] - arr[i - 1][0], p[1] - arr[i - 1][1]) > 2);
    if (pts.length < 3) {
        toast('Uma vaga precisa de pelo menos 3 pontos.', 'error');
        return;
    }
    const name = nextVagaName();
    E.regions[name] = pts;
    E.current = [];
    E.dirty = true;
    renderEditorList();
    drawEditor();
}

function hitVertex(sx, sy) {
    for (const name of Object.keys(E.regions)) {
        const pts = E.regions[name];
        for (let i = 0; i < pts.length; i++) {
            const [x, y] = toScreen(pts[i]);
            if (Math.hypot(x - sx, y - sy) <= 8) return { name, i };
        }
    }
    return null;
}

const canvasPos = (canvas, e) => { const r = canvas.getBoundingClientRect(); return [e.clientX - r.left, e.clientY - r.top]; };
const roiCanvas = $('roiCanvas');

roiCanvas.addEventListener('mousedown', e => {
    if (!E.bounds || E.current.length) return;
    const [sx, sy] = canvasPos(roiCanvas, e);
    const hit = hitVertex(sx, sy);
    if (hit) { E.drag = { ...hit, moved: false }; e.preventDefault(); }
});
window.addEventListener('mousemove', e => {
    if (!$('roiModal').classList.contains('open') || !E.bounds) return;
    const [sx, sy] = canvasPos(roiCanvas, e);
    if (E.drag) {
        E.regions[E.drag.name][E.drag.i] = toVideo(sx, sy);
        E.drag.moved = true;
        E.dirty = true;
        drawEditor();
        return;
    }
    if (E.current.length) { E.mouse = [sx, sy]; drawEditor(); }
    roiCanvas.style.cursor = !E.current.length && hitVertex(sx, sy) ? 'move' : 'crosshair';
});
window.addEventListener('mouseup', () => {
    if (E.drag) {
        E.suppressClick = E.drag.moved;
        E.drag = null;
    }
});
roiCanvas.addEventListener('click', e => {
    if (E.suppressClick) { E.suppressClick = false; return; }
    if (!E.bounds) return;
    const [sx, sy] = canvasPos(roiCanvas, e);
    if (E.current.length >= 3) {
        const [fx, fy] = toScreen(E.current[0]);
        if (Math.hypot(fx - sx, fy - sy) <= 10) { closeCurrentPolygon(); return; }
    }
    E.current.push(toVideo(sx, sy));
    drawEditor();
});
roiCanvas.addEventListener('dblclick', e => { e.preventDefault(); if (E.current.length >= 3) closeCurrentPolygon(); });

$('roiClose').onclick = () => closeCurrentPolygon();
$('roiUndo').onclick = () => { E.current.pop(); drawEditor(); };
$('roiClearAll').onclick = () => {
    if (!Object.keys(E.regions).length || !confirm('Apagar todas as vagas deste ambiente?')) return;
    E.regions = {};
    E.current = [];
    E.dirty = true;
    renderEditorList();
    drawEditor();
};

$('roiSave').onclick = async () => {
    if (E.current.length >= 3) closeCurrentPolygon();
    else if (E.current.length) { toast('Termine (ou cancele com Esc) a vaga que está desenhando.', 'error'); return; }
    if (!Object.keys(E.regions).length) { toast('Desenhe pelo menos uma vaga.', 'error'); return; }
    const projection = $('roiProjection').value;
    const button = $('roiSave');
    button.disabled = true;
    button.textContent = 'Salvando...';
    try {
        await postJSON('/api/tests/environments/' + encodeURIComponent(E.name), {
            source: S.video.path,
            t: E.t,
            regions: E.regions,
            detection: {
                projection,
                fov: (projection === 'fisheye' || projection === 'cylindrical') ? Number($('roiFov').value) || 180 : null,
                stabilize: $('roiStabilize').checked,
            },
        });
        localStorage.setItem('exp:env', E.name);
        await loadEnvs(E.name);
        closeEditor(true);
        renderEnvPanel();
        toast(`Vagas salvas no ambiente ${E.name}.`, 'ok');
    } catch (error) {
        toast(error.message, 'error');
    } finally {
        button.disabled = false;
        button.textContent = 'Salvar vagas';
    }
};

/* ---------------------------------------------------------
 * 3. Execução
 * --------------------------------------------------------- */
function speedFactor() {
    const v = $('runSpeed').value;
    const f = v === 'outro' ? Number($('runSpeedOther').value) : Number(v);
    return f > 0 ? f : 1;
}

function renderRunPanel() {
    const env = currentEnv(), video = S.video;
    if (!env || !video) return;
    if (S.labelVideo !== video.path) {
        $('runLabel').value = video.name.replace(/\.[^.]+$/, '');
        S.labelVideo = video.path;
    }
    const cameras = { perspective: 'Convencional', equirectangular: '360° equirretangular', fisheye: '360° olho de peixe', cylindrical: 'Panorâmica cilíndrica' };
    $('runSummary').innerHTML = [
        ['Vídeo', esc(video.name)],
        ['Duração', fmtDuration(video.duration_s)],
        ['Resolução', `${video.width}×${video.height} · ${num(video.fps, 2)} fps`],
        ['Ambiente', esc(env.name)],
        ['Vagas', regionCount(env)],
        ['Câmera', cameras[(env.detection || {}).projection] || 'Convencional'],
    ].map(([k, v]) => `<dt>${k}</dt><dd>${v}</dd>`).join('');
    updateEstimate();
}

function updateEstimate() {
    const env = currentEnv(), video = S.video;
    if (!env || !video) return;
    const real = Number($('runInterval').value);
    const speed = speedFactor();
    if (!(real > 0)) { $('runEstimate').innerHTML = 'Informe um intervalo maior que zero.'; return; }
    const fileInterval = real / speed;
    const samples = video.duration_s ? sampleCount(video.duration_s, fileInterval) : null;
    const vagas = regionCount(env);
    $('runEstimate').innerHTML =
        (samples ? `<b>${intBR(samples)}</b> amostras × <b>${vagas}</b> vagas = <b>${intBR(samples * vagas)}</b> observações para anotar.<br>` : '') +
        (speed !== 1 ? `Como o vídeo está ${num(speed, 1)}× mais rápido, a amostra é tirada a cada <b>${num(fileInterval, 2)} s do arquivo</b> (= ${num(real, 1)} s reais).<br>` : '') +
        `<span class="muted">Referência: Rocha (2025) teve 22.032 observações (18 vagas, 1h45min, a cada 5 s).</span>`;
}

['runInterval', 'runSpeedOther'].forEach(id => $(id).addEventListener('input', updateEstimate));
$('runSpeed').addEventListener('change', () => {
    $('runSpeedOther').classList.toggle('hidden', $('runSpeed').value !== 'outro');
    updateEstimate();
});
$('runConfidence').addEventListener('input', () => { $('runConfidenceValue').textContent = num($('runConfidence').value, 2); });

function setRunning(running) {
    $('btnStart').disabled = running;
    $('btnStart').textContent = running ? 'Processando...' : '▶ Iniciar teste';
    $('runStatus').classList.toggle('hidden', false);
    $('btnCancel').classList.toggle('hidden', !running);
    ['runLabel', 'runInterval', 'runSpeed', 'runSpeedOther', 'runConfidence', 'runCriterion', 'runStabilize', 'runTracking']
        .forEach(id => { $(id).disabled = running; });
}

$('btnStart').onclick = async () => {
    const env = currentEnv();
    const real = Number($('runInterval').value);
    if (!(real > 0)) { toast('Informe um intervalo maior que zero.', 'error'); return; }
    if ($('runSpeed').value === 'outro' && !(Number($('runSpeedOther').value) > 0)) {
        toast('Informe o fator de aceleração do vídeo.', 'error');
        return;
    }
    try {
        const data = await postJSON('/api/tests', {
            environment: env.name,
            source: S.video.path,
            label: $('runLabel').value.trim(),
            confidence: Number($('runConfidence').value),
            interval_real_s: real,
            speed_factor: speedFactor(),
            frame_step: 1,
            criterion: $('runCriterion').value,
            stabilize: $('runStabilize').value,
            tracking: $('runTracking').checked,
        });
        S.runningJob = data.id;
        $('runStatusText').textContent = 'Carregando o modelo YOLO...';
        $('runBar').style.width = '0%';
        $('runPct').textContent = '0%';
        $('runDetail').textContent = '';
        setRunning(true);
        updateStepper();
        pollRun();
    } catch (error) {
        toast(error.message, 'error');
    }
};

$('btnCancel').onclick = async () => {
    if (!S.runningJob || !confirm('Cancelar este teste?')) return;
    try { await postJSON(`/api/tests/${S.runningJob}/cancel`, {}); } catch (error) { toast(error.message, 'error'); }
};

async function pollRun() {
    clearTimeout(S.polling);
    const id = S.runningJob;
    if (!id) return;
    let data;
    try {
        data = await req('/api/tests/' + id);
    } catch (error) {
        $('runStatusText').textContent = error.message;
        S.polling = setTimeout(pollRun, 2000);
        return;
    }
    const p = Number(data.progress) || 0;
    $('runBar').style.width = p + '%';
    $('runPct').textContent = p + '%';

    if (data.status === 'running') {
        $('runStatusText').textContent = data.rows ? `Analisando... ${intBR(data.rows)} amostra(s) processada(s)` : 'Carregando o modelo YOLO...';
        const eta = p > 2 && data.elapsed_s ? data.elapsed_s * (100 - p) / p : null;
        $('runDetail').textContent = `Tempo decorrido ${fmtTime(data.elapsed_s)}` + (eta ? ` · faltam ~${fmtTime(eta)}` : '');
        S.polling = setTimeout(pollRun, 1000);
        return;
    }

    S.runningJob = null;
    setRunning(false);
    if (data.status === 'completed') {
        $('runStatusText').textContent = `Concluído: ${intBR(data.rows)} amostras.`;
        $('runBar').style.width = '100%';
        toast('Teste concluído. Agora faça o gabarito.', 'ok');
        await openJob(id);
    } else {
        $('runStatusText').textContent = data.status === 'cancelled' ? 'Teste cancelado.' : 'Erro: ' + (data.error || 'falha desconhecida');
        $('runBar').style.width = '0%';
        if (data.status !== 'cancelled') toast(data.error || 'O teste falhou.', 'error');
    }
    updateStepper();
}

/* ---------------------------------------------------------
 * 4. Análise (gabarito manual + métricas)
 * --------------------------------------------------------- */
const A = {
    page: [], offset: 0, total: 0, regions: [], regionPolygons: {},
    pos: 0, truth: {}, dirty: false, prefilled: false, hit: [],
    overview: null, metadata: null,
};

$('anShowYolo').checked = localStorage.getItem('exp:showYolo') === '1';
$('anCarry').checked = localStorage.getItem('exp:carry') !== '0';
$('anShowYolo').onchange = () => { localStorage.setItem('exp:showYolo', $('anShowYolo').checked ? '1' : '0'); renderSample(); };
$('anCarry').onchange = () => localStorage.setItem('exp:carry', $('anCarry').checked ? '1' : '0');

const sampleAt = () => A.page[A.pos - A.offset];
const evaluable = (s, n) => s.detections[n] !== 'fora_de_vista';

async function openJob(id) {
    const status = await req('/api/tests/' + id).catch(error => { toast(error.message, 'error'); return null; });
    if (!status) return;
    if (status.status === 'running') {
        S.runningJob = id;
        setRunning(true);
        S.step = 3;
        document.querySelectorAll('.panel').forEach(p => p.classList.toggle('hidden', Number(p.dataset.panel) !== 3));
        updateStepper();
        pollRun();
        return;
    }
    if (status.status !== 'completed') {
        toast('Este teste não terminou com sucesso: ' + (status.error || status.status), 'error');
        return;
    }
    S.job = id;
    history.replaceState(null, '', '/tests?job=' + encodeURIComponent(id));
    A.page = [];
    A.metadata = await req(`/api/tests/${id}/metadata`).catch(() => ({}));
    renderAnalysisHead();
    await loadPageFor(0);
    await refreshOverview();
    const first = A.overview ? A.overview.complete.indexOf(0) : -1;
    await goSample(first > 0 ? first : 0, first > 0 ? await truthOf(first - 1) : null);
    refreshMetrics();
    goStep(4);
}

function renderAnalysisHead() {
    const md = A.metadata || {};
    const criteria = { sobreposicao: 'sobreposição ≥ 15%', centro: 'centro da caixa' };
    $('anTitle').textContent = md.label || `Teste ${S.job}`;
    const interval = md.interval_real_s
        ? `a cada ${num(md.interval_real_s, 1)} s` + (md.speed_factor && md.speed_factor !== 1 ? ` (vídeo ${num(md.speed_factor, 1)}×)` : '')
        : (md.interval_s ? `a cada ${num(md.interval_s, 1)} s` : `a cada ${md.frame_step} quadros`);
    $('anSubtitle').textContent = [
        (md.source || '').split(/[\\/]/).pop(), md.environment, interval,
        criteria[md.occupancy_criterion] || '', `confiança ${num(md.confidence, 2)}`,
        md.created_at ? new Date(md.created_at * 1000).toLocaleString('pt-BR') : '',
    ].filter(Boolean).join(' · ');
    $('anDownloadData').href = `${API()}/api/tests/${S.job}/download`;
    $('anDownloadMetrics').href = `${API()}/api/tests/${S.job}/metrics/download`;
}

async function loadPageFor(pos) {
    const offset = Math.floor(pos / PAGE_SIZE) * PAGE_SIZE;
    if (A.page.length && offset === A.offset) return;
    const data = await req(`/api/tests/${S.job}/samples?offset=${offset}&limit=${PAGE_SIZE}`);
    A.page = data.samples;
    A.offset = data.offset;
    A.total = data.total;
    A.regions = data.regions;
    A.regionPolygons = data.region_polygons || {};
}

async function truthOf(pos) {
    if (pos < 0) return null;
    const offset = Math.floor(pos / PAGE_SIZE) * PAGE_SIZE;
    if (A.page.length && offset === A.offset) return { ...(A.page[pos - offset].truth || {}) };
    const data = await req(`/api/tests/${S.job}/samples?offset=${pos}&limit=1`);
    return data.samples.length ? { ...(data.samples[0].truth || {}) } : null;
}

async function goSample(pos, carry = null) {
    if (!S.job || pos < 0 || (A.total && pos >= A.total)) return;
    if (A.dirty && !A.prefilled && !confirm('Há marcações não salvas nesta amostra. Descartar?')) return;
    await loadPageFor(pos);
    A.pos = pos;
    const s = sampleAt();
    A.truth = { ...(s.truth || {}) };
    A.dirty = false;
    A.prefilled = false;
    if (carry && $('anCarry').checked && !Object.keys(A.truth).length) {
        A.regions.forEach(n => { if (evaluable(s, n) && n in carry) A.truth[n] = carry[n]; });
        A.prefilled = Object.keys(A.truth).length > 0;
        A.dirty = A.prefilled;
    }
    $('anGoto').value = '';
    renderSample();
}

function renderSample() {
    const s = sampleAt();
    if (!s) return;
    const img = $('anImage');
    const src = `${API()}/api/tests/${S.job}/samples/${encodeURIComponent(s.image)}`;
    img.onload = drawAnalysis;
    if (img.dataset.src !== src) { img.dataset.src = src; img.src = src; } else drawAnalysis();

    const time = s.timestamp_s !== '' ? ' · ' + fmtTime(Number(s.timestamp_s)) + ' do vídeo' : '';
    $('anInfo').textContent = `Amostra ${s.sample + 1} de ${A.total}${time}`;

    const showYolo = $('anShowYolo').checked;
    $('anRegions').innerHTML = A.regions.map((n, i) => {
        if (!evaluable(s, n)) {
            return `<div class="region-row"><span class="rname">${esc(n)}</span><span class="badge">fora de vista</span></div>`;
        }
        const t = A.truth[n];
        const yolo = showYolo ? `<span class="badge ${s.detections[n] === 'ocupada' ? 'badge-red' : 'badge-green'}" title="Previsão do YOLO">Y: ${s.detections[n]}</span>` : '';
        return `<div class="region-row ${t === undefined ? 'unmarked' : ''}">
            <span class="rname">${i < 9 ? `<span class="kbd">${i + 1}</span>` : ''}${esc(n)} ${yolo}</span>
            <span class="seg">
                <button data-r="${esc(n)}" data-v="true" class="${t === true ? 'on-occ' : ''}">Ocupada</button>
                <button data-r="${esc(n)}" data-v="false" class="${t === false ? 'on-free' : ''}">Livre</button>
                <button data-r="${esc(n)}" data-v="ign" class="${t === IGNORE ? 'on-ign' : ''}" title="Não dá para saber pela imagem">?</button>
            </span></div>`;
    }).join('');

    $('anPrefill').classList.toggle('hidden', !A.prefilled);
    $('anPrev').disabled = A.pos === 0;
    $('anNext').disabled = A.pos >= A.total - 1;
    $('anSaveStatus').textContent = A.dirty && !A.prefilled ? 'Alteração não salva.' : '';
    renderTimeline();
}

$('anRegions').addEventListener('click', e => {
    const b = e.target.closest('button[data-r]');
    if (!b) return;
    setTruth(b.dataset.r, b.dataset.v === 'true' ? true : b.dataset.v === 'false' ? false : IGNORE);
});

function setTruth(name, value) {
    A.truth[name] = value;
    A.dirty = true;
    A.prefilled = false;
    renderSample();
}

function toggleRegion(name) {
    const s = sampleAt();
    if (!s || !evaluable(s, name)) return;
    setTruth(name, A.truth[name] === true ? false : true);
}

function drawAnalysis() {
    const s = sampleAt();
    const img = $('anImage');
    const b = fitCanvas($('anStage'), img, $('anCanvas'));
    A.hit = [];
    if (!b.ready || !s) return;
    const polys = s.rois || Object.fromEntries(Object.entries(A.regionPolygons).map(([n, p]) => [n, [p]]));
    const showYolo = $('anShowYolo').checked;

    Object.entries(polys).forEach(([name, parts]) => {
        const t = A.truth[name];
        const hidden = !evaluable(s, name);
        const color = hidden || t === IGNORE ? '#9ca3af' : t === true ? '#ef4444' : t === false ? '#22c55e' : '#f59e0b';
        let lx = Infinity, ly = Infinity, best = 0;
        (parts || []).forEach(points => {
            if (!points || points.length < 3) return;
            const screen = points.map(([x, y]) => [b.ox + x * b.scale, b.oy + y * b.scale]);
            A.hit.push({ name, screen });
            pathPolygon(b.ctx, screen);
            b.ctx.setLineDash(t === undefined && !hidden ? [6, 4] : []);
            b.ctx.fillStyle = color + '33';
            b.ctx.fill();
            b.ctx.lineWidth = 2.5;
            b.ctx.strokeStyle = color;
            b.ctx.stroke();
            b.ctx.setLineDash([]);
            const area = Math.abs(screen.reduce((acc, [x, y], k) => {
                const [nx, ny] = screen[(k + 1) % screen.length]; return acc + x * ny - nx * y; }, 0));
            if (area > best) { best = area; lx = Math.min(...screen.map(p => p[0])); ly = Math.min(...screen.map(p => p[1])); }
        });
        if (!isFinite(lx)) return;
        let label = name;
        if (showYolo && !hidden) label += ' · Y:' + (s.detections[name] === 'ocupada' ? 'ocup.' : 'livre');
        drawLabel(b.ctx, label, lx + 3, ly + 16, color, b);
    });
}

$('anCanvas').addEventListener('click', e => {
    const [x, y] = canvasPos($('anCanvas'), e);
    const hit = A.hit.find(h => pointInPolygon(x, y, h.screen));
    if (hit) toggleRegion(hit.name);
});

async function save() {
    const s = sampleAt();
    if (!s) return false;
    const pending = A.regions.filter(n => evaluable(s, n) && !(n in A.truth));
    if (pending.length && !confirm(`${pending.length} vaga(s) sem marcação (${pending.join(', ')}). Salvar mesmo assim?`)) return false;
    const annotations = {};
    A.regions.forEach(n => { annotations[n] = n in A.truth ? A.truth[n] : null; });
    try {
        const result = await postJSON(`/api/tests/${S.job}/ground-truth`, { annotations: { [s.sample]: annotations } });
        s.truth = { ...A.truth };
        A.dirty = false;
        A.prefilled = false;
        $('anSaveStatus').textContent = `Amostra ${s.sample + 1} salva.`;
        updateOverviewFor(s);
        renderMetrics(result.metrics);
        return true;
    } catch (error) {
        $('anSaveStatus').textContent = 'Erro ao salvar: ' + error.message;
        toast(error.message, 'error');
        return false;
    }
}

async function saveAndNext() {
    if (!(await save())) return;
    if (A.pos < A.total - 1) await goSample(A.pos + 1, { ...A.truth });
    else { renderSample(); toast('Última amostra salva.', 'ok'); }
}

$('anSaveNext').onclick = saveAndNext;
$('anPrev').onclick = () => goSample(A.pos - 1);
$('anNext').onclick = () => goSample(A.pos + 1, { ...A.truth });
$('anFirstPending').onclick = async () => {
    await refreshOverview();
    const first = A.overview.complete.indexOf(0);
    if (first < 0) { toast('Todas as amostras estão anotadas.', 'ok'); return; }
    await goSample(first, await truthOf(first - 1));
};
$('anGoto').addEventListener('keydown', e => {
    if (e.key !== 'Enter') return;
    e.preventDefault();
    e.stopPropagation();
    const n = parseInt(e.target.value, 10);
    if (n >= 1 && n <= A.total) goSample(n - 1);
});

/* linha do tempo */
async function refreshOverview() {
    try { A.overview = await req(`/api/tests/${S.job}/overview`); }
    catch (error) { A.overview = null; }
    renderTimeline();
}

function updateOverviewFor(s) {
    if (!A.overview) return;
    const i = s.sample;
    const marked = A.regions.filter(n => evaluable(s, n) && n in s.truth);
    const evaluableCount = A.regions.filter(n => evaluable(s, n)).length;
    A.overview.complete[i] = marked.length === evaluableCount ? 1 : 0;
    A.overview.partial[i] = marked.length ? 1 : 0;
    A.overview.errors[i] = marked.filter(n => typeof s.truth[n] === 'boolean' && s.truth[n] !== (s.detections[n] === 'ocupada')).length;
}

function renderTimeline() {
    const ov = A.overview;
    if (!ov) { $('anTimeline').innerHTML = ''; return; }
    const done = ov.complete.reduce((a, b) => a + b, 0);
    $('anProgressBadge').textContent = `${intBR(done)} de ${intBR(ov.total)} amostras anotadas`;
    $('anProgressBadge').className = 'badge ' + (done === ov.total ? 'badge-green' : 'badge-amber');
    document.querySelector('.step[data-step="4"]').classList.toggle('done', done === ov.total && S.step !== 4);
    $('sum4').textContent = `${Math.round(done / (ov.total || 1) * 100)}% anotado`;

    const html = [];
    for (let i = 0; i < ov.total; i++) {
        let cls = '';
        if (ov.complete[i]) cls = ov.errors[i] ? 'err' : 'done';
        else if (ov.partial[i]) cls = 'partial';
        if (i === A.pos) cls += ' current';
        const t = ov.errors[i] ? ` · ${ov.errors[i]} erro(s) do YOLO` : '';
        html.push(`<button class="${cls}" data-i="${i}" title="Amostra ${i + 1}${t}"></button>`);
    }
    $('anTimeline').innerHTML = html.join('');
}
$('anTimeline').addEventListener('click', e => {
    const b = e.target.closest('button[data-i]');
    if (b) goSample(Number(b.dataset.i));
});

/* métricas */
async function refreshMetrics() {
    try { renderMetrics(await req(`/api/tests/${S.job}/metrics`)); }
    catch (error) { $('anMetricsNote').textContent = error.message; }
}

function renderMetrics(m) {
    if (!m) return;
    const o = m.overall || {}, r = m.rocha_2025 || {};
    $('anConfusion').innerHTML = `
        <div class="h"></div><div class="h">Previsto ocupada</div><div class="h">Previsto livre</div>
        <div class="h">Real ocupada</div><div class="ok">TP ${intBR(o.tp)}</div><div class="bad">FN ${intBR(o.fn)}</div>
        <div class="h">Real livre</div><div class="bad">FP ${intBR(o.fp)}</div><div class="ok">TN ${intBR(o.tn)}</div>`;
    const rows = [
        ['Acurácia', o.accuracy, r.accuracy],
        ['Precisão', o.precision, r.precision],
        ['Recall', o.recall, r.recall],
        ['Taxa de falsos positivos', o.false_positive_rate, r.false_positive_rate],
        ['F1', o.f1, r.f1],
        ['Precisão (livre = positiva)', o.precision_livre, r.precision_livre],
    ];
    $('anMetrics').innerHTML = '<thead><tr><th></th><th>Este teste</th><th>Rocha (2025)</th></tr></thead><tbody>' +
        rows.map(([k, a, b]) => `<tr><td>${k}</td><td><b>${pct(a)}</b></td><td>${pct(b)}</td></tr>`).join('') + '</tbody>';
    const tempo = m.tempo_por_quadro_ms ? ` Tempo médio por quadro: ${num(m.tempo_por_quadro_ms.media, 1)} ms.` : '';
    $('anMetricsNote').textContent =
        `${intBR(m.annotated_cells)} de ${intBR(m.total_cells)} observações anotadas` +
        (m.ignored_cells ? `, ${m.ignored_cells} indeterminada(s)` : '') +
        (m.out_of_view_cells ? `, ${m.out_of_view_cells} fora de vista` : '') + '.' + tempo +
        ' Classe positiva = vaga ocupada. A precisão de 60,45% de Rocha corresponde à linha “livre = positiva”.';
    $('anPerRegion').innerHTML = '<table class="table"><thead><tr><th>Vaga</th><th>N</th><th>Acc.</th><th>Prec.</th><th>Rec.</th><th>FPR</th></tr></thead><tbody>' +
        Object.entries(m.per_region || {}).map(([n, v]) =>
            `<tr><td>${esc(n)}</td><td>${v.total}</td><td>${pct(v.accuracy)}</td><td>${pct(v.precision)}</td><td>${pct(v.recall)}</td><td>${pct(v.false_positive_rate)}</td></tr>`).join('') +
        '</tbody></table>';
}

/* ---------------------------------------------------------
 * Histórico e relatório
 * --------------------------------------------------------- */
let selectedForReport = [];

$('btnHistory').onclick = () => { openModal('historyModal'); loadHistory(); };

async function loadHistory() {
    const wrap = $('historyTable');
    wrap.innerHTML = '<div class="empty">Carregando...</div>';
    let jobs;
    try { jobs = await req('/api/tests'); }
    catch (error) { wrap.innerHTML = `<div class="alert alert-error">${esc(error.message)}</div>`; return; }
    selectedForReport = selectedForReport.filter(id => jobs.some(j => j.id === id && j.status === 'completed'));
    if (!jobs.length) { wrap.innerHTML = '<div class="empty">Nenhum teste executado ainda.</div>'; updateReportButton(); return; }

    const statusBadge = {
        completed: '<span class="badge badge-green">concluído</span>',
        running: '<span class="badge badge-blue">em execução</span>',
        cancelled: '<span class="badge">cancelado</span>',
        error: '<span class="badge badge-red">erro</span>',
    };
    wrap.innerHTML = `<table class="table"><thead><tr>
        <th></th><th>Teste</th><th>Ambiente</th><th>Vídeo</th><th>Data</th><th>Amostras</th><th>Anotado</th><th>Acurácia</th><th>Precisão</th><th>Status</th><th></th>
        </tr></thead><tbody>` + jobs.map(j => {
            const order = selectedForReport.indexOf(j.id);
            const can = j.status === 'completed';
            const annotated = j.annotated_fraction === null || j.annotated_fraction === undefined ? '—' : Math.round(j.annotated_fraction * 100) + '%';
            return `<tr title="${esc(j.error || '')}">
                <td class="nowrap">${can ? `<label class="check"><input type="checkbox" data-sel="${j.id}" ${order >= 0 ? 'checked' : ''}>${order >= 0 ? `<span class="badge badge-blue">${order + 1}º</span>` : ''}</label>` : ''}</td>
                <td><b>${esc(j.label || j.id)}</b><div class="muted small mono">${j.id}</div></td>
                <td>${esc(j.environment)}</td>
                <td>${esc((j.source || '').split(/[\\/]/).pop())}</td>
                <td class="nowrap">${j.created_at ? new Date(j.created_at * 1000).toLocaleString('pt-BR') : '—'}</td>
                <td>${intBR(j.samples || j.rows)}</td>
                <td>${annotated}</td>
                <td>${pct(j.accuracy)}</td>
                <td>${pct(j.precision)}</td>
                <td>${statusBadge[j.status] || esc(j.status)}</td>
                <td class="nowrap">
                    ${can || j.status === 'running' ? `<button class="btn btn-sm" data-open="${j.id}">Abrir</button>` : ''}
                    ${j.status !== 'running' ? `<button class="btn btn-sm btn-danger" data-delete="${j.id}" title="Excluir">×</button>` : ''}
                </td></tr>`;
        }).join('') + '</tbody></table>';
    updateReportButton();
}

$('historyTable').addEventListener('change', e => {
    const box = e.target.closest('input[data-sel]');
    if (!box) return;
    const id = box.dataset.sel;
    selectedForReport = selectedForReport.filter(x => x !== id);
    if (box.checked) selectedForReport.push(id);
    loadHistory();
});
$('historyTable').addEventListener('click', async e => {
    const open = e.target.closest('[data-open]');
    const del = e.target.closest('[data-delete]');
    if (open) { closeModal('historyModal'); await openJob(open.dataset.open); }
    if (del) {
        if (!confirm('Excluir este teste e todas as anotações dele? Não dá para desfazer.')) return;
        try {
            await req('/api/tests/' + del.dataset.delete, { method: 'DELETE' });
            if (S.job === del.dataset.delete) { S.job = null; history.replaceState(null, '', '/tests'); if (S.step === 4) goStep(1); }
            toast('Teste excluído.', 'ok');
            loadHistory();
            updateStepper();
        } catch (error) { toast(error.message, 'error'); }
    }
});

function updateReportButton() {
    $('btnReport').disabled = !selectedForReport.length;
    $('histSelInfo').textContent = selectedForReport.length
        ? `${selectedForReport.length} teste(s) selecionado(s), na ordem marcada.`
        : 'Marque os testes que vão para o TCC, na ordem dos vídeos.';
}

$('btnReport').onclick = async () => {
    const button = $('btnReport');
    button.disabled = true;
    button.textContent = 'Gerando...';
    try {
        const response = await fetch(API() + '/api/tests/report', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ ids: selectedForReport, allow_incomplete: $('histAllowIncomplete').checked }),
        });
        if (!response.ok) {
            const data = await response.json().catch(() => ({}));
            throw new Error(data.error || 'Falha ao gerar as tabelas.');
        }
        const blob = await response.blob();
        const a = document.createElement('a');
        a.href = URL.createObjectURL(blob);
        a.download = 'resultados_tcc.zip';
        document.body.appendChild(a);
        a.click();
        a.remove();
        toast('Tabelas e figuras geradas (também ficam na pasta resultados_tcc).', 'ok');
    } catch (error) {
        toast(error.message, 'error');
    } finally {
        button.textContent = 'Gerar tabelas e figuras do TCC (.zip)';
        updateReportButton();
    }
};

/* ---------------------------------------------------------
 * Teclado e redimensionamento
 * --------------------------------------------------------- */
document.addEventListener('keydown', e => {
    const tag = (e.target.tagName || '').toLowerCase();
    const typing = tag === 'input' || tag === 'select' || tag === 'textarea';

    if ($('roiModal').classList.contains('open')) {
        if (typing) return;
        if (e.key === 'Enter') { e.preventDefault(); if (E.current.length) closeCurrentPolygon(); }
        else if (e.key === 'Backspace' || (e.key === 'z' && (e.ctrlKey || e.metaKey))) { e.preventDefault(); E.current.pop(); drawEditor(); }
        else if (e.key === 'Escape') { e.preventDefault(); if (E.current.length) { E.current = []; drawEditor(); } else closeEditor(); }
        return;
    }
    if ($('historyModal').classList.contains('open')) {
        if (e.key === 'Escape') closeModal('historyModal');
        return;
    }
    if (S.step !== 4 || !S.job || typing) return;
    if (e.key === 'Enter') { e.preventDefault(); saveAndNext(); }
    else if (e.key === 'ArrowRight') { e.preventDefault(); if (A.pos < A.total - 1) goSample(A.pos + 1, { ...A.truth }); }
    else if (e.key === 'ArrowLeft') { e.preventDefault(); goSample(A.pos - 1); }
    else if (/^[1-9]$/.test(e.key)) { const n = A.regions[Number(e.key) - 1]; if (n) toggleRegion(n); }
});

window.addEventListener('resize', debounce(() => {
    if (S.step === 2) drawEnvPreview();
    if (S.step === 4) drawAnalysis();
    if ($('roiModal').classList.contains('open')) drawEditor();
}, 100));

window.addEventListener('beforeunload', e => {
    if ((A.dirty && !A.prefilled) || ($('roiModal').classList.contains('open') && E.dirty)) {
        e.preventDefault();
        e.returnValue = '';
    }
});

/* ---------------------------------------------------------
 * Início
 * --------------------------------------------------------- */
(async () => {
    await loadEnvs();
    await loadVideos();
    goStep(1);
    const job = new URLSearchParams(location.search).get('job');
    if (job) await openJob(job);
})();

})();
