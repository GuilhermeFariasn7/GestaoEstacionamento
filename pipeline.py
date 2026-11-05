"""
Receita única do detector do Smart Parking.

O monitoramento ao vivo (live_monitor.py) e os testes do TCC
(test_runner.py) montam o detector POR AQUI, para usarem exatamente o
mesmo modelo, as mesmas classes, a mesma confiança, o mesmo critério de
ocupação e a mesma estabilização das vagas. Qualquer ajuste feito aqui
vale para os dois.

Fluxo de um quadro no protótipo:
    quadro da câmera/vídeo
      -> RegionStabilizer.update()      (só se o ambiente pedir estabilização)
      -> YOLO (model.track / predict)   (dentro do RegionCounterLive)
      -> associação veículo x vaga      (critério de ocupação)
      -> region_counts {vaga: True/False}
"""

import regions_store as store
from roi_stabilizer import build_stabilizer_for_environment

# Pesos do YOLO (arquivo na pasta do projeto).
MODEL_PATH = "yolo12x.pt"

# Classe 2 do COCO = "car".
VEHICLE_CLASSES = [2]

# O protótipo roda o YOLO em 1 de cada N quadros lidos.
DETECT_EVERY_N_FRAMES = 3

DEFAULT_CONFIDENCE = 0.4
DEFAULT_CRITERION = "sobreposicao"


def environment_confidence(environment_name):
    """Confiança salva no ambiente (a mesma usada pelo monitoramento ao vivo)."""
    config = store.get_detection_config(environment_name) or {}
    try:
        return float(config.get("confidence", DEFAULT_CONFIDENCE))
    except (TypeError, ValueError):
        return DEFAULT_CONFIDENCE


def is_detection_frame(frame_number):
    """
    Mesmo ritmo do protótipo: frame_number começa em 1 no primeiro quadro
    lido; o YOLO roda nos quadros 3, 6, 9...
    """
    return frame_number % DETECT_EVERY_N_FRAMES == 0


def build_counter(environment_name, coordinates, confidence=None, stabilize=None,
                  criterion=DEFAULT_CRITERION, use_tracking=True, show=False, log=print):
    """
    Monta o RegionCounterLive (YOLO + vagas) e o estabilizador do ambiente.

    Args:
        confidence: None = a do ambiente.
        stabilize: None = conforme o ambiente; True/False força.
        use_tracking: True = model.track (ByteTrack), como no protótipo.
        show: abre a janela do OpenCV (só o monitoramento ao vivo usa).

    Returns:
        (counter, stabilizer) -- stabilizer pode ser None.
    """
    from regionCounterLive import RegionCounterLive

    if confidence is None:
        confidence = environment_confidence(environment_name)

    stabilizer = build_stabilizer_for_environment(
        environment_name, coordinates, enabled=stabilize, log=log
    )

    counter = RegionCounterLive(
        show=show,
        region=coordinates,
        model=MODEL_PATH,
        classes=VEHICLE_CLASSES,
        conf=confidence,
        stabilizer=stabilizer,
        criterion=criterion,
        use_tracking=use_tracking,
    )
    return counter, stabilizer
