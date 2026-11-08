"""
Modelos de projeção das câmeras suportadas pelo Smart Parking.

Por que isso existe:

    Uma câmera convencional (perspectiva) que gira em torno do próprio
    centro produz, entre dois frames, uma transformação que é
    exatamente uma homografia. É isso que o estabilizador original
    usava.

    Câmeras 360° e panorâmicas NÃO seguem esse modelo. Nelas, cada
    pixel corresponde a uma DIREÇÃO no espaço (um "raio" saindo do
    centro da câmera), e a imagem é uma forma específica de "planificar"
    a esfera de direções:

        - equirretangular (360° x 180°): x = longitude, y = latitude.
          Uma rotação horizontal da câmera vira um deslocamento que dá
          a volta na emenda da imagem; inclinações viram curvas.
        - olho de peixe (fisheye equidistante): r = f * theta.
        - panorâmica cilíndrica: x = f * longitude, y = f * tan(lat).

    Em todos os casos, se a câmera apenas gira/balança no suporte, a
    relação entre dois frames é uma ROTAÇÃO 3D dos raios. Então o
    caminho correto é: pixel -> raio -> aplica rotação -> pixel.

Convenção dos raios: X para a direita, Y para baixo, Z para frente.
"""

import math

import numpy as np


PROJECTION_PERSPECTIVE = "perspective"
PROJECTION_EQUIRECTANGULAR = "equirectangular"
PROJECTION_FISHEYE = "fisheye"
PROJECTION_CYLINDRICAL = "cylindrical"

VALID_PROJECTIONS = {
    PROJECTION_PERSPECTIVE,
    PROJECTION_EQUIRECTANGULAR,
    PROJECTION_FISHEYE,
    PROJECTION_CYLINDRICAL,
}

# Aceita também os nomes em português, caso alguém edite o JSON à mão.
_ALIASES = {
    "convencional": PROJECTION_PERSPECTIVE,
    "perspectiva": PROJECTION_PERSPECTIVE,
    "normal": PROJECTION_PERSPECTIVE,
    "360": PROJECTION_EQUIRECTANGULAR,
    "equirretangular": PROJECTION_EQUIRECTANGULAR,
    "olho_de_peixe": PROJECTION_FISHEYE,
    "olho-de-peixe": PROJECTION_FISHEYE,
    "panoramica": PROJECTION_CYLINDRICAL,
    "panorâmica": PROJECTION_CYLINDRICAL,
    "cilindrica": PROJECTION_CYLINDRICAL,
    "cilíndrica": PROJECTION_CYLINDRICAL,
}

DEFAULT_FOV = {
    PROJECTION_FISHEYE: 180.0,
    PROJECTION_CYLINDRICAL: 180.0,
}

# Uma lente olho de peixe real cobre no máximo ~220° num único círculo.
# Valores como 360° só fazem sentido na projeção equirretangular; num
# olho de peixe eles encolhem o círculo útil da lente (raio = metade da
# menor dimensão da imagem / FOV) e jogam para "trás da câmera" tudo o
# que estiver fora dele, o que distorce as vagas.
MIN_FISHEYE_FOV = 60.0
MAX_FISHEYE_FOV = 220.0
MIN_CYLINDRICAL_FOV = 30.0
MAX_CYLINDRICAL_FOV = 360.0


def clamp_fov(projection, fov):
    """Limita o FOV ao intervalo fisicamente possível da projeção."""
    projection = normalize_projection(projection)
    if projection == PROJECTION_FISHEYE:
        low, high = MIN_FISHEYE_FOV, MAX_FISHEYE_FOV
    elif projection == PROJECTION_CYLINDRICAL:
        low, high = MIN_CYLINDRICAL_FOV, MAX_CYLINDRICAL_FOV
    else:
        return None
    try:
        value = float(fov) if fov not in (None, "") else DEFAULT_FOV[projection]
    except (TypeError, ValueError):
        value = DEFAULT_FOV[projection]
    return min(high, max(low, value))


