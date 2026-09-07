/*
 * Editor visual das vagas.
 *
 * Fluxo:
 *
 * 1. criar/selecionar ambiente;
 * 2. informar a câmera;
 * 3. capturar frame;
 * 4. clicar nos cantos das vagas;
 * 5. fechar a vaga;
 * 6. salvar.
 *
 * Também permite:
 * - editar vagas já existentes;
 * - arrastar pontos;
 * - adicionar novos pontos;
 * - desfazer pontos;
 * - renomear vagas;
 * - excluir vagas;
 * - configurar confiança do YOLO;
 * - configurar FPS.
 *
 * A configuração de detecção é armazenada em:
 *
 *     regions.__detection__
 *
 * Essa chave não é considerada uma vaga.
 */

const DETECTION_KEY = "__detection__";
const STORAGE_KEY_API_URL = "smartparking:roiApiUrl";

const POINT_HIT_RADIUS = 9;


/* ============================================================
 * ESTADO
 * ============================================================ */

const canvas =
    document.getElementById("canvas");

const ctx =
    canvas.getContext("2d");

let image = null;

let regions = {};

let currentPoints = [];

let editingName = null;

let currentEnvironment = "";

let detectionConfig = {
    confidence: 0.4,
    fps: 5
};

let renamingName = null;

const drag = {
    active: false,
    index: -1,
    moved: false
};


/* ============================================================
 * ELEMENTOS
 * ============================================================ */

const els = {

    apiUrl:
        document.getElementById("apiUrl"),

    testApiBtn:
        document.getElementById("testApiBtn"),

    apiUrlHint:
        document.getElementById("apiUrlHint"),

    envSelect:
        document.getElementById("envSelect"),

    envName:
        document.getElementById("envName"),

    envSource:
        document.getElementById("envSource"),

    imageInput:
        document.getElementById("imageInput"),

    regionList:
        document.getElementById("regionList"),

    regionCount:
        document.getElementById("regionCountBadge"),

    status:
        document.getElementById("status"),

    imageInfo:
        document.getElementById("imageInfo"),

    canvasWrap:
        document.getElementById("canvasWrap"),

    emptyCanvas:
        document.getElementById("emptyCanvas"),

    apiStatus:
        document.getElementById("apiStatus"),

    confidenceRange:
        document.getElementById("confidenceRange"),

    confidenceValue:
        document.getElementById("confidenceValue"),

    fpsInput:
        document.getElementById("fpsInput"),

    pointCounter:
        document.getElementById("pointCounter"),

    editingIndicator:
        document.getElementById("editingIndicator"),

    finishRegionLabel:
        document.getElementById("finishRegionLabel"),

    undoPointBtn:
        document.getElementById("undoPointBtn"),

    cursorCoords:
        document.getElementById("cursorCoords")
};


/* ============================================================
 * INICIALIZAÇÃO
 * ============================================================ */

document.addEventListener(
    "DOMContentLoaded",
    init
);


async function init() {

    restoreApiUrl();

    document
        .getElementById("loadBtn")
        .addEventListener(
            "click",
            loadSelectedEnvironment
        );


    document
        .getElementById("captureBtn")
        .addEventListener(
            "click",
            captureFrame
        );


    document
        .getElementById("saveBtn")
        .addEventListener(
            "click",
            saveEnvironment
        );


    document
        .getElementById("finishRegionBtn")
        .addEventListener(
            "click",
            finishRegion
        );


    document
        .getElementById("cancelRegionBtn")
        .addEventListener(
            "click",
            cancelRegion
        );


    els.undoPointBtn.addEventListener(
        "click",
        undoLastPoint
    );


    if (els.imageInput) {

        els.imageInput.addEventListener(
            "change",
            handleImageUpload
        );
    }


    els.testApiBtn.addEventListener(
        "click",
        testApiConnection
    );


    els.apiUrl.addEventListener(
        "change",
        persistApiUrl
    );


    if (els.confidenceRange) {

        els.confidenceRange.addEventListener(
            "input",
            () => {

                if (els.confidenceValue) {

                    els.confidenceValue.textContent =
                        Number(
                            els.confidenceRange.value
                        ).toFixed(2);
                }
            }
        );
    }


    canvas.addEventListener(
        "mousedown",
        onCanvasMouseDown
    );


    canvas.addEventListener(
        "mousemove",
        onCanvasMouseMove
    );


    canvas.addEventListener(
        "mouseup",
        onCanvasMouseUp
    );


    canvas.addEventListener(
        "mouseleave",
        onCanvasMouseLeave
    );


    canvas.addEventListener(
        "click",
        addPoint
    );


    document.addEventListener(
        "keydown",
        onKeyDown
    );


    window.addEventListener(
        "mouseup",
        onCanvasMouseUp
    );


    window.addEventListener(
        "resize",
        draw
    );


    applyDetectionConfigToInputs();

    updatePointCounter();

    await loadEnvironments();
}


