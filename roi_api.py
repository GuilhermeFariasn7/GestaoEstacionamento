"""
API de configuração dos ambientes e ROIs.

Responsabilidades:

    - criar ambientes;
    - editar ambientes;
    - excluir ambientes;
    - salvar ROIs;
    - capturar frame da câmera/vídeo;
    - salvar o frame de referência.

O navegador não precisa executar nenhum .py.
Ele apenas chama estas rotas.
"""

import base64
import os
import sys

import cv2

from flask import (
    Flask,
    jsonify,
    request,
    send_file
)
from werkzeug.utils import secure_filename

from flask_cors import CORS

import regions_store as store
import test_runner


app = Flask(__name__)

CORS(app)

# No Windows, o backend padrão (MSMF) trava com "abriu mas não
# conseguiu ler frame" em algumas câmeras/webcams. DirectShow é
# mais confiável nesse ambiente.
_CAM_BACKEND = cv2.CAP_DSHOW if sys.platform == "win32" else cv2.CAP_ANY


def open_video_source(source):
    """
    Abre uma fonte de vídeo.

    Aceitamos:
        0
        1
        2

    para webcams, além de:

        arquivo.mp4
        URL de câmera IP
        RTSP
        etc.
    """

    source = str(source).strip()

    if not source:
        raise ValueError(
            "Fonte da câmera não informada."
        )

    # Se o usuário colocou apenas um número,
    # tratamos como índice da webcam.
    if source.isdigit():

        source_value = int(source)
        capture = cv2.VideoCapture(source_value, _CAM_BACKEND)

    else:

        source_value = source
        capture = cv2.VideoCapture(source_value)

    if not capture.isOpened():

        capture.release()

        raise RuntimeError(
            f"Não foi possível abrir a fonte: {source}"
        )

    return capture


def capture_frame_from_source(source):
    """Captura um frame da fonte configurada."""

    capture = open_video_source(
        source
    )

    try:

        # Algumas câmeras precisam de alguns frames
        # para começar a entregar uma imagem válida.
        frame = None

        for _ in range(10):

            success, current_frame = (
                capture.read()
            )

            if success and current_frame is not None:
                frame = current_frame

        if frame is None:

            raise RuntimeError(
                "A câmera foi aberta, mas não retornou imagem."
            )

        success, encoded = cv2.imencode(
            ".jpg",
            frame
        )

        if not success:

            raise RuntimeError(
                "Não foi possível converter o frame para JPEG."
            )

        return encoded.tobytes()

    finally:

        capture.release()


@app.route(
    "/api/environments",
    methods=["GET"]
)
def list_environments():

    return jsonify(
        store.list_environments()
    )


@app.route(
    "/api/environments/<name>",
    methods=["GET"]
)
def get_environment(name):

    environment = store.get_environment(
        name
    )

    if environment is None:

        return jsonify({
            "error": "Ambiente não encontrado."
        }), 404

    return jsonify(
        environment
    )


@app.route(
    "/api/environments/<name>",
    methods=["POST"]
)
def save_environment(name):

    body = request.get_json(
        silent=True
    ) or {}

    source = str(
        body.get(
            "source",
            ""
        )
    ).strip()

    regions = body.get(
        "regions",
        {}
    )

    if not source:

        return jsonify({
            "error": "Informe a fonte da câmera ou vídeo."
        }), 400

    if not isinstance(
        regions,
        dict
    ):

        return jsonify({
            "error": "Formato de regiões inválido."
        }), 400

    try:

        saved = store.save_environment(
            name,
            source,
            regions
        )

        return jsonify(
            saved
        )

    except ValueError as error:

        return jsonify({
            "error": str(error)
        }), 400


@app.route(
    "/api/environments/<name>",
    methods=["DELETE"]
)
def delete_environment(name):

    deleted = store.delete_environment(
        name
    )

    if not deleted:

        return jsonify({
            "error": "Ambiente não encontrado."
        }), 404

    return jsonify({
        "deleted": name
    })


@app.route(
    "/api/environments/<name>/capture-frame",
    methods=["POST"]
)
def capture_frame(name):

    environment = store.get_environment(
        name
    )

    if not environment:

        return jsonify({
            "error": "Ambiente não encontrado."
        }), 404

    source = environment.get(
        "source"
    )

    try:

        image_bytes = capture_frame_from_source(
            source
        )

        # O mesmo frame usado pelo editor
        # fica salvo como referência do ambiente.
        store.save_reference_image(
            name,
            image_bytes
        )

        return send_file(
            __import__("io").BytesIO(image_bytes),
            mimetype="image/jpeg",
            as_attachment=False,
            download_name=f"{name}.jpg"
        )

    except Exception as error:

        print(
            f"[ROI] Erro ao capturar frame: {error}"
        )

        return jsonify({
            "error": str(error)
        }), 500

