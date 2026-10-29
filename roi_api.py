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
import io
import os
import re
import shutil
import sys
import tempfile
import threading
import time

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
        lowered = stabilize_value.strip().lower()
        if lowered in {"", "padrao", "padrão", "auto", "none", "null"}:
            # Usa o que está salvo no ambiente (editor de ROI).
            stabilize_value = None
        else:
            stabilize_value = lowered in {"1", "true", "sim", "yes", "ligada"}

    # Amostragem por tempo (ex.: 5 s, como em Rocha, 2025). Quando
    # informada, tem prioridade sobre frame_step.
    interval_s = body.get("interval_s")
    try:
        interval_s = float(interval_s) if interval_s not in (None, "", "null") else None
    except (TypeError, ValueError):
        interval_s = None
    if interval_s is not None and interval_s <= 0:
        interval_s = None

    # Intervalo em tempo REAL (como o de Rocha, 2025) e fator de aceleração
    # do vídeo. Em um vídeo 4x mais rápido, 5 s reais = 1,25 s de vídeo.
    def _float(key, default=None):
        value = body.get(key)
        try:
            return float(value) if value not in (None, "", "null") else default
        except (TypeError, ValueError):
            return default

    speed_factor = max(0.01, _float("speed_factor", 1.0) or 1.0)
    interval_real_s = _float("interval_real_s")
    if interval_real_s and interval_real_s > 0:
        interval_s = interval_real_s / speed_factor

    criterion = str(body.get("criterion", "sobreposicao")).strip().lower() or "sobreposicao"
    if criterion not in {"sobreposicao", "centro"}:
        return jsonify({"error": "Critério de ocupação inválido."}), 400

    tracking = body.get("tracking", False)
    if isinstance(tracking, str):
        tracking = tracking.strip().lower() in {"1", "true", "sim", "yes"}

    job_id = test_runner.start(
        environment,
        confidence=confidence,
        frame_step=frame_step,
        source_override=source_override,
        stabilize=stabilize_value,
        interval_s=interval_s,
        criterion=criterion,
        tracking=bool(tracking),
        extra={
            "label": str(body.get("label") or "").strip() or None,
            "speed_factor": speed_factor,
            "interval_real_s": interval_real_s if interval_real_s else (
                interval_s * speed_factor if interval_s else None
            ),
        }
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

    # Retorna as coordenadas originais das ROIs para desenhá-las sobre
    # cada quadro da revisão manual, mantendo os nomes das vagas.
    status = test_runner.get_status(job_id) or {}
    environment_name = status.get("environment", "")
    try:
        polygons = store.get_regions_for_program(environment_name)
    except (ValueError, KeyError, TypeError):
        polygons = {}
    # Testes antigos (sem rois.jsonl): as coordenadas estão em pixels do
    # vídeo original, mas a miniatura salva pode ter sido reduzida.
    scale = data.get("thumbnail_scale") or 1.0
    data["region_polygons"] = {
        name: [[round(x * scale, 1), round(y * scale, 1)] for x, y in points]
        for name, points in polygons.items()
    }
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


# ------------------------------------------------------------
# Experimentos: vídeos, frames e vagas usados na tela /tests
# ------------------------------------------------------------

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(BASE_DIR, "data")
TEST_VIDEOS_DIR = os.path.join(DATA_DIR, "videos_teste")
_video_cache = {}
_video_cache_lock = threading.Lock()


def _resolve_video_path(source):
    """Caminho absoluto de um vídeo existente; erro se não for um arquivo de vídeo."""
    source = str(source or "").strip()
    if not source:
        raise ValueError("Nenhum vídeo informado.")
    path = source if os.path.isabs(source) else os.path.join(BASE_DIR, source)
    path = os.path.normpath(path)
    if os.path.splitext(path)[1].lower() not in ALLOWED_VIDEO_EXTS:
        raise ValueError("O arquivo informado não é um vídeo suportado.")
    if not os.path.isfile(path):
        raise FileNotFoundError(f"Vídeo não encontrado: {source}")
    return path


def _video_info(path):
    stat = os.stat(path)
    key = (path, stat.st_mtime, stat.st_size)
    with _video_cache_lock:
        if key in _video_cache:
            return dict(_video_cache[key])

    capture = cv2.VideoCapture(path)
    try:
        if not capture.isOpened():
            raise RuntimeError("O OpenCV não conseguiu abrir este vídeo.")
        fps = capture.get(cv2.CAP_PROP_FPS) or 0
        frames = int(capture.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
        width = int(capture.get(cv2.CAP_PROP_FRAME_WIDTH) or 0)
        height = int(capture.get(cv2.CAP_PROP_FRAME_HEIGHT) or 0)
    finally:
        capture.release()

    rel = os.path.relpath(path, BASE_DIR) if path.startswith(BASE_DIR) else path
    info = {
        "path": path.replace("\\", "/"),
        "name": os.path.basename(path),
        "folder": os.path.dirname(rel).replace("\\", "/"),
        "size_mb": round(stat.st_size / (1024 * 1024), 1),
        "fps": round(fps, 3) if fps else None,
        "frames": frames,
        "duration_s": round(frames / fps, 2) if fps and frames else None,
        "width": width,
        "height": height,
        "modified": stat.st_mtime,
    }
    with _video_cache_lock:
        _video_cache[key] = info
    return dict(info)


def _read_frame_at(path, t=0.0):
    capture = cv2.VideoCapture(path)
    try:
        if not capture.isOpened():
            raise RuntimeError("Não foi possível abrir o vídeo.")
        fps = capture.get(cv2.CAP_PROP_FPS) or 0
        frames = int(capture.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
        if t and t > 0:
            if fps and frames:
                capture.set(cv2.CAP_PROP_POS_FRAMES, min(int(round(t * fps)), max(0, frames - 1)))
            else:
                capture.set(cv2.CAP_PROP_POS_MSEC, t * 1000.0)
        success, frame = capture.read()
        if not success or frame is None:
            capture.set(cv2.CAP_PROP_POS_FRAMES, 0)
            success, frame = capture.read()
        if not success or frame is None:
            raise RuntimeError("O vídeo não retornou nenhum quadro.")
        return frame
    finally:
        capture.release()


def _reference_size(name):
    path = store.get_reference_image_path(name)
    if not path:
        return None, None
    image = cv2.imread(path)
    if image is None:
        return None, None
    return int(image.shape[1]), int(image.shape[0])


def _environment_details(name):
    env = store.get_environment(name) or {}
    regions = {
        key: value for key, value in (env.get("regions") or {}).items()
        if key != "__detection__"
    }
    ref_w, ref_h = _reference_size(name)
    return {
        "name": name,
        "source": env.get("source", ""),
        "regions": regions,
        "detection": (env.get("regions") or {}).get("__detection__", {}),
        "ref_width": ref_w,
        "ref_height": ref_h,
    }


@app.route("/api/tests/videos", methods=["GET"])
def list_test_videos():
    """Vídeos dentro de data/ e os vídeos salvos como fonte dos ambientes."""
    paths = set()
    if os.path.isdir(DATA_DIR):
        for root, _dirs, files in os.walk(DATA_DIR):
            for filename in files:
                if os.path.splitext(filename)[1].lower() in ALLOWED_VIDEO_EXTS:
                    paths.add(os.path.normpath(os.path.join(root, filename)))
    for name in store.list_environments():
        source = (store.get_environment(name) or {}).get("source", "")
        try:
            paths.add(_resolve_video_path(source))
        except (ValueError, FileNotFoundError):
            pass

    videos, problems = [], []
    for path in sorted(paths, key=lambda item: os.path.basename(item).lower()):
        try:
            videos.append(_video_info(path))
        except Exception as error:
            problems.append({"path": path, "error": str(error)})
    return jsonify({"videos": videos, "problems": problems})


@app.route("/api/tests/videos", methods=["POST"])
def upload_test_video():
    uploaded = request.files.get("file")
    if uploaded is None or not uploaded.filename:
        return jsonify({"error": "Nenhum arquivo enviado."}), 400
    filename = secure_filename(uploaded.filename)
    stem, ext = os.path.splitext(filename)
    if ext.lower() not in ALLOWED_VIDEO_EXTS:
        return jsonify({"error": f"Formato não suportado: {ext or '(sem extensão)'}."}), 400
    os.makedirs(TEST_VIDEOS_DIR, exist_ok=True)
    dest = os.path.join(TEST_VIDEOS_DIR, filename)
    counter = 2
    while os.path.exists(dest):
        dest = os.path.join(TEST_VIDEOS_DIR, f"{stem}_{counter}{ext}")
        counter += 1
    uploaded.save(dest)
    try:
        return jsonify(_video_info(dest))
    except Exception as error:
        os.remove(dest)
        return jsonify({"error": f"Vídeo inválido: {error}"}), 400


@app.route("/api/tests/video-frame", methods=["GET"])
def test_video_frame():
    try:
        path = _resolve_video_path(request.args.get("source"))
        t = float(request.args.get("t", 0) or 0)
        max_width = int(request.args.get("max_width", 0) or 0)
        frame = _read_frame_at(path, t)
    except (ValueError, FileNotFoundError) as error:
        return jsonify({"error": str(error)}), 400
    except RuntimeError as error:
        return jsonify({"error": str(error)}), 500

    if max_width and frame.shape[1] > max_width:
        scale = max_width / frame.shape[1]
        frame = cv2.resize(frame, (max_width, int(round(frame.shape[0] * scale))))
    success, encoded = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 88])
    if not success:
        return jsonify({"error": "Falha ao converter o quadro."}), 500
    return send_file(io.BytesIO(encoded.tobytes()), mimetype="image/jpeg")


@app.route("/api/tests/environments", methods=["GET"])
def list_test_environments():
    return jsonify([_environment_details(name) for name in store.list_environments()])


_ENV_NAME = re.compile(r"^[\w\-]{1,60}$", re.UNICODE)


@app.route("/api/tests/environments/<name>", methods=["POST"])
def save_test_environment(name):
    """
    Salva as vagas desenhadas sobre um quadro do vídeo de teste.

    O próprio quadro (instante t do vídeo) vira a imagem de referência do
    ambiente, o que garante que as coordenadas das vagas estão na mesma
    resolução do vídeo que será analisado.
    """
    if not _ENV_NAME.match(name or ""):
        return jsonify({"error": "Use só letras, números, - e _ no nome do ambiente (sem espaços)."}), 400
    body = request.get_json(silent=True) or {}
    regions = body.get("regions") or {}
    if not isinstance(regions, dict) or not regions:
        return jsonify({"error": "Desenhe pelo menos uma vaga antes de salvar."}), 400

    clean = {}
    for region_name, points in regions.items():
        region_name = str(region_name).strip()
        if not region_name or region_name == "__detection__":
            return jsonify({"error": "Nome de vaga inválido."}), 400
        if not isinstance(points, list) or len(points) < 3:
            return jsonify({"error": f"A vaga {region_name} precisa de pelo menos 3 pontos."}), 400
        try:
            clean[region_name] = [[int(round(float(x))), int(round(float(y)))] for x, y in points]
        except (TypeError, ValueError):
            return jsonify({"error": f"Coordenadas inválidas na vaga {region_name}."}), 400

    try:
        path = _resolve_video_path(body.get("source"))
        frame = _read_frame_at(path, float(body.get("t", 0) or 0))
    except (ValueError, FileNotFoundError, RuntimeError) as error:
        return jsonify({"error": str(error)}), 400

    current = store.get_environment(name) or {}
    detection = dict((current.get("regions") or {}).get("__detection__", {}))
    detection.setdefault("confidence", 0.4)
    detection.setdefault("fps", 5)
    for key in ("stabilize", "projection", "fov"):
        if key in (body.get("detection") or {}):
            detection[key] = body["detection"][key]
    detection.setdefault("stabilize", False)
    detection.setdefault("projection", "perspective")
    detection.setdefault("fov", None)
    clean["__detection__"] = detection

    success, encoded = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 95])
    if not success:
        return jsonify({"error": "Falha ao salvar o quadro de referência."}), 500
    store.save_reference_image(name, encoded.tobytes())
    store.save_environment(name, path.replace("\\", "/"), clean)
    return jsonify(_environment_details(name))