/* ============================================================
 * UTILITÁRIOS
 * ============================================================ */

function getApiUrl() {

    return (
        els.apiUrl.value || ""
    )
        .trim()
        .replace(/\/+$/, "");
}


function environmentUrl(name) {

    return (
        `${getApiUrl()}/api/environments/` +
        encodeURIComponent(name)
    );
}


function referenceImageUrl(name) {

    return (
        `${environmentUrl(name)}/reference-image`
    );
}


function captureFrameUrl(name) {

    return (
        `${environmentUrl(name)}/capture-frame`
    );
}


/* ============================================================
 * URL DA API
 * ============================================================ */

function restoreApiUrl() {

    const saved =
        window.localStorage.getItem(
            STORAGE_KEY_API_URL
        );

    if (saved) {

        els.apiUrl.value =
            saved;
    }
}


function persistApiUrl() {

    const url =
        getApiUrl();

    if (url) {

        window.localStorage.setItem(
            STORAGE_KEY_API_URL,
            url
        );
    }
}


async function testApiConnection() {

    const apiUrl =
        getApiUrl();

    if (!apiUrl) {

        setApiStatus(false);

        els.apiUrlHint.textContent =
            "Informe a URL da API.";

        return;
    }


    els.testApiBtn.disabled = true;

    const originalIcon =
        els.testApiBtn.innerHTML;

    els.testApiBtn.innerHTML =
        '<span class="spinner-border spinner-border-sm"></span>';


    try {

        const controller =
            new AbortController();

        const timeout =
            setTimeout(
                () => controller.abort(),
                4000
            );


        const response =
            await fetch(
                `${apiUrl}/`,
                {
                    signal:
                        controller.signal
                }
            );


        clearTimeout(timeout);


        if (!response.ok) {

            throw new Error(
                `Resposta HTTP ${response.status}`
            );
        }


        let body = {};

        try {

            body =
                await response.json();

        } catch (_) {
            body = {};
        }


        setApiStatus(true);

        els.apiUrlHint.textContent =
            body.service
                ? `Conectado a "${body.service}".`
                : "API conectada.";


        persistApiUrl();

        await loadEnvironments();

    } catch (error) {

        setApiStatus(false);

        els.apiUrlHint.textContent =
            error.name === "AbortError"
                ? "Tempo de resposta esgotado. Verifique a URL."
                : `Falha na conexão: ${error.message}`;

    } finally {

        els.testApiBtn.disabled = false;

        els.testApiBtn.innerHTML =
            originalIcon;
    }
}


/* ============================================================
 * AMBIENTES
 * ============================================================ */

