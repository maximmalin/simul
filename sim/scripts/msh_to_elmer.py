#!/usr/bin/env python3
"""msh_to_elmer.py — конвертер Gmsh .msh → полная корректная Elmer mesh.*.

Исправлено (июль/2026) — старые версии этого файла писали:
  * mesh.elements в неверном формате  `eid type group nodes...`
    (Elmer ждёт `eid body 504 node1 node2 node3 node4`);
  * mesh.boundary пустым (BC через Target Boundaries не могли применяться).

Теперь, как в проверенном fix_elmer_mesh.py:
  * поддерживаются форматы 2.2 (плоские секции) и 4.1 (блочные секции);
  * граничные треугольники вычисляются из граней тетраэдров
    (внешние — свободные/BC, внутренние — межфазные left/right);
  * BC-коды внешним граням назначаются геометрически:
    грань x≈0 с нормалью -x → front-code, грань z≈0 с нормалью -z → bottom-code;
  * mesh.header/mesh.elements/mesh.boundary/mesh.names пишутся в формате,
    который читает MeshIO.F90 из Elmer 26.2.

Использование:
  msh_to_elmer.py <input.msh> <output_dir>
  msh_to_elmer.py <input.msh> <output_dir> --front-code 1 --bottom-code 5 --tol 1e-6
"""
import re
import sys
from collections import defaultdict
from pathlib import Path


# ----------------------------------------------------------------------------
# Разбор .msh
# ----------------------------------------------------------------------------
def parse_msh_v22(content):
    """Gmsh 2.2: секции $Nodes/$Elements — плоские строки вида:
    узлы:   id x y z
    эл-ты:  eid type ntags tag1..tagN node1..nodeN
    Соглашение: первая физическая метка из тегов — физическая группа элемента.
    Возвращает nodes {id:(x,y,z)} и elements [(eid, physical_tag, type, node_ids)].
    """
    nodes = {}
    m = re.search(r"\$Nodes\n(.*?)\n\$EndNodes", content, re.DOTALL)
    if m:
        lines = m.group(1).strip().split("\n")
        try:
            n = int(lines[0])
            for i in range(1, n + 1):
                parts = lines[i].split()
                nodes[int(parts[0])] = (float(parts[1]), float(parts[2]), float(parts[3]))
        except (ValueError, IndexError) as exc:
            raise ValueError(f"Ошибка разбора $Nodes v2.2: {exc}") from None

    elements = []
    m = re.search(r"\$Elements\n(.*?)\n\$EndElements", content, re.DOTALL)
    if m:
        lines = m.group(1).strip().split("\n")
        try:
            n = int(lines[0])
            for i in range(1, n + 1):
                parts = [int(x) for x in lines[i].split()]
                eid, etype, ntags = parts[0], parts[1], parts[2]
                tags = parts[3:3 + ntags]
                nids = parts[3 + ntags:]
                phys = tags[0] if tags else 0          # первая физ. метка
                elements.append((eid, phys, etype, nids))
        except (ValueError, IndexError) as exc:
            raise ValueError(f"Ошибка разбора $Elements v2.2: {exc}") from None
    return nodes, elements


def parse_msh_v41(content):
    """Gmsh 4.1: блочные секции.
    $Nodes:  1-я строка <numBlocks> <numNodes> <minTag> <maxTag>;
             блок: <dim> <entityTag> <parametric> <num> +
             num строк тегов узлов + num строк координат.
    $Elements: 1-я строка <numBlocks> <numElements> <minTag> <maxTag>;
             блок: <dim> <entityTag> <type> <num> +
             num строк `eid n1 ... nN`.
    В 4.1 физическая группа = entityTag блока (для Physical Volume).
    """
    nodes = {}
    m = re.search(r"\$Nodes\n(.*?)\n\$EndNodes", content, re.DOTALL)
    if not m:
        raise ValueError("нет секции $Nodes")
    blocks, lines = parse_v41_blocks(m.group(1))
    for dim, etag, param, num, s in blocks:
        ntags = lines[s:s + num]
        ncoords = lines[s + num:s + 2 * num]
        for tln, cln in zip(ntags, ncoords):
            tid = int(tln.split()[0])
            c = cln.split()
            nodes[tid] = (float(c[0]), float(c[1]), float(c[2]))

    elements = []
    m = re.search(r"\$Elements\n(.*?)\n\$EndElements", content, re.DOTALL)
    if not m:
        raise ValueError("нет секции $Elements")
    blocks, lines = parse_v41_blocks(m.group(1))
    for dim, etag, etype, num, s in blocks:
        phys = etag                       # в 4.1 это физическая метка/группа
        for ln in lines[s:s + num]:
            p = [int(x) for x in ln.split()]
            elements.append((p[0], phys, etype, p[1:]))
    return nodes, elements


