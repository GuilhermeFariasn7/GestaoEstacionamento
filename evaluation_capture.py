"""Registro de previsões do monitoramento principal para avaliação acadêmica."""
import csv
import os
import threading
import time
import uuid
from datetime import datetime

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.join(BASE_DIR, 'data', 'avaliacoes')
_lock = threading.RLock()
_active = None
_latest = None


def start(environment, sample_every=5):
    global _active, _latest
    with _lock:
        if _active and _active.get('active'):
            raise RuntimeError('Já existe uma coleta de avaliação em andamento.')
        try:
            sample_every = max(1, min(100, int(sample_every)))
        except (TypeError, ValueError):
            sample_every = 5
        stamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        session_id = f'{stamp}_{uuid.uuid4().hex[:6]}'
        folder = os.path.join(ROOT, session_id)
        os.makedirs(os.path.join(folder, 'amostras'), exist_ok=True)
        csv_path = os.path.join(folder, 'previsoes.csv')
        with open(csv_path, 'w', newline='', encoding='utf-8-sig') as f:
            writer = csv.writer(f)
            writer.writerow(['session_id','ambiente','amostra_id','frame','timestamp','vaga','previsao','visivel','tempo_inferencia_ms','imagem'])
        _active = {'active': True, 'id': session_id, 'environment': environment,
                   'folder': folder, 'csv': csv_path, 'sample_every': sample_every,
                   'detections': 0, 'samples': 0, 'started_at': datetime.now().isoformat(timespec='seconds')}
        _latest = _active
        return status()


def stop():
    global _active, _latest
    with _lock:
        if not _active:
            return status()
        _active['active'] = False
        _active['finished_at'] = datetime.now().isoformat(timespec='seconds')
        _latest = _active
        _active = None
        return status()


def status():
    with _lock:
        item = _active or _latest
        if not item:
            return {'active': False, 'exists': False}
        return {k: item.get(k) for k in ('active','id','environment','sample_every','detections','samples','started_at','finished_at') if k in item} | {'exists': True}


def record(environment, frame_index, timestamp_seconds, predictions, visibility, inference_ms, annotated_frame):
    """Persiste amostras e previsões somente quando a coleta está ativa."""
    import cv2
    with _lock:
        item = _active
        if not item or not item.get('active') or item['environment'] != environment:
            return
        item['detections'] += 1
        if (item['detections'] - 1) % item['sample_every'] != 0:
            return
        sample_id = f"{item['samples'] + 1:05d}"
        filename = f'{sample_id}_frame_{int(frame_index):08d}.jpg'
        image_path = os.path.join(item['folder'], 'amostras', filename)
        if annotated_frame is not None:
            cv2.imwrite(image_path, annotated_frame, [int(cv2.IMWRITE_JPEG_QUALITY), 92])
        else:
            filename = ''
        with open(item['csv'], 'a', newline='', encoding='utf-8') as f:
            writer = csv.writer(f)
            for vacancy, used in predictions.items():
                visible = bool(visibility.get(vacancy, True))
                prediction = 'fora_de_vista' if not visible else ('ocupada' if int(used or 0) > 0 else 'livre')
                writer.writerow([item['id'], environment, sample_id, frame_index,
                                 f'{float(timestamp_seconds):.3f}', vacancy, prediction,
                                 int(visible), f'{float(inference_ms):.3f}', filename])
        item['samples'] += 1


def latest_paths():
    with _lock:
        item = _active or _latest
        if not item:
            return None
        return {'folder': item['folder'], 'csv': item['csv'], 'id': item['id'], 'environment': item['environment']}


def create_ground_truth_template():
    paths = latest_paths()
    if not paths:
        raise RuntimeError('Ainda não existe uma coleta. Inicie e finalize uma coleta primeiro.')
    import pandas as pd
    predictions = pd.read_csv(paths['csv'], encoding='utf-8-sig')
    unique = predictions[['session_id','ambiente','amostra_id','frame','timestamp','vaga','imagem']].drop_duplicates()
    out = os.path.join(paths['folder'], 'gabarito.csv')
    unique['estado_real'] = ''
    unique.to_csv(out, index=False, encoding='utf-8-sig')
    return out
