/*
 * JavaScript do dashboard principal.
 *
 * O Python fica responsável por:
 *   - câmera;
 *   - reconhecimento;
 *   - armazenamento;
 *   - Socket.IO.
 *
 * O navegador fica responsável somente pela interface
 * e pela comunicação com as APIs.
 */


const STORAGE_KEYS = {

    apiUrl:
        "smartparking:dashboardApiUrl",

    roiApiUrl:
        "smartparking:roiApiUrl",

    envName:
        "smartparking:lastEnvironment"
};


const STALE_AFTER_MS =
    10000;


/* ============================================================
 * ESTADO
 * ============================================================ */

let socket = null;

let latestStatus = [];

let regionsConfig = {};

let referenceImage = null;

let lastUpdateAt = null;

let currentEnvironment = "";


/* ============================================================
 * ELEMENTOS
 * ============================================================ */

const els = {

    apiUrl:
        document.getElementById("apiUrl"),

    roiApiUrl:
        document.getElementById("roiApiUrl"),


    apiUrlBadge:
        document.getElementById("apiUrlBadge"),

    roiApiUrlBadge:
        document.getElementById("roiApiUrlBadge"),


    testConnBtn:
        document.getElementById("testConnBtn"),

    connHint:
        document.getElementById("connHint"),


    envSelect:
        document.getElementById("envSelect"),

    envLabel:
        document.getElementById("envLabel"),


    connDot:
        document.getElementById("connDot"),

    connText:
        document.getElementById("connText"),


    grid:
        document.getElementById("grid"),


    freeCount:
        document.getElementById("freeCount"),

    occupiedCount:
        document.getElementById("occupiedCount"),

    totalCount:
        document.getElementById("totalCount"),


    lastUpdate:
        document.getElementById("lastUpdate"),

    staleDot:
        document.getElementById("staleDot"),


    emptyState:
        document.getElementById("emptyState"),


    mapWrap:
        document.getElementById("mapWrap"),

    mapCanvas:
        document.getElementById("mapCanvas"),


    captureBtn:
        document.getElementById("captureBtn"),

    connectBtn:
        document.getElementById("connectBtn"),


    /*
     * Elementos novos do layout (card de ocupação e barra lateral
     * "Estado das vagas"). Todos opcionais — se o HTML não tiver
     * algum deles, o restante do dashboard continua funcionando
     * normalmente (ver uso com "if (els.x)" mais abaixo).
     */
    occupancyRate:
        document.getElementById("occupancyRate"),

    occupancyBar:
        document.getElementById("occupancyBar"),

    vagasSidebarList:
        document.getElementById("vagasSidebarList"),

    vagasSidebarEmpty:
        document.getElementById("vagasSidebarEmpty"),

    sidebarVagasCount:
        document.getElementById("sidebarVagasCount")
};


/* ============================================================
 * INICIALIZAÇÃO
 * ============================================================ */

document.addEventListener(
    "DOMContentLoaded",
    init
);


async function init() {

    restoreSavedUrls();


    els.envSelect.addEventListener(
        "change",
        onEnvironmentChange
    );


    if (els.captureBtn) {

        els.captureBtn.addEventListener(
            "click",
            captureFirstFrame
        );
    }


    els.connectBtn.addEventListener(
        "click",
        onConnectButtonClick
    );


    if (els.testConnBtn) {

        els.testConnBtn.addEventListener(
            "click",
            testConnections
        );
    }


    els.apiUrl.addEventListener(
        "change",
        persistUrls
    );


    els.roiApiUrl.addEventListener(
        "change",
        () => {

            persistUrls();

            loadEnvironments();
        }
    );


    const gridViewBtn =
        document.getElementById(
            "gridViewBtn"
        );


    const mapViewBtn =
        document.getElementById(
            "mapViewBtn"
        );


    if (gridViewBtn) {

        gridViewBtn.addEventListener(
            "click",
            () => switchView("grid")
        );
    }


    if (mapViewBtn) {

        mapViewBtn.addEventListener(
            "click",
            () => switchView("map")
        );
    }


    setInterval(
        updateStaleIndicator,
        2000
    );


    await loadEnvironments();


    await testConnections();


    await resumeMonitoringIfAlreadyRunning();
}