def parse_v41_blocks(block_text):
    """Разбирает блочную секцию v4.1 в (blocks, lines).
    blocks: список (dim, entityTag, type, num, start_idx) — start_idx указывает
    на первую строку данных блока внутри lines. Строк данных в блоке:
    $Elements — num штук, $Nodes — 2*num штук (теги + координаты), поэтому
    потребление оставлено вызывающему.
    """
    lines = [ln for ln in block_text.strip().split("\n") if ln.strip()]
    if not lines:
        return [], []
    nblocks = int(lines[0].split()[0])
    blocks = []
    idx = 1
    for _ in range(nblocks):
        hdr = lines[idx].split()
        idx += 1
        dim, etag, etype, num = (int(hdr[0]), int(hdr[1]), int(hdr[2]), int(hdr[3]))
        blocks.append((dim, etag, etype, num, idx))
        if etype == 0:            # $Nodes: теги + координаты = 2*num строк
            idx += 2 * num
        else:                     # $Elements: num строк
            idx += num
    return blocks, lines


def parse_physical_names(content):
    """$PhysicalNames: строки `dim tag "name"`. Возвращает {(dim, tag): name}."""
    m = re.search(r"\$PhysicalNames\n(.*?)\n\$EndPhysicalNames", content, re.DOTALL)
    out = {}
    if not m:
        return out
    lines = m.group(1).strip().split("\n")
    for ln in lines[1:]:
        parts = ln.split()
        if len(parts) >= 3:
            out[(int(parts[0]), int(parts[1]))] = " ".join(parts[2:]).strip('"')
    return out


def parse_msh(path):
    """Читает .msh и возвращает (nodes, elements, physical_names)."""
    content = Path(path).read_text(encoding="latin1")
    fmt = re.search(r"\$MeshFormat\n([\d.]+)", content)
    if not fmt:
        raise ValueError("не найден $MeshFormat")
    version = float(fmt.group(1))
    if version >= 4.0:
        nodes, elements = parse_msh_v41(content)
    else:
        nodes, elements = parse_msh_v22(content)
    names = parse_physical_names(content)
    return nodes, elements, names


# ----------------------------------------------------------------------------
# Построение Elmer-сетки
# ----------------------------------------------------------------------------
def face_key(node_ids):
    """Нормализованный ключ грани — не зависит от порядка узлов."""
    return tuple(sorted(node_ids))


def tet_centroid(nodes, nids):
    xs = [nodes[n][0] for n in nids]
    ys = [nodes[n][1] for n in nids]
    zs = [nodes[n][2] for n in nids]
    return (sum(xs) / 4, sum(ys) / 4, sum(zs) / 4)


def face_centroid(nodes, nids):
    xs = [nodes[n][0] for n in nids]
    ys = [nodes[n][1] for n in nids]
    zs = [nodes[n][2] for n in nids]
    return (sum(xs) / 3, sum(ys) / 3, sum(zs) / 3)


def face_normal(nodes, nids):
    """Нормаль по правилу правой руки для (n0,n1,n2)."""
    p0, p1, p2 = (nodes[nids[0]], nodes[nids[1]], nodes[nids[2]])
    u = (p1[0] - p0[0], p1[1] - p0[1], p1[2] - p0[2])
    v = (p2[0] - p0[0], p2[1] - p0[1], p2[2] - p0[2])
    return (u[1] * v[2] - u[2] * v[1],
            u[2] * v[0] - u[0] * v[2],
            u[0] * v[1] - u[1] * v[0])


def orient_outward(nodes, nids, owner_tet):
    """Разворачивает грань так, чтобы нормаль указывала наружу от тетра."""
    fcx = face_centroid(nodes, nids)
    tc = tet_centroid(nodes, owner_tet)
    outward = (fcx[0] - tc[0], fcx[1] - tc[1], fcx[2] - tc[2])
    nrm = face_normal(nodes, nids)
    if nrm[0] * outward[0] + nrm[1] * outward[1] + nrm[2] * outward[2] < 0:
        return (nids[0], nids[2], nids[1])
    return nids


