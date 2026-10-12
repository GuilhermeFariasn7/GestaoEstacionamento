"""
Estabilização das vagas quando a câmera se move (câmeras convencionais,
360° e panorâmicas).

O problema:

    As vagas são polígonos desenhados sobre o frame de referência
    (reference_frames/<ambiente>.jpg). Se a câmera girar ou balançar,
    a cena "anda" por baixo do polígono e a vaga passa a apontar para o
    lugar errado -- a marcação acompanha a câmera, e não o chão.

A solução:

    1. Detectamos pontos característicos (ORB) no frame atual e no
       frame de referência e casamos esses pontos.
    2. Com as correspondências estimamos o movimento da câmera usando
       o MODELO CERTO PARA CADA TIPO DE CÂMERA:

         - convencional (perspectiva): homografia, como antes. Para uma
           câmera que gira em torno do próprio centro (ou que observa
           uma cena aproximadamente plana), a homografia é exata.

         - 360° equirretangular, olho de peixe e panorâmica cilíndrica:
           cada pixel é convertido em um raio 3D (camera_models.py) e
           estimamos uma ROTAÇÃO 3D da câmera (Kabsch + RANSAC). A
           homografia NÃO funciona nesses formatos: numa imagem 360°
           uma rotação horizontal "dá a volta" na emenda da imagem, e
           inclinações transformam retas em curvas. A rotação 3D trata
           os dois casos corretamente.

    3. Aplicamos o movimento aos pontos de cada vaga. Nos formatos
       esféricos as arestas das vagas são subdivididas antes, para que
       elas possam se curvar acompanhando a distorção da lente.
    4. Refinamento local: o movimento global nunca é perfeito em toda
       a imagem (paralaxe, FOV aproximado, lente imperfeita). Para cada
       vaga calculamos a mediana do erro residual dos pontos próximos
       dela e corrigimos a vaga com esse deslocamento.
    5. Robustez temporal:
         - suavização exponencial do movimento (evita a vaga "tremer");
         - se o frame não casar com a referência (mudança de luz, borrão
           de movimento), tentamos casar com o último frame que casou
           (âncora) e encadeamos as duas transformações;
         - se nada funcionar, mantemos a última posição válida.

Limitações (para o texto do TCC):

    - O modelo assume que a câmera GIRA/BALANÇA no suporte. Se ela se
      deslocar muito no espaço (andar pelo estacionamento), surge
      paralaxe que nenhum modelo 2D/rotação representa; o refinamento
      local reduz, mas não elimina, esse erro.
    - Depende de textura na cena (faixas, meio-fio, prédios, árvores).
    - Para olho de peixe/cilíndrica, o FOV informado precisa ser
      próximo do real; um FOV errado aparece como erro residual, que é
      parcialmente compensado pelo refinamento local.
    - A imagem 360° "dupla" (duas lentes lado a lado, sem costura) não
      é suportada: exporte o vídeo da câmera em equirretangular.

Uso:

    stabilizer = RegionStabilizer(reference_frame, regions,
                                  projection="equirectangular")
    vagas_atuais = stabilizer.update(frame)   # dict nome -> pontos
"""

import math

import cv2
import numpy as np

from camera_models import (
    PROJECTION_EQUIRECTANGULAR,
    PROJECTION_FISHEYE,
    PROJECTION_PERSPECTIVE,
    build_camera_model,
    normalize_projection,
    ransac_rotation,
    rotation_angle_degrees,
)


# Quantidade mínima de correspondências/inliers para confiar no movimento.
MIN_MATCHES = 15

# Fração mínima de inliers entre as correspondências boas.
MIN_INLIER_RATIO = 0.15

# Limites para rejeitar homografias fisicamente absurdas.
MIN_SCALE = 0.35
MAX_SCALE = 2.80
MAX_CONDITION = 1.0e4

# Erro de reprojeção (pixels da imagem de trabalho) aceito pelo RANSAC.
RANSAC_REPROJ_THRESHOLD = 4.0

# Teste de razão de Lowe.
LOWE_RATIO = 0.75

