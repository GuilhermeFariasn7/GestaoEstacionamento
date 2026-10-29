"""
API principal do Smart Parking.

Responsabilidades:
    - disponibilizar o dashboard;
    - disponibilizar os dados atuais das vagas;
    - monitorar region_status.json;
    - enviar atualizações para o dashboard usando Socket.IO.

A captura de imagens e configuração das ROIs ficam na roi_api.py.
"""

import json
import os
import threading
import time

from flask import Flask, jsonify, render_template, send_file
from flask_cors import CORS
from flask_socketio import SocketIO

import live_monitor
import evaluation_capture

app = Flask(
    __name__,
    template_folder="templates",
    static_folder="static"
)

CORS(app)

socketio = SocketIO(
    app,
    cors_allowed_origins="*",
    async_mode="threading"
)


BASE_DIR = os.path.dirname(os.path.abspath(__file__))

STATUS_FILE = os.path.join(
    BASE_DIR,
    "region_status.json"
)

_monitor_started = False
_monitor_lock = threading.Lock()


def read_json():
    """
    Lê o arquivo de status das vagas.

    Se o arquivo ainda não existir ou estiver inválido,
    retornamos uma lista vazia para o dashboard continuar funcionando.
    """

    if not os.path.exists(STATUS_FILE):
        return []

    try:
        with open(
            STATUS_FILE,
            "r",
            encoding="utf-8"
        ) as file:
            data = json.load(file)

        if isinstance(data, list):
            return data

        return []

    except (json.JSONDecodeError, OSError) as error:
        print(f"[STATUS] Erro ao ler JSON: {error}")
        return []


def ensure_status_file():
    """Cria o arquivo de status caso ainda não exista."""

    if os.path.exists(STATUS_FILE):
        return

    with open(
        STATUS_FILE,
        "w",
        encoding="utf-8"
    ) as file:
        json.dump([], file, indent=2)


def monitor_file():
    """
    Fica observando o region_status.json.

    Quando o reconhecimento alterar o arquivo,
    o novo conteúdo é enviado automaticamente para
    todos os dashboards conectados.
    """

    print("[STATUS] Monitorando region_status.json...")

    ensure_status_file()

    last_modified = os.path.getmtime(STATUS_FILE)

    initial_data = read_json()

    print(
        f"[STATUS] Última alteração: "
        f"{time.ctime(last_modified)}"
    )

    print(
        f"[STATUS] Estado inicial: "
        f"{initial_data}"
    )

    # Envia o estado inicial para quem já estiver conectado.
    socketio.emit(
        "update",
        initial_data
    )

    while True:

        try:
            current_modified = os.path.getmtime(
                STATUS_FILE
            )

            if current_modified != last_modified:

                last_modified = current_modified

                data = read_json()

                print(
                    f"[STATUS] Atualização detectada: "
                    f"{data}"
                )

                socketio.emit(
                    "update",
                    data
                )

        except OSError as error:
            print(
                f"[STATUS] Erro monitorando arquivo: "
                f"{error}"
            )

        time.sleep(1)


def start_status_monitor():
    """
    Inicia o monitoramento apenas uma vez.

    Isso evita que o app.py crie duas threads observando
    o mesmo arquivo.
    """

    global _monitor_started

    with _monitor_lock:

        if _monitor_started:
            return

        _monitor_started = True

        thread = threading.Thread(
            target=monitor_file,
            daemon=True
        )

        thread.start()


@app.route("/")
def dashboard():
    """Página principal do sistema."""

    return render_template(
        "parking_dashboard.html"
    )


@app.route("/roi")
def roi_editor():
    """Página de configuração das vagas."""

    return render_template(
        "roi_editor.html"
    )


@app.route("/tests")
def tests_page():
    """Página independente para os experimentos e testes do TCC.

    Esta página não faz parte do fluxo do usuário final e não é aberta
    automaticamente pelo sistema. Pode ser acessada diretamente por
    http://127.0.0.1:5000/tests.
    """
    return render_template("tests.html")