async function loadEnvironments() {

    const apiUrl =
        getApiUrl();


    if (!apiUrl) {

        setApiStatus(false);

        return;
    }


    try {

        const response =
            await fetch(
                `${apiUrl}/api/environments`
            );


        if (!response.ok) {

            throw new Error(
                `HTTP ${response.status}`
            );
        }


        const names =
            await response.json();


        if (!Array.isArray(names)) {

            throw new Error(
                "Resposta inválida da API de ambientes."
            );
        }


        els.envSelect.innerHTML =
            '<option value="">Selecione um ambiente</option>';


        names.forEach(
            name => {

                const option =
                    document.createElement(
                        "option"
                    );

                option.value =
                    name;

                option.textContent =
                    name;

                els.envSelect.appendChild(
                    option
                );
            }
        );


        setApiStatus(true);


        /*
         * Se já havia um ambiente selecionado,
         * mantém a seleção.
         */
        if (
            currentEnvironment &&
            names.includes(currentEnvironment)
        ) {

            els.envSelect.value =
                currentEnvironment;

        }


    } catch (error) {

        setApiStatus(false);

        setStatus(
            `Erro ao carregar ambientes: ${error.message}`,
            true
        );
    }
}


async function loadSelectedEnvironment() {

    const name =
        els.envSelect.value;


    if (!name) {

        setStatus(
            "Selecione um ambiente primeiro.",
            true
        );

        return;
    }


    await loadEnvironment(name);
}


async function loadEnvironment(name) {

    if (!name) {
        return;
    }


    currentEnvironment =
        name;

    els.envName.value =
        name;


    try {

        const response =
            await fetch(
                environmentUrl(name)
            );


        if (!response.ok) {

            throw new Error(
                `HTTP ${response.status}`
            );
        }


        const data =
            await response.json();


        const rawRegions =
            data.regions &&
                typeof data.regions === "object"
                ? data.regions
                : {};


        /*
         * Recupera a configuração da detecção.
         */
        if (
            rawRegions[DETECTION_KEY] &&
            typeof rawRegions[DETECTION_KEY] === "object"
        ) {

            detectionConfig = {
                ...detectionConfig,
                ...rawRegions[DETECTION_KEY]
            };
        }


        /*
         * Copia somente as vagas.
         */
        regions = {};

        Object.entries(rawRegions)
            .forEach(
                ([regionName, points]) => {

                    if (
                        regionName === DETECTION_KEY
                    ) {
                        return;
                    }

                    if (
                        !Array.isArray(points)
                    ) {
                        return;
                    }

                    regions[regionName] =
                        points.map(
                            point => [
                                Number(point[0]),
                                Number(point[1])
                            ]
                        );
                }
            );


        applyDetectionConfigToInputs();


        els.envSource.value =
            data.source || "";


        exitEditingState();


        renderRegionList();


        await loadReferenceImage(name);


        draw();


        setStatus(
            `Ambiente "${name}" carregado.`
        );


    } catch (error) {

        setStatus(
            `Erro ao carregar ambiente: ${error.message}`,
            true
        );
    }
}


/* ============================================================
 * DETECÇÃO
 * ============================================================ */

function applyDetectionConfigToInputs() {

    if (
        els.confidenceRange
    ) {

        const confidence =
            Number(
                detectionConfig.confidence
            );

        els.confidenceRange.value =
            Number.isFinite(confidence)
                ? confidence
                : 0.4;
    }


    if (
        els.confidenceValue &&
        els.confidenceRange
    ) {

        els.confidenceValue.textContent =
            Number(
                els.confidenceRange.value
            ).toFixed(2);
    }


    if (
        els.fpsInput
    ) {

        els.fpsInput.value =
            Number(
                detectionConfig.fps
            ) || 5;
    }
}


function readDetectionConfigFromInputs() {

    const confidence =
        els.confidenceRange
            ? Number(
                parseFloat(
                    els.confidenceRange.value
                ).toFixed(2)
            )
            : Number(
                detectionConfig.confidence
            );


    const fps =
        els.fpsInput
            ? Math.max(
                1,
                parseInt(
                    els.fpsInput.value,
                    10
                ) || 5
            )
            : Number(
                detectionConfig.fps
            ) || 5;


    return {

        confidence:
            Number.isFinite(confidence)
                ? confidence
                : 0.4,

        fps
    };
}


/* ============================================================
 * IMAGEM DE REFERÊNCIA
 * ============================================================ */

async function loadReferenceImage(name) {

    try {

        const response =
            await fetch(
                referenceImageUrl(name)
            );


        if (!response.ok) {

            image = null;

            showEmptyCanvas();

            return;
        }


        const blob =
            await response.blob();


        await setImageFromBlob(blob);


    } catch (error) {

        image = null;

        showEmptyCanvas();
    }
}


