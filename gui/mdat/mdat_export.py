# mdat_export.py
import numpy as np
import json
import base64
from PIL import Image
from PyQt6.QtGui import QImage
from typing import Dict, Any

import io
def export_mdat_to_gltf(model_data: Dict[str, Any], vram_qimage: QImage, clut_map: Dict[int, int], file_path: str) -> bool:
    """
    Export MDAT model data to GLTF format (text-based).

    Args:
        model_data: Dictionary containing model data from MDAT
        vram_qimage: QImage containing the VRAM texture atlas
        clut_map: Dictionary mapping CLUT addresses to OpenGL texture IDs
        file_path: Path to save the GLTF file

    Returns:
        bool: True if export succeeded, False otherwise
    """
    try:
        # Convert model data to numpy arrays
        vertices = np.array(model_data['vertices'], dtype=np.float32).reshape(-1, 3)
        normals = np.zeros_like(vertices)  # Placeholder normals
        tex_coords = np.array(model_data['texture_coords'], dtype=np.float32).reshape(-1, 2)
        colors = np.array(model_data['vertex_colors'], dtype=np.float32).reshape(-1, 3)

        # Prepare faces (convert quads to triangles)
        all_faces = []
        for face in model_data['faces']:
            if len(face) == 3:  # Triangle
                all_faces.append(face)
            elif len(face) == 4:  # Quad (convert to 2 triangles)
                all_faces.append([face[0], face[1], face[2]])
                all_faces.append([face[0], face[2], face[3]])
        faces = np.array(all_faces, dtype=np.uint32).flatten()

        # Prepare GLTF structure
        gltf = {
            "asset": {
                "version": "2.0",
                "generator": "Tomba2Edit"
            },
            "scene": 0,
            "scenes": [{
                "nodes": [0]
            }],
            "nodes": [{
                "mesh": 0
            }],
            "meshes": [{
                "primitives": [{
                    "attributes": {
                        "POSITION": 0,
                        "NORMAL": 1,
                        "TEXCOORD_0": 2,
                        "COLOR_0": 3
                    },
                    "indices": 4,
                    "mode": 4  # TRIANGLES
                }]
            }],
            "buffers": [],
            "bufferViews": [],
            "accessors": [],
            "materials": [],
            "textures": [],
            "images": [],
            "samplers": [{
                "magFilter": 9728,  # NEAREST
                "minFilter": 9728,  # NEAREST
                "wrapS": 10497,  # REPEAT
                "wrapT": 10497  # REPEAT
            }]
        }

        # Prepare binary data
        binary_data = bytearray()

        # Add vertex data
        vertex_data = vertices.tobytes()
        normal_data = normals.tobytes()
        texcoord_data = tex_coords.tobytes()
        index_data = faces.tobytes()
        color_data = colors.tobytes()


        # Add buffer views and accessors
        def add_buffer_view(data, target=None):
            offset = len(binary_data)
            binary_data.extend(data)
            view_idx = len(gltf["bufferViews"])
            gltf["bufferViews"].append({
                "buffer": 0,
                "byteOffset": offset,
                "byteLength": len(data),
                **({"target": target} if target else {})
            })
            return view_idx

        # Vertex attributes
        pos_view = add_buffer_view(vertex_data, 34962)  # ARRAY_BUFFER
        gltf["accessors"].append({
            "bufferView": pos_view,
            "componentType": 5126,  # FLOAT
            "count": len(vertices),
            "type": "VEC3",
            "max": vertices.max(axis=0).tolist(),
            "min": vertices.min(axis=0).tolist()
        })

        normal_view = add_buffer_view(normal_data, 34962)  # ARRAY_BUFFER
        gltf["accessors"].append({
            "bufferView": normal_view,
            "componentType": 5126,  # FLOAT
            "count": len(normals),
            "type": "VEC3"
        })

        texcoord_view = add_buffer_view(texcoord_data, 34962)  # ARRAY_BUFFER
        gltf["accessors"].append({
            "bufferView": texcoord_view,
            "componentType": 5126,  # FLOAT
            "count": len(tex_coords),
            "type": "VEC2"
        })

        color_view = add_buffer_view(color_data, 34962)  # ARRAY_BUFFER
        gltf["accessors"].append({
            "bufferView": color_view,
            "componentType": 5126,  # FLOAT
            "count": len(colors),
            "type": "VEC3"
        })

        # Indices
        index_view = add_buffer_view(index_data, 34963)  # ELEMENT_ARRAY_BUFFER
        gltf["accessors"].append({
            "bufferView": index_view,
            "componentType": 5125,  # UNSIGNED_INT
            "count": len(faces),
            "type": "SCALAR"
        })

        # Add main buffer
        gltf["buffers"].append({
            "uri": "data:application/octet-stream;base64," + base64.b64encode(binary_data).decode('ascii'),
            "byteLength": len(binary_data)
        })

        # Convert VRAM to texture
        if vram_qimage:
            # Convert QImage to PIL Image
            vram_image = Image.fromqpixmap(vram_qimage)

            # Save texture as embedded base64
            img_byte_arr = io.BytesIO()
            vram_image.save(img_byte_arr, format='PNG')
            img_base64 = base64.b64encode(img_byte_arr.getvalue()).decode('ascii')

            # Add texture to GLTF
            gltf["images"].append({
                "uri": f"data:image/png;base64,{img_base64}"
            })
            gltf["textures"].append({
                "sampler": 0,
                "source": 0
            })

            # Create material with texture
            gltf["materials"].append({
                "pbrMetallicRoughness": {
                    "baseColorTexture": {
                        "index": 0,
                        "texCoord": 0
                    },
                    "baseColorFactor": [1.0, 1.0, 1.0, 1.0],
                    "metallicFactor": 0,
                    "roughnessFactor": 1
                },
                "vertexColor": True
            })

            # Assign material to primitive
            gltf["meshes"][0]["primitives"][0]["material"] = 0

        # Write GLTF file
        with open(file_path, 'w') as f:
            json.dump(gltf, f, indent=2)

        return True

    except Exception as e:
        print(f"Error exporting to GLTF: {e}")
        return False