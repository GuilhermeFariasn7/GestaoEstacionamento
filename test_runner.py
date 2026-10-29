"""Execução e avaliação dos testes do Smart Parking."""

import csv
import json
import os
import platform
import shutil
import statistics
import sys
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
    "movimento_camera", "vagas_fora_de_vista",
    "num_veiculos", "tempo_total_ms", "tempo_inferencia_ms"
}

# Resultado publicado por Rocha (2025), Figura 8 (matriz de confusão).
# Linhas = real, colunas = previsto. Usado como referência nas métricas.
ROCHA_2025 = {"tp": 16141, "fn": 2330, "fp": 0, "tn": 3561}

MODEL_NAME = "yolo12x.pt"
ROIS_FILE = "rois.jsonl"
IGNORE_VALUE = "ignorar"
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


def _thumbnail_scale(frame):
    width = frame.shape[1]
    return _THUMBNAIL_MAX_WIDTH / width if width > _THUMBNAIL_MAX_WIDTH else 1.0


def _save_thumbnail(frame, path):
    """Salva a amostra reduzida e devolve a escala aplicada (miniatura/original)."""
    height, width = frame.shape[:2]
    scale = _thumbnail_scale(frame)
    if scale != 1.0:
        frame = cv2.resize(frame, (int(width * scale), int(height * scale)))
    cv2.imwrite(path, frame, [cv2.IMWRITE_JPEG_QUALITY, 85])
    return scale


def _region_parts_for_review(counter, scale):
    """
    Polígonos efetivamente usados na amostra (já estabilizados, recortados
    na borda e divididos na emenda 360°), em coordenadas da miniatura.
    Usados para desenhar as vagas no lugar certo na revisão manual.
    """
    parts_by_name = {}
    for region in getattr(counter, "counting_regions", []):
        parts = counter._polygon_parts(region["polygon"])
        parts_by_name[region["name"]] = [
            [[round(float(x) * scale, 1), round(float(y) * scale, 1)] for x, y in part]
            for part in parts
        ]
    return parts_by_name


def _system_info():
    """Hardware e versões de software, para a Tabela 4 do TCC."""
    info = {
        "sistema_operacional": platform.platform(),
        "processador": platform.processor() or platform.machine(),
        "python": sys.version.split()[0],
        "opencv": cv2.__version__,
    }
    try:
        import psutil
        info["memoria_ram_gb"] = round(psutil.virtual_memory().total / (1024 ** 3), 1)
        info["nucleos_cpu"] = psutil.cpu_count(logical=True)
    except Exception:
        pass
    try:
        import ultralytics
        info["ultralytics"] = ultralytics.__version__
    except Exception:
        pass
    try:
        import torch
        info["torch"] = torch.__version__
        if torch.cuda.is_available():
            info["gpu"] = torch.cuda.get_device_name(0)
            info["cuda"] = torch.version.cuda
        else:
            info["gpu"] = "não utilizada (inferência em CPU)"
    except Exception:
        pass
    for module_name, key in (("flask", "flask"), ("flask_socketio", "flask_socketio")):
        try:
            module = __import__(module_name)
            info[key] = getattr(module, "__version__", None) or _package_version(module_name)
        except Exception:
            pass
    return info


def _package_version(name):
    try:
        from importlib.metadata import version
        return version(name.replace("_", "-"))
    except Exception:
        return None


def _sample_targets(total_frames, video_fps, interval_s, frame_step):
    """
    Define em quais quadros as amostras serão feitas.

    Com interval_s (segundos), as amostras ficam exatamente em
    0 s, 5 s, 10 s... do vídeo (quadro = round(k * intervalo * fps)),
    igual à contagem manual de Rocha (2025), feita a cada 5 s de vídeo.
    Sem interval_s, mantém o comportamento antigo (1 a cada N quadros).
    """
    if interval_s and video_fps:
        step_frames = interval_s * video_fps

        def is_sample(frame_index, k):
            return frame_index >= round(k * step_frames)
        return is_sample, step_frames

    def is_sample_by_step(frame_index, _k):
        return frame_index % frame_step == 0
    return is_sample_by_step, frame_step