async function setImageFromBlob(blob) {

    const objectUrl =
        URL.createObjectURL(blob);


    await new Promise(
        (resolve, reject) => {

            const img =
                new Image();


            img.onload = () => {

                image =
                    img;


                canvas.width =
                    img.naturalWidth ||
                    img.width;


                canvas.height =
                    img.naturalHeight ||
                    img.height;


                els.canvasWrap.classList.remove(
                    "d-none"
                );


                els.emptyCanvas.classList.add(
                    "d-none"
                );


                els.imageInfo.textContent =
                    `${canvas.width} × ${canvas.height}px`;


                URL.revokeObjectURL(
                    objectUrl
                );


                draw();


                resolve();
            };


            img.onerror = () => {

                URL.revokeObjectURL(
                    objectUrl
                );

                reject(
                    new Error(
                        "Não foi possível carregar a imagem."
                    )
                );
            };


            img.src =
                objectUrl;
        }
    );
}


function showEmptyCanvas() {

    els.canvasWrap.classList.add(
        "d-none"
    );

    els.emptyCanvas.classList.remove(
        "d-none"
    );

    els.imageInfo.textContent =
        "";

    canvas.width = 1;
    canvas.height = 1;

    ctx.clearRect(
        0,
        0,
        canvas.width,
        canvas.height
    );
}


/* ============================================================
 * CAPTURA DE FRAME
 * ============================================================ */

async function captureFrame() {

    const name =
        els.envSelect.value ||
        els.envName.value.trim();


    if (!name) {

        setStatus(
            "Informe o nome do ambiente.",
            true
        );

        return;
    }


    const source =
        els.envSource.value.trim();


    if (!source) {

        setStatus(
            "Informe a fonte da câmera ou vídeo.",
            true
        );

        return;
    }


    try {

        setStatus(
            "Salvando configuração da câmera..."
        );


        await createOrUpdateEnvironment(
            name,
            source
        );


        setStatus(
            "Capturando frame..."
        );


        const response =
            await fetch(
                captureFrameUrl(name),
                {
                    method: "POST"
                }
            );


        if (!response.ok) {

            const body =
                await response
                    .json()
                    .catch(
                        () => ({})
                    );


            throw new Error(
                body.error ||
                `HTTP ${response.status}`
            );
        }


        const blob =
            await response.blob();


        await setImageFromBlob(blob);


        currentEnvironment =
            name;


        els.envSelect.value =
            name;


        setStatus(
            "Frame capturado. Agora marque as vagas."
        );


    } catch (error) {

        setStatus(
            `Erro ao capturar: ${error.message}`,
            true
        );
    }
}


/* ============================================================
 * SALVAR AMBIENTE
 * ============================================================ */

async function createOrUpdateEnvironment(
    name,
    source
) {

    const detection =
        readDetectionConfigFromInputs();


    const payload = {
        ...regions,
        [DETECTION_KEY]:
            detection
    };


    const response =
        await fetch(
            environmentUrl(name),
            {
                method: "POST",

                headers: {
                    "Content-Type":
                        "application/json"
                },

                body:
                    JSON.stringify({
                        source,
                        regions:
                            payload
                    })
            }
        );


    if (!response.ok) {

        const body =
            await response
                .json()
                .catch(
                    () => ({})
                );


        throw new Error(
            body.error ||
            `HTTP ${response.status}`
        );
    }


    currentEnvironment =
        name;


    detectionConfig =
        detection;


    await loadEnvironments();


    els.envSelect.value =
        name;
}