def normalize_projection(value):
    """Converte o valor salvo no JSON para um dos nomes internos."""
    if not value:
        return PROJECTION_PERSPECTIVE
    value = str(value).strip().lower()
    value = _ALIASES.get(value, value)
    return value if value in VALID_PROJECTIONS else PROJECTION_PERSPECTIVE


class CameraModel:
    """Interface comum: pixel <-> raio unitário."""

    name = "base"
    wraps_horizontally = False

    def __init__(self, width, height):
        self.width = float(width)
        self.height = float(height)

    def pixel_to_ray(self, points):
        raise NotImplementedError

    def ray_to_pixel(self, rays):
        raise NotImplementedError

    def pixels_per_radian(self):
        """Escala aproximada, usada para converter limiares em pixels para ângulo."""
        raise NotImplementedError

    def valid_pixel_mask(self, points):
        """Pontos que realmente pertencem à área útil da imagem."""
        points = np.asarray(points, dtype=np.float64).reshape(-1, 2)
        return (
            (points[:, 0] >= 0) & (points[:, 0] < self.width) &
            (points[:, 1] >= 0) & (points[:, 1] < self.height)
        )


class EquirectangularModel(CameraModel):
    """Panorama 360° x 180° (formato exportado pela maioria das câmeras 360)."""

    name = PROJECTION_EQUIRECTANGULAR
    wraps_horizontally = True

    def pixel_to_ray(self, points):
        points = np.asarray(points, dtype=np.float64).reshape(-1, 2)
        lon = (points[:, 0] / self.width) * 2.0 * math.pi - math.pi
        lat = math.pi / 2.0 - (points[:, 1] / self.height) * math.pi
        cos_lat = np.cos(lat)
        return np.stack([
            cos_lat * np.sin(lon),
            -np.sin(lat),
            cos_lat * np.cos(lon)
        ], axis=1)

    def ray_to_pixel(self, rays):
        rays = np.asarray(rays, dtype=np.float64).reshape(-1, 3)
        rays = rays / np.linalg.norm(rays, axis=1, keepdims=True)
        lon = np.arctan2(rays[:, 0], rays[:, 2])
        lat = np.arcsin(np.clip(-rays[:, 1], -1.0, 1.0))
        x = (lon + math.pi) / (2.0 * math.pi) * self.width
        y = (math.pi / 2.0 - lat) / math.pi * self.height
        return np.stack([x, y], axis=1)

    def pixels_per_radian(self):
        return self.width / (2.0 * math.pi)


class FisheyeModel(CameraModel):
    """
    Olho de peixe equidistante (r = f * theta), com o círculo da imagem
    centralizado. É o modelo mais comum de câmeras 360° de teto.
    """

    name = PROJECTION_FISHEYE

    def __init__(self, width, height, fov_degrees=180.0):
        super().__init__(width, height)
        fov_degrees = clamp_fov(PROJECTION_FISHEYE, fov_degrees)
        self.fov = math.radians(float(fov_degrees))
        self.cx = self.width / 2.0
        self.cy = self.height / 2.0
        self.radius = min(self.width, self.height) / 2.0
        self.focal = self.radius / (self.fov / 2.0)

    def pixel_to_ray(self, points):
        points = np.asarray(points, dtype=np.float64).reshape(-1, 2)
        dx = points[:, 0] - self.cx
        dy = points[:, 1] - self.cy
        r = np.hypot(dx, dy)
        theta = r / self.focal
        phi = np.arctan2(dy, dx)
        sin_t = np.sin(theta)
        return np.stack([
            sin_t * np.cos(phi),
            sin_t * np.sin(phi),
            np.cos(theta)
        ], axis=1)

    def ray_to_pixel(self, rays):
        rays = np.asarray(rays, dtype=np.float64).reshape(-1, 3)
        rays = rays / np.linalg.norm(rays, axis=1, keepdims=True)
        theta = np.arccos(np.clip(rays[:, 2], -1.0, 1.0))
        phi = np.arctan2(rays[:, 1], rays[:, 0])
        r = self.focal * theta
        return np.stack([
            self.cx + r * np.cos(phi),
            self.cy + r * np.sin(phi)
        ], axis=1)

    def pixels_per_radian(self):
        return self.focal

    def valid_pixel_mask(self, points):
        points = np.asarray(points, dtype=np.float64).reshape(-1, 2)
        inside = super().valid_pixel_mask(points)
        r = np.hypot(points[:, 0] - self.cx, points[:, 1] - self.cy)
        return inside & (r <= self.radius)


