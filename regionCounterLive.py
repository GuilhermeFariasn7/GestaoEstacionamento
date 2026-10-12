"""
Contador de ocupação das vagas.

Cada região representa uma vaga cadastrada no ambiente.

A lógica é simples:
- o YOLO detecta os veículos;
- para cada bounding box, calculamos a sobreposição entre o veículo
  detectado e o polígono da vaga;
- se uma parte mínima da área do veículo estiver dentro da vaga,
  ela é considerada ocupada;
- a região é desenhada em verde ou vermelho.

A decisão não depende mais do centro da bounding box. Isso evita que
um veículo seja considerado ausente quando o centro da detecção fica
fora do polígono, mesmo estando visualmente sobre a vaga.
"""

import cv2
import numpy as np
from shapely.affinity import translate
from shapely.geometry import box as shapely_box
from shapely.geometry import Polygon as ShapelyPolygon
from shapely.ops import unary_union

from ultralytics.solutions.solutions import (
    BaseSolution,
    SolutionAnnotator,
    SolutionResults
)


# As cores são BGR porque a imagem utilizada pelo OpenCV segue esse formato.
COLOR_FREE = (0, 200, 0)
COLOR_OCCUPIED = (0, 0, 255)
COLOR_TEXT = (255, 255, 255)
COLOR_VEHICLE = (255, 200, 0)
COLOR_HIDDEN = (140, 140, 140)
COLOR_TRACK_OK = (0, 200, 0)
COLOR_TRACK_LOST = (0, 165, 255)

# Fração mínima da vaga que precisa estar dentro da imagem para ela ser
# avaliada. Abaixo disso (câmera girou e a vaga saiu do quadro) a vaga
# fica "fora de vista" em vez de ser reportada, incorretamente, como livre.
MIN_VISIBLE_FRACTION = 0.5


# ------------------------------------------------------------------
# Critério de ocupação
# ------------------------------------------------------------------
#
# A decisão usa a área da bounding box do veículo que está dentro da
# região da vaga.
#
# Exemplo:
#
#     área do veículo dentro da vaga
#     ------------------------------- >= MIN_VEHICLE_OVERLAP
#             área do veículo
#
# Isso é mais adequado que testar somente o centro da caixa, porque
# um veículo pode estar claramente sobre a vaga enquanto seu centro
# fica fora do polígono configurado.
#
# 0.15 = 15% da área do veículo dentro da vaga.
# ------------------------------------------------------------------
MIN_VEHICLE_OVERLAP = 0.15