async function saveEnvironment() {

    const name =
        els.envName.value.trim();


    const source =
        els.envSource.value.trim();


    if (!name) {

        setStatus(
            "Informe o nome do ambiente.",
            true
        );

        return;
    }


    if (!source) {

        setStatus(
            "Informe a fonte da câmera ou vídeo.",
            true
        );

        return;
    }


    const regionNames =
        Object.keys(regions);


    if (!regionNames.length) {

        setStatus(
            "Adicione pelo menos uma vaga.",
            true
        );

        return;
    }


    try {

        setStatus(
            "Salvando ambiente..."
        );


        await createOrUpdateEnvironment(
            name,
            source
        );


        if (image) {

            await saveReferenceImage(
                name
            );
        }


        currentEnvironment =
            name;


        setStatus(
            `Ambiente salvo com ${regionNames.length} vaga(s).`
        );


    } catch (error) {

        setStatus(
            `Erro ao salvar: ${error.message}`,
            true
        );
    }
}


async function saveReferenceImage(name) {

    if (!image) {
        return;
    }


    const imageCanvas =
        document.createElement(
            "canvas"
        );


    imageCanvas.width =
        image.naturalWidth ||
        image.width;


    imageCanvas.height =
        image.naturalHeight ||
        image.height;


    const imageCtx =
        imageCanvas.getContext(
            "2d"
        );


    imageCtx.drawImage(
        image,
        0,
        0,
        imageCanvas.width,
        imageCanvas.height
    );


    const response =
        await fetch(
            referenceImageUrl(name),
            {
                method: "POST",

                headers: {
                    "Content-Type":
                        "application/json"
                },

                body:
                    JSON.stringify({
                        image_base64:
                            imageCanvas.toDataURL(
                                "image/jpeg",
                                0.88
                            )
                    })
            }
        );


    if (!response.ok) {

        const body =
            await response
                .json()
                .catch(
                    () => ({})
                );


        throw new Error(
            body.error ||
            `HTTP ${response.status}`
        );
    }
}


/* ============================================================
 * CANVAS
 * ============================================================ */

function getCanvasPoint(event) {

    const rect =
        canvas.getBoundingClientRect();


    const scaleX =
        canvas.width /
        rect.width;


    const scaleY =
        canvas.height /
        rect.height;


    return {

        x:
            (event.clientX - rect.left) *
            scaleX,

        y:
            (event.clientY - rect.top) *
            scaleY,

        scaleX,
        scaleY
    };
}


function findPointIndexAt(
    x,
    y,
    scale
) {

    const radius =
        POINT_HIT_RADIUS *
        Math.max(scale, 1);


    for (
        let i = 0;
        i < currentPoints.length;
        i++
    ) {

        const point =
            currentPoints[i];


        const px =
            point[0];

        const py =
            point[1];


        const distance =
            Math.hypot(
                px - x,
                py - y
            );


        if (
            distance <= radius
        ) {

            return i;
        }
    }


    return -1;
}


function onCanvasMouseDown(event) {

    if (!image) {
        return;
    }


    const {
        x,
        y,
        scaleX
    } =
        getCanvasPoint(event);


    const hitIndex =
        findPointIndexAt(
            x,
            y,
            scaleX
        );


    if (hitIndex !== -1) {

        drag.active = true;
        drag.index = hitIndex;
        drag.moved = false;

        event.preventDefault();
    }
}


function onCanvasMouseMove(event) {

    if (!image) {
        return;
    }


    const {
        x,
        y,
        scaleX
    } =
        getCanvasPoint(event);


    if (els.cursorCoords) {

        els.cursorCoords.textContent =
            `${Math.round(x)}, ${Math.round(y)}`;
    }


    if (drag.active) {

        currentPoints[
            drag.index
        ] = [
                Math.round(x),
                Math.round(y)
            ];


        drag.moved = true;


        updatePointCounter();

        draw();

        return;
    }


    const hovering =
        findPointIndexAt(
            x,
            y,
            scaleX
        ) !== -1;


    canvas.classList.toggle(
        "hover-point",
        hovering
    );
}


function onCanvasMouseUp() {

    if (!drag.active) {
        return;
    }


    drag.active = false;

    drag.index = -1;


    setTimeout(
        () => {
            drag.moved = false;
        },
        0
    );
}


function onCanvasMouseLeave() {

    if (els.cursorCoords) {

        els.cursorCoords.textContent =
            "";
    }

    canvas.classList.remove(
        "hover-point"
    );
}


