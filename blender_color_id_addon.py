bl_info = {
    "name": "Color ID Painter",
    "author": "OpenAI",
    "version": (1, 0, 0),
    "blender": (5, 1, 1),
    "location": "3D View > Sidebar > Color ID",
    "description": "Assign unique vertex colors for ID map baking",
    "category": "Paint",
}

import random
import bpy
import bmesh
from bpy.props import (
    FloatVectorProperty,
    IntProperty,
    StringProperty,
    CollectionProperty,
    EnumProperty,
)
from bpy.types import Operator, Panel, PropertyGroup, UIList


LAYER_NAME = "ColorID"


def active_color_layer(bm):
    layer = bm.loops.layers.color.get(LAYER_NAME)
    if layer is None:
        layer = bm.loops.layers.color.new(LAYER_NAME)
    return layer


def color_distance(a, b):
    return abs(a[0] - b[0]) + abs(a[1] - b[1]) + abs(a[2] - b[2])


def random_unique_color(existing, min_dist=0.15, max_tries=500):
    for _ in range(max_tries):
        c = (random.random(), random.random(), random.random(), 1.0)
        if all(color_distance(c, e) >= min_dist for e in existing):
            return c
    # Fallback if palette is very crowded
    c = (random.random(), random.random(), random.random(), 1.0)
    return c


def get_existing_palette(context):
    palette = context.scene.cid_palette
    return [tuple(item.color) for item in palette]


def ensure_item(scene, color, label=""):
    for item in scene.cid_palette:
        if color_distance(item.color, color) < 1e-4:
            return item
    it = scene.cid_palette.add()
    it.color = color
    it.name = label or f"ID_{len(scene.cid_palette):03d}"
    return it


def selected_mesh_objects(context):
    return [o for o in context.selected_objects if o and o.type == 'MESH']


def set_edit_mode(context):
    if context.mode != 'EDIT_MESH':
        bpy.ops.object.mode_set(mode='EDIT')


def assign_to_selected_faces(obj, color):
    bm = bmesh.from_edit_mesh(obj.data)
    layer = active_color_layer(bm)
    changed = False
    for f in bm.faces:
        if f.select:
            for loop in f.loops:
                loop[layer] = color
            changed = True
    if changed:
        bmesh.update_edit_mesh(obj.data, loop_triangles=False, destructive=False)
    return changed


def collect_selected_color(obj):
    bm = bmesh.from_edit_mesh(obj.data)
    layer = active_color_layer(bm)
    for f in bm.faces:
        if f.select and f.loops:
            c = f.loops[0][layer]
            return (c[0], c[1], c[2], 1.0)
    return None


def select_faces_by_color(obj, color, threshold=0.02):
    bm = bmesh.from_edit_mesh(obj.data)
    layer = active_color_layer(bm)
    for f in bm.faces:
        if not f.loops:
            continue
        c = f.loops[0][layer]
        d = abs(c[0]-color[0]) + abs(c[1]-color[1]) + abs(c[2]-color[2])
        f.select = d <= threshold
    bmesh.update_edit_mesh(obj.data, loop_triangles=False, destructive=False)


def auto_assign_loose_parts(obj, existing_palette, min_dist):
    bm = bmesh.from_edit_mesh(obj.data)
    layer = active_color_layer(bm)
    uncolored = [f for f in bm.faces if not f.select]
    if not uncolored:
        return 0

    for f in bm.faces:
        f.tag = False

    count_parts = 0

    for start in uncolored:
        if start.tag:
            continue
        # flood fill linked faces => loose part island
        stack = [start]
        island = []
        start.tag = True
        while stack:
            f = stack.pop()
            island.append(f)
            for e in f.edges:
                for linked in e.link_faces:
                    if not linked.tag and not linked.select:
                        linked.tag = True
                        stack.append(linked)

        new_color = random_unique_color(existing_palette, min_dist=min_dist)
        existing_palette.append(new_color)
        for f in island:
            for loop in f.loops:
                loop[layer] = new_color
        count_parts += 1

    bmesh.update_edit_mesh(obj.data, loop_triangles=False, destructive=False)
    return count_parts


class CID_PaletteItem(PropertyGroup):
    color: FloatVectorProperty(
        name="Color",
        subtype='COLOR',
        size=4,
        min=0.0,
        max=1.0,
        default=(0.8, 0.8, 0.8, 1.0)
    )
    name: StringProperty(name="Name", default="ID")


class CID_UL_palette(UIList):
    def draw_item(self, context, layout, data, item, icon, active_data, active_propname, index):
        row = layout.row(align=True)
        row.prop(item, "color", text="")
        row.prop(item, "name", text="", emboss=False)


class CID_OT_add_random(Operator):
    bl_idname = "cid.add_random"
    bl_label = "Assign New Random"
    bl_description = "Assign new unique random color to selected polygons"

    def execute(self, context):
        obj = context.edit_object
        if not obj or obj.type != 'MESH':
            self.report({'ERROR'}, "Enter Edit Mode on a mesh")
            return {'CANCELLED'}

        scene = context.scene
        existing = get_existing_palette(context)
        color = random_unique_color(existing, min_dist=scene.cid_min_distance)
        assigned = assign_to_selected_faces(obj, color)
        if not assigned:
            self.report({'WARNING'}, "No selected polygons")
            return {'CANCELLED'}

        item = ensure_item(scene, color)
        scene.cid_palette_index = len(scene.cid_palette) - 1
        self.report({'INFO'}, f"Assigned {item.name}")
        return {'FINISHED'}