class RegionCounterLive(BaseSolution):
    def __init__(self, stabilizer=None, **kwargs):
        """
        Args:
            stabilizer (RegionStabilizer, optional): quando informado,
                a posição de cada vaga é recalculada a cada frame (ver
                roi_stabilizer.py), útil para câmera 360°/vídeo que se
                move. Se None (padrão), o comportamento é de vaga fixa
                nos pixels configurados.

            **kwargs: demais argumentos repassados para o BaseSolution
                do ultralytics (model, region, classes, conf, show...).
        """
        super().__init__(**kwargs)
        self.stabilizer = stabilizer
        self.counting_regions = []
        self._prepared_shape = None
        self._prepare_regions()

    # ------------------------------------------------------------------
    # Preparação das regiões
    # ------------------------------------------------------------------

    def _prepare_regions(self, points_by_name=None, frame_shape=None, wrap_width=None):
        """
        Prepara os polígonos utilizados para verificar ocupação.

        Args:
            points_by_name: pontos de cada vaga (padrão: os configurados).
            frame_shape: (altura, largura) do frame, para recortar as
                vagas na borda da imagem e calcular a visibilidade.
            wrap_width: largura da imagem quando ela é 360°
                equirretangular. Nesse caso, uma vaga que cruza a emenda
                (lado esquerdo/direito) é dividida em duas partes, uma
                em cada borda da imagem, e as duas contam como a mesma
                vaga.
        """
        points_by_name = points_by_name or self.region
        self.counting_regions = []

        for region_name, region_points in points_by_name.items():
            raw = ShapelyPolygon(region_points)
            if not raw.is_valid:
                raw = raw.buffer(0)

            geometry = raw
            visible_fraction = 1.0

            if frame_shape is not None and raw.area > 0:
                height, width = frame_shape[:2]
                frame_box = shapely_box(0, 0, width, height)

                if wrap_width:
                    pieces = [
                        translate(raw, xoff=shift * wrap_width).intersection(frame_box)
                        for shift in (-1, 0, 1)
                    ]
                    geometry = unary_union([
                        piece for piece in pieces if not piece.is_empty
                    ])
                else:
                    geometry = raw.intersection(frame_box)

                visible_fraction = geometry.area / raw.area if raw.area else 0.0

            visible = (
                not geometry.is_empty and
                visible_fraction >= MIN_VISIBLE_FRACTION
            )

            self.counting_regions.append({
                "name": region_name,
                "points": region_points,
                "polygon": geometry if not geometry.is_empty else raw,
                "visible": visible,
                "visible_fraction": round(float(visible_fraction), 3),
                "counts": 0
            })

    @staticmethod
    def _polygon_parts(geometry):
        """Lista de contornos (para desenhar), mesmo para MultiPolygon."""
        if geometry.is_empty:
            return []
        geoms = getattr(geometry, "geoms", [geometry])
        parts = []
        for geom in geoms:
            exterior = getattr(geom, "exterior", None)
            if exterior is not None and len(exterior.coords) >= 3:
                parts.append(np.array(exterior.coords, dtype=np.int32))
        return parts

    def _draw_stabilizer_info(self, image):
        info = self.stabilizer.diagnostics()
        color = COLOR_TRACK_OK if info["tracking"] else COLOR_TRACK_LOST
        text = (
            f'{info["projection"]} | {info["mode"]} | '
            f'inliers {info["inliers"]}/{info["matches"]} | mov {info["motion"]}'
        )
        scale = max(0.5, image.shape[1] / 2200)
        thickness = max(1, int(round(scale * 2)))
        cv2.putText(image, text, (12, int(32 * scale)), cv2.FONT_HERSHEY_SIMPLEX,
                    scale, (0, 0, 0), thickness + 2, cv2.LINE_AA)
        cv2.putText(image, text, (12, int(32 * scale)), cv2.FONT_HERSHEY_SIMPLEX,
                    scale, color, thickness, cv2.LINE_AA)

    # ------------------------------------------------------------------
    # Análise de cada frame
    # ------------------------------------------------------------------

    def process(self, im0):
        """
        Analisa um frame e atualiza o estado das vagas.

        Um veículo ocupa uma vaga quando pelo menos
        MIN_VEHICLE_OVERLAP da área da sua bounding box está dentro
        do polígono da vaga.
        """
        # Primeiro o modelo faz a detecção/rastreamento dos veículos.
        self.extract_tracks(im0)

        # A posição das vagas é atualizada ANTES de qualquer desenho,
        # para o estabilizador ver o frame original.
        if self.stabilizer is not None:
            current_points = self.stabilizer.update(im0)
            self._prepare_regions(
                current_points,
                frame_shape=im0.shape,
                wrap_width=self.stabilizer.wrap_width
            )
        elif self._prepared_shape != im0.shape[:2]:
            self._prepare_regions(frame_shape=im0.shape)
            self._prepared_shape = im0.shape[:2]
        else:
            for region in self.counting_regions:
                region["counts"] = 0

        annotator = SolutionAnnotator(
            im0,
            line_width=self.line_width
        )

        # ---------------------------------------------------------
        # Identificação dos veículos detectados
        # ---------------------------------------------------------

        boxes = self.boxes

        if boxes is not None and len(boxes) > 0:
            boxes_np = np.asarray(
                boxes,
                dtype=np.float32
            )

            for cls, box in zip(self.clss, boxes):
                annotator.box_label(
                    box,
                    label=self.names[cls],
                    color=COLOR_VEHICLE
                )

                x1, y1, x2, y2 = [
                    float(box[0]),
                    float(box[1]),
                    float(box[2]),
                    float(box[3])
                ]

                # Garante uma caixa válida mesmo que algum detector
                # retorne coordenadas invertidas.
                left = min(x1, x2)
                right = max(x1, x2)
                top = min(y1, y2)
                bottom = max(y1, y2)

                vehicle_area = max(0.0, (right - left) * (bottom - top))

                if vehicle_area <= 0:
                    continue

                vehicle_polygon = ShapelyPolygon([
                    (left, top),
                    (right, top),
                    (right, bottom),
                    (left, bottom)
                ])

                # Verifica a sobreposição da detecção com cada vaga.
                # A detecção é atribuída somente à vaga com maior
                # sobreposição. Isso evita que um carro próximo da
                # divisa entre duas vagas marque as duas como ocupadas.
                best_region = None
                best_overlap = 0.0

                for region in self.counting_regions:
                    if not region["visible"]:
                        continue

                    intersection = vehicle_polygon.intersection(
                        region["polygon"]
                    )

                    if intersection.is_empty:
                        continue

                    overlap_area = intersection.area / vehicle_area

                    if overlap_area > best_overlap:
                        best_overlap = overlap_area
                        best_region = region

                if best_region is not None and best_overlap >= MIN_VEHICLE_OVERLAP:
                    best_region["counts"] += 1

        # ---------------------------------------------------------
        # Desenho das vagas
        # ---------------------------------------------------------

        image = annotator.im

        for region in self.counting_regions:
            occupied = region["counts"] > 0

            if not region["visible"]:
                color = COLOR_HIDDEN
                status = "fora de vista"
            elif occupied:
                color = COLOR_OCCUPIED
                status = "ocupada"
            else:
                color = COLOR_FREE
                status = "livre"

            parts = self._polygon_parts(region["polygon"])
            if parts:
                cv2.polylines(image, parts, True, color, self.line_width * 2, cv2.LINE_AA)

                # Rótulo na maior parte visível da vaga.
                largest = max(parts, key=lambda part: cv2.contourArea(part))
                x1, y1 = largest.min(axis=0)
                x2, y2 = largest.max(axis=0)
                annotator.text_label(
                    (float(x1), float(y1), float(x2), float(y2)),
                    label=f'{region["name"]}: {status}',
                    color=color,
                    txt_color=COLOR_TEXT
                )

        if self.stabilizer is not None:
            self._draw_stabilizer_info(image)

        self.region_counts = {
            region["name"]: region["counts"]
            for region in self.counting_regions
        }
        self.region_visibility = {
            region["name"]: region["visible"]
            for region in self.counting_regions
        }

        plot_im = annotator.result()
        self.display_output(plot_im)

        return SolutionResults(
            plot_im=plot_im,
            total_tracks=len(self.track_ids),
            region_counts=self.region_counts
        )