/*
 * Só adiciona ponto se o clique não tiver
 * iniciado um arraste.
 */
function addPoint(event) {

    if (!image) {

        setStatus(
            "Capture ou carregue um frame primeiro.",
            true
        );

        return;
    }


    if (drag.moved) {
        return;
    }


    const {
        x,
        y,
        scaleX
    } =
        getCanvasPoint(event);


    if (
        findPointIndexAt(
            x,
            y,
            scaleX
        ) !== -1
    ) {

        return;
    }


    currentPoints.push([
        Math.round(x),
        Math.round(y)
    ]);


    updatePointCounter();

    draw();
}


/* ============================================================
 * TECLADO
 * ============================================================ */

function onKeyDown(event) {

    const tag =
        (
            event.target.tagName ||
            ""
        ).toLowerCase();


    const isTyping =
        tag === "input" ||
        tag === "textarea" ||
        event.target.isContentEditable;


    if (isTyping) {
        return;
    }


    if (
        event.key === "Enter"
    ) {

        event.preventDefault();

        finishRegion();

        return;
    }


    if (
        event.key === "Escape"
    ) {

        event.preventDefault();

        cancelRegion();

        return;
    }


    if (
        event.key === "Backspace" ||
        event.key === "Delete"
    ) {

        event.preventDefault();

        undoLastPoint();
    }
}


function undoLastPoint() {

    if (!currentPoints.length) {

        setStatus(
            "Não há pontos para desfazer."
        );

        return;
    }


    currentPoints.pop();

    updatePointCounter();

    draw();
}


function updatePointCounter() {

    const count =
        currentPoints.length;


    els.pointCounter.textContent =
        count === 1
            ? "1 ponto"
            : `${count} pontos`;
}


/* ============================================================
 * REGIÕES
 * ============================================================ */

function finishRegion() {

    if (
        currentPoints.length < 3
    ) {

        setStatus(
            "Uma vaga precisa de pelo menos 3 pontos.",
            true
        );

        return;
    }


    const name =
        editingName ||
        generateRegionName();


    const isEdit =
        Boolean(editingName);


    regions[name] =
        currentPoints.map(
            point => [...point]
        );


    exitEditingState();


    renderRegionList();

    draw();


    setStatus(
        isEdit
            ? `${name} atualizada.`
            : `${name} criada automaticamente.`
    );
}


function generateRegionName() {

    let number = 1;


    while (
        Object.prototype.hasOwnProperty.call(
            regions,
            `vaga-${String(number).padStart(2, "0")}`
        )
    ) {

        number++;
    }


    return (
        `vaga-${String(number).padStart(2, "0")}`
    );
}


function cancelRegion() {

    const wasEditing =
        Boolean(editingName);


    exitEditingState();

    draw();


    setStatus(
        wasEditing
            ? "Edição cancelada."
            : "Desenho cancelado."
    );
}


function startEditingRegion(name) {

    if (
        !regions[name]
    ) {

        return;
    }


    editingName =
        name;


    currentPoints =
        regions[name].map(
            point => [...point]
        );


    els.finishRegionLabel.textContent =
        "Salvar edição";


    els.editingIndicator.classList.remove(
        "d-none"
    );


    updatePointCounter();

    renderRegionList();

    draw();


    setStatus(
        `Editando "${name}". Arraste os pontos ou clique para adicionar cantos.`
    );
}


function exitEditingState() {

    editingName =
        null;


    currentPoints =
        [];


    els.finishRegionLabel.textContent =
        "Fechar vaga";


    els.editingIndicator.classList.add(
        "d-none"
    );


    updatePointCounter();
}


/* ============================================================
 * RENOMEAR
 * ============================================================ */