def build_elmer_mesh(nodes, elements, output_dir, body_names=None,
                     bc_front_code=1, bc_bottom_code=5, tol=1e-6):
    """Строит полную Elmer-сетку из тетраэдров + граничных элементов.

    nodes:     {id: (x,y,z)}
    elements:  [(eid, physical_tag, type, node_ids)]  — только тетраэдры (type 4)
    body_names:{tag: имя_тела} — по умолчанию glass_body=1, quartz_body=2
    """
    body_names = body_names or {1: "glass_body", 2: "quartz_body"}

    tets = [(eid, ptag, nids) for (eid, ptag, etype, nids) in elements if etype == 4]
    if not tets:
        raise ValueError("в .msh нет тетраэдров (тип 4)")
    print(f"Тетраэдров: {len(tets)}")

    node_renum = {old: new for new, old in enumerate(sorted(nodes), start=1)}
    rn = lambda nid: node_renum[nid]

    # --- грани и их владельцы ---
    face_owners = defaultdict(list)          # key -> [(tet_eid, (n1,n2,n3))]
    for eid, ptag, nids in tets:
        for combo in ((0, 1, 2), (0, 1, 3), (0, 2, 3), (1, 2, 3)):
            fn = (nids[combo[0]], nids[combo[1]], nids[combo[2]])
            face_owners[face_key(fn)].append((eid, fn))

    exterior = []                            # (owner_tet, face)
    interior = []                            # (tet1, tet2, face)
    for key, owners in face_owners.items():
        if len(owners) == 1:
            exterior.append(owners[0])
        elif len(owners) == 2:
            interior.append((owners[0][0], owners[1][0], owners[0][1]))
        else:
            print(f"  ! предупреждение: грань с {len(owners)} владельцами")
    print(f"Внешних граней: {len(exterior)}, внутренних (interface): {len(interior)}")

    # --- классификация внешних граней по BC ---
    tet_nodes = {eid: nids for eid, _, nids in tets}

    def classify(fn, owner):
        c = face_centroid(nodes, fn)
        ori = orient_outward(nodes, fn, tet_nodes[owner])
        nrm = face_normal(nodes, ori)
        if abs(c[0]) < tol and nrm[0] < 0:
            return bc_front_code, ori
        if abs(c[2]) < tol and nrm[2] < 0:
            return bc_bottom_code, ori
        return 0, ori

    boundary = []
    bid = 1
    for tet, fn in exterior:
        code, ori = classify(fn, tet)
        boundary.append((bid, code, tet, 0, ori))
        bid += 1
    for t1, t2, fn in interior:
        boundary.append((bid, 0, t1, t2, fn))
        bid += 1

    n_front = sum(1 for b in boundary if b[1] == bc_front_code)
    n_bottom = sum(1 for b in boundary if b[1] == bc_bottom_code)
    n_other = sum(1 for b in boundary if b[1] == 0)
    print(f"BC front (код {bc_front_code}): {n_front}, "
          f"BC bottom (код {bc_bottom_code}): {n_bottom}, свободные: {n_other}")

    # --- запись файлов ---
    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)

    nnodes, nbulk, nbnd = len(nodes), len(tets), len(boundary)
    with open(out / "mesh.header", "w") as f:
        f.write(f"{nnodes}    {nbulk}    {nbnd}    \n")
        f.write("2     \n")
        f.write(f"504    {nbulk}   \n")
        f.write(f"303    {nbnd}   \n")

    with open(out / "mesh.nodes", "w") as f:
        for old in sorted(nodes):
            x, y, z = nodes[old]
            f.write(f"{rn(old)} -1 {x:.10e} {y:.10e} {z:.10e}\n")

    with open(out / "mesh.elements", "w") as f:
        for eid, ptag, nids in tets:
            body = ptag if ptag else 1
            f.write(f"{eid} {body} 504 "
                    + " ".join(str(rn(n)) for n in nids) + "\n")

    with open(out / "mesh.boundary", "w") as f:
        for btag, code, left, right, fn in boundary:
            f.write(f"{btag} {code} {left} {right} 303 "
                    + " ".join(str(rn(n)) for n in fn) + "\n")

    with open(out / "mesh.names", "w") as f:
        f.write("! ----- names for bodies -----\n")
        for tag, name in sorted(body_names.items()):
            f.write(f"$ {name} = {tag}\n")
        f.write("! ----- names for boundaries -----\n")
        f.write(f"$ front_fixed = {bc_front_code}\n")
        f.write(f"$ bottom_fixed = {bc_bottom_code}\n")

    with open(out / "entities.sif", "w") as f:
        for tag, name in sorted(body_names.items()):
            f.write(f"Body {tag}\n  Name = {name}\nEnd\n\n")

    print(f"Готово: {out}")
    print(f"  mesh.header: {nnodes} узлов, {nbulk} объёмных, {nbnd} граничных")
    return out


def convert(input_path, output_dir, bc_front_code=1, bc_bottom_code=5, tol=1e-6):
    """Полный конвейер: .msh → Elmer mesh."""
    nodes, elements, phys_names = parse_msh(Path(input_path))
    print(f"Узлов: {len(nodes)}, элементов: {len(elements)}")
    for (dim, tag), name in sorted(phys_names.items()):
        print(f"    физическая группа dim={dim}, tag={tag}: {name}")

    body_names = {tag: name for (dim, tag), name in phys_names.items() if dim == 3}
    build_elmer_mesh(nodes, elements, output_dir, body_names=body_names,
                     bc_front_code=bc_front_code, bc_bottom_code=bc_bottom_code,
                     tol=tol)


if __name__ == "__main__":
    args = sys.argv[1:]
    if len(args) < 2:
        print(f"Использование: {sys.argv[0]} <input.msh> <output_dir> "
              f"[--front-code N] [--bottom-code N] [--tol 1e-6]")
        sys.exit(1)
    front, bottom, tol = 1, 5, 1e-6
    i = 2
    while i < len(args):
        if args[i] == "--front-code":
            front = int(args[i + 1]); i += 2
        elif args[i] == "--bottom-code":
            bottom = int(args[i + 1]); i += 2
        elif args[i] == "--tol":
            tol = float(args[i + 1]); i += 2
        else:
            print(f"Неизвестный аргумент: {args[i]}"); sys.exit(2)
    convert(args[0], args[1], bc_front_code=front, bc_bottom_code=bottom, tol=tol)