"""
Classificação das vagas com o Gemini (modelo de visão e linguagem), como
abordagem complementar ao YOLO no TCC.

Como funciona:
    Para cada amostra de um teste já executado (test_results/<teste>/):
      1. lê do vídeo o MESMO quadro que o YOLO analisou (coluna "frame"
         do data.csv); se o vídeo não estiver mais disponível, usa a
         miniatura salva em samples/;
      2. desenha sobre ele os MESMOS polígonos de vaga usados pelo YOLO
         naquela amostra (rois.jsonl, já estabilizados), com o nome de
         cada vaga;
      3. envia a imagem + a instrução (prompt) para a API do Gemini
         (generateContent), pedindo resposta em JSON:
             {"vagas": [{"vaga": "vaga-01", "ocupada": true}, ...]}
      4. salva a resposta, o tempo de resposta (inclui a rede) e o
         consumo de tokens.
    As métricas usam o MESMO gabarito manual (ground_truth.json) do YOLO.

Arquivos gerados em test_results/<teste>/gemini/<execução>/:
    metadata.json   modelo, prompt, temperatura, parâmetros e tempos
    predicoes.csv   uma linha por amostra: estado de cada vaga
    respostas.jsonl texto bruto devolvido pela API em cada amostra
    imagens/        imagens exatamente como foram enviadas

A chave da API NUNCA é gravada em disco: vem da tela ou da variável de
ambiente GEMINI_API_KEY.
"""

import base64
import csv
import json
import os
import statistics
import threading
import time
import urllib.error
import urllib.request
import uuid

import cv2
import numpy as np

import test_runner

API_BASE = os.environ.get("GEMINI_API_BASE", "https://generativelanguage.googleapis.com/v1beta")
REQUEST_TIMEOUT_S = 90
MAX_ATTEMPTS = 8
MAX_WAIT_S = 30.0
RETRY_STATUS = {429, 500, 502, 503, 504}
DEFAULT_MAX_WIDTH = 1600
# Se tantas amostras SEGUIDAS falharem por erro temporário da API, o serviço está fora do ar:
# a execução é interrompida (em vez de gastar minutos em cada amostra seguinte).
MAX_CONSECUTIVE_FAILURES = 5

OCCUPIED = "ocupada"
FREE = "livre"
NO_ANSWER = "sem_resposta"
OUT_OF_VIEW = "fora_de_vista"

DEFAULT_PROMPT = (
    "Você está analisando uma imagem de uma câmera de monitoramento de um estacionamento.\n"
    "As vagas que devem ser avaliadas estão contornadas por polígonos coloridos e identificadas "
    "por um rótulo escrito sobre cada uma: {vagas}.\n"
    "Para cada vaga, informe se ela está OCUPADA (existe um veículo estacionado dentro do contorno, "
    "mesmo que só parte dele esteja visível ou que esteja parcialmente encoberto) ou LIVRE "
    "(não há veículo ocupando a vaga).\n"
    "Considere apenas a área dentro de cada contorno. Veículos apenas passando pela via, fora dos "
    "contornos, não ocupam a vaga.\n"
    "Responda somente com o JSON solicitado, com exatamente uma entrada para cada vaga listada."
)

_COLORS = [  # BGR, bem visíveis sobre asfalto
    (0, 255, 255), (255, 0, 255), (0, 165, 255), (255, 255, 0), (0, 255, 0),
    (255, 128, 0), (203, 192, 255), (0, 128, 255), (255, 0, 128), (128, 255, 0),
]

_runs = {}
_runs_lock = threading.Lock()


class GeminiError(RuntimeError):
    """transient=True: erro temporário da API (sobrecarga, limite, conexão) que já esgotou as
    tentativas; a amostra é marcada como sem resposta e a execução continua. Erros de
    configuração (chave, modelo inexistente, pedido inválido) e cancelamento interrompem tudo."""

    def __init__(self, message, transient=False):
        super().__init__(message)
        self.transient = transient


# ---------------------------------------------------------------------------
# API do Gemini
# ---------------------------------------------------------------------------

def resolve_api_key(api_key=None):
    key = (api_key or "").strip() or os.environ.get("GEMINI_API_KEY", "").strip()
    if not key:
        raise GeminiError("Informe a chave da API do Gemini (ou defina a variável GEMINI_API_KEY).")
    return key


