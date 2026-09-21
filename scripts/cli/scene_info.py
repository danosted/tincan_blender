"""Print a quick summary of a .blend file's scene: collections, objects, dims.

Run headless, no GUI needed:
    blender --background path/to/file.blend --python scripts/cli/scene_info.py

Useful as a sanity check after a Blender MCP session, or before export, to
confirm the object/collection structure looks right.
"""

import bpy


def _collection_tree(collection, depth=0):
    print("{}[{}]".format("  " * depth, collection.name))
    for obj in collection.objects:
        dims = obj.dimensions
        print(
            "{}- {} ({})  dims={:.2f}x{:.2f}x{:.2f}".format(
                "  " * (depth + 1), obj.name, obj.type, dims.x, dims.y, dims.z
            )
        )
    for child in collection.children:
        _collection_tree(child, depth + 1)


def main():
    scene = bpy.context.scene
    print("Scene: {}".format(scene.name))
    print("Objects: {}".format(len(bpy.data.objects)))
    print()
    _collection_tree(scene.collection)


if __name__ == "__main__":
    main()
