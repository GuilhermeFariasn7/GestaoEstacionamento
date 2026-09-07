"""
Executa o reconhecimento (YOLO) ao vivo para um ambiente configurado,
em uma thread separada, controlada pelo dashboard.

É este módulo que o botão "Iniciar monitoramento" do dashboard aciona
(via apiFlask_dev.py). Ele substitui a necessidade de rodar
programV3_live_example.py manualmente em um segundo terminal: a lógica
de captura de câmera + YOLO + salvar region_status.json é a mesma,
só que agora roda em background dentro do próprio processo do Flask.

A janela do OpenCV (cv2.imshow) continua abrindo normalmente na tela
do computador que está rodando o servidor — igual acontecia rodando
o script manualmente. Isso é esperado: o dashboard no navegador não
mostra vídeo, só o status (ver conversa/README sobre isso).
"""

import json
import os
import sys
import threading

import cv2

import regions_store as store
from regionCounterLive import RegionCounterLive


BASE_DIR = os.path.dirname(os.path.abspath(__file__))

STATUS_FILE = os.path.join(BASE_DIR, "region_status.json")

# Mesmo ajuste usado na roi_api.py: no Windows, o backend padrão (MSMF)
# trava/erra em várias webcams; DirectShow é mais confiável.
_CAM_BACKEND = cv2.CAP_DSHOW if sys.platform == "win32" else cv2.CAP_ANY

# Mesmos valores usados no programV3_live_example.py.
# Ajuste aqui se quiser mudar a fluidez/uso de CPU-GPU globalmente.
DETECT_EVERY_N_FRAMES = 3
SAVE_JSON_EVERY_N_DETECTIONS = 5


# RLock (e não Lock comum) porque start() pode precisar encerrar um
# monitoramento anterior antes de iniciar o novo, e essa parte reusa
# o mesmo trecho de código protegido pelo lock.
_lock = threading.RLock()

_thread = None
_stop_event = None
_current_environment = None
_last_error = None


def _open_source(source):
    """
    Abre a fonte de vídeo/câmera.

    Mesma regra usada na roi_api.py: se for só um número, trata
    como índice de webcam; senão, trata como caminho/URL de vídeo.
    """

    source = str(source).strip()

    if source.isdigit():
        return cv2.VideoCapture(int(source), _CAM_BACKEND)

    return cv2.VideoCapture(source)


def _save_region_status(data):
    """
    Salva o resultado no region_status.json.

    O apiFlask_dev.py fica de olho nesse arquivo (monitor_file) e
    avisa o dashboard via Socket.IO sempre que ele muda — não é
    necessário emitir nada diretamente daqui.
    """

    with open(STATUS_FILE, "w", encoding="utf-8") as file:
        json.dump(data, file)


def _run(environment_name, stop_event):
    """Loop principal: roda em uma thread separada até stop_event ser sinalizado."""

    global _last_error

    env = store.get_environment(environment_name)

    if env is None:
        _last_error = f"Ambiente '{environment_name}' não encontrado."
        print(f"[MONITOR] {_last_error}")
        return

    coordinates = store.get_regions_for_program(environment_name)

    if not coordinates:
        _last_error = f"Nenhuma vaga configurada para '{environment_name}'."
        print(f"[MONITOR] {_last_error}")
        return

    capture = _open_source(env["source"])

    if not capture.isOpened():
        _last_error = f"Não foi possível abrir a fonte: {env['source']}"
        print(f"[MONITOR] {_last_error}")
        capture.release()
        return

    live_counter = RegionCounterLive(
        show=True,
        region=coordinates,
        model="yolo12x.pt",
        classes=[2]
    )

    print(f"[MONITOR] Monitoramento iniciado: {environment_name}")

    frame_counter = 0
    detection_counter = 0

    try:

        while capture.isOpened() and not stop_event.is_set():

            success, frame = capture.read()

            if not success:
                _last_error = "A fonte de vídeo parou de responder."
                print(f"[MONITOR] {_last_error}")
                break

            frame_counter += 1

            if frame_counter % DETECT_EVERY_N_FRAMES == 0:

                results = live_counter.process(frame)

                detection_counter += 1

                if detection_counter % SAVE_JSON_EVERY_N_DETECTIONS == 0:

                    result = [
                        {"id": key, "used": value}
                        for key, value in results.region_counts.items()
                    ]

                    _save_region_status(result)

            # Necessário para a janela do OpenCV atualizar e continuar
            # respondendo (mesmo papel do cv2.waitKey nos scripts manuais).
            cv2.waitKey(1)

    finally:

        capture.release()
        cv2.destroyAllWindows()

        print(f"[MONITOR] Monitoramento encerrado: {environment_name}")


def _stop_locked():
    """Encerra a thread atual. Deve ser chamado com _lock já adquirido."""

    global _thread, _current_environment

    if _thread is not None:

        _stop_event.set()
        _thread.join(timeout=5)

    _thread = None
    _current_environment = None


def start(environment_name):
    """
    Inicia o monitoramento ao vivo para um ambiente.

    Se já houver um monitoramento rodando para outro ambiente,
    ele é encerrado antes de iniciar o novo (só faz sentido observar
    uma câmera de cada vez neste projeto).
    """

    global _thread, _stop_event, _current_environment, _last_error

    with _lock:

        if _thread is not None and _thread.is_alive():

            if _current_environment == environment_name:

                return {
                    "running": True,
                    "environment": _current_environment,
                    "already_running": True
                }

            _stop_locked()

        _last_error = None
        _stop_event = threading.Event()
        _current_environment = environment_name

        _thread = threading.Thread(
            target=_run,
            args=(environment_name, _stop_event),
            daemon=True
        )

        _thread.start()

    return {
        "running": True,
        "environment": environment_name
    }


def stop():
    """Encerra o monitoramento ao vivo, se houver algum rodando."""

    with _lock:

        was_running = (
            _thread is not None and
            _thread.is_alive()
        )

        _stop_locked()

    return {
        "running": False,
        "was_running": was_running
    }


def status():
    """Estado atual do monitoramento (usado pelo dashboard ao carregar a página)."""

    with _lock:

        running = (
            _thread is not None and
            _thread.is_alive()
        )

        return {
            "running": running,
            "environment": _current_environment if running else None,
            "last_error": _last_error
        }
