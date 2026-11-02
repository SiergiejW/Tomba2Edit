"""Render native textured model parts to transparent isometric PNGs on CPU.

No Qt, OpenGL context, external renderer or invented artwork is involved.
``model`` is the dictionary returned by ``parse_smst``/``parse_mdat`` and
``vram`` is the game's decoded one-megabyte VRAM buffer. Vertices and poses
use the parsers' Y-up axes, so negate native-game Y offsets before passing
them here. Vertex colours already carry the game's authored shading.

    image = render_model(model, vram, groups=[body_group, lid_group])
    image.save("chest.png")

For separately posed parts:

    image = render_parts([(model, body_group, body_matrix),
                          (model, lid_group, lid_matrix)], vram)

Each matrix is a 4x4 affine transform, or None for identity. The camera is
orthographic, with an actual isometric 35.264-degree elevation by default.
This is an asset-preview renderer, not an emulator of PSX ordering tables.
"""
from dataclasses import dataclass
import math

import numpy as np
from PIL import Image

from formats.models.gltf_export import index_atlas, palette
from psx import vram as psx_vram


@dataclass
class _Triangle:
    points: np.ndarray
    uvs: np.ndarray
    colors: np.ndarray
    page: int
    clut: int
    transparent: bool
    blend: int


def pose(*, offset=(0, 0, 0), yaw=0.0):
    """A Y-up translation/yaw matrix; yaw is in ordinary degrees."""
    angle = math.radians(yaw)
    c, s = math.cos(angle), math.sin(angle)
    matrix = np.array(((c, 0, s, 0), (0, 1, 0, 0), (-s, 0, c, 0), (0, 0, 0, 1)), float)
    matrix[:3, 3] = np.asarray(offset, dtype=float)
    return matrix


def _part_triangles(model, group, matrix):
    vertices = np.asarray(model.get("vertices", ()), dtype=float)
    if vertices.size == 0:
        return []
    vertices = vertices.reshape(-1, 3)
    faces = np.asarray(model.get("faces", ()), dtype=np.int64).reshape(-1, 3)
    if not len(faces):
        return []
    if group is not None:
        groups = model.get("groups", ())
        if not 0 <= int(group) < len(groups):
            raise ValueError(f"Model has no group {group}.")
        selected = groups[int(group)]
        start = selected.first_face if hasattr(selected, "first_face") else selected["first_face"]
        count = selected.face_count if hasattr(selected, "face_count") else selected["face_count"]
        face_indices = range(start, start + count)
    else:
        face_indices = range(len(faces))
    matrix = np.eye(4) if matrix is None else np.asarray(matrix, dtype=float)
    if matrix.shape != (4, 4) or not np.isfinite(matrix).all():
        raise ValueError("A model pose must be a finite 4x4 matrix.")
    vertices = vertices @ matrix[:3, :3].T + matrix[:3, 3]
    colors = np.asarray(model.get("vertex_colors", ()), dtype=float)
    if colors.shape != vertices.shape:
        colors = np.ones_like(vertices)
    uvs = np.asarray(model.get("texture_coords", ()), dtype=float)
    if uvs.shape != (len(vertices), 2):
        raise ValueError("Native textured icons need UV coordinates for every vertex.")
    materials = model.get("texture_info", ())
    if len(materials) != len(faces):
        raise ValueError("Native textured icons need a page/CLUT for every face.")
    triangles = []
    for index in face_indices:
        face = faces[index]
        material = materials[index]
        if len(material) < 3:
            raise ValueError("Incomplete native texture material.")
        page, clut, transparent = material[:3]
        blend = material[3] if len(material) > 3 else 0
        triangles.append(_Triangle(vertices[face], uvs[face], colors[face],
                                   int(page), int(clut), bool(transparent), int(blend)))
    return triangles


def _camera(yaw, elevation):
    yaw, elevation = math.radians(yaw), math.radians(elevation)
    cy, sy = math.cos(yaw), math.sin(yaw)
    ce, se = math.cos(elevation), math.sin(elevation)
    # Rows project to screen right, screen down, and depth toward the eye.
    return np.array(((cy, 0, -sy), (sy * se, -ce, cy * se),
                     (sy * ce, se, cy * ce)), dtype=float)


def render_model(model, vram, *, groups=None, poses=None, **options):
    """PIL RGBA image of one native model, optionally selecting its groups.

    ``poses`` maps group indices to 4x4 matrices. With groups=None and no
    poses, all groups retain the authored coordinates already in the model.
    """
    if groups is None and not poses:
        parts = [(model, None, None)]
    else:
        groups = list(range(len(model.get("groups", ())))) if groups is None else list(groups)
        parts = [(model, group, (poses or {}).get(group)) for group in groups]
    return render_parts(parts, vram, **options)


