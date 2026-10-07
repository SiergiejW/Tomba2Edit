"""Floating-point OBJ placement, before PS1 coordinate quantisation."""
from dataclasses import replace
import math

from .obj_exchange import ExchangeError


def bounds(faces):
    points = [p for f in faces for p in f.vertices]
    if not points:
        raise ExchangeError('Choose at least one object containing faces.')
    return tuple(min(p[a] for p in points) for a in range(3)), tuple(max(p[a] for p in points) for a in range(3))


def place(faces, scale=100., yaw=0., offset=(0., 0., 0.), *, quantize=True):
    if not math.isfinite(scale) or scale <= 0:
        raise ExchangeError('Scale must be greater than zero.')
    if not all(math.isfinite(v) for v in (*offset, yaw)):
        raise ExchangeError('Placement must contain finite numbers.')
    c, s = math.cos(math.radians(yaw)), math.sin(math.radians(yaw))
    def point(p):
        x, y, z = p
        result = (scale*(c*x+s*z)+offset[0], scale*y+offset[1], scale*(-s*x+c*z)+offset[2])
        return tuple(round(v) for v in result) if quantize else result
    return [replace(f, vertices=tuple(point(p) for p in f.vertices)) for f in faces]


def fit(faces, target, yaw=0.):
    """Uniform fit with a visible margin; never distort source proportions."""
    lo, hi = bounds(place(faces, 1, yaw, quantize=False))
    tl, th = bounds(target)
    ratios = [(th[a]-tl[a])/(hi[a]-lo[a]) for a in range(3)
              if hi[a]-lo[a] > 1e-8 and th[a]-tl[a] > 1]
    scale = min(ratios)*.9 if ratios else 1.
    offset = tuple((tl[a]+th[a])/2-scale*(lo[a]+hi[a])/2 for a in range(3))
    return scale, offset


def subdivide(faces, span):
    """Split oversized polygons, interpolating their UVs and vertex colours."""
    from .obj_exchange import MAX_FACES
    pending, output = list(faces), []
    def corner(face, indices):
        return replace(face, vertices=tuple(face.vertices[i] for i in indices),
                       uvs=tuple(face.uvs[i] for i in indices),
                       colors=tuple(face.colors[i] for i in indices))
    while pending:
        face = pending.pop()
        if all(max(v[a] for v in face.vertices)-min(v[a] for v in face.vertices) <= span for a in (0,2)):
            output.append(face)
            continue
        if len(face.vertices)==4:
            pending.extend((corner(face,(0,1,2)),corner(face,(0,2,3))))
        else:
            i,j=max(((0,1),(1,2),(2,0)),key=lambda ij:sum((face.vertices[ij[0]][a]-face.vertices[ij[1]][a])**2 for a in (0,2)))
            k=3-i-j
            def midpoint(a,b):
                return None if a is None or b is None else tuple(round((x+y)/2) for x,y in zip(a,b))
            extended=replace(face,vertices=face.vertices+(midpoint(face.vertices[i],face.vertices[j]),),
                uvs=face.uvs+(midpoint(face.uvs[i],face.uvs[j]),),
                colors=face.colors+(midpoint(face.colors[i],face.colors[j]),))
            pending.extend((corner(extended,(i,3,k)),corner(extended,(3,j,k))))
        if len(pending)+len(output)>MAX_FACES:
            raise ExchangeError('Subdivision exceeds 20,000 faces. Reduce the model scale or simplify it in Blender.')
    return output
