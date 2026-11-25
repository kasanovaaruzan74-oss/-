import argparse
import ast
from html import escape
from pathlib import Path
import subprocess


TITLES = {
    "roles": "Рөлдер",
    "departments": "Бөлімдер",
    "users": "Пайдаланушылар",
    "document_types": "Құжат түрлері",
    "document_statuses": "Құжат мәртебелері",
    "documents": "Құжаттар",
    "document_executors": "Орындаушылар",
    "document_approvals": "Келісімдер",
    "document_history": "Өзгерістер тарихы",
    "document_comments": "Түсініктемелер",
    "attachments": "Тіркемелер",
    "user_credentials": "Құпиясөздер",
    "login_sessions": "Кіру сессиялары",
}
CARD_HEAD = 79
ROW = 24


def model_fields(model_path):
    tables = {}
    source = ast.parse(model_path.read_text(encoding="utf-8"))
    for model in source.body:
        if not isinstance(model, ast.ClassDef):
            continue
        table = next(
            (
                item.value.value
                for item in model.body
                if isinstance(item, ast.Assign)
                and any(isinstance(target, ast.Name) and target.id == "__tablename__" for target in item.targets)
            ),
            None,
        )
        if table is None:
            continue
        fields = []
        for item in model.body:
            if not isinstance(item, ast.AnnAssign):
                continue
            value = item.value
            keys = {}
            if isinstance(value, ast.Call):
                if isinstance(value.func, ast.Name) and value.func.id == "Relationship":
                    continue
                keys = {argument.arg: argument.value for argument in value.keywords}
            marks = []
            if "primary_key" in keys and ast.literal_eval(keys["primary_key"]):
                marks.append("PK")
            if "foreign_key" in keys:
                marks.append("FK")
            if "unique" in keys and ast.literal_eval(keys["unique"]):
                marks.append("UNIQUE")
            fields.append((item.target.id, " / ".join(marks)))
        tables[table] = fields
    return tables


def text(x, y, content, css="field", anchor="start"):
    return f'<text x="{x}" y="{y}" class="{css}" text-anchor="{anchor}">{escape(content)}</text>'


def polyline(points, dashed=False):
    positions = " ".join(f"{x},{y}" for x, y in points)
    modifier = " optional" if dashed else ""
    return f'<polyline class="line{modifier}" points="{positions}"/>'


def circle(x, y):
    return f'<circle class="junction" cx="{x}" cy="{y}" r="3.3"/>'


def card(table, fields, bounds):
    x, y, width = bounds
    height = card_height(fields)
    parts = [
        f'<g id="{table}">',
        f'<rect class="shadow-soft" x="{x - 2}" y="{y + 4}" width="{width + 4}" height="{height + 4}" rx="15"/>',
        f'<rect class="shadow-close" x="{x}" y="{y + 3}" width="{width}" height="{height + 1}" rx="13"/>',
        f'<rect class="card" x="{x}" y="{y}" width="{width}" height="{height}" rx="13"/>',
        text(x + 24, y + 34, TITLES[table], "card-title"),
        text(x + 24, y + 59, table, "table-name"),
        f'<path class="divider" d="M {x + 24},{y + 70} H {x + width - 24}"/>',
    ]
    for index, (field, mark) in enumerate(fields):
        row_y = y + CARD_HEAD + 18 + index * ROW
        marker = f'<tspan class="key">  ({escape(mark)})</tspan>' if mark else ""
        parts.append(f'<text xml:space="preserve" x="{x + 24}" y="{row_y}" class="field">{escape(field)}{marker}</text>')
    parts.append("</g>")
    return "\n".join(parts)


def card_height(fields):
    return CARD_HEAD + ROW * len(fields) + 12