/*
 * Se o backend já estiver monitorando algum ambiente ao vivo (por
 * exemplo, o usuário deu F5 na página com o monitoramento ainda
 * ativo), reconecta o dashboard nesse ambiente automaticamente em
 * vez de deixar tudo zerado até o usuário clicar de novo.
 */
async function resumeMonitoringIfAlreadyRunning() {

    const dashboardUrl =
        getDashboardApiUrl();


    if (!dashboardUrl) {
        return;
    }


    try {

        const response =
            await fetch(
                `${dashboardUrl}/api/monitoring/status`
            );


        if (!response.ok) {
            return;
        }


        const data =
            await response.json();


        if (
            !data ||
            !data.running ||
            !data.environment
        ) {

            return;
        }


        const hasOption =
            Array.from(
                els.envSelect.options
            ).some(
                option =>
                    option.value === data.environment
            );


        if (!hasOption) {
            return;
        }


        els.envSelect.value =
            data.environment;


        await onEnvironmentChange();


        await connect();

    } catch (error) {

        console.warn(
            "Erro ao verificar monitoramento em andamento:",
            error
        );
    }
}


/* ============================================================
 * UTILITÁRIOS
 * ============================================================ */

function normalizeUrl(value) {

    return (
        value || ""
    )
        .trim()
        .replace(/\/+$/, "");
}


function getDashboardApiUrl() {

    return normalizeUrl(
        els.apiUrl.value
    );
}


function getRoiApiUrl() {

    return normalizeUrl(
        els.roiApiUrl.value
    );
}