@app.route("/tests/<job_id>/review")
def test_review_page(job_id):
    """Endereço antigo da revisão: a análise agora fica na etapa 4 da tela /tests."""
    from flask import redirect
    return redirect(f"/tests?job={job_id}")


@app.route("/data")
def get_data():
    """Retorna o estado atual das vagas em JSON."""

    return jsonify(
        read_json()
    )


@app.route("/api/monitoring/start/<name>", methods=["POST"])
def start_monitoring(name):
    """
    Inicia o reconhecimento (YOLO) ao vivo para o ambiente informado.

    É isso que o botão "Iniciar monitoramento" do dashboard chama antes
    de abrir a conexão Socket.IO. Substitui a necessidade de rodar
    programV3_live_example.py manualmente em outro terminal.
    """

    try:
        result = live_monitor.start(name)
        return jsonify(result)

    except Exception as error:
        print(f"[MONITOR] Erro ao iniciar monitoramento: {error}")

        return jsonify({
            "running": False,
            "error": str(error)
        }), 500


@app.route("/api/monitoring/stop", methods=["POST"])
def stop_monitoring():
    """Encerra o reconhecimento ao vivo, se houver algum rodando."""

    result = live_monitor.stop()
    return jsonify(result)


@app.route("/api/monitoring/status", methods=["GET"])
def monitoring_status():
    """
    Estado atual do monitoramento ao vivo.

    Útil para o dashboard verificar, ao carregar a página, se já
    existe um monitoramento rodando em segundo plano (ex.: após dar
    F5 na página com o monitoramento ainda ativo).
    """

    return jsonify(
        live_monitor.status()
    )




@app.route("/api/evaluation/status")
def evaluation_status():
    return jsonify(evaluation_capture.status())


@app.route("/api/evaluation/start", methods=["POST"])
def evaluation_start():
    from flask import request
    body = request.get_json(silent=True) or {}
    environment = str(body.get("environment", "")).strip()
    if not environment:
        return jsonify({"error": "Selecione um ambiente no dashboard."}), 400
    monitor = live_monitor.status()
    if not monitor.get("running") or monitor.get("environment") != environment:
        return jsonify({"error": "Inicie o monitoramento desse ambiente antes de iniciar a coleta."}), 400
    try:
        return jsonify(evaluation_capture.start(environment, body.get("sample_every", 5)))
    except (RuntimeError, ValueError) as error:
        return jsonify({"error": str(error)}), 409


@app.route("/api/evaluation/stop", methods=["POST"])
def evaluation_stop():
    return jsonify(evaluation_capture.stop())


@app.route("/api/evaluation/predictions.csv")
def evaluation_predictions_csv():
    paths = evaluation_capture.latest_paths()
    if not paths or not os.path.isfile(paths["csv"]):
        return jsonify({"error": "Nenhuma coleta encontrada."}), 404
    return send_file(paths["csv"], as_attachment=True, download_name=f"previsoes_{paths['id']}.csv")


@app.route("/api/evaluation/gabarito.csv")
def evaluation_ground_truth_template():
    try:
        path = evaluation_capture.create_ground_truth_template()
    except (RuntimeError, OSError, ValueError) as error:
        return jsonify({"error": str(error)}), 400
    paths = evaluation_capture.latest_paths()
    return send_file(path, as_attachment=True, download_name=f"gabarito_{paths['id']}.csv")


@app.route("/health")
def health():
    """Endpoint simples para verificar se a API está funcionando."""

    return jsonify({
        "status": "ok",
        "service": "smart-parking"
    })


if __name__ == "__main__":
    start_status_monitor()

    socketio.run(
        app,
        host="127.0.0.1",
        port=5000,
        debug=False,
        allow_unsafe_werkzeug=True
    )