def render_parts(parts, vram, *, size=128, yaw=45.0, elevation=35.264389682754654,
                 margin=8, supersample=3):
    """PIL RGBA image from ``(model, group-or-None, matrix-or-None)`` parts.

    ``size`` is an integer square size or (width, height). Transparent texels
    are cut out; visible native texels use nearest sampling and their actual
    CLUT, modulated by the native interpolated vertex colours. Supersampling
    antialiases the silhouette without blurring the source texture lookup.
    """
    width, height = (int(size), int(size)) if np.isscalar(size) else map(int, size)
    supersample = int(supersample)
    if min(width, height, supersample) < 1:
        raise ValueError("Image dimensions and supersampling must be positive.")
    if not 0 <= margin < min(width, height) / 2:
        raise ValueError("The icon margin must leave room for the model.")
    psx_vram.check_vram(vram)
    atlas = index_atlas(vram)
    triangles = []
    for model, group, matrix in parts:
        triangles.extend(_part_triangles(model, group, matrix))
    if not triangles:
        raise ValueError("The selected native model parts contain no triangles.")
    camera = _camera(yaw, elevation)
    projected = [triangle.points @ camera.T for triangle in triangles]
    all_points = np.concatenate(projected)
    if not np.isfinite(all_points).all():
        raise ValueError("The model contains non-finite coordinates.")
    low, high = all_points[:, :2].min(axis=0), all_points[:, :2].max(axis=0)
    extent = np.maximum(high - low, 1e-9)
    scale = min((width - margin * 2) / extent[0], (height - margin * 2) / extent[1]) * supersample
    centre = (low + high) / 2
    shape = (height * supersample, width * supersample)
    depth = np.full(shape, -np.inf, dtype=float)
    rgba = np.zeros((*shape, 4), dtype=float)
    palettes = {}
    screens = []
    for points in projected:
        screen = points.copy()
        screen[:, :2] = (screen[:, :2] - centre) * scale + np.array((shape[1], shape[0])) / 2
        screens.append(screen)

    def draw(triangle, screen, translucent):
        x0, y0 = np.maximum(np.floor(screen[:, :2].min(axis=0)).astype(int), 0)
        x1, y1 = np.minimum(np.ceil(screen[:, :2].max(axis=0)).astype(int), (shape[1] - 1, shape[0] - 1))
        if x1 < x0 or y1 < y0:
            return
        a, b, c = screen
        denominator = (b[1] - c[1]) * (a[0] - c[0]) + (c[0] - b[0]) * (a[1] - c[1])
        if abs(denominator) < 1e-10:
            return
        xx, yy = np.meshgrid(np.arange(x0, x1 + 1) + 0.5, np.arange(y0, y1 + 1) + 0.5)
        w0 = ((b[1] - c[1]) * (xx - c[0]) + (c[0] - b[0]) * (yy - c[1])) / denominator
        w1 = ((c[1] - a[1]) * (xx - c[0]) + (a[0] - c[0]) * (yy - c[1])) / denominator
        w2 = 1 - w0 - w1
        weights = np.stack((w0, w1, w2), axis=-1)
        z = weights @ screen[:, 2]
        window_depth = depth[y0:y1 + 1, x0:x1 + 1]
        inside = (np.minimum(np.minimum(w0, w1), w2) >= -1e-7) & (z >= window_depth - 1e-8)
        if not inside.any():
            return
        uv = weights @ triangle.uvs
        page = triangle.page & 31
        # UVs are atlas-normalized texel centres; interpolate within the
        # selected page and wrap native eight-bit U/V coordinates.
        u = np.floor(uv[..., 0] * psx_vram.ATLAS_WIDTH).astype(int)
        v = np.floor(uv[..., 1] * psx_vram.ATLAS_HEIGHT).astype(int)
        u = page % 16 * 256 + (u % 256)
        v = page // 16 * 256 + (v % 256)
        key = (triangle.clut, triangle.transparent)
        if key not in palettes:
            palettes[key] = palette(vram, triangle.clut, triangle.transparent).astype(float) / 255.0
        pixels = palettes[key][atlas[v, u]].copy()
        pixels[..., :3] = np.clip(pixels[..., :3] * (weights @ triangle.colors), 0, 1)
        if translucent:
            mask = inside & (pixels[..., 3] > 0) & (pixels[..., 3] < 1)
        else:
            mask = inside & (pixels[..., 3] >= 1)
        if not mask.any():
            return
        window_rgba = rgba[y0:y1 + 1, x0:x1 + 1]
        if translucent:
            src, dst = pixels[mask], window_rgba[mask]
            # STP's default half mix is represented by alpha. The other
            # native blend modes combine with an existing opaque surface;
            # a transparent preview background keeps the source readable.
            if triangle.blend and np.any(dst[:, 3] > 0):
                behind = dst[:, 3] > 0
                if triangle.blend == 1:
                    src[behind, :3] = np.clip(dst[behind, :3] + src[behind, :3], 0, 1)
                elif triangle.blend == 2:
                    src[behind, :3] = np.clip(dst[behind, :3] - src[behind, :3], 0, 1)
                elif triangle.blend == 3:
                    src[behind, :3] = np.clip(dst[behind, :3] + src[behind, :3] / 4, 0, 1)
                src[behind, 3] = 1
            sa, da = src[:, 3:4], dst[:, 3:4]
            alpha = sa + da * (1 - sa)
            out = np.zeros_like(src)
            out[:, :3] = (src[:, :3] * sa + dst[:, :3] * da * (1 - sa)) / np.maximum(alpha, 1e-10)
            out[:, 3:4] = alpha
            window_rgba[mask] = out
        else:
            window_rgba[mask] = pixels[mask]
            window_depth[mask] = z[mask]

    for triangle, screen in zip(triangles, screens):
        draw(triangle, screen, False)
    for index in sorted(range(len(triangles)), key=lambda i: float(screens[i][:, 2].mean())):
        if triangles[index].transparent:
            draw(triangles[index], screens[index], True)
    result = Image.fromarray(np.rint(np.clip(rgba, 0, 1) * 255).astype(np.uint8), "RGBA")
    if supersample != 1:
        result = result.resize((width, height), Image.Resampling.LANCZOS)
    return result