function environmentUrl(name) {

    return (
        `${getRoiApiUrl()}/api/environments/` +
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
 * URLS
 * ============================================================ */

function restoreSavedUrls() {

    const savedApiUrl =
        window.localStorage.getItem(
            STORAGE_KEYS.apiUrl
        );


    const savedRoiApiUrl =
        window.localStorage.getItem(
            STORAGE_KEYS.roiApiUrl
        );


    if (
        savedApiUrl
    ) {

        els.apiUrl.value =
            savedApiUrl;
    }


    if (
        savedRoiApiUrl
    ) {

        els.roiApiUrl.value =
            savedRoiApiUrl;
    }
}


function persistUrls() {

    const apiUrl =
        getDashboardApiUrl();


    const roiApiUrl =
        getRoiApiUrl();


    if (apiUrl) {

        window.localStorage.setItem(
            STORAGE_KEYS.apiUrl,
            apiUrl
        );
    }


    if (roiApiUrl) {

        window.localStorage.setItem(
            STORAGE_KEYS.roiApiUrl,
            roiApiUrl
        );
    }
}


/* ============================================================
 * TESTE DAS APIS
 * ============================================================ */

async function pingEndpoint(
    url,
    timeoutMs = 4000
) {

    if (!url) {
        return false;
    }


    let timeout = null;


    try {

        const controller =
            new AbortController();


        timeout =
            setTimeout(
                () => controller.abort(),
                timeoutMs
            );


        const response =
            await fetch(
                url,
                {
                    signal:
                        controller.signal
                }
            );


        return response.ok;

    } catch (error) {

        return false;

    } finally {

        if (timeout) {

            clearTimeout(timeout);
        }
    }
}


async function testConnections() {

    const originalHtml =
        els.testConnBtn
            ? els.testConnBtn.innerHTML
            : null;


    if (els.testConnBtn) {

        els.testConnBtn.disabled =
            true;

        els.testConnBtn.innerHTML =
            '<span class="spinner-border spinner-border-sm me-1"></span>Testando...';
    }


    const dashboardUrl =
        getDashboardApiUrl();


    const roiUrl =
        getRoiApiUrl();


    const [
        dashboardOk,
        roiOk
    ] =
        await Promise.all([

            pingEndpoint(
                `${dashboardUrl}/health`
            ),

            pingEndpoint(
                `${roiUrl}/`
            )
        ]);


    setBadgeStatus(
        els.apiUrlBadge,
        dashboardOk
    );


    setBadgeStatus(
        els.roiApiUrlBadge,
        roiOk
    );


    if (els.connHint) {

        if (
            dashboardOk &&
            roiOk
        ) {

            els.connHint.textContent =
                "Ambas as APIs estão respondendo.";

        } else if (
            !dashboardOk &&
            !roiOk
        ) {

            els.connHint.textContent =
                "Nenhuma das APIs respondeu. Verifique se o Smart Parking está rodando.";

        } else {

            els.connHint.textContent =
                !dashboardOk
                    ? "A API de monitoramento não respondeu."
                    : "A API de ambientes não respondeu.";
        }
    }


    persistUrls();


    if (els.testConnBtn) {

        els.testConnBtn.disabled =
            false;

        els.testConnBtn.innerHTML =
            originalHtml;
    }
}


function setBadgeStatus(
    el,
    online
) {

    if (!el) {
        return;
    }


    el.classList.remove(
        "online",
        "offline"
    );


    el.classList.add(
        online
            ? "online"
            : "offline"
    );
}


/* ============================================================
 * AMBIENTES
 * ============================================================ */

async function loadEnvironments() {

    const roiUrl =
        getRoiApiUrl();


    if (!roiUrl) {

        setBadgeStatus(
            els.roiApiUrlBadge,
            false
        );

        return;
    }


    try {

        const response =
            await fetch(
                `${roiUrl}/api/environments`
            );


        if (!response.ok) {

            throw new Error(
                `HTTP ${response.status}`
            );
        }


        const names =
            await response.json();


        if (
            !Array.isArray(names)
        ) {

            throw new Error(
                "Resposta inválida da API."
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


        setBadgeStatus(
            els.roiApiUrlBadge,
            true
        );


        const savedEnv =
            window.localStorage.getItem(
                STORAGE_KEYS.envName
            );


        if (
            savedEnv &&
            names.includes(savedEnv)
        ) {

            els.envSelect.value =
                savedEnv;


            await onEnvironmentChange();

        } else if (
            currentEnvironment &&
            names.includes(
                currentEnvironment
            )
        ) {

            els.envSelect.value =
                currentEnvironment;


            await onEnvironmentChange();
        }


    } catch (error) {

        console.error(
            "Erro carregando ambientes:",
            error
        );


        setBadgeStatus(
            els.roiApiUrlBadge,
            false
        );


        els.envSelect.innerHTML =
            '<option value="">API indisponível</option>';
    }
}


async function onEnvironmentChange() {

    const name =
        els.envSelect.value;


    /*
     * Se havia conexão com outro ambiente,
     * encerra para evitar receber dados antigos.
     */
    disconnectSocket();


    latestStatus = [];

    lastUpdateAt = null;


    if (!name) {

        currentEnvironment =
            "";


        els.envLabel.textContent =
            "Nenhum ambiente selecionado";


        window.localStorage.removeItem(
            STORAGE_KEYS.envName
        );


        regionsConfig = {};

        referenceImage = null;


        renderStatus();

        return;
    }


    currentEnvironment =
        name;


    els.envLabel.textContent =
        `Ambiente: ${name}`;


    window.localStorage.setItem(
        STORAGE_KEYS.envName,
        name
    );


    await loadEnvironment(name);
}


async function loadEnvironment(name) {

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
         * Remove a configuração de detecção.
         * Ela não é uma vaga.
         */
        regionsConfig =
            { ...rawRegions };


        delete regionsConfig[
            "__detection__"
        ];


        await loadReferenceFrame(
            name
        );


        drawMap();


    } catch (error) {

        console.error(
            "Erro carregando ambiente:",
            error
        );


        regionsConfig = {};

        referenceImage = null;


        drawMap();
    }
}


/* ============================================================
 * IMAGEM DE REFERÊNCIA
 * ============================================================ */

async function loadReferenceFrame(name) {

    try {

        const response =
            await fetch(
                referenceImageUrl(name)
            );


        if (!response.ok) {

            referenceImage =
                null;

            return;
        }


        const blob =
            await response.blob();


        await setReferenceImageFromBlob(
            blob
        );


    } catch (error) {

        referenceImage =
            null;


        console.error(
            "Erro carregando imagem:",
            error
        );
    }
}


async function setReferenceImageFromBlob(
    blob
) {

    const objectUrl =
        URL.createObjectURL(
            blob
        );


    await new Promise(
        (
            resolve,
            reject
        ) => {

            const img =
                new Image();


            img.onload = () => {

                referenceImage =
                    img;


                URL.revokeObjectURL(
                    objectUrl
                );


                resolve();
            };


            img.onerror = () => {

                URL.revokeObjectURL(
                    objectUrl
                );


                reject(
                    new Error(
                        "Imagem inválida."
                    )
                );
            };


            img.src =
                objectUrl;
        }
    );
}


/* ============================================================
 * CAPTURA
 * ============================================================ */

async function captureFirstFrame() {

    const name =
        els.envSelect.value;


    if (!name) {

        alert(
            "Selecione um ambiente primeiro."
        );

        return;
    }


    setCaptureLoading(
        true
    );


    try {

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


        await setReferenceImageFromBlob(
            blob
        );


        drawMap();


        switchView(
            "map"
        );


    } catch (error) {

        alert(
            `Erro ao capturar frame: ${error.message}`
        );

    } finally {

        setCaptureLoading(
            false
        );
    }
}


function setCaptureLoading(
    loading
) {

    if (!els.captureBtn) {
        return;
    }


    els.captureBtn.disabled =
        loading;


    els.captureBtn.innerHTML =
        loading

            ? `
                <span class="spinner-border spinner-border-sm me-1"></span>
                Capturando...
              `

            : `
                <i class="bi bi-camera me-1"></i>
                Capturar primeiro frame
              `;
}


/*
 * Clique único no botão liga/desliga:
 * - se ainda não há socket conectado, inicia (câmera + YOLO + socket);
 * - se já há um socket conectado, encerra tudo.
 */
function onConnectButtonClick() {

    if (socket) {

        disconnectSocket();

        return;
    }


    connect();
}


/* ============================================================
 * MONITORAMENTO AO VIVO (YOLO no backend)
 * ============================================================ */

/*
 * Aciona o reconhecimento ao vivo no backend (live_monitor.py, via
 * apiFlask_dev.py). É isso que abre a câmera e a janela do YOLO,
 * frame a frame — equivalente a rodar programV3_live_example.py
 * manualmente, só que disparado pelo próprio dashboard.
 */
async function requestStartMonitoring(
    dashboardUrl,
    name
) {

    const response =
        await fetch(
            `${dashboardUrl}/api/monitoring/start/${encodeURIComponent(name)}`,
            {
                method: "POST"
            }
        );


    const body =
        await response
            .json()
            .catch(
                () => ({})
            );


    if (!response.ok) {

        throw new Error(
            body.error ||
            `HTTP ${response.status}`
        );
    }


    return body;
}


/*
 * Pede pro backend encerrar o monitoramento ao vivo (fecha a câmera
 * e a janela do YOLO). "Fire and forget": se falhar, só loga —
 * não queremos travar o fluxo de desconexão do socket por causa disso.
 */
async function requestStopMonitoring(
    dashboardUrl
) {

    if (!dashboardUrl) {
        return;
    }


    try {

        await fetch(
            `${dashboardUrl}/api/monitoring/stop`,
            {
                method: "POST"
            }
        );

    } catch (error) {

        console.warn(
            "Erro ao parar monitoramento no backend:",
            error
        );
    }
}


/* ============================================================
 * SOCKET.IO
 * ============================================================ */

async function connect() {

    const name =
        els.envSelect.value;


    if (!name) {

        alert(
            "Selecione um ambiente primeiro."
        );

        return;
    }


    const dashboardUrl =
        getDashboardApiUrl();


    if (!dashboardUrl) {

        alert(
            "Informe a URL da API de monitoramento."
        );

        return;
    }


    persistUrls();


    disconnectSocket();


    latestStatus = [];

    lastUpdateAt = null;

    renderStatus();


    /*
     * Primeiro liga a câmera/YOLO no backend. Só abrimos o socket
     * se isso funcionar — assim, se a câmera estiver ocupada ou o
     * ambiente não tiver vagas configuradas, o usuário vê o erro
     * na hora em vez de ficar com o socket conectado e nada
     * acontecendo.
     */
    if (
        els.connectBtn
    ) {

        els.connectBtn.disabled =
            true;

        els.connectBtn.innerHTML =
            `
            <span class="spinner-border spinner-border-sm me-1"></span>
            Iniciando câmera...
            `;
    }


    try {

        await requestStartMonitoring(
            dashboardUrl,
            name
        );

    } catch (error) {

        alert(
            `Erro ao iniciar o monitoramento: ${error.message}`
        );


        if (
            els.connectBtn
        ) {

            els.connectBtn.disabled =
                false;

            els.connectBtn.innerHTML =
                `
                <i class="bi bi-play-circle me-1"></i>
                Iniciar monitoramento
                `;
        }

        return;
    }


    if (
        els.connectBtn
    ) {

        els.connectBtn.disabled =
            false;
    }


    socket =
        io(
            dashboardUrl,
            {
                transports: [
                    "websocket",
                    "polling"
                ],

                reconnection: true,

                reconnectionAttempts: Infinity,

                reconnectionDelay: 1000
            }
        );


    socket.on(
        "connect",
        () => {

            setConnectionStatus(
                true
            );


            setBadgeStatus(
                els.apiUrlBadge,
                true
            );


            els.connectBtn.innerHTML =
                `
                <i class="bi bi-broadcast me-1"></i>
                Monitoramento ativo
                `;
        }
    );


    socket.on(
        "disconnect",
        reason => {

            console.warn(
                "Socket desconectado:",
                reason
            );


            setConnectionStatus(
                false
            );


            setBadgeStatus(
                els.apiUrlBadge,
                false
            );


            els.connectBtn.innerHTML =
                `
                <i class="bi bi-play-circle me-1"></i>
                Iniciar monitoramento
                `;
        }
    );


    socket.on(
        "connect_error",
        error => {

            console.error(
                "Erro Socket.IO:",
                error
            );


            setConnectionStatus(
                false
            );


            setBadgeStatus(
                els.apiUrlBadge,
                false
            );
        }
    );


    socket.on(
        "update",
        data => {

            /*
             * Aceita diretamente:
             *
             * [
             *   { id: "vaga-01", used: 0 }
             * ]
             *
             * e também respostas que eventualmente
             * venham dentro de "spaces" ou "status".
             */
            if (
                Array.isArray(data)
            ) {

                latestStatus =
                    data;

            } else if (
                data &&
                Array.isArray(
                    data.spaces
                )
            ) {

                latestStatus =
                    data.spaces;

            } else if (
                data &&
                Array.isArray(
                    data.status
                )
            ) {

                latestStatus =
                    data.status;

            } else {

                latestStatus =
                    [];
            }


            lastUpdateAt =
                Date.now();


            renderStatus();
        }
    );
}


function disconnectSocket() {

    if (!socket) {
        return;
    }


    try {

        socket.removeAllListeners();

        socket.disconnect();

    } catch (error) {

        console.warn(
            "Erro desconectando Socket.IO:",
            error
        );
    }


    socket =
        null;


    /*
     * Desliga a câmera/YOLO no backend também — sem isso, o
     * reconhecimento continuaria rodando (e a janela do OpenCV
     * aberta) mesmo com o dashboard "desconectado".
     */
    requestStopMonitoring(
        getDashboardApiUrl()
    );


    setConnectionStatus(
        false
    );


    if (
        els.connectBtn
    ) {

        els.connectBtn.innerHTML =
            `
            <i class="bi bi-play-circle me-1"></i>
            Iniciar monitoramento
            `;
    }
}


function setConnectionStatus(
    connected
) {

    if (
        els.connDot
    ) {

        els.connDot.classList.toggle(
            "text-success",
            connected
        );


        els.connDot.classList.toggle(
            "text-danger",
            !connected
        );
    }


    if (
        els.connText
    ) {

        els.connText.textContent =
            connected
                ? "Conectado"
                : "Desconectado";
    }
}


/* ============================================================
 * STATUS DESATUALIZADO
 * ============================================================ */

function updateStaleIndicator() {

    if (
        lastUpdateAt === null
    ) {

        els.staleDot.classList.add(
            "d-none"
        );

        return;
    }


    const isStale =
        Date.now() -
        lastUpdateAt >
        STALE_AFTER_MS;


    els.staleDot.classList.toggle(
        "d-none",
        !isStale
    );
}


/* ============================================================
 * VIEWS
 * ============================================================ */

function switchView(
    view
) {

    const gridButton =
        document.getElementById(
            "gridViewBtn"
        );


    const mapButton =
        document.getElementById(
            "mapViewBtn"
        );


    if (gridButton) {

        gridButton.classList.toggle(
            "active",
            view === "grid"
        );
    }


    if (mapButton) {

        mapButton.classList.toggle(
            "active",
            view === "map"
        );
    }


    els.grid.classList.toggle(
        "d-none",
        view !== "grid"
    );


    els.mapWrap.classList.toggle(
        "d-none",
        view !== "map"
    );


    if (
        view === "map"
    ) {

        requestAnimationFrame(
            drawMap
        );
    }
}


/* ============================================================
 * STATUS DAS VAGAS
 * ============================================================ */

function renderStatus() {

    const hasStatus =
        latestStatus.length > 0;


    els.emptyState.classList.toggle(
        "d-none",
        hasStatus
    );


    const free =
        latestStatus.filter(
            space =>
                Number(
                    space.used
                ) === 0
        ).length;


    const occupied =
        latestStatus.filter(
            space =>
                Number(
                    space.used
                ) > 0
        ).length;


    els.freeCount.textContent =
        free;


    els.occupiedCount.textContent =
        occupied;


    if (els.totalCount) {

        els.totalCount.textContent =
            latestStatus.length;
    }


    /*
     * Card "Ocupação" (%) — calculado a partir dos mesmos dados,
     * sem depender de nenhuma informação nova do backend.
     */
    if (
        els.occupancyRate ||
        els.occupancyBar
    ) {

        const total =
            free + occupied;

        const rate =
            total > 0
                ? Math.round((occupied / total) * 100)
                : 0;


        if (els.occupancyRate) {

            els.occupancyRate.textContent =
                total > 0
                    ? `${rate}%`
                    : "–";
        }


        if (els.occupancyBar) {

            els.occupancyBar.style.width =
                `${rate}%`;
        }
    }


    renderVagasSidebar();


    if (
        lastUpdateAt
    ) {

        els.lastUpdate.textContent =
            new Date(
                lastUpdateAt
            ).toLocaleTimeString(
                "pt-BR"
            );

    } else {

        els.lastUpdate.textContent =
            "--:--:--";
    }


    updateStaleIndicator();


    els.grid.innerHTML =
        "";


    latestStatus.forEach(
        (
            space,
            index
        ) => {

            const isFree =
                Number(
                    space.used
                ) === 0;


            const name =
                space.id ||
                space.name ||
                `Vaga ${index + 1}`;


            const col =
                document.createElement(
                    "div"
                );


            col.className =
                "col";


            const card =
                document.createElement(
                    "div"
                );


            card.className =
                `card parking-space ${isFree
                    ? "free"
                    : "occupied"
                } h-100 shadow-sm`;


            const body =
                document.createElement(
                    "div"
                );


            body.className =
                "card-body text-center d-flex flex-column justify-content-center";


            const icon =
                document.createElement(
                    "i"
                );


            icon.className =
                `bi ${isFree
                    ? "bi-check-circle text-success"
                    : "bi-car-front-fill text-danger"
                } fs-3 mb-2`;


            const title =
                document.createElement(
                    "div"
                );


            title.className =
                "fw-semibold text-truncate";


            title.textContent =
                name;


            const status =
                document.createElement(
                    "div"
                );


            status.className =
                `space-status ${isFree
                    ? "text-success"
                    : "text-danger"
                }`;


            status.textContent =
                isFree
                    ? "Livre"
                    : "Ocupada";


            body.append(
                icon,
                title,
                status
            );


            card.appendChild(
                body
            );


            col.appendChild(
                card
            );


            els.grid.appendChild(
                col
            );
        }
    );


    if (
        !els.mapWrap.classList.contains(
            "d-none"
        )
    ) {

        drawMap();
    }
}


/*
 * Lista compacta "Estado das vagas" da barra lateral.
 * Usa exatamente os mesmos dados de latestStatus já usados no
 * grid principal — é só uma segunda forma de visualizar a
 * mesma informação, lado a lado com a câmera/mapa.
 */
function renderVagasSidebar() {

    if (!els.vagasSidebarList) {
        return;
    }


    if (els.sidebarVagasCount) {

        els.sidebarVagasCount.textContent =
            latestStatus.length;
    }


    if (els.vagasSidebarEmpty) {

        els.vagasSidebarEmpty.classList.toggle(
            "d-none",
            latestStatus.length > 0
        );
    }


    els.vagasSidebarList.innerHTML =
        "";


    latestStatus.forEach(
        (
            space,
            index
        ) => {

            const isFree =
                Number(
                    space.used
                ) === 0;


            const name =
                space.id ||
                space.name ||
                `Vaga ${index + 1}`;


            const item =
                document.createElement(
                    "li"
                );


            item.className =
                "list-group-item";


            const dot =
                document.createElement(
                    "span"
                );

            dot.className =
                `sidebar-dot ${isFree ? "free" : "occupied"}`;


            const idSpan =
                document.createElement(
                    "span"
                );

            idSpan.className =
                "sidebar-vaga-id";

            idSpan.textContent =
                name;


            const statusSpan =
                document.createElement(
                    "span"
                );

            statusSpan.className =
                `sidebar-vaga-status ${isFree ? "free" : "occupied"}`;

            statusSpan.textContent =
                isFree
                    ? "Livre"
                    : "Ocupada";


            item.append(
                dot,
                idSpan,
                statusSpan
            );


            els.vagasSidebarList.appendChild(
                item
            );
        }
    );
}


/* ============================================================
 * MAPA
 * ============================================================ */

function drawMap() {

    const canvas =
        els.mapCanvas;


    if (
        !canvas
    ) {

        return;
    }


    const ctx =
        canvas.getContext(
            "2d"
        );


    if (
        !referenceImage
    ) {

        canvas.width =
            1;

        canvas.height =
            1;


        ctx.clearRect(
            0,
            0,
            1,
            1
        );


        return;
    }


    canvas.width =
        referenceImage.naturalWidth ||
        referenceImage.width;


    canvas.height =
        referenceImage.naturalHeight ||
        referenceImage.height;


    ctx.clearRect(
        0,
        0,
        canvas.width,
        canvas.height
    );


    ctx.drawImage(
        referenceImage,
        0,
        0,
        canvas.width,
        canvas.height
    );


    /*
     * Monta mapa:
     *
     * vaga-01 -> 0
     * vaga-02 -> 1
     *
     * etc.
     */
    const statusById =
        {};


    latestStatus.forEach(
        space => {

            if (
                !space
            ) {

                return;
            }


            const id =
                space.id ||
                space.name;


            if (
                id
            ) {

                statusById[id] =
                    Number(
                        space.used
                    );
            }
        }
    );


    Object.entries(
        regionsConfig
    ).forEach(
        (
            [
                name,
                points
            ]
        ) => {

            if (
                !Array.isArray(points) ||
                points.length < 3
            ) {

                return;
            }


            const occupied =
                statusById[name] > 0;


            drawMapRegion(
                ctx,
                name,
                points,
                occupied
            );
        }
    );
}


function drawMapRegion(
    ctx,
    name,
    points,
    occupied
) {

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


    ctx.closePath();


    ctx.fillStyle =
        occupied
            ? "rgba(220,53,69,.30)"
            : "rgba(25,135,84,.30)";


    ctx.strokeStyle =
        occupied
            ? "#dc3545"
            : "#198754";


    ctx.lineWidth =
        3;


    ctx.fill();

    ctx.stroke();


    /*
     * Pontos pequenos nos vértices.
     */
    points.forEach(
        point => {

            ctx.beginPath();


            ctx.arc(
                point[0],
                point[1],
                3,
                0,
                Math.PI * 2
            );


            ctx.fillStyle =
                occupied
                    ? "#dc3545"
                    : "#198754";


            ctx.fill();
        }
    );


    const center =
        getPolygonCenter(
            points
        );


    ctx.font =
        "bold 16px sans-serif";


    ctx.textAlign =
        "center";


    ctx.textBaseline =
        "middle";


    ctx.lineWidth =
        4;


    ctx.strokeStyle =
        "rgba(0,0,0,.75)";


    ctx.strokeText(
        name,
        center.x,
        center.y
    );


    ctx.fillStyle =
        "#fff";


    ctx.fillText(
        name,
        center.x,
        center.y
    );
}


function getPolygonCenter(
    points
) {

    return {

        x:
            points.reduce(
                (
                    sum,
                    point
                ) =>
                    sum +
                    Number(point[0]),
                0
            ) /
            points.length,

        y:
            points.reduce(
                (
                    sum,
                    point
                ) =>
                    sum +
                    Number(point[1]),
                0
            ) /
            points.length
    };
}


/* ============================================================
 * REDIMENSIONAMENTO
 * ============================================================ */

window.addEventListener(
    "resize",
    () => {

        if (
            !els.mapWrap.classList.contains(
                "d-none"
            )
        ) {

            drawMap();
        }
    }
);