function renameRegionPrompt(
    oldName,
    inputEl
) {

    const newName =
        (
            inputEl.value ||
            ""
        ).trim();


    if (
        !newName ||
        newName === oldName
    ) {

        renamingName =
            null;

        renderRegionList();

        return;
    }


    if (
        Object.prototype.hasOwnProperty.call(
            regions,
            newName
        )
    ) {

        setStatus(
            `Já existe uma vaga chamada "${newName}".`,
            true
        );

        inputEl.focus();
        inputEl.select();

        return;
    }


    regions[newName] =
        regions[oldName];


    delete regions[oldName];


    if (
        editingName === oldName
    ) {

        editingName =
            newName;
    }


    renamingName =
        null;


    renderRegionList();

    draw();


    setStatus(
        `Renomeada para "${newName}".`
    );
}


/* ============================================================
 * LISTA DE REGIÕES
 * ============================================================ */

function renderRegionList() {

    els.regionList.innerHTML =
        "";


    const names =
        Object.keys(regions)
            .filter(
                name =>
                    name !== DETECTION_KEY
            );


    if (els.regionCount) {

        els.regionCount.textContent =
            names.length;
    }


    if (!names.length) {

        const empty =
            document.createElement(
                "div"
            );

        empty.className =
            "text-muted small p-3";

        empty.textContent =
            "Nenhuma vaga cadastrada.";

        els.regionList.appendChild(
            empty
        );

        return;
    }


    names.forEach(
        name => {

            const item =
                document.createElement(
                    "div"
                );


            item.className =
                "list-group-item region-item" +
                (
                    name === editingName
                        ? " editing"
                        : ""
                );


            if (
                renamingName === name
            ) {

                const input =
                    document.createElement(
                        "input"
                    );


                input.type =
                    "text";


                input.value =
                    name;


                input.className =
                    "form-control form-control-sm region-name-input";


                input.addEventListener(
                    "keydown",
                    event => {

                        if (
                            event.key === "Enter"
                        ) {

                            event.preventDefault();

                            renameRegionPrompt(
                                name,
                                input
                            );

                        } else if (
                            event.key === "Escape"
                        ) {

                            event.preventDefault();

                            renamingName =
                                null;

                            renderRegionList();
                        }
                    }
                );


                input.addEventListener(
                    "blur",
                    () => {

                        /*
                         * Pequeno atraso para permitir
                         * que o Enter seja processado.
                         */
                        setTimeout(
                            () => {

                                if (
                                    renamingName === name
                                ) {

                                    renameRegionPrompt(
                                        name,
                                        input
                                    );
                                }
                            },
                            50
                        );
                    }
                );


                item.appendChild(
                    input
                );


                els.regionList.appendChild(
                    item
                );


                requestAnimationFrame(
                    () => {

                        input.focus();
                        input.select();
                    }
                );


                return;
            }


            const label =
                document.createElement(
                    "span"
                );


            label.className =
                "region-name";


            label.textContent =
                name;


            const actions =
                document.createElement(
                    "div"
                );


            actions.className =
                "region-actions";


            const editBtn =
                document.createElement(
                    "button"
                );


            editBtn.type =
                "button";


            editBtn.className =
                "btn btn-sm btn-outline-primary";


            editBtn.title =
                "Editar formato";


            editBtn.innerHTML =
                '<i class="bi bi-bounding-box-circles"></i>';


            editBtn.addEventListener(
                "click",
                () => {

                    startEditingRegion(
                        name
                    );
                }
            );


            const renameBtn =
                document.createElement(
                    "button"
                );


            renameBtn.type =
                "button";


            renameBtn.className =
                "btn btn-sm btn-outline-secondary";


            renameBtn.title =
                "Renomear";


            renameBtn.innerHTML =
                '<i class="bi bi-pencil"></i>';


            renameBtn.addEventListener(
                "click",
                () => {

                    renamingName =
                        name;

                    renderRegionList();
                }
            );


            const deleteBtn =
                document.createElement(
                    "button"
                );


            deleteBtn.type =
                "button";


            deleteBtn.className =
                "btn btn-sm btn-outline-danger";


            deleteBtn.title =
                "Excluir";


            deleteBtn.innerHTML =
                '<i class="bi bi-trash"></i>';


            deleteBtn.addEventListener(
                "click",
                () => {

                    const confirmed =
                        window.confirm(
                            `Deseja excluir a vaga "${name}"?`
                        );


                    if (!confirmed) {
                        return;
                    }


                    delete regions[name];


                    if (
                        editingName === name
                    ) {

                        exitEditingState();
                    }


                    renderRegionList();

                    draw();


                    setStatus(
                        `${name} removida.`
                    );
                }
            );


            actions.append(
                editBtn,
                renameBtn,
                deleteBtn
            );


            item.append(
                label,
                actions
            );


            els.regionList.appendChild(
                item
            );
        }
    );
}


