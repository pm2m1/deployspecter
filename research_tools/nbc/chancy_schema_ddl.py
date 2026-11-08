"""ds-nbc/1, chancy: the DDL a pinned chancy tree's own migrations apply, read statically (never executed).

    .venv/bin/python research_tools/nbc/chancy_schema_ddl.py ARCHIVE.tar

Parses ``chancy/migrations/v*.py`` of the archive with ``ast`` (data only: no third-party code is imported or run),
and, for each ``up()`` in version order, prints every literal SQL statement passed to ``cursor.execute`` whose text
starts with CREATE, ALTER or DROP, with ``sql.Identifier`` placeholders resolved for the default prefix ``chancy_``.
Data migrations (UPDATE) and catalogue probes (SELECT) are left out. The integrations embed this output verbatim as
the R1 schema in ``MUSE_CONTEXT``; their tests check that it is regenerated exactly from the pinned R1 archive.
"""

from __future__ import annotations

import ast
import re
import sys
import tarfile
from pathlib import Path

PREFIX = "chancy_"
DDL = re.compile(r"^\s*(CREATE|ALTER|DROP)\b", re.I)


def _str(node: ast.AST, local: dict[str, str]) -> str | None:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.Name):
        return local.get(node.id)
    if isinstance(node, ast.JoinedStr):
        out = []
        for v in node.values:
            if isinstance(v, ast.Constant):
                out.append(v.value)
            elif isinstance(v, ast.FormattedValue) and ast.unparse(v.value) == "migrator.prefix":
                out.append(PREFIX)
            else:
                return None
        return "".join(out)
    return None


def _identifier(node: ast.AST, local: dict[str, str]) -> str | None:
    if isinstance(node, ast.Call) and ast.unparse(node.func) == "sql.Identifier" and len(node.args) == 1:
        s = _str(node.args[0], local)
        return None if s is None else '"' + s.replace('"', '""') + '"'
    return None


def statements(source: str) -> list[str]:
    tree = ast.parse(source)
    out: list[str] = []
    for fn in ast.walk(tree):
        if not (isinstance(fn, ast.AsyncFunctionDef) and fn.name == "up"):
            continue
        local: dict[str, str] = {}
        for node in ast.walk(fn):
            if isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name):
                if (s := _str(node.value, local)) is not None:
                    local[node.targets[0].id] = s
        for node in ast.walk(fn):
            if not (isinstance(node, ast.Call) and ast.unparse(node.func) == "cursor.execute" and node.args):
                continue
            q = node.args[0]
            text = None
            if isinstance(q, ast.Call) and isinstance(q.func, ast.Attribute) and q.func.attr == "format" and \
                    isinstance(q.func.value, ast.Call) and ast.unparse(q.func.value.func) == "sql.SQL":
                lit = _str(q.func.value.args[0], local)
                kw = {k.arg: _identifier(k.value, local) for k in q.keywords}
                if lit is not None and None not in kw.values():
                    text = lit.format(**kw)
            elif isinstance(q, ast.Call) and ast.unparse(q.func) == "sql.SQL":
                lit = _str(q.args[0], local)
                text = None if lit is None else lit.replace("{{", "{").replace("}}", "}")
            else:
                text = _str(q, local)
            if text is not None and DDL.match(text):
                out.append("\n".join(line.rstrip() for line in _dedent(text).strip().splitlines()))
    return out


def _dedent(text: str) -> str:
    import textwrap
    return textwrap.dedent(text)


def ddl_from_archive(archive: Path) -> str:
    parts = []
    with tarfile.open(archive) as t:
        names = sorted((int(m.group(1)), n) for n in t.getnames()
                       if (m := re.fullmatch(r"chancy/migrations/v(\d+)\.py", n)))
        for v, n in names:
            f = t.extractfile(n)
            assert f is not None
            for s in statements(f.read().decode()):
                parts.append(f"-- migration v{v}\n{s.rstrip(';')};")
    return "\n\n".join(parts) + "\n"


if __name__ == "__main__":
    sys.stdout.write(ddl_from_archive(Path(sys.argv[1])))
