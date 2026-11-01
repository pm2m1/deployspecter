"""Static extraction of a code bundle's statements and transaction structure.

Python source is only parsed with ``ast``; it is never imported or executed.
Supported shapes: string-literal or module-constant SQL passed to
``x.execute(...)``, ``with x.transaction():`` blocks, ``for`` loops and ``if``
branches. Anything else yields an extraction warning instead of a guess.
"""

from __future__ import annotations

import ast
import re
from dataclasses import dataclass, field

PLACEHOLDER_RE = re.compile(r"%\((\w+)\)s")


@dataclass
class ExtractedStatement:
    statement_id: str
    function: str
    sql: str
    placeholders: list[str]
    txn_block: int | None
    txn_in_loop: bool
    in_loop: bool
    conditional: bool
    # placeholder -> ("row", column) | ("arg", name) | ("const", value) | ("unknown", source text)
    flows: dict[str, tuple[str, object]] = field(default_factory=dict)
    assigns_to: str | None = None


@dataclass
class FunctionModel:
    ref: str
    file: str
    name: str
    args: list[str]
    statements: list[ExtractedStatement]


@dataclass
class SourceModel:
    functions: dict[str, FunctionModel]
    warnings: list[str]

    def statement(self, statement_id: str) -> ExtractedStatement | None:
        for fn in self.functions.values():
            for st in fn.statements:
                if st.statement_id == statement_id:
                    return st
        return None


class _FunctionVisitor:
    def __init__(self, file: str, node: ast.FunctionDef, constants: dict[str, str], warnings: list[str]):
        self.file = file
        self.node = node
        self.constants = constants
        self.warnings = warnings
        self.ref = f"{file}:{node.name}"
        self.args = [a.arg for a in node.args.args + node.args.kwonlyargs]
        self.statements: list[ExtractedStatement] = []
        self.locals: dict[str, dict[str, ast.expr]] = {}
        self.row_vars: set[str] = set()
        self._blocks = 0

    def run(self) -> FunctionModel:
        self._walk(self.node.body, block=None, block_in_loop=False, in_loop=False, cond=False)
        return FunctionModel(self.ref, self.file, self.node.name, self.args, self.statements)

    def _walk(self, body, block, block_in_loop, in_loop, cond) -> None:
        for node in body:
            if isinstance(node, ast.With):
                is_txn = any(
                    isinstance(i.context_expr, ast.Call)
                    and isinstance(i.context_expr.func, ast.Attribute)
                    and i.context_expr.func.attr == "transaction"
                    for i in node.items
                )
                if is_txn:
                    self._blocks += 1
                    self._walk(node.body, self._blocks, in_loop, in_loop, cond)
                else:
                    self._walk(node.body, block, block_in_loop, in_loop, cond)
            elif isinstance(node, ast.For):
                if isinstance(node.target, ast.Name):
                    self.row_vars.add(node.target.id)
                self._walk(node.body, block, block_in_loop, True, cond)
            elif isinstance(node, ast.If):
                self._walk(node.body, block, block_in_loop, in_loop, True)
                self._walk(node.orelse, block, block_in_loop, in_loop, True)
            elif isinstance(node, ast.Try):
                self._walk(node.body, block, block_in_loop, in_loop, cond)
            else:
                self._simple(node, block, block_in_loop, in_loop, cond)

    def _simple(self, node, block, block_in_loop, in_loop, cond) -> None:
        if isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name):
            target = node.targets[0].id
            if isinstance(node.value, ast.Dict):
                self.locals[target] = {
                    k.value: v for k, v in zip(node.value.keys, node.value.values, strict=True)
                    if isinstance(k, ast.Constant)
                }
            if _fetch_kind(node.value) == "one":
                self.row_vars.add(target)
        for call in (c for c in ast.walk(node) if _is_execute(c)):
            self._record(call, block, block_in_loop, in_loop, cond)
        if isinstance(node, ast.Expr) and isinstance(node.value, ast.Call):
            attr = getattr(node.value.func, "attr", "")
            if attr in {"commit", "rollback"}:
                self.warnings.append(f"{self.ref}: explicit {attr}() is not modelled; use conn.transaction()")

    def _record(self, call: ast.Call, block, block_in_loop, in_loop, cond) -> None:
        sql_text = self._sql_of(call.args[0] if call.args else None)
        if sql_text is None:
            self.warnings.append(f"{self.ref}: line {call.lineno}: non-constant SQL was not extracted")
            return
        sql_text = " ".join(sql_text.split())
        idx = len(self.statements)
        placeholders = list(dict.fromkeys(PLACEHOLDER_RE.findall(sql_text)))
        flows = self._flows(call.args[1] if len(call.args) > 1 else None, placeholders)
        self.statements.append(ExtractedStatement(
            statement_id=f"{self.ref}#{idx}", function=self.ref, sql=sql_text,
            placeholders=placeholders, txn_block=block, txn_in_loop=block_in_loop,
            in_loop=in_loop, conditional=cond, flows=flows,
        ))

    def _sql_of(self, node) -> str | None:
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            return node.value
        if isinstance(node, ast.Name):
            return self.constants.get(node.id)
        return None

    def _flows(self, node, placeholders) -> dict[str, tuple[str, object]]:
        mapping: dict[str, ast.expr] = {}
        if isinstance(node, ast.Dict):
            mapping = {
                k.value: v for k, v in zip(node.keys, node.values, strict=True)
                if isinstance(k, ast.Constant)
            }
        elif isinstance(node, ast.Name) and node.id in self.locals:
            mapping = self.locals[node.id]
        return {p: self._classify(mapping[p]) if p in mapping else ("unknown", "") for p in placeholders}

    def _classify(self, expr: ast.expr) -> tuple[str, object]:
        if isinstance(expr, ast.Constant):
            return ("const", expr.value)
        if isinstance(expr, ast.Name) and expr.id in self.args:
            return ("arg", expr.id)
        if (
            isinstance(expr, ast.Subscript)
            and isinstance(expr.value, ast.Name)
            and expr.value.id in self.row_vars
            and isinstance(expr.slice, ast.Constant)
        ):
            return ("row", expr.slice.value)
        return ("unknown", ast.unparse(expr))


def _is_execute(node) -> bool:
    return (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "execute"
    )


def _fetch_kind(node) -> str | None:
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
        return {"fetchone": "one", "fetchall": "all"}.get(node.func.attr)
    return None


def extract_source_model(files: dict[str, str]) -> SourceModel:
    functions: dict[str, FunctionModel] = {}
    warnings: list[str] = []
    for filename, text in sorted(files.items()):
        if not filename.endswith(".py"):
            continue
        tree = ast.parse(text, filename=filename)
        constants = {
            node.targets[0].id: node.value.value
            for node in tree.body
            if isinstance(node, ast.Assign)
            and len(node.targets) == 1
            and isinstance(node.targets[0], ast.Name)
            and isinstance(node.value, ast.Constant)
            and isinstance(node.value.value, str)
        }
        for node in tree.body:
            if isinstance(node, ast.FunctionDef):
                model = _FunctionVisitor(filename, node, constants, warnings).run()
                if model.statements:
                    functions[model.ref] = model
    return SourceModel(functions, warnings)
