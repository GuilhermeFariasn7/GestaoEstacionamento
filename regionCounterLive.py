"""
Contador de ocupação das vagas.

Cada região representa uma vaga cadastrada no ambiente.

A lógica é simples:

- o YOLO detecta os veículos;
- pegamos o centro de cada bounding box;
- verificamos em qual vaga esse centro está;
- se houver pelo menos um veículo dentro da região,
  a vaga é considerada ocupada;
- a região é desenhada em verde ou vermelho.
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


class RegionCounterLive(BaseSolution):

    def __init__(self, **kwargs):
        super().__init__(**kwargs)

        self.counting_regions = []

        # As regiões são preparadas uma vez.
        # Não precisamos recriar os polígonos em todos os frames.
        self._prepare_regions()

    def _prepare_regions(self):
        """Prepara os polígonos utilizados para verificar ocupação."""

        self.counting_regions = []

        for region_name, region_points in self.region.items():

            polygon = self.Polygon(region_points)

            self.counting_regions.append({
                "name": region_name,
                "points": region_points,
                "polygon": polygon,
                "prepared_polygon": self.prep(polygon),
                "counts": 0
            })

    def process(self, im0):
        """
        Analisa um frame e atualiza o estado das vagas.

        O resultado de cada vaga fica disponível em region_counts.
        """

        # Primeiro o modelo faz a detecção/rastreamento dos veículos.
        self.extract_tracks(im0)

        annotator = SolutionAnnotator(
            im0,
            line_width=self.line_width
        )

        # Zera os contadores antes de analisar o novo frame.
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

            # Calcula o centro de cada bounding box.
            centers = np.column_stack((
                (boxes_np[:, 0] + boxes_np[:, 2]) / 2,
                (boxes_np[:, 1] + boxes_np[:, 3]) / 2
            ))

            points = [
                self.Point(
                    float(center[0]),
                    float(center[1])
                )
                for center in centers
            ]

            # Verifica cada veículo em cada vaga.
            for point, cls, box in zip(
                points,
                self.clss,
                boxes
            ):

                annotator.box_label(
                    box,
                    label=self.names[cls],
                    color=COLOR_VEHICLE
                )

                for region in self.counting_regions:

                    if region["prepared_polygon"].contains(point):
                        region["counts"] += 1

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

            label = (
                f'{region["name"]}: {status}'
            )

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

        # Guarda somente o resultado necessário para o restante
        # do sistema consumir.
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