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

import numpy as np

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
        self._prepare_regions()

    # ------------------------------------------------------------------
    # Preparação das regiões
    # ------------------------------------------------------------------

    def _prepare_regions(self, points_by_name=None):
        """
        Prepara os polígonos utilizados para verificar ocupação.
        """
        points_by_name = points_by_name or self.region
        self.counting_regions = []

        for region_name, region_points in points_by_name.items():
            polygon = self.Polygon(region_points)
            self.counting_regions.append({
                "name": region_name,
                "points": region_points,
                "polygon": polygon,
                "prepared_polygon": self.prep(polygon),
                "counts": 0
            })

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

        annotator = SolutionAnnotator(
            im0,
            line_width=self.line_width
        )

        if self.stabilizer is not None:
            current_points = self.stabilizer.update(im0)
            self._prepare_regions(current_points)
        else:
            for region in self.counting_regions:
                region["counts"] = 0

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

                vehicle_polygon = self.Polygon([
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

        for region in self.counting_regions:
            occupied = region["counts"] > 0
            color = (
                COLOR_OCCUPIED
                if occupied
                else COLOR_FREE
            )
            status = (
                "ocupada"
                if occupied
                else "livre"
            )
            label = f'{region["name"]}: {status}'

            annotator.draw_region(
                region["points"],
                color,
                self.line_width * 2
            )
            annotator.text_label(
                region["polygon"].bounds,
                label=label,
                color=color,
                txt_color=COLOR_TEXT
            )

        self.region_counts = {
            region["name"]: region["counts"]
            for region in self.counting_regions
        }

        plot_im = annotator.result()
        self.display_output(plot_im)

        return SolutionResults(
            plot_im=plot_im,
            total_tracks=len(self.track_ids),
            region_counts=self.region_counts
        )