def _write_csv(csv_path, region_names, rows):
    fieldnames = [
        "sample", "frame", "timestamp_s", "tracking_estavel",
        "match_count", "inliers", "modo_tracking", "movimento_camera",
        "vagas_fora_de_vista", "num_veiculos", "tempo_total_ms",
        "tempo_inferencia_ms", "image"
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
        "label": job.get("label"),
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


def _run_job(job_id, environment_name, confidence, frame_step, source_override, stabilize,
             interval_s=None, criterion="sobreposicao", tracking=False, extra=None):
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
        frame_width = int(capture.get(cv2.CAP_PROP_FRAME_WIDTH) or 0)
        frame_height = int(capture.get(cv2.CAP_PROP_FRAME_HEIGHT) or 0)
        if interval_s and not video_fps:
            raise RuntimeError(
                "Não foi possível ler o FPS do vídeo; use a amostragem por quadros."
            )
        is_sample, step_frames = _sample_targets(total_frames, video_fps, interval_s, frame_step)
        stabilizer = _build_stabilizer(environment_name, coordinates, stabilize)

        from regionCounterLive import RegionCounterLive

        counter = RegionCounterLive(
            region=coordinates,
            model=MODEL_NAME,
            classes=[2],
            conf=confidence,
            stabilizer=stabilizer,
            criterion=criterion,
            use_tracking=tracking
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
            "model": MODEL_NAME,
            "classes": [2],
            "confidence": confidence,
            "sampling": "segundos" if interval_s else "quadros",
            "interval_s": interval_s,
            "frame_step": frame_step if not interval_s else None,
            "frames_entre_amostras": round(float(step_frames), 3),
            "occupancy_criterion": criterion,
            "min_vehicle_overlap": (
                __import__("regionCounterLive").MIN_VEHICLE_OVERLAP
                if criterion == "sobreposicao" else None
            ),
            "tracking": bool(tracking),
            "video_fps": round(float(video_fps), 3) if video_fps else None,
            "total_frames": total_frames,
            "duration_s": round(total_frames / video_fps, 2) if video_fps and total_frames else None,
            "frame_width": frame_width,
            "frame_height": frame_height,
            "stabilize": bool(stabilizer is not None),
            "projection": (
                stabilizer.projection if stabilizer is not None
                else store.get_detection_config(environment_name).get("projection", "perspective")
            ),
            "fov": store.get_detection_config(environment_name).get("fov"),
            "created_at": time.time(),
            "regions": list(coordinates.keys()),
            "ambiente_computacional": _system_info()
        }
        metadata.update(extra or {})
        _write_metadata(job_dir, metadata)

        rois_path = os.path.join(job_dir, ROIS_FILE)
        rois_file = open(rois_path, "w", encoding="utf-8")
        thumb_scale = None
        total_times = []
        inference_times = []

        while True:
            if job.get("cancel"):
                raise RuntimeError("Teste cancelado pelo usuário.")

            # grab() avança sem converter o quadro; só as amostras são
            # decodificadas por completo (retrieve), o que acelera vídeos longos.
            if not capture.grab():
                break

            if is_sample(frame_index, sample_index):
                success, frame = capture.retrieve()
                if not success or frame is None:
                    frame_index += 1
                    continue

                # Mantém a amostra original para o gabarito manual.
                # A rotina de detecção pode desenhar informações sobre o frame.
                sample_frame = frame.copy()
                started = time.perf_counter()
                results = counter.process(frame)
                total_ms = (time.perf_counter() - started) * 1000.0
                inference_ms = counter.last_speed.get("inference")
                total_times.append(total_ms)
                if inference_ms is not None:
                    inference_times.append(float(inference_ms))

                timestamp_s = frame_index / video_fps if video_fps else None
                image_name = f"{sample_index:06d}.jpg"
                thumb_scale = _save_thumbnail(sample_frame, os.path.join(samples_dir, image_name))
                rois_file.write(json.dumps({
                    "sample": sample_index,
                    "rois": _region_parts_for_review(counter, thumb_scale)
                }) + "\n")

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
                    "num_veiculos": len(counter.boxes) if counter.boxes is not None else 0,
                    "tempo_total_ms": round(total_ms, 2),
                    "tempo_inferencia_ms": (
                        round(float(inference_ms), 2) if inference_ms is not None else ""
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

        rois_file.close()

        if not rows:
            raise RuntimeError("Nenhum frame foi lido da fonte de vídeo informada.")

        metadata["thumbnail_scale"] = thumb_scale
        metadata["frames_lidos"] = frame_index
        metadata["amostras"] = len(rows)
        metadata["tempo_por_quadro_ms"] = _time_stats(total_times)
        metadata["tempo_inferencia_ms"] = _time_stats(inference_times)
        _write_metadata(job_dir, metadata)

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
        if not job.get("cancel"):
            import traceback
            traceback.print_exc()
        with _jobs_lock:
            job["status"] = "cancelled" if job.get("cancel") else "error"
            job["error"] = str(error)
            job_snapshot = dict(job)
        _write_status_file(job_dir, job_snapshot)
    finally:
        if capture is not None:
            capture.release()
        try:
            rois_file.close()
        except Exception:
            pass


def _time_stats(values):
    """Média, desvio padrão, mínimo e máximo (ms). O 1º quadro inclui o aquecimento do modelo."""
    if not values:
        return None
    steady = values[1:] if len(values) > 1 else values
    return {
        "n": len(values),
        "media": round(statistics.mean(steady), 2),
        "desvio_padrao": round(statistics.pstdev(steady), 2),
        "minimo": round(min(steady), 2),
        "maximo": round(max(steady), 2),
        "primeiro_quadro": round(values[0], 2),
        "observacao": "estatísticas sem o primeiro quadro (aquecimento do modelo)"
    }


def start(environment_name, confidence=0.4, frame_step=5, source_override=None, stabilize=None,
          interval_s=None, criterion="sobreposicao", tracking=False, extra=None):
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
            "label": (extra or {}).get("label"),
            "source": source_override,
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
        args=(job_id, environment_name, confidence, max(1, int(frame_step or 1)), source_override, stabilize,
              interval_s, criterion, tracking, extra),
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
            "summary": job["summary"],
            "label": job.get("label"),
            "elapsed_s": round(time.time() - job["created_at"], 1)
        }


def cancel(job_id):
    with _jobs_lock:
        job = _jobs.get(job_id)
        if not job or job.get("status") != "running":
            return False
        job["cancel"] = True
        return True


def delete(job_id):
    """Apaga a pasta de um teste que não esteja em execução."""
    with _jobs_lock:
        job = _jobs.get(job_id)
        if job and job.get("status") == "running":
            raise RuntimeError("Cancele o teste antes de excluí-lo.")
        _jobs.pop(job_id, None)
    job_dir = _safe_job_dir(job_id)
    if not job_dir:
        return False
    shutil.rmtree(job_dir, ignore_errors=True)
    return True


def get_metadata(job_id):
    job_dir = _resolve_job_dir(job_id)
    if not job_dir:
        return None
    metadata = _load_metadata(job_dir)
    metadata.setdefault("id", job_id)
    return metadata


def get_overview(job_id):
    """
    Situação de cada amostra para a linha do tempo da análise:
    complete = todas as vagas avaliáveis anotadas; errors = quantas vagas
    anotadas o detector errou (só faz sentido depois da anotação).
    """
    job_dir = _resolve_job_dir(job_id)
    if not job_dir:
        return None
    region_names, rows = _read_rows(job_dir)
    if region_names is None:
        return None
    ground_truth = load_ground_truth(job_dir)
    complete, errors, partial = [], [], []
    for row in rows:
        truth_row = ground_truth.get(str(row["sample"]), {})
        evaluable = [name for name in region_names if row.get(name) != "fora_de_vista"]
        marked = [name for name in evaluable if name in truth_row]
        complete.append(1 if len(marked) == len(evaluable) else 0)
        partial.append(1 if marked else 0)
        wrong = 0
        for name in marked:
            value = truth_row[name]
            if isinstance(value, bool) and value != (row.get(name) == "ocupada"):
                wrong += 1
        errors.append(wrong)
    return {"total": len(rows), "complete": complete, "partial": partial, "errors": errors}


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
            annotated_fraction = _annotated_fraction(job_dir)

            entries[job_id] = {
                "label": metadata.get("label") or (status or {}).get("label"),
                "annotated_fraction": annotated_fraction,
                "samples": metadata.get("amostras") or (status or {}).get("rows", 0),
                "regions": len(metadata.get("regions", [])),
                "recall": overall.get("recall") if overall else None,
                "false_positive_rate": overall.get("false_positive_rate") if overall else None,
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
                "label": job.get("label"),
                "annotated_fraction": None,
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


def _annotated_fraction(job_dir):
    path = os.path.join(job_dir, "metrics.json")
    if not os.path.exists(path):
        return 0.0 if os.path.exists(os.path.join(job_dir, "data.csv")) else None
    try:
        with open(path, "r", encoding="utf-8") as file:
            metrics = json.load(file)
    except (OSError, json.JSONDecodeError):
        return None
    evaluable = metrics.get("total_cells", 0) - metrics.get("out_of_view_cells", 0)
    done = metrics.get("annotated_cells", 0) + metrics.get("ignored_cells", 0)
    return round(done / evaluable, 4) if evaluable else None


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
    rois = _load_rois(job_dir, {int(row["sample"]) for row in selected})

    def is_complete(row):
        truth_row = ground_truth.get(str(row["sample"]), {})
        return all(
            name in truth_row or row.get(name) == "fora_de_vista"
            for name in region_names
        )

    first_unannotated = next(
        (position for position, row in enumerate(rows) if not is_complete(row)),
        None
    )
    complete_samples = sum(1 for row in rows if is_complete(row))

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
            "truth": ground_truth.get(sample_id, {}),
            "rois": rois.get(int(row["sample"]))
        })

    metadata = _load_metadata(job_dir)
    return {
        "regions": region_names,
        "samples": samples,
        "offset": offset,
        "limit": limit,
        "total": len(rows),
        "first_unannotated": first_unannotated,
        "complete_samples": complete_samples,
        "thumbnail_scale": metadata.get("thumbnail_scale")
    }


