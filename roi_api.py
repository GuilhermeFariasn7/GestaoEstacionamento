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

from flask_cors import CORS

import regions_store as store


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