def svg(width, height, title, legend, lines, labels, cards):
    return "\n".join([
        '<?xml version="1.0" encoding="UTF-8"?>',
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}" role="img">',
        f'<title>{escape(title)}</title>',
        f'<desc>{escape(legend)}</desc>',
        """<defs>
    <linearGradient id="card-fill" x1="0%" y1="0%" x2="0%" y2="100%">
        <stop offset="0%" stop-color="#f3f6fa"/>
        <stop offset="100%" stop-color="#e6ecf3"/>
    </linearGradient>
</defs>
<style>
    .title { font: 700 65px 'Noto Serif Display', 'DejaVu Serif', serif; fill: #101722; }
    .legend { font: 500 20px 'Noto Sans', 'DejaVu Sans', sans-serif; fill: #293645; }
    .card-title { font: 700 26px 'Noto Sans', 'DejaVu Sans', sans-serif; fill: #142031; }
    .table-name { font: 500 18px 'Noto Sans', 'DejaVu Sans', sans-serif; fill: #536579; }
    .field { font: 500 19px 'Noto Sans', 'DejaVu Sans', sans-serif; fill: #172336; }
    .key { font-weight: 700; fill: #233c58; }
    .card { fill: url(#card-fill); stroke: #c5cfdb; stroke-width: 1.4; }
    .shadow-soft { fill: #48607a; opacity: 0.055; }
    .shadow-close { fill: #48607a; opacity: 0.11; }
    .divider { fill: none; stroke: #bfcbd8; stroke-width: 1.1; }
    .line { fill: none; stroke: #344152; stroke-width: 2.05; stroke-linejoin: round; }
    .optional { stroke-dasharray: 7 5; }
    .junction { fill: #344152; }
    .cardinality { font: 700 18px 'Noto Sans', 'DejaVu Sans', sans-serif; fill: #19283a; }
</style>""",
        f'<rect width="{width}" height="{height}" fill="white"/>',
        text(width // 2, 82, title, "title", "middle"),
        text(width // 2, 122, legend, "legend", "middle"),
        *lines,
        *cards,
        *(text(x, y, value, "cardinality", anchor) for x, y, value, anchor in labels),
        "</svg>",
    ])


def document_diagram(tables):
    bounds = {
        "roles": (54, 193, 280),
        "departments": (401, 193, 320),
        "users": (805, 193, 330),
        "document_types": (1217, 193, 307),
        "document_statuses": (1550, 193, 318),
        "documents": (724, 483, 472),
        "document_executors": (49, 1011, 333),
        "document_approvals": (421, 1011, 333),
        "document_history": (793, 1011, 333),
        "document_comments": (1165, 1011, 333),
        "attachments": (1537, 1011, 333),
    }
    width, height = 1920, 1393
    lines = []
    labels = []

    def route(points, annotations=(), dashed=False):
        lines.append(polyline(points, dashed))
        labels.extend(annotations)

    def bottom(table):
        return bounds[table][1] + card_height(tables[table])

    route([(194, 193), (194, 162), (930, 162), (930, 193)],
          [(207, 185, "1", "start"), (944, 185, "0..N", "start")])
    route([(721, 279), (805, 279)],
          [(733, 270, "0..1", "start"), (793, 300, "0..N", "end")], dashed=True)
    route([(556, bottom("departments")), (556, 625), (724, 625)],
          [(570, bottom("departments") + 22, "0..1", "start"), (710, 615, "0..N", "end")], dashed=True)
    route([(967, bottom("users")), (967, 483)],
          [(954, bottom("users") + 18, "1", "end"), (980, 477, "0..N", "start")])
    route([(1370, bottom("document_types")), (1370, 554), (1196, 554)],
          [(1383, bottom("document_types") + 22, "1", "start"), (1210, 545, "0..N", "start")])
    route([(1722, bottom("document_statuses")), (1722, 672), (1196, 672)],
          [(1735, bottom("document_statuses") + 22, "1", "start"), (1210, 662, "0..N", "start")])

    document_bus = 966
    document_ports = (216, 588, 960, 1332, 1704)
    route([(960, bottom("documents")), (960, document_bus)],
          [(974, bottom("documents") + 21, "1", "start")])
    route([(document_ports[0], document_bus), (document_ports[-1], document_bus)])
    for x in document_ports:
        route([(x, document_bus), (x, 1011)], [(x + 12, 1002, "0..N", "start")])
    for x in document_ports[1:-1]:
        lines.append(circle(x, document_bus))

    user_bus = 1348
    route([(1135, 246), (1170, 246), (1170, 162), (1892, 162), (1892, user_bus), (169, user_bus)],
          [(1148, 236, "1", "start")])
    user_ports = {
        "document_executors": (169, 261),
        "document_approvals": (588,),
        "document_history": (960,),
        "document_comments": (1332,),
        "attachments": (1704,),
    }
    for table, ports in user_ports.items():
        for x in ports:
            optional = table == "document_history"
            route([(x, user_bus), (x, bottom(table))],
                  [(x + 12, bottom(table) + 21, "0..N", "start")], dashed=optional)
            if optional:
                labels.append((x + 12, user_bus - 11, "0..1", "start"))
            if x != 169:
                lines.append(circle(x, user_bus))

    title = "«Деканат» дерекқорының ER сызбасы"
    legend = "PK — бастапқы кілт     ·     FK — сыртқы кілт     ·     UNIQUE — бірегей мән     ·     1:0..N — бірден көпке     ·     0..1 — міндетті емес байланыс"
    cards = [card(table, tables[table], box) for table, box in bounds.items()]
    return svg(width, height, title, legend, lines, labels, cards)


def security_diagram(tables):
    bounds = {"users": (48, 194, 326), "user_credentials": (478, 194, 330), "login_sessions": (912, 194, 330)}
    users = [(name, kind) for name, kind in tables["users"] if name in {"user_id", "full_name", "email", "is_active"}]
    diagram_fields = {**tables, "users": users}
    user_bottom = bounds["users"][1] + card_height(diagram_fields["users"])
    session_bottom = bounds["login_sessions"][1] + card_height(diagram_fields["login_sessions"])
    lines = [
        polyline([(374, 271), (478, 271)]),
        polyline([(212, user_bottom), (212, 444), (1077, 444), (1077, session_bottom)]),
    ]
    labels = [
        (387, 262, "1", "start"), (463, 291, "0..1", "end"),
        (226, user_bottom + 21, "1", "start"), (1091, session_bottom + 21, "0..N", "start"),
    ]
    title = "Пайдаланушының кіру деректері"
    legend = "PK — бастапқы кілт     ·     FK — сыртқы кілт     ·     UNIQUE — бірегей мән"
    cards = [card(table, diagram_fields[table], box) for table, box in bounds.items()]
    return svg(1290, 488, title, legend, lines, labels, cards)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("models", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    tables = model_fields(args.models)
    for name, image, width in [
        ("deanflow-er", document_diagram(tables), 2240),
        ("deanflow-auth-er", security_diagram(tables), 1600),
    ]:
        svg_path = args.output / f"{name}.svg"
        png_path = args.output / f"{name}.png"
        svg_path.write_text(image, encoding="utf-8")
        subprocess.run(
            ["inkscape", str(svg_path), "--export-type=png", f"--export-width={width}", f"--export-filename={png_path}"],
            check=True,
        )


if __name__ == "__main__":
    main()
