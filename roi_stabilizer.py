"""
Estabilização das vagas quando a câmera balança ou o vídeo se move.

O problema:

    As vagas são desenhadas como polígonos de pixel fixos, em cima
    de um frame de referência (o mesmo salvo em reference_frames/<
    ambiente>.jpg pelo editor). Se depois, durante o monitoramento,
    a câmera se mexer (ex.: câmera 360° balançando no suporte) ou o
    vídeo em si tiver a câmera em movimento (drone, mão, veículo),
    a cena "anda" por baixo do polígono, que continua nos mesmos
    pixels -- e a vaga passa a apontar pro lugar errado.

A ideia aqui:

    A cada frame analisado, comparamos pontos de referência (ORB) do
    frame atual contra os pontos do frame de referência salvo. Com
    os pontos que combinam nos dois, calculamos uma homografia (a
    transformação que leva a cena da referência para a cena atual)
    e aplicamos essa transformação aos pontos originais de cada
    vaga. Assim a vaga "acompanha" a cena.

Limitações (importante deixar claro, inclusive pra escrever no TCC):

    - Funciona bem para pan/tilt/zoom e pequenos deslocamentos da
      câmera em uma cena predominantemente plana (o caso comum de
      estacionamento visto de cima/de longe). NÃO modela mudanças
      grandes de perspectiva (câmera que anda pelo estacionamento,
      mudando de posição 3D de forma relevante) -- pra isso seria
      necessário algo como SLAM ou recalibração por profundidade,
      fora do escopo aqui.
    - Depende de haver textura/pontos de referência suficientes na
      cena (funciona mal em superfícies muito lisas/uniformes ou com
      pouca luz).
    - É opcional e por ambiente: se não houver imagem de referência
      salva, ou o ambiente não pedir estabilização, o sistema volta
      a se comportar exatamente como antes (vaga fixa em pixel).

Uso:

    stabilizer = RegionStabilizer(reference_frame, regions)
    ...
    vagas_atuais = stabilizer.update(frame)  # dict nome -> pontos
"""

import cv2
import numpy as np


# Quantidade mínima de correspondências "boas" (após o teste de
# razão de Lowe) pra confiar na homografia calculada. Abaixo disso,
# reaproveitamos a última homografia válida em vez de arriscar uma
# transformação ruim.
MIN_MATCHES = 15

# Evita aceitar transformações numericamente válidas, mas fisicamente
# absurdas (por exemplo, uma correspondência errada que amplia a cena
# centenas de vezes).
MIN_SCALE = 0.35
MAX_SCALE = 2.80
MAX_CONDITION = 1.0e4

# Limite de erro de reprojeção (em pixels) usado pelo RANSAC ao
# calcular a homografia.
RANSAC_REPROJ_THRESHOLD = 5.0

# Teste de razão de Lowe: quanto menor, mais rigoroso na hora de
# aceitar uma correspondência entre os pontos de referência.
LOWE_RATIO = 0.75