class CylindricalModel(CameraModel):
    """Panorâmica cilíndrica (câmeras panorâmicas de 180°, panoramas de celular)."""

    name = PROJECTION_CYLINDRICAL

    def __init__(self, width, height, fov_degrees=180.0):
        super().__init__(width, height)
        fov_degrees = clamp_fov(PROJECTION_CYLINDRICAL, fov_degrees)
        self.hfov = math.radians(float(fov_degrees))
        self.focal = self.width / self.hfov
        self.cx = self.width / 2.0
        self.cy = self.height / 2.0

    def pixel_to_ray(self, points):
        points = np.asarray(points, dtype=np.float64).reshape(-1, 2)
        theta = (points[:, 0] - self.cx) / self.focal
        h = (points[:, 1] - self.cy) / self.focal
        rays = np.stack([np.sin(theta), h, np.cos(theta)], axis=1)
        return rays / np.linalg.norm(rays, axis=1, keepdims=True)

    def ray_to_pixel(self, rays):
        rays = np.asarray(rays, dtype=np.float64).reshape(-1, 3)
        theta = np.arctan2(rays[:, 0], rays[:, 2])
        horizontal = np.maximum(np.hypot(rays[:, 0], rays[:, 2]), 1e-9)
        h = rays[:, 1] / horizontal
        return np.stack([
            self.cx + self.focal * theta,
            self.cy + self.focal * h
        ], axis=1)

    def pixels_per_radian(self):
        return self.focal


def build_camera_model(projection, width, height, fov=None):
    """
    Cria o modelo de projeção. Para câmeras convencionais devolve None:
    nesse caso o estabilizador usa homografia, que é o modelo exato.
    """
    projection = normalize_projection(projection)

    if projection == PROJECTION_EQUIRECTANGULAR:
        return EquirectangularModel(width, height)
    if projection == PROJECTION_FISHEYE:
        return FisheyeModel(width, height, fov or DEFAULT_FOV[projection])
    if projection == PROJECTION_CYLINDRICAL:
        return CylindricalModel(width, height, fov or DEFAULT_FOV[projection])
    return None


def regions_outside_lens(projection, width, height, fov, regions, tolerance=0.02):
    """
    Nomes das vagas que têm pontos fora da área útil da lente.

    Só o olho de peixe tem área útil circular. Se uma vaga desenhada pelo
    operador está fora do círculo, a câmera NÃO é olho de peixe com esse
    FOV (ex.: vídeo 16:9 comum marcado como "olho de peixe 360°").
    """
    model = build_camera_model(projection, width, height, fov)
    if model is None or model.name != PROJECTION_FISHEYE:
        return []

    limit = model.radius * (1.0 + tolerance)
    outside = []
    for name, points in regions.items():
        pts = np.asarray(points, dtype=np.float64).reshape(-1, 2)
        if len(pts) == 0:
            continue
        distance = np.hypot(pts[:, 0] - model.cx, pts[:, 1] - model.cy)
        if (distance > limit).any():
            outside.append(name)
    return outside


# Uma imagem equirretangular cobre 360° x 180°, então tem proporção 2:1
# (3840x1920, 5760x2880...). Um vídeo 16:9 comum não pode ser equirretangular.
EQUIRECTANGULAR_ASPECT_RANGE = (1.9, 2.1)