def _load_rois(job_dir, wanted):
    """Lê do rois.jsonl apenas as amostras pedidas (testes antigos não têm o arquivo)."""
    path = os.path.join(job_dir, ROIS_FILE)
    found = {}
    if not wanted or not os.path.exists(path):
        return found
    with open(path, "r", encoding="utf-8") as file:
        for line in file:
            try:
                item = json.loads(line)
            except json.JSONDecodeError:
                continue
            sample = int(item.get("sample", -1))
            if sample in wanted:
                found[sample] = item.get("rois")
                if len(found) == len(wanted):
                    break
    return found


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
            elif value is None or str(value).lower() in {"", "limpar", "none", "null"}:
                current[key].pop(region, None)
            elif str(value).lower() in {"true", "1", "ocupada"}:
                current[key][region] = True
            elif str(value).lower() in {"false", "0", "livre"}:
                current[key][region] = False
            elif str(value).lower() in {IGNORE_VALUE, "indeterminada", "?"}:
                # Estado real impossível de determinar na imagem
                # (oclusão total, borrão). Fica fora da matriz de confusão
                # e é contado separadamente em "ignored_cells".
                current[key][region] = IGNORE_VALUE
        if not current[key]:
            current.pop(key, None)

    with open(_ground_truth_path(job_dir), "w", encoding="utf-8") as file:
        json.dump(current, file, ensure_ascii=False, indent=2)

    # O arquivo de métricas é atualizado imediatamente após cada alteração.
    compute_metrics(job_id)
    return True