class RegionStabilizer:
    """
    Recalcula, frame a frame, a posição das vagas de um ambiente
    cuja câmera/vídeo pode se mover.
    """

    def __init__(self, reference_frame, regions, max_features=1500):
        """
        Args:
            reference_frame: imagem (np.ndarray, BGR ou cinza) usada
                como base quando as vagas foram desenhadas -- em
                geral, a mesma salva em reference_frames/<nome>.jpg.
            regions: dict {nome_da_vaga: [[x, y], ...]} com os
                pontos originais, nas coordenadas dessa imagem de
                referência.
            max_features: número máximo de pontos ORB extraídos por
                frame. Mais pontos tendem a estabilizar melhor, mas
                custam mais processamento.
        """

        self._orb = cv2.ORB_create(nfeatures=max_features)
        self._matcher = cv2.BFMatcher(cv2.NORM_HAMMING)

        self._original_regions = {
            name: np.array(points, dtype=np.float32)
            for name, points in regions.items()
        }

        gray_ref = self._to_gray(reference_frame)
        self._ref_kp, self._ref_desc = self._orb.detectAndCompute(
            gray_ref, None
        )

        # Última homografia válida encontrada. Reaproveitada quando
        # um frame não tem pontos suficientes para recalcular (ex.:
        # motion blur num frame isolado) -- evita que a vaga "pisque"
        # de volta pro lugar errado por causa de um frame ruim.
        self._last_homography = None

        # Informações expostas para quem quiser logar/depurar.
        self.last_match_count = 0
        self.is_tracking = False

    @staticmethod
    def _to_gray(frame):
        if frame is not None and frame.ndim == 3:
            return cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        return frame

    def _estimate_homography(self, frame):
        """Tenta calcular a homografia referência -> frame atual."""

        if self._ref_desc is None:
            return None

        gray = self._to_gray(frame)
        kp, desc = self._orb.detectAndCompute(gray, None)

        if desc is None or len(kp) < MIN_MATCHES:
            self.last_match_count = 0
            return None

        matches = self._matcher.knnMatch(self._ref_desc, desc, k=2)

        good_matches = []

        for pair in matches:

            if len(pair) != 2:
                continue

            best, second_best = pair

            if best.distance < LOWE_RATIO * second_best.distance:
                good_matches.append(best)

        self.last_match_count = len(good_matches)

        if len(good_matches) < MIN_MATCHES:
            return None

        src_points = np.float32(
            [self._ref_kp[m.queryIdx].pt for m in good_matches]
        ).reshape(-1, 1, 2)

        dst_points = np.float32(
            [kp[m.trainIdx].pt for m in good_matches]
        ).reshape(-1, 1, 2)

        homography, inlier_mask = cv2.findHomography(
            src_points,
            dst_points,
            cv2.RANSAC,
            RANSAC_REPROJ_THRESHOLD
        )

        if homography is not None:
            inliers = int(inlier_mask.sum()) if inlier_mask is not None else 0
            if inliers >= MIN_MATCHES and self._valid_transform(homography):
                return homography

        # Fallback para movimentos predominantemente de translação/rotação
        # e pequenos zooms. Em cenas com perspectiva mais simples, a
        # transformação afim parcial costuma ser mais estável que uma
        # homografia baseada em poucas correspondências.
        affine, affine_mask = cv2.estimateAffinePartial2D(
            src_points.reshape(-1, 2),
            dst_points.reshape(-1, 2),
            method=cv2.RANSAC,
            ransacReprojThreshold=RANSAC_REPROJ_THRESHOLD
        )

        if affine is None:
            return None

        affine_inliers = int(affine_mask.sum()) if affine_mask is not None else 0
        if affine_inliers < MIN_MATCHES:
            return None

        affine_h = np.vstack([affine, [0.0, 0.0, 1.0]])
        if not self._valid_transform(affine_h):
            return None

        return affine_h

    @staticmethod
    def _valid_transform(matrix):
        """Rejeita transformações claramente incompatíveis com a cena."""
        if matrix is None or not np.isfinite(matrix).all():
            return False
        try:
            condition = np.linalg.cond(matrix[:2, :2])
            if condition > MAX_CONDITION:
                return False
            sx = np.linalg.norm(matrix[:2, 0])
            sy = np.linalg.norm(matrix[:2, 1])
            if sx < MIN_SCALE or sy < MIN_SCALE:
                return False
            if sx > MAX_SCALE or sy > MAX_SCALE:
                return False
        except (ValueError, np.linalg.LinAlgError):
            return False
        return True

    def update(self, frame):
        """
        Recalcula (quando possível) a posição de cada vaga para o
        frame atual.

        Returns:
            dict {nome_da_vaga: [[x, y], ...]} pronto para virar um
            polígono. Se a câmera estiver parada/sem movimento
            relevante (ou nunca tiver sido possível calcular uma
            homografia confiável), os pontos originais são devolvidos
            -- ou seja, cai no mesmo comportamento de antes.
        """

        homography = self._estimate_homography(frame)

        if homography is not None:
            self._last_homography = homography
            self.is_tracking = True
        else:
            self.is_tracking = False
            homography = self._last_homography

        if homography is None:
            return {
                name: np.round(points).astype(int).tolist()
                for name, points in self._original_regions.items()
            }

        transformed = {}

        for name, points in self._original_regions.items():

            reshaped = points.reshape(-1, 1, 2)
            warped = cv2.perspectiveTransform(reshaped, homography)

            # Arredondamos para inteiro porque o desenho do polígono
            # (cv2.polylines/cv2.circle) exige coordenadas inteiras.
            # A perda de precisão é irrelevante para o tamanho de uma
            # vaga de estacionamento.
            transformed[name] = np.round(
                warped.reshape(-1, 2)
            ).astype(int).tolist()

        return transformed
