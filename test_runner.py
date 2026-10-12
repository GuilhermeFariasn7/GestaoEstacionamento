"""Execução e avaliação dos testes do Smart Parking."""

import csv
import json
import os
import threading
import time
import uuid

import cv2

import regions_store as store
from roi_stabilizer import build_stabilizer_for_environment

# RegionCounterLive (e, por dentro dela, o ultralytics/YOLO) só é
# importado dentro de _run_job, na hora em que um teste é de fato
# executado. Isso evita que um problema no ultralytics impeça a API
# inteira de subir, já que roi_api.py importa este módulo no topo do
# arquivo apenas para expor as rotas de configuração/histórico, que
# não dependem do YOLO.


BASE_DIR = os.path.dirname(os.path.abspath(__file__))
RESULTS_DIR = os.path.join(BASE_DIR, "test_results")

_FIXED_COLUMNS = {
    "sample", "frame", "timestamp_s", "tracking_estavel",
    "match_count", "image", "inliers", "modo_tracking",
    "movimento_camera", "vagas_fora_de_vista"
}
_THUMBNAIL_MAX_WIDTH = 1200

_jobs = {}
_jobs_lock = threading.Lock()


def _safe_job_dir(job_id):
    """Localiza o diretório do teste mesmo após reiniciar o servidor."""
    suffix = f"_{job_id}"
    if not os.path.isdir(RESULTS_DIR):
        return None
    for name in os.listdir(RESULTS_DIR):
        if name.endswith(suffix):
            path = os.path.join(RESULTS_DIR, name)
            if os.path.isdir(path):
                return path
    return None


def _resolve_job_dir(job_id):
    with _jobs_lock:
        job = _jobs.get(job_id)
        if job:
            return job.get("dir")
    return _safe_job_dir(job_id)


def _resolve_source(source):
    source = str(source).strip()
    if not source:
        return source
    if source.isdigit():
        return source
    if not os.path.isabs(source):
        return os.path.join(BASE_DIR, source)
    return source


def _open_source(source):
    source = _resolve_source(source)
    if str(source).isdigit():
        return cv2.VideoCapture(int(source))
    return cv2.VideoCapture(source)


def _build_stabilizer(environment_name, coordinates, enabled=None):
    return build_stabilizer_for_environment(
        environment_name,
        coordinates,
        enabled=enabled,
        log=lambda message: print(f"[TESTE] {message}")
    )


def _save_thumbnail(frame, path):
    height, width = frame.shape[:2]
    if width > _THUMBNAIL_MAX_WIDTH:
        scale = _THUMBNAIL_MAX_WIDTH / width
        frame = cv2.resize(frame, (int(width * scale), int(height * scale)))
    cv2.imwrite(path, frame, [cv2.IMWRITE_JPEG_QUALITY, 85])