/* ============================================================
 * DESENHO
 * ============================================================ */

function draw() {

    if (
        !canvas.width ||
        !canvas.height
    ) {

        return;
    }


    ctx.clearRect(
        0,
        0,
        canvas.width,
        canvas.height
    );


    if (image) {

        ctx.drawImage(
            image,
            0,
            0,
            canvas.width,
            canvas.height
        );
    }


    Object.entries(
        regions
    ).forEach(
        ([name, points]) => {

            if (
                name === editingName ||
                name === DETECTION_KEY
            ) {

                return;
            }


            if (
                !Array.isArray(points) ||
                points.length < 1
            ) {

                return;
            }


            drawPolygon(
                points,
                "#198754",
                name,
                false
            );
        }
    );


    if (
        currentPoints.length
    ) {

        drawPolygon(
            currentPoints,
            "#0d6efd",
            editingName,
            currentPoints.length < 3
        );
    }
}


function drawPolygon(
    points,
    color,
    label,
    openPath = false
) {

    if (
        !points ||
        !points.length
    ) {

        return;
    }


    ctx.beginPath();


    ctx.moveTo(
        points[0][0],
        points[0][1]
    );


    points
        .slice(1)
        .forEach(
            point => {

                ctx.lineTo(
                    point[0],
                    point[1]
                );
            }
        );


    if (
        !openPath &&
        points.length >= 3
    ) {

        ctx.closePath();
    }


    ctx.fillStyle =
        color === "#198754"
            ? "rgba(25,135,84,.28)"
            : "rgba(13,110,253,.28)";


    ctx.strokeStyle =
        color;


    ctx.lineWidth =
        3;


    if (
        !openPath &&
        points.length >= 3
    ) {

        ctx.fill();
    }


    ctx.stroke();


    points.forEach(
        point => {

            ctx.beginPath();


            ctx.arc(
                point[0],
                point[1],
                5,
                0,
                Math.PI * 2
            );


            ctx.fillStyle =
                color;


            ctx.fill();


            ctx.lineWidth =
                2;


            ctx.strokeStyle =
                "#fff";


            ctx.stroke();
        }
    );


    if (
        !label ||
        points.length < 3
    ) {

        return;
    }


    const center =
        getPolygonCenter(points);


    ctx.font =
        "bold 16px sans-serif";


    ctx.textAlign =
        "center";


    ctx.textBaseline =
        "middle";


    ctx.lineWidth =
        4;


    ctx.strokeStyle =
        "rgba(0,0,0,.8)";


    ctx.strokeText(
        label,
        center.x,
        center.y
    );


    ctx.fillStyle =
        "#fff";


    ctx.fillText(
        label,
        center.x,
        center.y
    );
}


function getPolygonCenter(points) {

    return {

        x:
            points.reduce(
                (
                    sum,
                    point
                ) =>
                    sum + point[0],
                0
            ) /
            points.length,

        y:
            points.reduce(
                (
                    sum,
                    point
                ) =>
                    sum + point[1],
                0
            ) /
            points.length
    };
}


/* ============================================================
 * STATUS
 * ============================================================ */

function setStatus(
    message,
    error = false
) {

    if (!els.status) {
        return;
    }


    els.status.textContent =
        message;


    els.status.className =
        `alert border small mb-0 ${error
            ? "alert-danger"
            : "alert-light"
        }`;
}


function setApiStatus(online) {

    els.apiStatus.className =
        `badge ${online
            ? "text-bg-success"
            : "text-bg-danger"
        }`;


    els.apiStatus.textContent =
        online
            ? "API conectada"
            : "API indisponível";
}