# Largura máxima da imagem usada na extração de pontos. Câmeras 360°
# costumam ter 4K-6K de largura; processar nessa resolução seria lento
# sem ganho real de precisão.
WORK_WIDTH = 1600

# Suavização temporal: 0 = sem suavização, 0.9 = muito suave/lento.
SMOOTHING = 0.5

# A suavização é adaptativa: só atua sobre o TREMOR (movimentos
# pequenos entre frames). Movimentos maiores que estes limites são
# seguidos sem atraso, para a vaga não "deslizar" atrás da cena quando
# a câmera gira de verdade.
JITTER_ROTATION_DEG = 0.5
JITTER_CORNER_FRACTION = 0.004

# Refinamento local por vaga.
LOCAL_MIN_MATCHES = 8
LOCAL_MAX_RESIDUAL_FACTOR = 6.0   # múltiplos do limiar do RANSAC
LOCAL_MARGIN_FRACTION = 0.06      # margem em volta da vaga (fração da largura)
LOCAL_DECAY = 0.9                 # quanto o deslocamento local "esquece" sem dados

# Subdivisão das arestas das vagas nos modelos esféricos.
EDGE_SUBDIVISIONS = 12

# Máscaras padrão dos modelos esféricos. Na imagem 360° a parte de
# baixo (nadir) mostra o tripé/suporte/mão, que se move JUNTO com a
# câmera; esses pontos "votam" por movimento zero e atrapalham.
DEFAULT_MASK_TOP = 0.05
DEFAULT_MASK_BOTTOM = 0.12

MODE_REFERENCE = "referencia"
MODE_CHAINED = "encadeado"
MODE_HELD = "mantido"