def _http(method, url, api_key, body=None):
    data = json.dumps(body).encode("utf-8") if body is not None else None
    request = urllib.request.Request(url, data=data, method=method, headers={
        "x-goog-api-key": api_key,
        "Content-Type": "application/json",
    })
    with urllib.request.urlopen(request, timeout=REQUEST_TIMEOUT_S) as response:
        return json.loads(response.read().decode("utf-8"))


def _error_message(error):
    try:
        payload = json.loads(error.read().decode("utf-8"))
        return payload.get("error", {}).get("message") or str(error)
    except Exception:
        return str(error)


def list_models(api_key=None):
    """Modelos da conta que aceitam generateContent e entendem imagem."""
    key = resolve_api_key(api_key)
    try:
        data = _http("GET", f"{API_BASE}/models?pageSize=1000", key)
    except urllib.error.HTTPError as error:
        raise GeminiError(f"A API recusou a chave ({error.code}): {_error_message(error)}")
    except urllib.error.URLError as error:
        raise GeminiError(f"Sem conexão com a API do Gemini: {error.reason}")

    skip = ("tts", "image", "live", "audio", "embedding", "aqa", "veo", "imagen",
            "lyria", "robotics", "transcribe", "computer-use", "omni", "learnlm")
    models = []
    for model in data.get("models", []):
        name = model.get("name", "").replace("models/", "")
        methods = model.get("supportedGenerationMethods", [])
        if "generateContent" not in methods or not name.startswith("gemini"):
            continue
        if any(token in name for token in skip):
            continue
        models.append({
            "name": name,
            "display_name": model.get("displayName", name),
            "description": model.get("description", ""),
            "version": model.get("version", ""),
        })
    models.sort(key=lambda m: (("preview" in m["name"]) or ("exp" in m["name"]), m["name"]), reverse=False)
    return models


def _response_schema(region_names):
    return {
        "type": "OBJECT",
        "properties": {
            "vagas": {
                "type": "ARRAY",
                "items": {
                    "type": "OBJECT",
                    "properties": {
                        "vaga": {"type": "STRING", "enum": list(region_names)},
                        "ocupada": {"type": "BOOLEAN"},
                    },
                    "required": ["vaga", "ocupada"],
                },
            }
        },
        "required": ["vagas"],
    }


def _wait(seconds, note, on_wait=None, should_cancel=None):
    """Espera em pequenos passos, informando o motivo e respeitando o cancelamento."""
    if on_wait:
        on_wait(note)
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        if should_cancel and should_cancel():
            raise GeminiError("Execução cancelada pelo usuário.")
        time.sleep(0.5)
    if on_wait:
        on_wait(None)


