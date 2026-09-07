"""
Camada de persistência das configurações de ROI (vagas) por ambiente.

Formato do arquivo regions_config.json:
{
    "unesc-bloco-c": {
        "source": "0",
        "regions": {
            "vaga-01": [[106, 303], [106, 192], [27, 199], [7, 309]],
            "__detection__": { "confidence": 0.5, "fps": 5 }
        }
    }
}

A chave "__detection__" (se existir) guarda parâmetros de detecção
configurados no editor (confiança, fps) e não é uma vaga -- os
scripts que consomem as regiões para o YOLO devem ignorá-la.
"""
import json
import os
import threading

CONFIG_PATH = "regions_config.json"
REFERENCE_IMAGES_DIR = "reference_frames"
_lock = threading.Lock()


def _load_all():
    if not os.path.exists(CONFIG_PATH):
        return {}
    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        try:
            return json.load(f)
        except json.JSONDecodeError:
            return {}


def _save_all(data):
    with open(CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


def list_environments():
    return list(_load_all().keys())


def get_environment(name):
    return _load_all().get(name)


def save_environment(name, source, regions):
    with _lock:
        data = _load_all()
        data[name] = {"source": source, "regions": regions}
        _save_all(data)
    return data[name]


def delete_environment(name):
    with _lock:
        data = _load_all()
        if name in data:
            del data[name]
            _save_all(data)
            return True
        return False


def save_reference_image(name, image_bytes):
    os.makedirs(REFERENCE_IMAGES_DIR, exist_ok=True)
    path = os.path.join(REFERENCE_IMAGES_DIR, f"{name}.jpg")
    with open(path, "wb") as f:
        f.write(image_bytes)
    return path


def get_reference_image_path(name):
    path = os.path.join(REFERENCE_IMAGES_DIR, f"{name}.jpg")
    return path if os.path.exists(path) else None


def get_regions_for_program(name):
    env = get_environment(name)
    if not env:
        raise ValueError(f"Ambiente '{name}' não encontrado em {CONFIG_PATH}")
    return {
        region_name: [tuple(point) for point in points]
        for region_name, points in env["regions"].items()
        if region_name != "__detection__"
    }


def get_detection_config(name):
    env = get_environment(name)
    if not env:
        return {}
    return env["regions"].get("__detection__", {})