def _write_csv(csv_path, region_names, rows):
    fieldnames = [
        "sample", "frame", "timestamp_s", "tracking_estavel",
        "match_count", "inliers", "modo_tracking", "movimento_camera",
        "vagas_fora_de_vista", "image"
    ] + list(region_names)
    with open(csv_path, "w", newline="", encoding="utf-8") as file:
        writer = csv.DictWriter(file, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def _build_summary(region_names, rows):
    summary = {
        name: {"ocupada": 0, "livre": 0, "fora_de_vista": 0}
        for name in region_names
    }
    for row in rows:
        for name in region_names:
            status = row.get(name)
            if status in summary[name]:
                summary[name][status] += 1
    return summary


def _write_metadata(job_dir, metadata):
    with open(os.path.join(job_dir, "metadata.json"), "w", encoding="utf-8") as file:
        json.dump(metadata, file, ensure_ascii=False, indent=2)


def _write_status_file(job_dir, job):
    """
    Grava em disco (status.json) o estado atual do job — em particular
    o status ("running"/"completed"/"error") e a mensagem real de erro.

    Sem isso, se o servidor for reiniciado enquanto um job está em
    execução ou logo após ele falhar, a mensagem de erro (guardada só
    em memória em `_jobs`) se perde e o histórico passa a mostrar um
    erro genérico ("Resultado não encontrado.") em vez do motivo real.
    """
    payload = {
        "id": job.get("id"),
        "environment": job.get("environment"),
        "status": job.get("status"),
        "progress": job.get("progress"),
        "rows": job.get("rows"),
        "error": job.get("error"),
        "summary": job.get("summary"),
        "source": job.get("source"),
        "created_at": job.get("created_at"),
    }
    try:
        with open(os.path.join(job_dir, "status.json"), "w", encoding="utf-8") as file:
            json.dump(payload, file, ensure_ascii=False, indent=2)
    except OSError:
        # A gravação do status é um "melhor esforço": se falhar (ex.:
        # disco cheio), não deve derrubar o teste em andamento.
        pass


def _read_status_file(job_dir):
    path = os.path.join(job_dir, "status.json")
    if not os.path.exists(path):
        return None
    try:
        with open(path, "r", encoding="utf-8") as file:
            return json.load(file)
    except (OSError, json.JSONDecodeError):
        return None


def _run_job(job_id, environment_name, confidence, frame_step, source_override, stabilize):
    with _jobs_lock:
        job = _jobs[job_id]

    job_dir = job["dir"]
    samples_dir = os.path.join(job_dir, "samples")
    capture = None

    try:
        env = store.get_environment(environment_name)
        if env is None:
            raise ValueError(f"Ambiente '{environment_name}' não encontrado.")

        coordinates = store.get_regions_for_program(environment_name)
        if not coordinates:
            raise ValueError(f"Nenhuma vaga configurada para '{environment_name}'.")

        source = (source_override or env.get("source", "")).strip()
        if not source:
            raise ValueError("Nenhuma fonte de vídeo informada ou salva no ambiente.")

        # Guarda a fonte já aqui (e não só ao final, com sucesso), para
        # que o histórico mostre qual vídeo foi usado mesmo quando o
        # teste falha (ex.: fonte inválida, arquivo não encontrado).
        with _jobs_lock:
            job["source"] = source

        capture = _open_source(source)
        if not capture.isOpened():
            raise RuntimeError(f"Não foi possível abrir a fonte: {source}")

        total_frames = int(capture.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
        video_fps = capture.get(cv2.CAP_PROP_FPS) or 0
        stabilizer = _build_stabilizer(environment_name, coordinates, stabilize)

        from regionCounterLive import RegionCounterLive

        counter = RegionCounterLive(
            region=coordinates,
            model="yolo12x.pt",
            classes=[2],
            conf=confidence,
            stabilizer=stabilizer
        )

        os.makedirs(samples_dir, exist_ok=True)
        rows = []
        frame_index = 0
        sample_index = 0

        metadata = {
            "id": job_id,
            "environment": environment_name,
            "source": source,
            "source_override": bool(source_override),
            "confidence": confidence,
            "frame_step": frame_step,
            "stabilize": bool(stabilizer is not None),
            "projection": (
                stabilizer.projection if stabilizer is not None
                else store.get_detection_config(environment_name).get("projection", "perspective")
            ),
            "fov": store.get_detection_config(environment_name).get("fov"),
            "created_at": time.time(),
            "regions": list(coordinates.keys())
        }
        _write_metadata(job_dir, metadata)

        while True:
            success, frame = capture.read()
            if not success:
                break

            if frame_index % frame_step == 0:
                # Mantém a amostra original para o gabarito manual.
                # A rotina de detecção pode desenhar informações sobre o frame.
                sample_frame = frame.copy()
                results = counter.process(frame)
                timestamp_s = frame_index / video_fps if video_fps else None
                image_name = f"{sample_index:06d}.jpg"
                _save_thumbnail(sample_frame, os.path.join(samples_dir, image_name))

                row = {
                    "sample": sample_index,
                    "frame": frame_index,
                    "timestamp_s": round(timestamp_s, 2) if timestamp_s is not None else "",
                    "tracking_estavel": (
                        bool(stabilizer.is_tracking) if stabilizer is not None else ""
                    ),
                    "match_count": (
                        stabilizer.last_match_count if stabilizer is not None else ""
                    ),
                    "inliers": (
                        stabilizer.last_inlier_count if stabilizer is not None else ""
                    ),
                    "modo_tracking": (
                        stabilizer.tracking_mode if stabilizer is not None else ""
                    ),
                    "movimento_camera": (
                        stabilizer.motion_magnitude if stabilizer is not None else ""
                    ),
                    "vagas_fora_de_vista": ";".join(
                        name for name, visible in
                        getattr(counter, "region_visibility", {}).items()
                        if not visible
                    ),
                    "image": image_name
                }

                visibility = getattr(counter, "region_visibility", {})

                for name in coordinates:
                    if not visibility.get(name, True):
                        # A câmera girou e a vaga saiu do quadro: não há
                        # como afirmar se está livre ou ocupada.
                        row[name] = "fora_de_vista"
                    elif results.region_counts.get(name, 0) > 0:
                        row[name] = "ocupada"
                    else:
                        row[name] = "livre"

                rows.append(row)
                sample_index += 1

                with _jobs_lock:
                    if total_frames:
                        job["progress"] = min(99, int(frame_index / total_frames * 100))
                    job["rows"] = sample_index

            frame_index += 1

        if not rows:
            raise RuntimeError("Nenhum frame foi lido da fonte de vídeo informada.")

        csv_path = os.path.join(job_dir, "data.csv")
        _write_csv(csv_path, coordinates.keys(), rows)
        summary = _build_summary(coordinates.keys(), rows)

        with _jobs_lock:
            job["status"] = "completed"
            job["progress"] = 100
            job["rows"] = len(rows)
            job["csv_path"] = csv_path
            job["summary"] = summary
            job["source"] = source

        with _jobs_lock:
            job_snapshot = dict(job)
        _write_status_file(job_dir, job_snapshot)

    except Exception as error:
        with _jobs_lock:
            job["status"] = "error"
            job["error"] = str(error)
            job_snapshot = dict(job)
        _write_status_file(job_dir, job_snapshot)
    finally:
        if capture is not None:
            capture.release()


def start(environment_name, confidence=0.4, frame_step=5, source_override=None, stabilize=None):
    job_id = uuid.uuid4().hex[:12]
    os.makedirs(RESULTS_DIR, exist_ok=True)
    job_dir = os.path.join(RESULTS_DIR, f"{environment_name}_{job_id}")
    os.makedirs(job_dir, exist_ok=True)

    with _jobs_lock:
        _jobs[job_id] = {
            "id": job_id,
            "environment": environment_name,
            "status": "running",
            "progress": 0,
            "rows": 0,
            "error": None,
            "csv_path": None,
            "summary": None,
            "dir": job_dir,
            "created_at": time.time()
        }
        job_snapshot = dict(_jobs[job_id])

    # Grava o status inicial imediatamente, para que mesmo um erro que
    # aconteça antes de qualquer outra escrita em disco (ex.: ambiente
    # não encontrado, fonte de vídeo vazia) já tenha um job_dir com
    # status.json quando `_run_job` atualizá-lo no except.
    _write_status_file(job_dir, job_snapshot)

    thread = threading.Thread(
        target=_run_job,
        args=(job_id, environment_name, confidence, max(1, int(frame_step or 1)), source_override, stabilize),
        daemon=True
    )
    thread.start()
    return job_id


def get_status(job_id):
    with _jobs_lock:
        job = _jobs.get(job_id)
        if job is None:
            job_dir = _safe_job_dir(job_id)
            if job_dir is None:
                return None

            # Preferimos o status.json: ele guarda o status e a mensagem
            # de erro reais no momento em que o job terminou (ou falhou),
            # mesmo que o servidor tenha sido reiniciado depois.
            persisted = _read_status_file(job_dir)
            if persisted is not None:
                return {
                    "id": job_id,
                    "environment": persisted.get("environment", ""),
                    "status": persisted.get("status", "error"),
                    "progress": persisted.get("progress", 0),
                    "rows": persisted.get("rows", 0),
                    "error": persisted.get("error"),
                    "summary": persisted.get("summary")
                }

            # Compatibilidade com jobs antigos, criados antes de existir
            # o status.json: reconstrói uma estimativa a partir do CSV.
            metadata = _load_metadata(job_dir)
            _, rows = _read_rows(job_dir)
            return {
                "id": job_id,
                "environment": metadata.get("environment", ""),
                "status": "completed" if rows else "error",
                "progress": 100 if rows else 0,
                "rows": len(rows),
                "error": None if rows else "Resultado não encontrado.",
                "summary": _build_summary(
                    [name for name in (list(rows[0].keys()) if rows else []) if name not in _FIXED_COLUMNS],
                    rows
                ) if rows else None
            }
        return {
            "id": job["id"],
            "environment": job["environment"],
            "status": job["status"],
            "progress": job["progress"],
            "rows": job["rows"],
            "error": job["error"],
            "summary": job["summary"]
        }


def get_csv_path(job_id):
    job_dir = _resolve_job_dir(job_id)
    if not job_dir:
        return None
    path = os.path.join(job_dir, "data.csv")
    return path if os.path.exists(path) else None


def _read_metrics_summary(job_dir):
    """Lê apenas o resumo (overall) do metrics.json, se ele já existir."""
    path = os.path.join(job_dir, "metrics.json")
    if not os.path.exists(path):
        return None
    try:
        with open(path, "r", encoding="utf-8") as file:
            metrics = json.load(file)
    except (OSError, json.JSONDecodeError):
        return None
    return metrics.get("overall")


def list_jobs(limit=200):
    """
    Histórico de todos os testes já executados, mais recentes primeiro.

    Combina o que está registrado em disco (test_results/<ambiente>_<id>/,
    lendo status.json e, quando existir, o metrics.json já calculado a
    partir do gabarito) com o que ainda só existe em memória — assim o
    histórico funciona tanto para jobs desta sessão quanto para jobs de
    execuções anteriores do servidor.

    Cada item retornado traz o suficiente para comparar testes entre si
    (ambiente, fonte de vídeo, status, acurácia/precisão quando já
    avaliadas, e se o gabarito manual já foi preenchido).
    """
    entries = {}

    if os.path.isdir(RESULTS_DIR):
        for name in os.listdir(RESULTS_DIR):
            job_dir = os.path.join(RESULTS_DIR, name)
            if not os.path.isdir(job_dir):
                continue

            status = _read_status_file(job_dir)
            metadata = _load_metadata(job_dir)
            job_id = (status or {}).get("id") or metadata.get("id")
            if not job_id:
                continue

            overall = _read_metrics_summary(job_dir)

            entries[job_id] = {
                "id": job_id,
                "environment": (status or {}).get("environment") or metadata.get("environment", ""),
                "source": (status or {}).get("source") or metadata.get("source", ""),
                "status": (status or {}).get("status", "desconhecido"),
                "rows": (status or {}).get("rows", 0),
                "error": (status or {}).get("error"),
                "created_at": (status or {}).get("created_at") or metadata.get("created_at"),
                "accuracy": overall.get("accuracy") if overall else None,
                "precision": overall.get("precision") if overall else None,
                "has_ground_truth": overall is not None
            }

    with _jobs_lock:
        for job_id, job in _jobs.items():
            if job_id in entries:
                continue
            entries[job_id] = {
                "id": job_id,
                "environment": job.get("environment", ""),
                "source": job.get("source", ""),
                "status": job.get("status", "desconhecido"),
                "rows": job.get("rows", 0),
                "error": job.get("error"),
                "created_at": job.get("created_at"),
                "accuracy": None,
                "precision": None,
                "has_ground_truth": False
            }

    jobs = sorted(
        entries.values(),
        key=lambda item: item.get("created_at") or 0,
        reverse=True
    )
    return jobs[:limit]


def _read_rows(job_dir):
    csv_path = os.path.join(job_dir, "data.csv")
    if not os.path.exists(csv_path):
        return None, []
    with open(csv_path, newline="", encoding="utf-8") as file:
        reader = csv.DictReader(file)
        fieldnames = reader.fieldnames or []
        rows = list(reader)
    region_names = [name for name in fieldnames if name not in _FIXED_COLUMNS]
    return region_names, rows


def _load_metadata(job_dir):
    path = os.path.join(job_dir, "metadata.json")
    if not os.path.exists(path):
        return {}
    try:
        with open(path, "r", encoding="utf-8") as file:
            return json.load(file)
    except (OSError, json.JSONDecodeError):
        return {}


def _ground_truth_path(job_dir):
    return os.path.join(job_dir, "ground_truth.json")


def load_ground_truth(job_dir):
    path = _ground_truth_path(job_dir)
    if not os.path.exists(path):
        return {}
    try:
        with open(path, "r", encoding="utf-8") as file:
            return json.load(file)
    except (json.JSONDecodeError, OSError):
        return {}


def get_samples(job_id, offset=0, limit=20):
    job_dir = _resolve_job_dir(job_id)
    if not job_dir:
        return None
    region_names, rows = _read_rows(job_dir)
    if region_names is None:
        return None

    ground_truth = load_ground_truth(job_dir)
    offset = max(0, int(offset or 0))
    limit = max(1, min(100, int(limit or 20)))
    selected = rows[offset:offset + limit]

    samples = []
    for row in selected:
        sample_id = str(row["sample"])
        samples.append({
            "sample": int(row["sample"]),
            "frame": int(row["frame"]),
            "timestamp_s": row.get("timestamp_s", ""),
            "tracking_estavel": row.get("tracking_estavel", ""),
            "match_count": row.get("match_count", ""),
            "image": row.get("image", ""),
            "detections": {name: row.get(name) for name in region_names},
            "truth": ground_truth.get(sample_id, {})
        })

    return {
        "regions": region_names,
        "samples": samples,
        "offset": offset,
        "limit": limit,
        "total": len(rows)
    }


def get_sample_image_path(job_id, filename):
    job_dir = _resolve_job_dir(job_id)
    if not job_dir:
        return None
    safe_name = os.path.basename(filename)
    path = os.path.join(job_dir, "samples", safe_name)
    if not os.path.exists(path):
        return None
    return path


def save_ground_truth(job_id, annotations):
    job_dir = _resolve_job_dir(job_id)
    if not job_dir or not isinstance(annotations, dict):
        return False

    current = load_ground_truth(job_dir)
    for sample_id, values in annotations.items():
        if not isinstance(values, dict):
            continue
        key = str(sample_id)
        current.setdefault(key, {})
        for region, value in values.items():
            if isinstance(value, bool):
                current[key][region] = value
            elif str(value).lower() in {"true", "1", "ocupada"}:
                current[key][region] = True
            elif str(value).lower() in {"false", "0", "livre"}:
                current[key][region] = False

    with open(_ground_truth_path(job_dir), "w", encoding="utf-8") as file:
        json.dump(current, file, ensure_ascii=False, indent=2)

    # O arquivo de métricas é atualizado imediatamente após cada alteração.
    compute_metrics(job_id)
    return True


def _empty_confusion_cell():
    return {"tp": 0, "fp": 0, "tn": 0, "fn": 0}


def _derive_metrics(cell):
    tp, fp, tn, fn = cell["tp"], cell["fp"], cell["tn"], cell["fn"]
    total = tp + fp + tn + fn
    correct = tp + tn
    errors = fp + fn
    return {
        **cell,
        "total": total,
        "correct": correct,
        "errors": errors,
        "accuracy": round(correct / total, 4) if total else None,
        "precision": round(tp / (tp + fp), 4) if (tp + fp) else None,
        "recall": round(tp / (tp + fn), 4) if (tp + fn) else None,
        "false_positive_rate": round(fp / (fp + tn), 4) if (fp + tn) else None
    }


def _write_metrics_files(job_dir, metrics):
    with open(os.path.join(job_dir, "metrics.json"), "w", encoding="utf-8") as file:
        json.dump(metrics, file, ensure_ascii=False, indent=2)

    csv_path = os.path.join(job_dir, "metrics.csv")
    rows = []
    for name, values in metrics["per_region"].items():
        rows.append({"vaga": name, **values})
    rows.append({"vaga": "TOTAL", **metrics["overall"]})

    fieldnames = [
        "vaga", "tp", "fp", "tn", "fn", "total", "correct", "errors",
        "accuracy", "precision", "recall", "false_positive_rate"
    ]
    with open(csv_path, "w", newline="", encoding="utf-8") as file:
        writer = csv.DictWriter(file, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def compute_metrics(job_id):
    job_dir = _resolve_job_dir(job_id)
    if not job_dir:
        return None

    region_names, rows = _read_rows(job_dir)
    if region_names is None:
        return None

    ground_truth = load_ground_truth(job_dir)
    per_region = {name: _empty_confusion_cell() for name in region_names}
    annotated_samples = 0
    annotated_cells = 0

    for row in rows:
        sample_id = str(row["sample"])
        truth_row = ground_truth.get(sample_id, {})
        sample_has_truth = False

        for name in region_names:
            if name not in truth_row:
                continue
            # Vagas fora de vista não entram na matriz de confusão: o
            # sistema não emitiu uma decisão para elas.
            if row.get(name) == "fora_de_vista":
                continue
            sample_has_truth = True
            annotated_cells += 1
            detected_occupied = row.get(name) == "ocupada"
            truth_occupied = bool(truth_row[name])
            cell = per_region[name]
            if detected_occupied and truth_occupied:
                cell["tp"] += 1
            elif detected_occupied and not truth_occupied:
                cell["fp"] += 1
            elif not detected_occupied and truth_occupied:
                cell["fn"] += 1
            else:
                cell["tn"] += 1

        if sample_has_truth:
            annotated_samples += 1

    per_region_metrics = {name: _derive_metrics(cell) for name, cell in per_region.items()}
    overall_cell = _empty_confusion_cell()
    for cell in per_region.values():
        for key in overall_cell:
            overall_cell[key] += cell[key]

    metrics = {
        "annotated_samples": annotated_samples,
        "total_samples": len(rows),
        "annotated_cells": annotated_cells,
        "total_cells": len(rows) * len(region_names),
        "per_region": per_region_metrics,
        "overall": _derive_metrics(overall_cell)
    }
    _write_metrics_files(job_dir, metrics)
    return metrics


def get_metrics(job_id):
    return compute_metrics(job_id)


def get_metrics_csv_path(job_id):
    job_dir = _resolve_job_dir(job_id)
    if not job_dir:
        return None
    path = os.path.join(job_dir, "metrics.csv")
    return path if os.path.exists(path) else None