def classify_image(api_key, model, prompt, jpeg_bytes, region_names, temperature=None,
                   on_wait=None, should_cancel=None):
    """
    Envia uma imagem ao Gemini e devolve:
        (estados {vaga: "ocupada"/"livre"/"sem_resposta"}, info)
    info traz latência (ms) da tentativa que deu certo, nº de tentativas,
    tokens e o texto bruto.
    """
    body = {
        "contents": [{
            "role": "user",
            "parts": [
                {"text": prompt},
                {"inline_data": {"mime_type": "image/jpeg",
                                 "data": base64.b64encode(jpeg_bytes).decode("ascii")}},
            ],
        }],
        "generationConfig": {
            "responseMimeType": "application/json",
            "responseSchema": _response_schema(region_names),
        },
    }
    if temperature is not None:
        body["generationConfig"]["temperature"] = float(temperature)

    url = f"{API_BASE}/models/{model}:generateContent"
    attempts = 0
    wait_s = 2.0
    while True:
        attempts += 1
        started = time.perf_counter()
        if on_wait:
            on_wait(f"Enviando imagem ao Google (tentativa {attempts} de {MAX_ATTEMPTS}, "
                    f"aguarda até {REQUEST_TIMEOUT_S} s pela resposta)...")
        print(f"[GEMINI] {model}: enviando (tentativa {attempts}/{MAX_ATTEMPTS})...", flush=True)
        try:
            data = _http("POST", url, api_key, body)
            latency_ms = (time.perf_counter() - started) * 1000.0
            print(f"[GEMINI] {model}: OK em {latency_ms / 1000:.1f} s", flush=True)
            if on_wait:
                on_wait(None)
            break
        except urllib.error.HTTPError as error:
            message = _error_message(error)
            print(f"[GEMINI] {model}: HTTP {error.code} após "
                  f"{time.perf_counter() - started:.1f} s -> {message[:200]}", flush=True)
            if error.code in RETRY_STATUS and attempts < MAX_ATTEMPTS:
                retry_after = error.headers.get("Retry-After") if error.headers else None
                try:
                    pause = float(retry_after) if retry_after else wait_s
                except ValueError:
                    pause = wait_s
                pause = min(pause, MAX_WAIT_S if not retry_after else 60.0)
                _wait(pause,
                      f"Google respondeu {error.code} (modelo sobrecarregado/limite). "
                      f"Nova tentativa {attempts + 1} de {MAX_ATTEMPTS} em {pause:.0f} s...",
                      on_wait, should_cancel)
                wait_s = min(wait_s * 2, MAX_WAIT_S)
                continue
            raise GeminiError(f"Erro {error.code} da API: {message}",
                              transient=error.code in RETRY_STATUS)
        except (urllib.error.URLError, TimeoutError) as error:
            print(f"[GEMINI] {model}: sem resposta após {time.perf_counter() - started:.1f} s -> "
                  f"{getattr(error, 'reason', error)}", flush=True)
            if attempts < MAX_ATTEMPTS:
                _wait(wait_s,
                      f"Falha de conexão. Nova tentativa {attempts + 1} de {MAX_ATTEMPTS} em {wait_s:.0f} s...",
                      on_wait, should_cancel)
                wait_s = min(wait_s * 2, MAX_WAIT_S)
                continue
            raise GeminiError(f"Falha de conexão com a API: {getattr(error, 'reason', error)}",
                              transient=True)

    candidates = data.get("candidates") or []
    text = ""
    if candidates:
        parts = (candidates[0].get("content") or {}).get("parts") or []
        text = "".join(p.get("text", "") for p in parts if not p.get("thought"))
    usage = data.get("usageMetadata") or {}
    info = {
        "latency_ms": latency_ms,
        "attempts": attempts,
        "prompt_tokens": usage.get("promptTokenCount"),
        "output_tokens": usage.get("candidatesTokenCount"),
        "finish_reason": candidates[0].get("finishReason") if candidates else
                         (data.get("promptFeedback") or {}).get("blockReason"),
        "raw_text": text,
    }

    states = {name: NO_ANSWER for name in region_names}
    try:
        parsed = json.loads(text) if text else {}
        items = parsed.get("vagas", []) if isinstance(parsed, dict) else parsed
        for item in items or []:
            name = str(item.get("vaga", "")).strip()
            if name in states and isinstance(item.get("ocupada"), bool):
                states[name] = OCCUPIED if item["ocupada"] else FREE
    except (json.JSONDecodeError, AttributeError, TypeError):
        info["parse_error"] = True
    return states, info


# ---------------------------------------------------------------------------
# Preparação das imagens
# ---------------------------------------------------------------------------

def _load_rois(job_dir):
    path = os.path.join(job_dir, test_runner.ROIS_FILE)
    rois = {}
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8") as file:
            for line in file:
                try:
                    item = json.loads(line)
                    rois[int(item["sample"])] = item.get("rois") or {}
                except (json.JSONDecodeError, KeyError, ValueError):
                    continue
    return rois


