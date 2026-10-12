"""
Diagnóstico da estabilização das vagas (sem YOLO).

Roda apenas o rastreamento da câmera sobre um vídeo e gera:

    - um vídeo com as vagas desenhadas na posição estimada;
    - um CSV com, frame a frame: modo de rastreamento, correspondências,
      inliers e movimento estimado da câmera.

Serve para:
    - conferir visualmente se as vagas acompanham a cena;
    - escolher o tipo de projeção/FOV correto da sua câmera;
    - comparar projeções (ex.: perspectiva x equirretangular) e gerar
      dados para o capítulo de resultados do TCC.

Exemplos:

    python diagnostico_estabilizacao.py AMBIENTE_TESTE_C
    python diagnostico_estabilizacao.py AMBIENTE_TESTE_C --projection equirectangular
    python diagnostico_estabilizacao.py teste02 --projection fisheye --fov 190 --max-frames 600
    python diagnostico_estabilizacao.py teste02 --source D:/videos/outro.mp4 --step 2
"""

import argparse
import csv
import os
import sys
import time

import cv2
import numpy as np

import regions_store as store
from camera_models import normalize_projection
from roi_stabilizer import RegionStabilizer


BASE_DIR = os.path.dirname(os.path.abspath(__file__))


def _open(source):
    source = str(source).strip()
    if source.isdigit():
        return cv2.VideoCapture(int(source))
    if not os.path.isabs(source):
        source = os.path.join(BASE_DIR, source)
    return cv2.VideoCapture(source)


def _draw(frame, regions, stabilizer):
    height, width = frame.shape[:2]
    color = (0, 220, 0) if stabilizer.is_tracking else (0, 165, 255)

    for name, points in regions.items():
        pts = np.array(points, dtype=np.int32)
        shifts = (-width, 0, width) if stabilizer.wrap_width else (0,)
        for shift in shifts:
            moved = pts + np.array([shift, 0])
            cv2.polylines(frame, [moved], True, color, 2, cv2.LINE_AA)
        x, y = pts.min(axis=0)
        cv2.putText(frame, name, (int(x), max(15, int(y) - 6)),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.6, color, 2, cv2.LINE_AA)

    info = stabilizer.diagnostics()
    text = (f'{info["projection"]} | {info["mode"]} | '
            f'inliers {info["inliers"]}/{info["matches"]} | mov {info["motion"]}')
    cv2.putText(frame, text, (10, 30), cv2.FONT_HERSHEY_SIMPLEX, 0.8,
                (0, 0, 0), 4, cv2.LINE_AA)
    cv2.putText(frame, text, (10, 30), cv2.FONT_HERSHEY_SIMPLEX, 0.8,
                color, 2, cv2.LINE_AA)
    return frame


def main():
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("environment", help="nome do ambiente salvo no editor")
    parser.add_argument("--source", help="vídeo alternativo (padrão: o do ambiente)")
    parser.add_argument("--projection", help="sobrescreve a projeção salva")
    parser.add_argument("--fov", type=float, help="sobrescreve o FOV salvo (graus)")
    parser.add_argument("--step", type=int, default=1, help="processa 1 a cada N frames")
    parser.add_argument("--max-frames", type=int, default=0, help="limite de frames (0 = todos)")
    parser.add_argument("--output-width", type=int, default=1600,
                        help="largura do vídeo de saída")
    parser.add_argument("--show", action="store_true", help="mostra uma janela durante o processamento")
    args = parser.parse_args()

    env = store.get_environment(args.environment)
    if env is None:
        sys.exit(f"Ambiente '{args.environment}' não encontrado.")

    reference_path = store.get_reference_image_path(args.environment)
    if not reference_path:
        sys.exit("Ambiente sem frame de referência. Capture um frame no editor primeiro.")

    reference = cv2.imread(reference_path)
    regions = store.get_regions_for_program(args.environment)
    config = store.get_detection_config(args.environment)

    projection = normalize_projection(args.projection or config.get("projection"))
    fov = args.fov or config.get("fov")

    stabilizer = RegionStabilizer(reference, regions, projection=projection, fov=fov,
                                  mask_bottom=config.get("mask_bottom"))

    capture = _open(args.source or env["source"])
    if not capture.isOpened():
        sys.exit(f"Não foi possível abrir a fonte: {args.source or env['source']}")

    fps = capture.get(cv2.CAP_PROP_FPS) or 15
    out_dir = os.path.join(BASE_DIR, "diagnosticos")
    os.makedirs(out_dir, exist_ok=True)
    stamp = time.strftime("%Y%m%d_%H%M%S")
    base = os.path.join(out_dir, f"{args.environment}_{projection}_{stamp}")

    writer = None
    rows = []
    frame_index = 0
    processed = 0
    started = time.time()

    print(f"Projeção: {projection} | FOV: {fov or '-'} | "
          f"referência {stabilizer.ref_w}x{stabilizer.ref_h} | "
          f"{len(stabilizer._ref_pts)} pontos na referência")

    while True:
        ok, frame = capture.read()
        if not ok:
            break

        if frame_index % max(1, args.step) == 0:
            current = stabilizer.update(frame)
            info = stabilizer.diagnostics()
            rows.append({"frame": frame_index, **info})

            annotated = _draw(frame.copy(), current, stabilizer)
            scale = args.output_width / annotated.shape[1]
            annotated = cv2.resize(annotated, (args.output_width, int(annotated.shape[0] * scale)))

            if writer is None:
                writer = cv2.VideoWriter(base + ".mp4", cv2.VideoWriter_fourcc(*"mp4v"),
                                         fps / max(1, args.step),
                                         (annotated.shape[1], annotated.shape[0]))
            writer.write(annotated)

            if args.show:
                cv2.imshow("Diagnóstico da estabilização", annotated)
                if cv2.waitKey(1) & 0xFF == 27:
                    break

            processed += 1
            if processed % 50 == 0:
                print(f"  {processed} frames processados...")

            if args.max_frames and processed >= args.max_frames:
                break

        frame_index += 1

    capture.release()
    if writer is not None:
        writer.release()
    cv2.destroyAllWindows()

    if not rows:
        sys.exit("Nenhum frame lido da fonte.")

    with open(base + ".csv", "w", newline="", encoding="utf-8") as file:
        csv_writer = csv.DictWriter(file, fieldnames=list(rows[0].keys()))
        csv_writer.writeheader()
        csv_writer.writerows(rows)

    tracked = sum(1 for row in rows if row["tracking"])
    by_mode = {}
    for row in rows:
        by_mode[row["mode"]] = by_mode.get(row["mode"], 0) + 1

    elapsed = time.time() - started
    print()
    print(f"Frames analisados : {len(rows)} ({len(rows) / elapsed:.1f} fps)")
    print(f"Com rastreamento  : {tracked} ({100 * tracked / len(rows):.1f}%)")
    print(f"Por modo          : {by_mode}")
    print(f"Inliers (mediana) : {int(np.median([row['inliers'] for row in rows]))}")
    print(f"Vídeo             : {base}.mp4")
    print(f"CSV               : {base}.csv")


if __name__ == "__main__":
    main()
