#!/usr/bin/env python3
import os, sys
HERE = os.path.dirname(os.path.abspath(__file__))
MESH_DIR = os.path.abspath(os.path.join(HERE, "..", "work", "bragg_cell"))
OUT_FCSTD = os.path.join(HERE, "bragg_elmer_mesh.FCStd")
OUT_STL = os.path.join(HERE, "bragg_elmer_mesh.stl")
print("START script", file=sys.stderr)
sys.stderr.flush()

import FreeCAD as App
print("FreeCAD loaded", file=sys.stderr)
sys.stderr.flush()

import Mesh
print("Mesh loaded", file=sys.stderr)
sys.stderr.flush()

import Fem
print("Fem loaded", file=sys.stderr)
sys.stderr.flush()

nodes = {}
nodes_path = os.path.join(MESH_DIR, "mesh.nodes")
with open(nodes_path) as f:
    for line in f:
        parts = line.strip().split()
        if len(parts) >= 4:
            nid = int(parts[0])
            x, y, z = float(parts[-3]), float(parts[-2]), float(parts[-1])
            nodes[nid] = (x, y, z)
print(f"nodes parsed: {len(nodes)}", file=sys.stderr)
sys.stderr.flush()

elems = []
els_path = os.path.join(MESH_DIR, "mesh.elements")
with open(els_path) as f:
    for line in f:
        p = line.strip().split()
        if len(p) >= 7:
            try:
                eid = int(p[0]); body = int(p[1]); etype = int(p[2]); nids = [int(v) for v in p[3:7]]
                if etype == 504:
                    elems.append((eid, body, nids))
            except Exception as e:
                print(f"elem parse error: {e}", file=sys.stderr)
print(f"volumes parsed: {len(elems)}", file=sys.stderr)
sys.stderr.flush()

tris = []
bdy_path = os.path.join(MESH_DIR, "mesh.boundary")
with open(bdy_path) as f:
    for line in f:
        p = line.strip().split()
        if len(p) >= 8:
            try:
                btag = int(p[0]); code = int(p[1]); tri = [int(v) for v in p[5:8]]
                tris.append((btag, code, tri))
            except Exception as e:
                print(f"boundary parse error: {e}", file=sys.stderr)
print(f"faces parsed: {len(tris)}", file=sys.stderr)
sys.stderr.flush()

try:
    fm = Fem.FemMesh()
    for nid, (x, y, z) in nodes.items():
        fm.addNode(x, y, z, nid)
    print(f"nodes added to FemMesh: {len(nodes)}", file=sys.stderr)
    sys.stderr.flush()
    for _, _, tet in elems:
        try:
            fm.addVolume(*tet)
        except Exception as e:
            print(f"addVolume error: {e}", file=sys.stderr)
    print(f"volumes in FemMesh: {len(fm.Volumes)}", file=sys.stderr)
    sys.stderr.flush()
    for _, _, tri in tris:
        try:
            fm.addFace(*tri)
        except Exception as e:
            print(f"addFace error: {e}", file=sys.stderr)
    print(f"faces in FemMesh: {len(fm.Faces)}", file=sys.stderr)
    sys.stderr.flush()
except Exception as e:
    print(f"FemMesh construction error: {e}", file=sys.stderr)
    import traceback; traceback.print_exc(file=sys.stderr)
sys.stderr.flush()

try:
    write_ascii_stl = False
    if True:
        def normal(a, b, c):
            ux, uy, uz = b[0]-a[0], b[1]-a[1], b[2]-a[2]
            vx, vy, vz = c[0]-a[0], c[1]-a[1], c[2]-a[2]
            return (uy*vz - uz*vy, uz*vx - ux*vz, ux*vy - uy*vz)
        with open(OUT_STL, "w") as f:
            f.write("solid elmer_mesh\n")
            for _, _, tri in tris:
                a = nodes[tri[0]]; b = nodes[tri[1]]; c = nodes[tri[2]]
                nx, ny, nz = normal(a, b, c)
                mx, my, mz = (a[0]+b[0]+c[0])/3, (a[1]+b[1]+c[1])/3, (a[2]+b[2]+c[2])/3
                if nx*(mx-0) + ny*(my-0) + nz*(mz-0) < 0:
                    b, c = c, b
                    a = nodes[tri[0]]; b = nodes[tri[1]]; c = nodes[tri[2]]
                    nx, ny, nz = normal(a, b, c)
                f.write(f"  facet normal {nx:.6e} {ny:.6e} {nz:.6e}\n")
                f.write("    outer loop\n")
                for p in (a, b, c):
                    f.write(f"      vertex {p[0]:.9e} {p[1]:.9e} {p[2]:.9e}\n")
                f.write("    endloop\n")
                f.write("  endfacet\n")
            f.write("endsolid elmer_mesh\n")
        write_ascii_stl = True
    if write_ascii_stl:
        print(f"STL written: {OUT_STL}", file=sys.stderr)
        sys.stderr.flush()
except Exception as e:
    print(f"STL error: {e}", file=sys.stderr)
    import traceback; traceback.print_exc(file=sys.stderr)

try:
    docName = "BraggElmerMesh"
    if App.ActiveDocument is not None and App.ActiveDocument.Name == docName:
        doc = App.ActiveDocument
    else:
        doc = App.newDocument(docName)
    for o in list(doc.Objects):
        doc.removeObject(o.Name)
    print("new document done", file=sys.stderr)
    sys.stderr.flush()
    fem_obj = doc.addObject("Fem::FemMeshObject", "ElmerMesh")
    fem_obj.FemMesh = fm
    print("FemMeshObject added", file=sys.stderr)
    sys.stderr.flush()
    surf = Mesh.Mesh()
    surf.read(OUT_STL)
    mesh_obj = doc.addObject("Mesh::Feature", "ElmerMeshSurface")
    mesh_obj.Mesh = surf
    print("MeshFeature added", file=sys.stderr)
    sys.stderr.flush()
    doc.recompute()
    doc.saveAs(OUT_FCSTD)
    print(f"FCStd saved: {OUT_FCSTD}", file=sys.stderr)
    sys.stderr.flush()
    print("objects:", [o.Name for o in doc.Objects], file=sys.stderr)
    sys.stderr.flush()
except Exception as e:
    print(f"Doc construction error: {e}", file=sys.stderr)
    import traceback; traceback.print_exc(file=sys.stderr)
sys.stderr.flush()