class RegionStabilizer:
    """
    Recalcula, frame a frame, a posição das vagas de um ambiente cuja
    câmera pode se mover.
    """

    def __init__(
        self,
        reference_frame,
        regions,
        projection=PROJECTION_PERSPECTIVE,
        fov=None,
        max_features=3000,
        work_width=WORK_WIDTH,
        mask_top=None,
        mask_bottom=None,
        smoothing=SMOOTHING,
        local_refine=True,
    ):
        """
        Args:
            reference_frame: imagem em que as vagas foram desenhadas.
            regions: dict {nome: [[x, y], ...]} em coordenadas dessa imagem.
            projection: "perspective", "equirectangular", "fisheye" ou
                "cylindrical" (ver camera_models.py).
            fov: campo de visão em graus (fisheye/cilíndrica).
            max_features: pontos ORB por frame.
            work_width: largura usada na extração de pontos.
            mask_top / mask_bottom: fração da altura ignorada no topo/
                base da imagem (padrão só para 360°).
            smoothing: suavização temporal (0 a 0.95).
            local_refine: ativa o refinamento local por vaga.
        """

        if reference_frame is None:
            raise ValueError("Frame de referência inválido.")

        self.projection = normalize_projection(projection)
        self.smoothing = float(min(0.95, max(0.0, smoothing)))
        self.local_refine = bool(local_refine)

        self.ref_h, self.ref_w = reference_frame.shape[:2]
        self.model = build_camera_model(self.projection, self.ref_w, self.ref_h, fov)
        self.wraps = bool(self.model is not None and self.model.wraps_horizontally)

        self._scale = min(1.0, float(work_width) / float(self.ref_w))
        self._work_size = (
            max(1, int(round(self.ref_w * self._scale))),
            max(1, int(round(self.ref_h * self._scale))),
        )

        self._orb = cv2.ORB_create(nfeatures=max_features, fastThreshold=10)
        self._matcher = cv2.BFMatcher(cv2.NORM_HAMMING)
        self._clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8))

        if self.projection == PROJECTION_EQUIRECTANGULAR:
            mask_top = DEFAULT_MASK_TOP if mask_top is None else mask_top
            mask_bottom = DEFAULT_MASK_BOTTOM if mask_bottom is None else mask_bottom
        self._feature_mask = self._build_feature_mask(mask_top or 0.0, mask_bottom or 0.0)

        # Limiar do RANSAC em pixels da referência.
        self._threshold_ref = RANSAC_REPROJ_THRESHOLD / self._scale

        # Vagas originais e versão subdividida (usada nos modelos esféricos).
        self._original_regions = {
            name: np.array(points, dtype=np.float64).reshape(-1, 2)
            for name, points in regions.items()
        }
        self._dense_regions = {
            name: (self._densify(points) if self.model is not None else points)
            for name, points in self._original_regions.items()
        }
        self._region_boxes = {
            name: self._region_box(points)
            for name, points in self._original_regions.items()
        }
        self._local_offsets = {
            name: np.zeros(2) for name in self._original_regions
        }

        self._ref_pts, self._ref_desc = self._extract(reference_frame)

        # Estado do rastreamento.
        self._transform = None          # referência -> frame atual
        self._anchor = None             # (pontos, descritores, transformação)

        # Diagnóstico (gravado nos testes / exibido na janela).
        self.last_match_count = 0
        self.last_inlier_count = 0
        self.is_tracking = False
        self.tracking_mode = MODE_HELD
        self.frames_without_tracking = 0
        self.motion_magnitude = 0.0     # graus (esférico) ou pixels (perspectiva)
        self.wrap_width = None          # largura do frame, quando a imagem é 360°

    # ------------------------------------------------------------------
    # Preparação
    # ------------------------------------------------------------------

    def _build_feature_mask(self, mask_top, mask_bottom):
        width, height = self._work_size
        mask = np.full((height, width), 255, dtype=np.uint8)

        top = int(round(height * float(mask_top)))
        bottom = int(round(height * float(mask_bottom)))
        if top > 0:
            mask[:top, :] = 0
        if bottom > 0:
            mask[height - bottom:, :] = 0

        if self.projection == PROJECTION_FISHEYE:
            # Fora do círculo da lente não há imagem (bordas pretas).
            circle = np.zeros_like(mask)
            radius = int(min(width, height) / 2 * 0.97)
            cv2.circle(circle, (width // 2, height // 2), radius, 255, -1)
            mask = cv2.bitwise_and(mask, circle)

        return mask

    @staticmethod
    def _densify(points):
        dense = []
        count = len(points)
        for index in range(count):
            start = points[index]
            end = points[(index + 1) % count]
            for step in range(EDGE_SUBDIVISIONS):
                t = step / EDGE_SUBDIVISIONS
                dense.append(start + (end - start) * t)
        return np.array(dense, dtype=np.float64)

    def _region_box(self, points):
        center = points.mean(axis=0)
        half = (points.max(axis=0) - points.min(axis=0)) / 2.0
        margin = self.ref_w * LOCAL_MARGIN_FRACTION
        return center, half + margin

    def _extract(self, frame):
        """Pontos ORB (em coordenadas da referência) e descritores."""
        work = cv2.resize(frame, self._work_size, interpolation=cv2.INTER_AREA)
        if work.ndim == 3:
            work = cv2.cvtColor(work, cv2.COLOR_BGR2GRAY)
        work = self._clahe.apply(work)

        keypoints, descriptors = self._orb.detectAndCompute(work, self._feature_mask)

        if descriptors is None or not keypoints:
            return np.empty((0, 2)), None

        points = np.array([kp.pt for kp in keypoints], dtype=np.float64) / self._scale
        return points, descriptors

    # ------------------------------------------------------------------
    # Correspondência e estimativa de movimento
    # ------------------------------------------------------------------

    def _match(self, src_pts, src_desc, dst_pts, dst_desc):
        if src_desc is None or dst_desc is None:
            return None, None
        if len(src_pts) < MIN_MATCHES or len(dst_pts) < MIN_MATCHES:
            return None, None

        pairs = self._matcher.knnMatch(src_desc, dst_desc, k=2)
        good = [
            pair[0] for pair in pairs
            if len(pair) == 2 and pair[0].distance < LOWE_RATIO * pair[1].distance
        ]

        if len(good) < MIN_MATCHES:
            return None, None

        src = src_pts[[m.queryIdx for m in good]]
        dst = dst_pts[[m.trainIdx for m in good]]
        return src, dst

    def _estimate(self, src, dst):
        """Retorna (transformação, máscara de inliers) ou (None, None)."""
        if self.model is None:
            transform, inliers = self._estimate_homography(src, dst)
        else:
            transform, inliers = self._estimate_rotation(src, dst)

        if transform is None:
            return None, None

        count = int(inliers.sum())
        if count < MIN_MATCHES or count < MIN_INLIER_RATIO * len(src):
            return None, None

        return transform, inliers

    def _estimate_rotation(self, src, dst):
        src_rays = self.model.pixel_to_ray(src)
        dst_rays = self.model.pixel_to_ray(dst)
        threshold = self._threshold_ref / self.model.pixels_per_radian()
        return ransac_rotation(src_rays, dst_rays, threshold)

    def _estimate_homography(self, src, dst):
        src32 = src.astype(np.float32).reshape(-1, 1, 2)
        dst32 = dst.astype(np.float32).reshape(-1, 1, 2)

        homography, mask = cv2.findHomography(
            src32, dst32, cv2.RANSAC, self._threshold_ref
        )
        if homography is not None and mask is not None and self._valid_homography(homography):
            return homography, mask.ravel().astype(bool)

        # Fallback: translação/rotação/zoom (mais estável com poucos pontos).
        affine, mask = cv2.estimateAffinePartial2D(
            src32.reshape(-1, 2), dst32.reshape(-1, 2),
            method=cv2.RANSAC, ransacReprojThreshold=self._threshold_ref
        )
        if affine is None or mask is None:
            return None, None

        homography = np.vstack([affine, [0.0, 0.0, 1.0]])
        if not self._valid_homography(homography):
            return None, None
        return homography, mask.ravel().astype(bool)

    @staticmethod
    def _valid_homography(matrix):
        if matrix is None or not np.isfinite(matrix).all():
            return False
        try:
            if np.linalg.cond(matrix[:2, :2]) > MAX_CONDITION:
                return False
            sx = np.linalg.norm(matrix[:2, 0])
            sy = np.linalg.norm(matrix[:2, 1])
            return MIN_SCALE <= sx <= MAX_SCALE and MIN_SCALE <= sy <= MAX_SCALE
        except (ValueError, np.linalg.LinAlgError):
            return False

    # ------------------------------------------------------------------
    # Aplicação da transformação
    # ------------------------------------------------------------------

    def _apply(self, transform, points):
        """Leva pontos da referência para o frame atual (coords. da referência)."""
        if transform is None:
            return np.array(points, dtype=np.float64)

        if self.model is None:
            warped = cv2.perspectiveTransform(
                np.asarray(points, dtype=np.float64).reshape(-1, 1, 2), transform
            )
            return warped.reshape(-1, 2)

        rays = self.model.pixel_to_ray(points)
        return self.model.ray_to_pixel(rays @ transform.T)

    def _wrap_dx(self, dx):
        if not self.wraps:
            return dx
        return (dx + self.ref_w / 2.0) % self.ref_w - self.ref_w / 2.0

    def _unwrap_polygon(self, points):
        """Na imagem 360°, deixa o polígono contínuo mesmo cruzando a emenda."""
        if not self.wraps or len(points) == 0:
            return points
        points = points.copy()
        for index in range(1, len(points)):
            points[index, 0] = points[index - 1, 0] + self._wrap_dx(
                points[index, 0] - points[index - 1, 0]
            )
        shift = math.floor(points[:, 0].mean() / self.ref_w) * self.ref_w
        points[:, 0] -= shift
        return points

    # ------------------------------------------------------------------
    # Suavização
    # ------------------------------------------------------------------

    def _smooth(self, previous, current):
        if previous is None or self.smoothing <= 0:
            return current

        if self.model is not None:
            delta = current @ previous.T
            rotvec, _ = cv2.Rodrigues(delta)
            change = math.degrees(np.linalg.norm(rotvec)) / JITTER_ROTATION_DEG
        else:
            corners = np.array(
                [[0, 0], [self.ref_w, 0], [self.ref_w, self.ref_h], [0, self.ref_h]],
                dtype=np.float64
            )
            moved = np.abs(self._apply(current, corners) - self._apply(previous, corners)).max()
            change = moved / (JITTER_CORNER_FRACTION * self.ref_w)

        # alpha = peso do valor novo. Tremor pequeno -> alpha baixo
        # (suaviza); movimento real -> alpha = 1 (segue imediatamente).
        alpha = 1.0 - self.smoothing * max(0.0, 1.0 - change)

        if alpha >= 0.999:
            return current

        if self.model is not None:
            step, _ = cv2.Rodrigues(rotvec * alpha)
            return step @ previous

        prev_n = previous / previous[2, 2]
        curr_n = current / current[2, 2]
        return (1.0 - alpha) * prev_n + alpha * curr_n

    # ------------------------------------------------------------------
    # Refinamento local
    # ------------------------------------------------------------------

    def _update_local_offsets(self, transform, src, dst):
        residual = dst - self._apply(transform, src)
        residual[:, 0] = self._wrap_dx(residual[:, 0])

        limit = self._threshold_ref * LOCAL_MAX_RESIDUAL_FACTOR
        usable = np.linalg.norm(residual, axis=1) <= limit
        src = src[usable]
        residual = residual[usable]

        alpha = 1.0 - self.smoothing

        for name, (center, half) in self._region_boxes.items():
            dx = np.abs(self._wrap_dx(src[:, 0] - center[0]))
            dy = np.abs(src[:, 1] - center[1])
            near = (dx <= half[0]) & (dy <= half[1])

            if near.sum() >= LOCAL_MIN_MATCHES:
                target = np.median(residual[near], axis=0)
                self._local_offsets[name] = (
                    (1.0 - alpha) * self._local_offsets[name] + alpha * target
                )
            else:
                self._local_offsets[name] *= LOCAL_DECAY

    def _decay_local_offsets(self):
        for name in self._local_offsets:
            self._local_offsets[name] *= LOCAL_DECAY

    # ------------------------------------------------------------------
    # API pública
    # ------------------------------------------------------------------

    def update(self, frame):
        """
        Recalcula a posição de cada vaga para o frame atual.

        Returns:
            dict {nome_da_vaga: [[x, y], ...]} em coordenadas do frame
            recebido. Na imagem 360° o polígono pode ter x < 0 ou x >
            largura quando cruza a emenda; RegionCounterLive trata isso
            usando `wrap_width`.
        """

        frame_h, frame_w = frame.shape[:2]
        self.wrap_width = frame_w if self.wraps else None

        cur_pts, cur_desc = self._extract(frame)

        new_transform = None
        mode = MODE_HELD
        direct_pairs = None

        # 1) Casamento direto com a referência.
        src, dst = self._match(self._ref_pts, self._ref_desc, cur_pts, cur_desc)
        self.last_match_count = 0 if src is None else len(src)
        self.last_inlier_count = 0

        if src is not None:
            transform, inliers = self._estimate(src, dst)
            if transform is not None:
                new_transform = transform
                mode = MODE_REFERENCE
                direct_pairs = (src, dst)
                self.last_inlier_count = int(inliers.sum())
                self._anchor = (cur_pts, cur_desc, transform)

        # 2) Encadeado: referência -> âncora -> frame atual.
        if new_transform is None and self._anchor is not None:
            anchor_pts, anchor_desc, anchor_transform = self._anchor
            src_a, dst_a = self._match(anchor_pts, anchor_desc, cur_pts, cur_desc)
            if src_a is not None:
                transform, inliers = self._estimate(src_a, dst_a)
                if transform is not None:
                    new_transform = transform @ anchor_transform
                    mode = MODE_CHAINED
                    self.last_inlier_count = int(inliers.sum())
                    # Correspondência muito forte: o frame atual vira a
                    # nova âncora. Permite seguir rastreando mesmo que a
                    # iluminação mude a ponto de a referência não casar.
                    if inliers.sum() >= 3 * MIN_MATCHES:
                        self._anchor = (cur_pts, cur_desc, new_transform)

        if new_transform is not None:
            self._transform = self._smooth(self._transform, new_transform)
            self.frames_without_tracking = 0
            if self.local_refine and direct_pairs is not None:
                self._update_local_offsets(self._transform, *direct_pairs)
            elif mode == MODE_CHAINED:
                self._decay_local_offsets()
        else:
            self.frames_without_tracking += 1

        self.tracking_mode = mode
        self.is_tracking = mode != MODE_HELD
        self.motion_magnitude = self._motion_magnitude()

        # 3) Aplica às vagas e converte para a resolução do frame.
        sx = frame_w / float(self.ref_w)
        sy = frame_h / float(self.ref_h)
        result = {}

        for name in self._original_regions:
            points = self._apply(self._transform, self._dense_regions[name])
            points = points + self._local_offsets[name]
            points = self._unwrap_polygon(points)
            points[:, 0] *= sx
            points[:, 1] *= sy
            result[name] = np.round(points).astype(int).tolist()

        return result

    def _motion_magnitude(self):
        if self._transform is None:
            return 0.0
        if self.model is not None:
            return round(rotation_angle_degrees(self._transform), 2)
        center = np.array([[self.ref_w / 2.0, self.ref_h / 2.0]])
        moved = self._apply(self._transform, center) - center
        return round(float(np.linalg.norm(moved)), 1)

    def diagnostics(self):
        return {
            "projection": self.projection,
            "tracking": self.is_tracking,
            "mode": self.tracking_mode,
            "matches": self.last_match_count,
            "inliers": self.last_inlier_count,
            "motion": self.motion_magnitude,
            "frames_without_tracking": self.frames_without_tracking,
        }


def build_stabilizer_for_environment(environment_name, coordinates, enabled=None, log=print):
    """
    Monta o estabilizador de um ambiente a partir do que foi salvo no
    editor (chave __detection__):

        stabilize   (bool)  ativa a estabilização;
        projection  (str)   perspective | equirectangular | fisheye | cylindrical;
        fov         (float) campo de visão em graus (fisheye/cilíndrica);
        mask_bottom (float) fração da base ignorada (tripé na 360°);
        smoothing   (float) suavização do tremor (0 a 0.95).

    Usado tanto pelo monitoramento ao vivo quanto pelos testes, para os
    dois se comportarem exatamente igual.

    Args:
        enabled: força ligar/desligar (tela de testes). None = usa o
            que está salvo no ambiente.
    """
    import regions_store as store

    config = store.get_detection_config(environment_name)
    stabilize = config.get("stabilize", False) if enabled is None else bool(enabled)

    if not stabilize:
        return None

    reference_path = store.get_reference_image_path(environment_name)
    if not reference_path:
        log(
            f"[ESTABILIZAÇÃO] '{environment_name}' não tem frame de referência "
            "salvo (capture um frame no editor). Seguindo sem estabilização."
        )
        return None

    reference_frame = cv2.imread(reference_path)
    if reference_frame is None:
        log(
            f"[ESTABILIZAÇÃO] Não foi possível ler {reference_path}. "
            "Seguindo sem estabilização."
        )
        return None

    def _number(key):
        try:
            value = config.get(key)
            return None if value in (None, "") else float(value)
        except (TypeError, ValueError):
            return None

    smoothing = _number("smoothing")

    stabilizer = RegionStabilizer(
        reference_frame,
        coordinates,
        projection=config.get("projection", PROJECTION_PERSPECTIVE),
        fov=_number("fov"),
        mask_bottom=_number("mask_bottom"),
        smoothing=SMOOTHING if smoothing is None else smoothing,
    )

    log(
        f"[ESTABILIZAÇÃO] '{environment_name}': projeção={stabilizer.projection}, "
        f"referência {stabilizer.ref_w}x{stabilizer.ref_h}, "
        f"{len(stabilizer._ref_pts)} pontos de referência."
    )

    if len(stabilizer._ref_pts) < MIN_MATCHES * 4:
        log(
            "[ESTABILIZAÇÃO] Atenção: a imagem de referência tem pouca textura; "
            "o rastreamento pode falhar."
        )

    return stabilizer