class CID_OT_assign_existing(Operator):
    bl_idname = "cid.assign_existing"
    bl_label = "Assign Selected Color"

    def execute(self, context):
        obj = context.edit_object
        scene = context.scene
        idx = scene.cid_palette_index
        if not obj or obj.type != 'MESH' or idx < 0 or idx >= len(scene.cid_palette):
            return {'CANCELLED'}
        color = tuple(scene.cid_palette[idx].color)
        if not assign_to_selected_faces(obj, color):
            self.report({'WARNING'}, "No selected polygons")
            return {'CANCELLED'}
        return {'FINISHED'}


class CID_OT_select_by_palette(Operator):
    bl_idname = "cid.select_by_palette"
    bl_label = "Select Faces by Color"

    def execute(self, context):
        obj = context.edit_object
        scene = context.scene
        idx = scene.cid_palette_index
        if not obj or obj.type != 'MESH' or idx < 0 or idx >= len(scene.cid_palette):
            return {'CANCELLED'}
        color = tuple(scene.cid_palette[idx].color)
        select_faces_by_color(obj, color, threshold=scene.cid_select_threshold)
        return {'FINISHED'}


class CID_OT_pick_from_selection(Operator):
    bl_idname = "cid.pick_from_selection"
    bl_label = "Pick Color from Selected"

    def execute(self, context):
        obj = context.edit_object
        if not obj or obj.type != 'MESH':
            return {'CANCELLED'}
        c = collect_selected_color(obj)
        if c is None:
            self.report({'WARNING'}, "Select at least one polygon")
            return {'CANCELLED'}
        ensure_item(context.scene, c, label="Picked")
        return {'FINISHED'}


class CID_OT_auto_loose_parts(Operator):
    bl_idname = "cid.auto_loose_parts"
    bl_label = "Auto Color (Loose Parts)"
    bl_description = "Assign random unique colors to all unselected polygons by loose parts"

    def execute(self, context):
        obj = context.edit_object
        scene = context.scene
        if not obj or obj.type != 'MESH':
            return {'CANCELLED'}
        existing = get_existing_palette(context)
        parts = auto_assign_loose_parts(obj, existing, scene.cid_min_distance)
        for col in existing:
            ensure_item(scene, col)
        self.report({'INFO'}, f"Processed loose parts: {parts}")
        return {'FINISHED'}


class CID_OT_clear_palette(Operator):
    bl_idname = "cid.clear_palette"
    bl_label = "Clear Palette"

    def execute(self, context):
        context.scene.cid_palette.clear()
        context.scene.cid_palette_index = 0
        return {'FINISHED'}


class CID_PT_panel(Panel):
    bl_label = "Color ID"
    bl_idname = "CID_PT_panel"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = 'Color ID'

    def draw(self, context):
        layout = self.layout
        scene = context.scene

        layout.label(text="Workflow: Edit Mode + Face Select")
        col = layout.column(align=True)
        col.operator("cid.add_random", icon='COLOR')
        col.operator("cid.assign_existing", icon='BRUSH_DATA')
        col.operator("cid.auto_loose_parts", icon='MOD_EXPLODE')

        layout.separator()
        layout.template_list("CID_UL_palette", "", scene, "cid_palette", scene, "cid_palette_index", rows=8)

        row = layout.row(align=True)
        row.operator("cid.select_by_palette", icon='RESTRICT_SELECT_OFF')
        row.operator("cid.pick_from_selection", icon='EYEDROPPER')

        row = layout.row(align=True)
        row.prop(scene, "cid_min_distance")
        row.prop(scene, "cid_select_threshold")

        layout.operator("cid.clear_palette", icon='TRASH')


classes = (
    CID_PaletteItem,
    CID_UL_palette,
    CID_OT_add_random,
    CID_OT_assign_existing,
    CID_OT_select_by_palette,
    CID_OT_pick_from_selection,
    CID_OT_auto_loose_parts,
    CID_OT_clear_palette,
    CID_PT_panel,
)


def register():
    for c in classes:
        bpy.utils.register_class(c)
    bpy.types.Scene.cid_palette = CollectionProperty(type=CID_PaletteItem)
    bpy.types.Scene.cid_palette_index = IntProperty(default=0)
    bpy.types.Scene.cid_min_distance = bpy.props.FloatProperty(
        name="Uniq Dist",
        description="Minimum distance between generated colors",
        default=0.35,
        min=0.01,
        max=2.0,
    )
    bpy.types.Scene.cid_select_threshold = bpy.props.FloatProperty(
        name="Sel Tol",
        description="Tolerance used for select by color",
        default=0.03,
        min=0.001,
        max=1.0,
    )


def unregister():
    del bpy.types.Scene.cid_select_threshold
    del bpy.types.Scene.cid_min_distance
    del bpy.types.Scene.cid_palette_index
    del bpy.types.Scene.cid_palette
    for c in reversed(classes):
        bpy.utils.unregister_class(c)


if __name__ == "__main__":
    register()