@app.route("/api/tests/<job_id>/cancel", methods=["POST"])
def cancel_test(job_id):
    if not test_runner.cancel(job_id):
        return jsonify({"error": "Este teste não está em execução."}), 409
    return jsonify({"cancelled": True})


@app.route("/api/tests/<job_id>", methods=["DELETE"])
def delete_test(job_id):
    try:
        deleted = test_runner.delete(job_id)
    except RuntimeError as error:
        return jsonify({"error": str(error)}), 409
    if not deleted:
        return jsonify({"error": "Teste não encontrado."}), 404
    return jsonify({"deleted": job_id})


@app.route("/api/tests/<job_id>/metadata", methods=["GET"])
def test_metadata(job_id):
    metadata = test_runner.get_metadata(job_id)
    if metadata is None:
        return jsonify({"error": "Teste não encontrado."}), 404
    return jsonify(metadata)


@app.route("/api/tests/<job_id>/overview", methods=["GET"])
def test_overview(job_id):
    overview = test_runner.get_overview(job_id)
    if overview is None:
        return jsonify({"error": "Teste não encontrado ou ainda não concluído."}), 404
    return jsonify(overview)


@app.route("/api/tests/report", methods=["POST"])
def test_report():
    """Gera as tabelas/figuras do TCC para os testes escolhidos e devolve um .zip."""
    body = request.get_json(silent=True) or {}
    ids = [str(item) for item in (body.get("ids") or []) if str(item).strip()]
    if not ids:
        return jsonify({"error": "Selecione pelo menos um teste."}), 400
    try:
        import gerar_resultados_tcc
        result = gerar_resultados_tcc.gerar(
            ids,
            rotulos=body.get("labels") or None,
            permitir_incompleto=bool(body.get("allow_incomplete")),
            log=lambda message: print(f"[RELATÓRIO] {message}")
        )
    except ValueError as error:
        return jsonify({"error": str(error)}), 400

    out_dir = result["out_dir"]
    archive = shutil.make_archive(
        os.path.join(tempfile.gettempdir(), os.path.basename(out_dir)), "zip", out_dir
    )
    return send_file(
        archive, mimetype="application/zip", as_attachment=True,
        download_name=f"resultados_tcc_{os.path.basename(out_dir)}.zip"
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