def projection_conflict(projection, width, height):
    """
    Mensagem explicando por que a projeção escolhida não combina com a
    resolução do vídeo, ou None se estiver coerente.

    Escolher "360° equirretangular" para um vídeo de câmera comum faz o
    estabilizador usar um modelo esférico que não existe nessa imagem, e as
    vagas ficam deformadas/fora do lugar mesmo com a câmera parada.
    """
    if normalize_projection(projection) != PROJECTION_EQUIRECTANGULAR:
        return None
    if not width or not height:
        return None
    ratio = float(width) / float(height)
    low, high = EQUIRECTANGULAR_ASPECT_RANGE
    if low <= ratio <= high:
        return None
    return (
        f"o tipo \u201c360\u00b0 equirretangular\u201d exige imagem 2:1, mas este "
        f"v\u00eddeo tem {int(width)}\u00d7{int(height)} (propor\u00e7\u00e3o {ratio:.2f}:1). "
        "Se for uma c\u00e2mera comum (CFTV, celular, v\u00eddeo gravado), escolha "
        "\u201cConvencional\u201d."
    )


# ----------------------------------------------------------------------
# Rotação 3D a partir de correspondências de raios
# ----------------------------------------------------------------------

def kabsch_rotation(src, dst, weights=None):
    """
    Melhor rotação R (mínimos quadrados) tal que dst ≈ R @ src.

    src, dst: arrays (N, 3) de vetores unitários. Também aceita lotes
    (B, N, 3), usado pelo RANSAC para testar várias hipóteses de uma vez.
    """
    src = np.asarray(src, dtype=np.float64)
    dst = np.asarray(dst, dtype=np.float64)

    if weights is not None:
        src = src * weights[..., None]

    cov = np.swapaxes(src, -1, -2) @ dst
    u, _, vt = np.linalg.svd(cov)
    d = np.sign(np.linalg.det(np.swapaxes(vt, -1, -2) @ np.swapaxes(u, -1, -2)))

    correction = np.zeros(cov.shape, dtype=np.float64)
    correction[..., 0, 0] = 1.0
    correction[..., 1, 1] = 1.0
    correction[..., 2, 2] = d

    return np.swapaxes(vt, -1, -2) @ correction @ np.swapaxes(u, -1, -2)


def ransac_rotation(src_rays, dst_rays, threshold_rad, iterations=300, seed=0):
    """
    Estima a rotação entre dois conjuntos de raios, robusta a
    correspondências erradas (carros passando, reflexos, etc.).

    Returns:
        (R, inlier_mask) ou (None, None)
    """
    n = len(src_rays)
    if n < 3:
        return None, None

    rng = np.random.default_rng(seed)
    samples = np.array([rng.choice(n, 3, replace=False) for _ in range(iterations)])

    hypotheses = kabsch_rotation(src_rays[samples], dst_rays[samples])

    predicted = np.einsum("bij,nj->bni", hypotheses, src_rays)
    cos_err = np.einsum("bni,ni->bn", predicted, dst_rays)
    cos_thr = math.cos(threshold_rad)
    inlier_counts = (cos_err >= cos_thr).sum(axis=1)

    best = int(np.argmax(inlier_counts))
    inliers = cos_err[best] >= cos_thr

    if inliers.sum() < 3:
        return None, None

    # Refinamento com todos os inliers (duas passadas).
    rotation = hypotheses[best]
    for _ in range(2):
        rotation = kabsch_rotation(src_rays[inliers], dst_rays[inliers])
        cos_all = (src_rays @ rotation.T * dst_rays).sum(axis=1)
        inliers = cos_all >= cos_thr
        if inliers.sum() < 3:
            return None, None

    return rotation, inliers


def rotation_angle_degrees(rotation):
    if rotation is None:
        return 0.0
    cos_angle = (np.trace(rotation) - 1.0) / 2.0
    return math.degrees(math.acos(max(-1.0, min(1.0, cos_angle))))