# ------------------------------------------------------------
# Upload de vídeo do ambiente
# ------------------------------------------------------------

ALLOWED_VIDEO_EXTS = {".mp4", ".avi", ".mov", ".mkv", ".webm"}


@app.route(
    "/api/environments/<name>/video",
    methods=["POST"]
)
def upload_environment_video(name):
    """
    Recebe um arquivo de vídeo via multipart/form-data e salva
    em data/<name>/<arquivo>. Retorna o caminho relativo que
    deve ser usado como `source` do ambiente.

    O frontend chama esta rota ANTES de salvar o ambiente,
    para depois enviar o `source` já apontando para o arquivo
    correto em POST /api/environments/<name>.
    """

    uploaded = request.files.get("file")

    if uploaded is None or not uploaded.filename:
        return jsonify({
            "error": "Nenhum arquivo enviado."
        }), 400

    ext = os.path.splitext(uploaded.filename)[1].lower()

    if ext not in ALLOWED_VIDEO_EXTS:
        return jsonify({
            "error": (
                f"Formato não suportado: {ext}. "
                f"Use: {', '.join(sorted(ALLOWED_VIDEO_EXTS))}"
            )
        }), 400

    # Garante um nome de ambiente seguro (não deixa escapar de data/).
    safe_name = "".join(
        c for c in name
        if c.isalnum() or c in ("-", "_")
    )

    if not safe_name:
        return jsonify({
            "error": "Nome de ambiente inválido."
        }), 400

    filename = secure_filename(uploaded.filename)

    if not filename:
        return jsonify({
            "error": "Nome de arquivo inválido."
        }), 400

    env_dir = os.path.join(
        os.path.dirname(os.path.abspath(__file__)),
        "data",
        safe_name
    )

    os.makedirs(env_dir, exist_ok=True)

    dest = os.path.join(env_dir, filename)

    uploaded.save(dest)

    # O cv2.VideoCapture aceita tanto caminho absoluto quanto relativo.
    # Mandamos o caminho absoluto para não depender do cwd do servidor.
    source = dest.replace("\\", "/")

    return jsonify({
        "source": source,
        "filename": filename,
        "size": os.path.getsize(dest)
    }), 200
@app.route(
    "/api/environments/<name>/reference-image",
    methods=["POST"]
)
def save_reference_image(name):

    body = request.get_json(
        silent=True
    ) or {}

    data_url = body.get(
        "image_base64",
        ""
    )

    if not data_url:

        return jsonify({
            "error": "Imagem não enviada."
        }), 400

    try:

        if "," in data_url:

            data_url = data_url.split(
                ",",
                1
            )[1]

        image_bytes = base64.b64decode(
            data_url
        )

        store.save_reference_image(
            name,
            image_bytes
        )

        return jsonify({
            "saved": True
        })

    except Exception as error:

        return jsonify({
            "error": f"Imagem inválida: {error}"
        }), 400


@app.route(
    "/api/environments/<name>/reference-image",
    methods=["GET"]
)
def get_reference_image(name):

    path = store.get_reference_image_path(
        name
    )

    if path is None:

        return jsonify({
            "error": "Imagem de referência não encontrada."
        }), 404

    return send_file(
        path,
        mimetype="image/jpeg"
    )


@app.route(
    "/api/tests",
    methods=["GET"]
)
def list_tests():
    """
    Histórico de testes já executados, mais recentes primeiro.

    Usado pela página de Testes para montar a tabela de comparação
    entre vídeos/ambientes (acurácia, precisão, status de cada teste).
    """
    try:
        limit = int(request.args.get("limit", 200))
    except (TypeError, ValueError):
        limit = 200

    return jsonify(
        test_runner.list_jobs(limit=max(1, limit))
    )