def _empty_confusion_cell():
    return {"tp": 0, "fp": 0, "tn": 0, "fn": 0}


def _ratio(numerator, denominator):
    return round(numerator / denominator, 4) if denominator else None


def _derive_metrics(cell):
    """
    Métricas com "vaga ocupada" como classe positiva (convenção do TCC):
        TP = ocupada prevista como ocupada; FP = livre prevista como ocupada
        TN = livre prevista como livre;     FN = ocupada prevista como livre

    Também são devolvidas as métricas com "vaga livre" como classe
    positiva (sufixo _livre). É essa a convenção que reproduz a precisão
    de 60,45% publicada por Rocha (2025): 3561 / (3561 + 2330).
    """
    tp, fp, tn, fn = cell["tp"], cell["fp"], cell["tn"], cell["fn"]
    total = tp + fp + tn + fn
    correct = tp + tn
    errors = fp + fn
    precision = _ratio(tp, tp + fp)
    recall = _ratio(tp, tp + fn)
    precision_free = _ratio(tn, tn + fn)
    recall_free = _ratio(tn, tn + fp)
    return {
        **cell,
        "total": total,
        "correct": correct,
        "errors": errors,
        "accuracy": _ratio(correct, total),
        "precision": precision,
        "recall": recall,
        "false_positive_rate": _ratio(fp, fp + tn),
        "f1": _ratio(2 * precision * recall, precision + recall) if precision and recall else None,
        "precision_livre": precision_free,
        "recall_livre": recall_free,
        "false_positive_rate_livre": _ratio(fn, fn + tp),
        "f1_livre": (
            _ratio(2 * precision_free * recall_free, precision_free + recall_free)
            if precision_free and recall_free else None
        )
    }


