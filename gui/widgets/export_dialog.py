"""The save dialog every model export shares: where, and lit or unlit.

Lit writes ordinary metallic-roughness materials, so a lamp in Blender
works on them. Unlit adds KHR_materials_unlit: the baked texture times the
vertex colour and nothing else - what the PlayStation draws.
"""
from PyQt6.QtWidgets import QFileDialog

LIT_GLB = "glTF binary, lit (*.glb)"
UNLIT_GLB = "glTF binary, unlit (*.glb)"
LIT_GLTF = "glTF, lit (*.gltf)"
UNLIT_GLTF = "glTF, unlit (*.gltf)"


def ask_model_path(parent, title, stem, text=True):
    """(path, unlit) from a save dialog, or (None, False) if cancelled.

    `text` also offers .gltf. The file's own extension picks the format;
    the chosen filter picks lit or unlit."""
    filters = [LIT_GLB, UNLIT_GLB] + ([LIT_GLTF, UNLIT_GLTF] if text else [])
    path, chosen = QFileDialog.getSaveFileName(
        parent, title, (stem or "model") + ".glb", ";;".join(filters))
    if not path:
        return None, False
    gltf = chosen in (LIT_GLTF, UNLIT_GLTF)
    if gltf and not path.lower().endswith(".gltf"):
        path = path.rsplit(".", 1)[0] + ".gltf" if path.lower().endswith(".glb") else path + ".gltf"
    elif not gltf and not path.lower().endswith((".glb", ".gltf")):
        path += ".glb"
    return path, "unlit" in chosen