@app.route(
    "/api/tests",
    methods=["POST"]
)
def create_test():
    """
    Inicia um teste em background para um ambiente já configurado.

    Corpo esperado (JSON):
        environment (obrigatório): nome do ambiente com vagas salvas.
        confidence (opcional): confiança do YOLO, padrão 0.4.
        frame_step (opcional): analisa 1 a cada N frames, padrão 5.
        source (opcional): caminho/URL de vídeo alternativo. Permite
            testar o mesmo conjunto de vagas contra um vídeo/ângulo
            diferente do que está salvo no ambiente.
    """

    if request.content_type and request.content_type.startswith("multipart/form-data"):
        body = request.form.to_dict()
    else:
        body = request.get_json(silent=True) or {}

    environment = str(body.get("environment", "")).strip()

    if not environment:
        return jsonify({
            "error": "Informe o ambiente a ser testado."
        }), 400

    if store.get_environment(environment) is None:
        return jsonify({
            "error": "Ambiente não encontrado."
        }), 404

    try:
        confidence = float(body.get("confidence", 0.4))
    except (TypeError, ValueError):
        confidence = 0.4
    confidence = max(0.01, min(1.0, confidence))

    try:
        frame_step = int(body.get("frame_step", 5))
    except (TypeError, ValueError):
        frame_step = 5
    frame_step = max(1, frame_step)

    source_override = body.get("source") or None

    # Permite selecionar um vídeo diretamente pelo navegador. O arquivo
    # fica dentro do diretório do projeto e passa a ser tratado como uma
    # fonte comum pelo test_runner.
    uploaded = request.files.get("video")
    if uploaded is not None and uploaded.filename:
        filename = secure_filename(uploaded.filename)
        if not filename:
            return jsonify({"error": "Nome de vídeo inválido."}), 400
        upload_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "test_uploads")
        os.makedirs(upload_dir, exist_ok=True)
        upload_path = os.path.join(upload_dir, f"{int(__import__('time').time())}_{filename}")
        uploaded.save(upload_path)
        source_override = upload_path

    stabilize_value = body.get("stabilize", None)
    if isinstance(stabilize_value, str):
        stabilize_value = stabilize_value.lower() in {"1", "true", "sim", "yes"}

    job_id = test_runner.start(
        environment,
        confidence=confidence,
        frame_step=frame_step,
        source_override=source_override,
        stabilize=stabilize_value
    )

    return jsonify({
        "id": job_id
    })


@app.route(
    "/api/tests/<job_id>/samples",
    methods=["GET"]
)
def get_test_samples(job_id):
    try:
        offset = int(request.args.get("offset", 0))
        limit = int(request.args.get("limit", 20))
    except (TypeError, ValueError):
        offset, limit = 0, 20

    data = test_runner.get_samples(job_id, offset=offset, limit=limit)
    if data is None:
        return jsonify({"error": "Teste não encontrado ou ainda não concluído."}), 404
    return jsonify(data)


@app.route(
    "/api/tests/<job_id>/samples/<filename>",
    methods=["GET"]
)
def get_test_sample_image(job_id, filename):
    path = test_runner.get_sample_image_path(job_id, filename)
    if path is None:
        return jsonify({"error": "Imagem da amostra não encontrada."}), 404
    return send_file(path, mimetype="image/jpeg")


@app.route(
    "/api/tests/<job_id>/ground-truth",
    methods=["POST"]
)
def save_test_ground_truth(job_id):
    body = request.get_json(silent=True) or {}
    annotations = body.get("annotations", body)
    if not test_runner.save_ground_truth(job_id, annotations):
        return jsonify({"error": "Não foi possível salvar o gabarito."}), 404
    return jsonify({"saved": True, "metrics": test_runner.get_metrics(job_id)})


@app.route(
    "/api/tests/<job_id>/metrics",
    methods=["GET"]
)
def get_test_metrics(job_id):
    metrics = test_runner.get_metrics(job_id)
    if metrics is None:
        return jsonify({"error": "Teste não encontrado ou sem resultado."}), 404
    return jsonify(metrics)


@app.route(
    "/api/tests/<job_id>/metrics/download",
    methods=["GET"]
)
def download_test_metrics(job_id):
    path = test_runner.get_metrics_csv_path(job_id)
    if path is None:
        return jsonify({"error": "Métricas ainda não disponíveis."}), 404
    return send_file(path, mimetype="text/csv", as_attachment=True, download_name=os.path.basename(path))


@app.route(
    "/api/tests/<job_id>",
    methods=["GET"]
)
def get_test(job_id):
    """Status/progresso de um teste em andamento ou já concluído."""

    status = test_runner.get_status(job_id)

    if status is None:
        return jsonify({
            "error": "Teste não encontrado."
        }), 404

    return jsonify(status)


@app.route(
    "/api/tests/<job_id>/download",
    methods=["GET"]
)
def download_test(job_id):
    """Baixa o CSV com o resultado (amostra a amostra) de um teste concluído."""

    csv_path = test_runner.get_csv_path(job_id)

    if csv_path is None:
        return jsonify({
            "error": "Teste não encontrado ou ainda não concluído."
        }), 404

    return send_file(
        csv_path,
        mimetype="text/csv",
        as_attachment=True,
        download_name=os.path.basename(csv_path)
    )


@app.route("/")
def index():

    return jsonify({
        "service": "Smart Parking ROI API",
        "status": "ok"
    })


if __name__ == "__main__":

    app.run(
        host="127.0.0.1",
        port=5001,
        debug=False
    )