def draw_regions(frame, parts_by_name, scale_to_frame, names):
    """Desenha os polígonos (coordenadas da miniatura × escala) e o nome de cada vaga."""
    image = frame.copy()
    height, width = image.shape[:2]
    thickness = max(2, int(round(width / 500)))
    font_scale = max(0.5, width / 1800)
    for index, name in enumerate(names):
        color = _COLORS[index % len(_COLORS)]
        biggest = None
        for part in parts_by_name.get(name) or []:
            if len(part) < 3:
                continue
            pts = np.array([[x * scale_to_frame, y * scale_to_frame] for x, y in part], dtype=np.int32)
            cv2.polylines(image, [pts], True, color, thickness, cv2.LINE_AA)
            area = cv2.contourArea(pts)
            if biggest is None or area > biggest[0]:
                biggest = (area, pts)
        if biggest is None:
            continue
        moments = cv2.moments(biggest[1])
        if moments["m00"]:
            cx, cy = int(moments["m10"] / moments["m00"]), int(moments["m01"] / moments["m00"])
        else:
            cx, cy = biggest[1][0]
        (tw, th), base = cv2.getTextSize(name, cv2.FONT_HERSHEY_SIMPLEX, font_scale, thickness)
        x0 = int(min(max(0, cx - tw // 2), width - tw - 6))
        y0 = int(min(max(th + 6, cy + th // 2), height - 4))
        cv2.rectangle(image, (x0 - 3, y0 - th - 5), (x0 + tw + 3, y0 + base), (0, 0, 0), -1)
        cv2.putText(image, name, (x0, y0), cv2.FONT_HERSHEY_SIMPLEX, font_scale, color, thickness, cv2.LINE_AA)
    return image


class _SequentialReader:
    """Lê quadros do vídeo em ordem crescente, com a mesma contagem do test_runner."""

    def __init__(self, path):
        self.capture = cv2.VideoCapture(path) if path and os.path.isfile(path) else None
        self.index = -1
        if self.capture is not None and not self.capture.isOpened():
            self.capture = None

    @property
    def available(self):
        return self.capture is not None

    def read(self, frame_index):
        if self.capture is None or frame_index <= self.index:
            return None
        while self.index < frame_index:
            if not self.capture.grab():
                return None
            self.index += 1
        success, frame = self.capture.retrieve()
        return frame if success else None

    def release(self):
        if self.capture is not None:
            self.capture.release()


# ---------------------------------------------------------------------------
# Execução em segundo plano
# ---------------------------------------------------------------------------

def _gemini_dir(job_dir):
    return os.path.join(job_dir, "gemini")


def _run_dir(job_id, run_id):
    job_dir = test_runner._resolve_job_dir(job_id)
    if not job_dir or not run_id or os.path.sep in run_id or "/" in run_id or ".." in run_id:
        return None
    path = os.path.join(_gemini_dir(job_dir), run_id)
    return path if os.path.isdir(path) else None


def start(job_id, api_key=None, model="", temperature=None, prompt_template=None,
          max_width=DEFAULT_MAX_WIDTH, pause_s=0.0):
    job_dir = test_runner._resolve_job_dir(job_id)
    if not job_dir:
        raise GeminiError("Teste não encontrado.")
    status = test_runner.get_status(job_id) or {}
    if status.get("status") not in (None, "completed"):
        raise GeminiError("O teste do YOLO ainda não terminou.")
    if not model:
        raise GeminiError("Escolha o modelo do Gemini.")
    key = resolve_api_key(api_key)
    prompt_template = (prompt_template or "").strip() or DEFAULT_PROMPT
    if "{vagas}" not in prompt_template:
        raise GeminiError("O prompt precisa conter {vagas}, que é trocado pela lista de vagas.")

    run_id = time.strftime("%Y%m%d_%H%M%S") + "_" + uuid.uuid4().hex[:4]
    run_dir = os.path.join(_gemini_dir(job_dir), run_id)
    os.makedirs(os.path.join(run_dir, "imagens"), exist_ok=True)

    run = {
        "id": run_id, "job_id": job_id, "status": "running", "progress": 0,
        "done": 0, "total": 0, "error": None, "note": None, "cancel": False, "created_at": time.time(),
        "model": model,
    }
    with _runs_lock:
        _runs[(job_id, run_id)] = run

    thread = threading.Thread(
        target=_run, daemon=True,
        args=(run, job_dir, run_dir, key, model, temperature, prompt_template,
              int(max_width or DEFAULT_MAX_WIDTH), float(pause_s or 0)),
    )
    thread.start()
    return run_id


def _run(run, job_dir, run_dir, api_key, model, temperature, prompt_template, max_width, pause_s):
    reader = None
    try:
        region_names, rows = test_runner._read_rows(job_dir)
        if not rows:
            raise GeminiError("O teste não tem amostras.")
        metadata_yolo = test_runner._load_metadata(job_dir)
        thumb_scale = metadata_yolo.get("thumbnail_scale") or 1.0
        rois = _load_rois(job_dir)
        reader = _SequentialReader(metadata_yolo.get("source"))
        run["total"] = len(rows)

        metadata = {
            "id": run["id"],
            "job_id": run["job_id"],
            "model": model,
            "mode": "zero-shot",
            "access": "API REST generateContent (v1beta)",
            "temperature": temperature,
            "prompt_template": prompt_template,
            "response_format": "JSON (responseSchema): {vagas: [{vaga, ocupada}]}",
            "max_width": max_width,
            "pause_s": pause_s,
            "image_source": "quadro original do vídeo" if reader.available else "miniatura salva (vídeo indisponível)",
            "regions_drawn": "polígonos usados pelo YOLO em cada amostra (rois.jsonl)",
            "latency_definition": "tempo entre enviar a requisição e receber a resposta, incluindo a rede",
            "created_at": run["created_at"],
            "status": "running",
        }
        _write_json(os.path.join(run_dir, "metadata.json"), metadata)

        fieldnames = ["sample", "frame", "latencia_ms", "tentativas", "tokens_entrada",
                      "tokens_saida", "status"] + list(region_names)
        latencies = []
        no_answer = 0
        consecutive_failures = 0
        failed_samples = []
        with open(os.path.join(run_dir, "predicoes.csv"), "w", newline="", encoding="utf-8") as csv_file, \
             open(os.path.join(run_dir, "respostas.jsonl"), "w", encoding="utf-8") as raw_file:
            writer = csv.DictWriter(csv_file, fieldnames=fieldnames)
            writer.writeheader()

            for position, row in enumerate(rows):
                if run["cancel"]:
                    raise GeminiError("Execução cancelada pelo usuário.")
                sample = int(row["sample"])
                evaluable = [n for n in region_names if row.get(n) != OUT_OF_VIEW]
                out = {"sample": sample, "frame": row.get("frame")}

                frame = reader.read(int(row["frame"])) if reader.available else None
                if frame is not None:
                    to_frame = 1.0 / thumb_scale
                else:
                    frame = cv2.imread(os.path.join(job_dir, "samples", row["image"]))
                    to_frame = 1.0
                if frame is None:
                    raise GeminiError(f"Não foi possível obter a imagem da amostra {sample + 1}.")

                annotated = draw_regions(frame, rois.get(sample, {}), to_frame, evaluable)
                if max_width and annotated.shape[1] > max_width:
                    factor = max_width / annotated.shape[1]
                    annotated = cv2.resize(annotated, (max_width, int(round(annotated.shape[0] * factor))),
                                           interpolation=cv2.INTER_AREA)
                ok, encoded = cv2.imencode(".jpg", annotated, [cv2.IMWRITE_JPEG_QUALITY, 90])
                if not ok:
                    raise GeminiError("Falha ao codificar a imagem.")
                jpeg = encoded.tobytes()
                with open(os.path.join(run_dir, "imagens", f"{sample:06d}.jpg"), "wb") as image_file:
                    image_file.write(jpeg)

                prompt = prompt_template.replace("{vagas}", ", ".join(evaluable))
                if position == 0:
                    metadata["prompt_exemplo"] = prompt
                if evaluable:
                    try:
                        states, info = classify_image(
                            api_key, model, prompt, jpeg, evaluable, temperature,
                            on_wait=lambda note: run.__setitem__("note", note),
                            should_cancel=lambda: run["cancel"],
                        )
                    except GeminiError as error:
                        # Cancelamento ou erro de configuração (chave/modelo): interrompe tudo.
                        if run["cancel"] or not error.transient:
                            raise
                        # Erro temporário que esgotou as tentativas: só esta amostra fica sem resposta.
                        consecutive_failures += 1
                        failed_samples.append({"sample": sample, "erro": str(error)})
                        print(f"[GEMINI] Amostra {sample + 1} sem resposta ({error}); "
                              f"seguindo para a próxima ({consecutive_failures} falha(s) seguida(s)).",
                              flush=True)
                        if consecutive_failures >= MAX_CONSECUTIVE_FAILURES:
                            raise GeminiError(
                                f"{consecutive_failures} amostras seguidas falharam por erro temporário da API "
                                f"(último erro: {error}). O serviço parece indisponível; tente mais tarde "
                                f"ou com outro modelo.")
                        states = {}
                        out.update({"tentativas": MAX_ATTEMPTS, "status": "erro_api"})
                        raw_file.write(json.dumps({"sample": sample, "texto": "", "erro": str(error)},
                                                  ensure_ascii=False) + "\n")
                    else:
                        consecutive_failures = 0
                        latencies.append(info["latency_ms"])
                        out.update({
                            "latencia_ms": round(info["latency_ms"], 1),
                            "tentativas": info["attempts"],
                            "tokens_entrada": info.get("prompt_tokens") or "",
                            "tokens_saida": info.get("output_tokens") or "",
                            "status": "erro_formato" if info.get("parse_error") else "ok",
                        })
                        raw_file.write(json.dumps({"sample": sample, "texto": info["raw_text"],
                                                   "finish_reason": info.get("finish_reason")},
                                                  ensure_ascii=False) + "\n")
                else:
                    states, out["status"] = {}, "sem_vagas_visiveis"
                for name in region_names:
                    out[name] = OUT_OF_VIEW if name not in evaluable else states.get(name, NO_ANSWER)
                    if out[name] == NO_ANSWER:
                        no_answer += 1
                writer.writerow(out)
                csv_file.flush()

                run["done"] = position + 1
                run["progress"] = int((position + 1) / len(rows) * 100)
                if pause_s > 0 and position < len(rows) - 1:
                    time.sleep(pause_s)

        metadata["status"] = "completed"
        metadata["amostras"] = len(rows)
        metadata["respostas_ausentes"] = no_answer
        metadata["amostras_com_falha_api"] = failed_samples
        metadata["latencia_ms"] = _stats(latencies)
        _write_json(os.path.join(run_dir, "metadata.json"), metadata)
        run["status"] = "completed"
        run["progress"] = 100
    except Exception as error:
        run["status"] = "cancelled" if run["cancel"] else "error"
        run["error"] = str(error)
        try:
            path = os.path.join(run_dir, "metadata.json")
            metadata = _read_json(path)
            metadata.update({"status": run["status"], "error": str(error)})
            _write_json(path, metadata)
        except Exception:
            pass
        if not run["cancel"]:
            print(f"[GEMINI] Erro: {error}")
    finally:
        if reader is not None:
            reader.release()


def _stats(values):
    if not values:
        return None
    return {
        "n": len(values),
        "media": round(statistics.mean(values), 1),
        "desvio_padrao": round(statistics.pstdev(values), 1),
        "mediana": round(statistics.median(values), 1),
        "minimo": round(min(values), 1),
        "maximo": round(max(values), 1),
    }


def _write_json(path, data):
    with open(path, "w", encoding="utf-8") as file:
        json.dump(data, file, ensure_ascii=False, indent=2)


def _read_json(path):
    with open(path, "r", encoding="utf-8") as file:
        return json.load(file)


# ---------------------------------------------------------------------------
# Consulta
# ---------------------------------------------------------------------------

def get_status(job_id, run_id):
    with _runs_lock:
        run = _runs.get((job_id, run_id))
    if run:
        return {k: run[k] for k in ("id", "job_id", "status", "progress", "done", "total", "error", "model", "note")}
    run_dir = _run_dir(job_id, run_id)
    if not run_dir:
        return None
    metadata = _read_json(os.path.join(run_dir, "metadata.json"))
    status = metadata.get("status", "error")
    if status == "running":  # servidor reiniciado no meio da execução
        status = "error"
    return {"id": run_id, "job_id": job_id, "status": status, "progress": 100 if status == "completed" else 0,
            "done": metadata.get("amostras"), "total": metadata.get("amostras"),
            "error": metadata.get("error") or ("Execução interrompida." if metadata.get("status") == "running" else None),
            "model": metadata.get("model")}


def cancel(job_id, run_id):
    with _runs_lock:
        run = _runs.get((job_id, run_id))
    if not run or run["status"] != "running":
        return False
    run["cancel"] = True
    return True


def delete(job_id, run_id):
    with _runs_lock:
        run = _runs.get((job_id, run_id))
        if run and run["status"] == "running":
            raise GeminiError("Cancele a execução antes de excluí-la.")
        _runs.pop((job_id, run_id), None)
    run_dir = _run_dir(job_id, run_id)
    if not run_dir:
        return False
    import shutil
    shutil.rmtree(run_dir, ignore_errors=True)
    return True


def list_runs(job_id):
    job_dir = test_runner._resolve_job_dir(job_id)
    if not job_dir or not os.path.isdir(_gemini_dir(job_dir)):
        return []
    runs = []
    for run_id in sorted(os.listdir(_gemini_dir(job_dir)), reverse=True):
        status = get_status(job_id, run_id)
        if not status:
            continue
        try:
            metadata = _read_json(os.path.join(_gemini_dir(job_dir), run_id, "metadata.json"))
        except Exception:
            metadata = {}
        status.update({"created_at": metadata.get("created_at"), "temperature": metadata.get("temperature"),
                       "latencia_ms": metadata.get("latencia_ms")})
        runs.append(status)
    return runs


def latest_completed_run(job_id):
    for run in list_runs(job_id):
        if run["status"] == "completed":
            return run["id"]
    return None


def get_metadata(job_id, run_id):
    run_dir = _run_dir(job_id, run_id)
    return _read_json(os.path.join(run_dir, "metadata.json")) if run_dir else None


def get_predictions(job_id, run_id):
    run_dir = _run_dir(job_id, run_id)
    path = os.path.join(run_dir, "predicoes.csv") if run_dir else None
    if not path or not os.path.exists(path):
        return None
    with open(path, "r", newline="", encoding="utf-8") as file:
        reader = csv.DictReader(file)
        fixed = {"sample", "frame", "latencia_ms", "tentativas", "tokens_entrada", "tokens_saida", "status"}
        return {
            str(row["sample"]): {k: v for k, v in row.items() if k not in fixed}
            for row in reader
        }


def image_path(job_id, run_id, name):
    run_dir = _run_dir(job_id, run_id)
    if not run_dir or os.path.basename(name) != name:
        return None
    path = os.path.join(run_dir, "imagens", name)
    return path if os.path.exists(path) else None


def compute_metrics(job_id, run_id):
    """
    Matriz de confusão do Gemini contra o gabarito manual e, para uma
    comparação justa, a do YOLO calculada sobre EXATAMENTE as mesmas
    observações (vaga × amostra) em que o Gemini respondeu.
    """
    job_dir = test_runner._resolve_job_dir(job_id)
    predictions = get_predictions(job_id, run_id)
    if not job_dir or predictions is None:
        return None
    region_names, rows = test_runner._read_rows(job_dir)
    truth = test_runner.load_ground_truth(job_dir)

    gemini_cell = {"tp": 0, "fp": 0, "tn": 0, "fn": 0}
    yolo_cell = {"tp": 0, "fp": 0, "tn": 0, "fn": 0}
    no_answer = 0
    annotated = 0

    def add(cell, real, predicted):
        if real and predicted:
            cell["tp"] += 1
        elif real and not predicted:
            cell["fn"] += 1
        elif not real and predicted:
            cell["fp"] += 1
        else:
            cell["tn"] += 1

    for row in rows:
        key = str(row["sample"])
        truth_row = truth.get(key, {})
        pred_row = predictions.get(key, {})
        for name in region_names:
            real = truth_row.get(name)
            if not isinstance(real, bool) or row.get(name) == OUT_OF_VIEW:
                continue
            annotated += 1
            gemini_state = pred_row.get(name)
            if gemini_state not in (OCCUPIED, FREE):
                no_answer += 1
                continue
            add(gemini_cell, real, gemini_state == OCCUPIED)
            add(yolo_cell, real, row.get(name) == OCCUPIED)

    metadata = get_metadata(job_id, run_id) or {}
    yolo_meta = test_runner._load_metadata(job_dir)
    return {
        "run_id": run_id,
        "model": metadata.get("model"),
        "annotated_cells": annotated,
        "no_answer_cells": no_answer,
        "gemini": test_runner._derive_metrics(gemini_cell),
        "yolo_same_cells": test_runner._derive_metrics(yolo_cell),
        "gemini_latency_ms": metadata.get("latencia_ms"),
        "yolo_time_ms": yolo_meta.get("tempo_por_quadro_ms"),
    }
