"""
KiCad-библиотеки: разбор .kicad_sym, извлечение определений символов и выпиливание
файла .kicad_sch формата KiCad 9 (version 20250114).

Ничего не рисуется «руками» — символы копируются дословно из установленных
библиотек KiCad (/usr/share/kicad/symbols), поэтому KiCad гарантированно их
подхватывает. Проверка — штатный kicad-cli.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from uuid import uuid4

SYMDIR = Path("/usr/share/kicad/symbols")
SCH_VERSION = "20250114"          # формат KiCad 9
SCH_GENERATOR = "brig-receiver-generator"
SCH_GENERATOR_VERSION = "9.0"
GRID = 1.27                       # мм, стандартная сетка KiCad


# --------------------------------------------------------------------------- #
#  Мини-парсер s-выражений (строки в кавычках игнорируются)
# --------------------------------------------------------------------------- #
@dataclass
class S:
    """Узел s-выражения: список либо скалярная строка."""
    items: list = field(default_factory=list)

    def __repr__(self):
        return f"S({self.items})"

    @property
    def head(self) -> str:
        return self.items[0] if self.items and isinstance(self.items[0], str) else ""

    def find_all(self, head: str) -> list["S"]:
        return [i for i in self.items if isinstance(i, S) and i.head == head]

    def find(self, head: str) -> "S | None":
        got = self.find_all(head)
        return got[0] if got else None

    def val(self, head: str, default=None):
        n = self.find(head)
        return n.items[1] if n is not None and len(n.items) > 1 else default


_TOKEN = re.compile(r'\(|\)|"(?:[^"\\]|\\.)*"|[^\s()]+')


class Str(str):
    """Строка, которая в файле KiCad обязана стоять в кавычках."""
    __slots__ = ()


class Bare(str):
    """Токен, который KiCad пишет БЕЗ кавычек: имена полей и перечисления
    (version, generator, at, unit, yes, no, default, …)."""
    __slots__ = ()


def Q(v) -> Str:
    """Пометить значение как строку в кавычках."""
    if isinstance(v, Bare):
        return Str(str(v))
    return v if isinstance(v, Str) else Str(str(v))


def B(v) -> Bare:
    """Пометить значение как «голый» токен без кавычек."""
    return v if isinstance(v, Bare) else Bare(str(v))


# Перечисления, которые KiCad печатает БЕЗ кавычек.
# Это значения, а не имена полей, поэтому просто str здесь не годится.
ENUM_BARE = {
    "no", "yes", "default", "dash", "dot", "dash_dot", "dot_dash", "none",
    "solid", "input", "output", "bidirectional", "tri_state", "passive",
    "free", "unspecified", "power", "line", "circle", "rectangle", "polyline",
    "left", "right", "center", "top", "bottom", "mirror", "repeat",
    "repeat_x", "repeat_y", "repeat_l", "repeat_v", "keep", "ratio", "align",
    "offset", "full", "page1only", "unknown", "max", "min", "inherit",
}


def E(v) -> str:
    """Значение-перечисление: без кавычек, если оно из известного набора."""
    s = str(v)
    return Bare(s) if s in ENUM_BARE else Q(s)


def parse_sexp(text: str) -> S:
    """Разбор одного s-выражения (быстрый токенизатор)."""
    stack: list[S] = []
    root: S | None = None
    for m in _TOKEN.finditer(text):
        tok = m.group(0)
        if tok == "(":
            node = S()
            if stack:
                stack[-1].items.append(node)
            else:
                root = node
            stack.append(node)
        elif tok == ")":
            if stack:
                stack.pop()
        elif tok.startswith('"'):
            stack[-1].items.append(Str(tok[1:-1].replace('\\"', '"').replace("\\\\", "\\")))
        else:
            stack[-1].items.append(Bare(tok))
    return root if root is not None else S()


_BARE = re.compile(r'^[^\s()"]+$')


def _q(v) -> str:
    """Скаляр для вывода.

    Str — в кавычках (значения); Bare — без кавычек (имена полей,
    перечисления); числа — как есть.
    """
    if isinstance(v, bool):
        return "yes" if v else "no"
    if isinstance(v, (int, float)):
        return str(v)
    if isinstance(v, Bare):
        return str(v)
    return '"' + str(v).replace("\\", "\\\\").replace('"', '\\"') + '"'


def dump_sexp(node, indent: int = 0, tab: str = "\t") -> str:
    """Печать s-выражения в стиле KiCad.

    Каждый токен — на отдельной строке, ровно как это делает сам KiCad.
    Имена полей (version, generator, lib_id, at, …) печатаются БЕЗ кавычек,
    значения (uuid, paper, номера пинов, тексты) — В кавычках.
    """
    pad = tab * indent
    if isinstance(node, str):
        return pad + _q(node)
    out = [pad + "(" + node.head]
    for it in node.items[1:]:
        if isinstance(it, S):
            out.append(dump_sexp(it, indent + 1, tab))
        else:
            out.append(pad + tab + _q(it))
    out.append(pad + ")")
    return "\n".join(out)


# --------------------------------------------------------------------------- #
#  Символьные библиотеки KiCad
# --------------------------------------------------------------------------- #
_LIB_CACHE: dict[str, S] = {}


def load_lib(lib: str) -> S:
    """Читает .kicad_sym и возвращает узел kicad_symbol_lib."""
    if lib in _LIB_CACHE:
        return _LIB_CACHE[lib]
    path = SYMDIR / f"{lib}.kicad_sym"
    if not path.exists():
        raise FileNotFoundError(f"нет библиотеки KiCad: {path}")
    node = parse_sexp(path.read_text(errors="ignore"))
    _LIB_CACHE[lib] = node
    return node


def sym_name(s: S) -> str:
    """Имя символа: в .kicad_sym это ПЕРВЫЙ скаляр ('R', 'R_1_1'),
    а не поле (name ...) — поэтому val('name') здесь не работает."""
    return str(s.items[1]) if len(s.items) > 1 else ""


def _raw_symbol(lib: str, name: str) -> S:
    top = load_lib(lib)
    if top.head != "kicad_symbol_lib":
        top = top.find("kicad_symbol_lib") or top
    for s in top.find_all("symbol"):
        if sym_name(s) == name:
            return s
    raise KeyError(f"символ {lib}:{name} не найден в {SYMDIR}")


_RESOLVED: dict[str, S] = {}


def lib_symbol(lib_id: str) -> S:
    """Определение символа с РАЗВЁРНУТЫМ наследованием (extends).

    KiCad хранит часть символов как производные: TL061 -> LM741,
    LM78L05_TO92 -> ..., STM32F103C8Tx -> .... Пока extends не развёрнут,
    у символа нет ни пинов, ни графики. Здесь родительский блок сливается
    в дочерний, а поле (extends ...) удаляется — определение становится
    самодостаточным.

    Именование как в самом KiCad: верхний узел получает 'Lib:Name',
    а под-символы юнитов остаются 'Name_0_1' / 'Name_1_1' (без 'Lib:').
    """
    if lib_id in _RESOLVED:
        return _RESOLVED[lib_id]
    lib, name = lib_id.split(":", 1)
    node = _raw_symbol(lib, name)
    ext = node.find("extends")
    if ext is not None:
        parent_name = str(ext.items[1])
        merged = lib_symbol(f"{lib}:{parent_name}")
        keep = [i for i in node.items
                if not (isinstance(i, S) and i.head == "extends")]
        out = S(["symbol", lib_id] + list(keep[2:]))
        have = {sym_name(u) for u in out.find_all("symbol")}
        for u in merged.find_all("symbol"):
            pn = sym_name(u)
            un = f"{name}_" + pn[len(parent_name) + 1:] if pn.startswith(parent_name + "_") else pn
            if un in have:
                continue
            clone = S(list(u.items))
            clone.items[1] = un     # под-символы НЕ получают префикс библиотеки
            out.items.append(clone)
            have.add(un)
        node = out
    else:
        node = S(list(node.items))
        node.items[1] = lib_id
    _RESOLVED[lib_id] = node
    return node


def pin_name_map(sym: S) -> dict[str, str]:
    """'Pin_1' -> '1' (номер пина), плюс имена для именованных пинов."""
    num_by_name: dict[str, str] = {}
    for unit in sym.find_all("symbol"):        # под-символы юнитов
        for p in unit.find_all("pin"):
            nm = p.val("name")
            num = p.val("number")
            if nm is None or num is None:
                continue
            num_by_name.setdefault(str(nm), str(num))
    return num_by_name


def units_of(sym: S) -> list[int]:
    """Список номеров юнитов символа (1, 2, 3 …), по возрастанию."""
    out = set()
    for u in sym.find_all("symbol"):
        m = re.search(r"_(\d+)_\d+$", sym_name(u))
        if m:
            out.add(int(m.group(1)))
    return sorted(out) or [1]


def unit_for_pin(sym: S, pin_number: str) -> int:
    """Номер юнита, в котором физически лежит пин.

    У мультиюнитовых символов (например TL082) выводы питания вынесены
    в отдельный юнит, поэтому каждому выводу соответствует свой юнит.
    """
    m = re.compile(r"_(\d+)_\d+$")
    for u in sym.find_all("symbol"):
        sn = sym_name(u)
        mm = m.search(sn)
        if not mm:
            continue
        for p in u.find_all("pin"):
            if str(p.val("number")) == str(pin_number):
                return int(mm.group(1))
    return 1


def pin_positions(sym: S, unit: int = 1) -> dict[str, tuple[float, float]]:
    """Номер пина -> (x, y) в координатах символа (Y вверх, как в библиотеке)."""
    out: dict[str, tuple[float, float]] = {}
    for u in sym.find_all("symbol"):
        tail = sym_name(u)
        m = re.search(r"_(\d+)_(\d+)$", tail)
        if m and int(m.group(1)) != unit:
            continue
        for p in u.find_all("pin"):
            at = p.find("at")
            num = p.val("number")
            if at and num is not None:
                out.setdefault(str(num), (float(at.items[1]), float(at.items[2])))
    return out


def embed_lib_symbol(lib_id: str) -> S:
    """Готовое определение символа для (lib_symbols ...) — уже с именем
    'Lib:Name' и без (extends ...), т.е. полностью самодостаточно."""
    sym = lib_symbol(lib_id)
    new = S(list(sym.items))
    new.items = [i for i in new.items
                 if not (isinstance(i, S) and i.head == "extends")]
    new.items[1] = lib_id
    return new


# --------------------------------------------------------------------------- #
#  Элементы схемы
# --------------------------------------------------------------------------- #
@dataclass
class Comp:
    lib_id: str
    ref: str
    value: str
    footprint: str = ""
    x: float = 0.0
    y: float = 0.0
    rot: int = 0
    unit: int = 1
    uuid: str = field(default_factory=lambda: str(uuid4()))
    pins: dict[str, str] = field(default_factory=dict)   # pin# -> uuid
    in_bom: bool = True
    hide_ref: bool = False        # символы питания: ref не показывается

    def pin_point(self, pin_number: str) -> tuple[float, float]:
        """Координаты пина на листе (ось Y схемы направлена вниз)."""
        sym = lib_symbol(self.lib_id)
        pp = pin_positions(sym, self.unit)
        if pin_number not in pp:
            raise KeyError(f"{self.ref}: пин {pin_number} отсутствует в {self.lib_id}")
        px, py = pp[pin_number]
        # поворот символа (Y вниз)
        if self.rot == 90:
            px, py = -py, px
        elif self.rot == 180:
            px, py = -px, -py
        elif self.rot == 270:
            px, py = py, -px
        return round(self.x + px, 4), round(self.y - py, 4)

    def resolve(self, pin_ref: str) -> str:
        """Имя или номер пина -> номер пина."""
        sym = lib_symbol(self.lib_id)
        m = pin_name_map(sym)
        if pin_ref in m:
            return m[pin_ref]
        if re.fullmatch(r"\d+", pin_ref):
            for un in units_of(sym):          # пин может лежать в любом юните
                if pin_ref in pin_positions(sym, un):
                    return pin_ref
        avail = ", ".join(sorted(m)[:24])
        raise KeyError(f"{self.ref} ({self.lib_id}): нет пина '{pin_ref}'. Доступно: {avail}")


def _fx(v: float) -> str:
    """Формат координаты как в KiCad: без хвостовых нулей."""
    s = f"{v:.4f}".rstrip("0").rstrip(".")
    return s if s not in ("", "-0") else "0"


def _eff(size: float = 1.27, hide: bool = False, bold: bool = False,
         italic: bool = False, justify: tuple | None = None) -> S:
    """(effects ...) как это пишет сам KiCad.

    ВАЖНО: (hide yes) — сосед (font ...), а не его содержимое.
    """
    font = S(["font", S(["size", size, size])])
    if bold:
        font.items.append(S([B("bold"), E("yes")]))
    if italic:
        font.items.append(S([B("italic"), E("yes")]))
    e = S(["effects", font])
    if justify:
        e.items.append(S([B("justify"), *[B(j) for j in justify]]))
    if hide:
        e.items.append(S([B("hide"), E("yes")]))
    return e


def symbol_node(c: Comp) -> S:
    """(symbol ...) для .kicad_sch — так пишет сам KiCad 9."""
    sym = lib_symbol(c.lib_id)
    unit_key = f"_{c.unit}_"
    body: list = [S([B("lib_id"), Q(c.lib_id)])]
    body.append(S([B("at"), c.x, c.y, c.rot]))
    body.append(S([B("unit"), c.unit]))
    body.append(S([B("exclude_from_sim"), E("no")]))
    body.append(S([B("in_bom"), E("yes" if c.in_bom else "no")]))
    body.append(S([B("on_board"), E("yes")]))
    body.append(S([B("dnp"), E("no")]))
    body.append(S([B("uuid"), Q(c.uuid)]))
    for pname, pval, psize, phide in (
        ("Reference", Q(c.ref), 1.27, c.hide_ref),
        ("Value", Q(c.value), 1.27, False),
        ("Footprint", Q(c.footprint), 0.254, True),
        ("Datasheet", Q("~"), 1.27, True),
        ("Description", Q(""), 1.27, True),
    ):
        dy = {"Reference": 2.54, "Value": 0.0, "Footprint": 5.08,
              "Datasheet": 0.0, "Description": 0.0}[pname]
        body.append(S(["property", Q(pname), pval,
                       S([B("at"), c.x, round(c.y + dy, 3), 0]),
                       _eff(psize, hide=phide)]))
    for u in sym.find_all("symbol"):
        if unit_key not in sym_name(u):        # только нужный юнит
            continue
        for p in u.find_all("pin"):
            num = str(p.val("number"))
            c.pins[num] = str(uuid4())
            body.append(S(["pin", Q(num), S([B("uuid"), Q(c.pins[num])])]))
    return S(["symbol"] + body)


def instances_node(c: Comp, project: str, root_uuid: str) -> S:
    """(instances ...) — аннотация экземпляра в KiCad 9 (живёт внутри символа)."""
    return S(["instances",
              S([B("project"), Q(project),
                 S([B("path"), Q(f"/{root_uuid}/{c.uuid}"),
                    S([B("reference"), Q(c.ref)]),
                    S([B("unit"), c.unit])])])])


def wire_node(x1, y1, x2, y2) -> S:
    return S(["wire",
              S(["pts", S([B("xy"), round(x1, 3), round(y1, 3)]),
                 S([B("xy"), round(x2, 3), round(y2, 3)])]),
              S(["stroke", S([B("width"), 0]), S([B("type"), E("default")])]),
              S([B("uuid"), Q(str(uuid4()))])])


def junction_node(x, y) -> S:
    return S(["junction",
              S([B("at"), round(x, 3), round(y, 3)]),
              S([B("diameter"), 0]),
              S([B("color"), 0, 0, 0, 0]),
              S([B("uuid"), Q(str(uuid4()))])])


def label_node(text: str, x, y, kind: str = "label", shape: str | None = None) -> S:
    n = S([kind, Q(text), S([B("at"), round(x, 3), round(y, 3), 0]),
           _eff(1.27, justify=("left", "bottom"))])
    if shape:
        n.items.insert(2, S([B("shape"), E(shape)]))
    n.items.append(S([B("uuid"), Q(str(uuid4()))]))
    return n


def text_node(text: str, x, y, size: float = 2.54, bold: bool = True) -> S:
    return S(["text", Q(text),
              S([B("exclude_from_sim"), E("no")]),
              S([B("at"), round(x, 3), round(y, 3), 0]),
              _eff(size, bold=bold),
              S([B("uuid"), Q(str(uuid4()))])])


def rgb(color: str, alpha: int = 1) -> S:
    """(color R G B A) — KiCad ждёт 0..255, НЕ 0..1 и без решётки."""
    s = color.lstrip("#")
    if len(s) != 6:
        raise ValueError(f"цвет должен быть RRGGBB, получено {color!r}")
    return S([B("color"), int(s[0:2], 16), int(s[2:4], 16), int(s[4:6], 16), alpha])


def rect_node(x1, y1, x2, y2, width=0.0, style="default",
              color="000000", fill="none") -> S:
    """(rectangle ...) ровно по схеме KiCad 9: цвет живёт ВНУТРИ (stroke ...)."""
    return S(["rectangle",
              S([B("start"), round(x1, 3), round(y1, 3)]),
              S([B("end"), round(x2, 3), round(y2, 3)]),
              S(["stroke", S([B("width"), width]), S([B("type"), E(style)]),
                 rgb(color)]),
              S(["fill", S([B("type"), E(fill)])]),
              S([B("uuid"), Q(str(uuid4()))])])


def build_sch(comps: list[Comp], wires: list, junctions: list, labels: list,
              texts: list, rects: list, paper: str = "A2",
              title: str = "", date: str = "", rev: str = "",
              project: str = "brig_receiver") -> S:
    """Собирает полный .kicad_sch одного листа (KiCad 9)."""
    root_uuid = str(uuid4())
    used: dict[str, S] = {}
    for c in comps:
        if c.lib_id not in used:
            used[c.lib_id] = embed_lib_symbol(c.lib_id)

    tb = S([B("title_block")])
    for key, val in (("title", title), ("date", date), ("rev", rev)):
        if val:
            tb.items.append(S([B(key), Q(val)]))

    root = S(["kicad_sch",
              S([B("version"), B(SCH_VERSION)]),
              S([B("generator"), Q(SCH_GENERATOR)]),
              S([B("generator_version"), Q(SCH_GENERATOR_VERSION)]),
              S([B("uuid"), Q(root_uuid)]),
              S([B("paper"), Q(paper)]),
              tb])
    libs = S([B("lib_symbols")])
    for lib_id in sorted(used):
        libs.items.append(used[lib_id])
    root.items.append(libs)
    for group in (rects, texts, wires, junctions, labels):
        for it in group:
            root.items.append(it)
    for c in comps:
        node = symbol_node(c)
        node.items.append(instances_node(c, project, root_uuid))
        root.items.append(node)
    # корневой лист обязан объявлять sheet_instances и embedded_fonts
    root.items.append(S([B("sheet_instances"),
                         S([B("path"), Q("/"), S([B("page"), Q("1")])])]))
    root.items.append(S([B("embedded_fonts"), E("no")]))
    return root


# --------------------------------------------------------------------------- #
#  Самопроверка сериализатора на НАСТОЯЩЕМ файле KiCad
# --------------------------------------------------------------------------- #
def self_test(verbose: bool = True) -> bool:
    """Round-trip настоящего .kicad_sch из демо KiCad.

    Эталонный файл читается, разбирается и печатается обратно; поток токенов
    обязан совпасть до единого символа. Если нет — сериализатор врёт,
    и «сгенерированную» схему KiCad не откроет.
    """
    import re as _re
    demo = Path("/usr/share/kicad/demos/complex_hierarchy/complex_hierarchy.kicad_sch")
    if not demo.exists():
        if verbose:
            print("self_test: эталонный файл не найден — пропуск")
        return True
    src = demo.read_text()
    out = dump_sexp(parse_sexp(src), 0)
    a = _TOKEN.findall(src)
    b = _TOKEN.findall(out)
    ok = a == b
    if verbose:
        print(f"self_test: эталон KiCad — токенов {len(a)}, после round-trip {len(b)}"
              f"  ->  {'СОВПАДАЕТ' if ok else 'РАСХОЖДЕНИЕ'}")
        if not ok:
            import difflib
            for i, (x, y) in enumerate(zip(a, b)):
                if x != y:
                    print(f"  первое расхождение на токене {i}:")
                    print("    эталон:", " ".join(a[max(0, i - 6):i + 6]))
                    print("    наш    :", " ".join(b[max(0, i - 6):i + 6]))
                    break
    return ok


def root_uuid(node: S) -> str:
    """UUID корня листа (нужен для .kicad_pro -> sheets)."""
    return str(node.val("uuid"))


def write_sch(node: S, path: Path):
    path.parent.mkdir(parents=True, exist_ok=True)
    body = dump_sexp(node, 0)
    path.write_text(body + "\n", encoding="utf-8")
    return path


MINIMAL_PRO = {
    "board": {"design_settings": {}},
    "meta": {"filename": "brig_receiver.kicad_pro", "version": 3},
    "net_settings": {},
    "pcbnew": {"page_layout_descr_file": ""},
    "schematic": {
        "legacy_lib_dir": "", "legacy_lib_list": [],
        "meta": {"version": 1},
        "page_layout_descr_file": "",
    },
    "sheets": [],
    "text_variables": {},
}