def _write_metrics_files(job_dir, metrics):
    with open(os.path.join(job_dir, "metrics.json"), "w", encoding="utf-8") as file:
        json.dump(metrics, file, ensure_ascii=False, indent=2)

    csv_path = os.path.join(job_dir, "metrics.csv")
    rows = []
    for name, values in metrics["per_region"].items():
        rows.append({"vaga": name, **values})
    rows.append({"vaga": "TOTAL", **metrics["overall"]})

    rows.append({"vaga": "ROCHA_2025 (referência)", **metrics["rocha_2025"]})

    fieldnames = [
        "vaga", "tp", "fp", "tn", "fn", "total", "correct", "errors",
        "accuracy", "precision", "recall", "false_positive_rate", "f1",
        "precision_livre", "recall_livre", "false_positive_rate_livre", "f1_livre"
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
    ignored_cells = 0
    out_of_view_cells = 0

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
                out_of_view_cells += 1
                continue
            # Estado real indeterminado na imagem: fora da matriz.
            if not isinstance(truth_row[name], bool):
                ignored_cells += 1
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

    metadata = _load_metadata(job_dir)
    metrics = {
        "annotated_samples": annotated_samples,
        "total_samples": len(rows),
        "annotated_cells": annotated_cells,
        "ignored_cells": ignored_cells,
        "out_of_view_cells": out_of_view_cells,
        "total_cells": len(rows) * len(region_names),
        "per_region": per_region_metrics,
        "overall": _derive_metrics(overall_cell),
        "rocha_2025": _derive_metrics(dict(ROCHA_2025)),
        "tempo_por_quadro_ms": metadata.get("tempo_por_quadro_ms"),
        "tempo_inferencia_ms": metadata.get("tempo_inferencia_ms")
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
