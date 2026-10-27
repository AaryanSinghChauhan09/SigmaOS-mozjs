"""Embedded JavaScript interpreter engine for SigmaOS-JS-interpreter / SigmaOS-mozjs
(inspired by cjs, omarchy-mullion, mozjs102).

Principles: split the PURE plan & validate layer (argv building, sandbox
validation, module text generation) from the thin side-effecting execute layer
(self_test never spawns a real interpreter), fail-fast on anything outside a
whitelisted expression subset, least astonishment (a strict subset of JS beats a
clever blacklist).
"""
import argparse, json, re, shlex, subprocess, sys

ENGINE = {
    "name": "sigma-js",
    "summary": "Embedded JS interpreter: plan, sandbox and module tooling",
    "language": "python",
    "verbs": ["eval-plan", "repl-plan", "sandbox", "module"],
    "principles": ["Separation of Concerns", "Defensive Programming",
                   "Fail-Fast", "Least Astonishment", "KISS",
                   "Deterministic Testing"],
}

cli_doc = """\
$ sigma-js eval-plan "Math.max(1, 41)" --engine mozjs
js102 -e 'Math.max(1, 41)'
$ sigma-js repl-plan --engine cjs
cjs
$ sigma-js sandbox "Math.sqrt(16) + 2"
safe
$ sigma-js sandbox "require('fs').readFileSync('/etc/shadow')"
error: blocked identifier: require
$ sigma-js module --name calc --format cjs
"""

# --------------------------------------------------------------------------- #
#  Plan layer: which argv runs which interpreter (pure data, no I/O)
# --------------------------------------------------------------------------- #
ENGINES = {
    "mozjs": {"bin": "js102", "eval": ["-e"], "repl": []},
    "cjs":   {"bin": "cjs",   "eval": ["-e"], "repl": []},
    "node":  {"bin": "node",  "eval": ["-e"], "repl": ["-i"]},
}

_MAX_CODE = 512
_MAX_TOKENS = 200


class JsError(ValueError):
    pass


def eval_plan(code, engine="mozjs", timeout=10):
    """Build (but never run) the argv that evaluates `code`. Validates first."""
    if engine not in ENGINES:
        raise JsError(f"unknown engine {engine!r} (have: {', '.join(sorted(ENGINES))})")
    ok, why = sandbox_check(code)
    if not ok:
        raise JsError(f"unsafe or invalid code: {why}")
    if not isinstance(timeout, (int, float)) or timeout <= 0:
        raise JsError("timeout must be a positive number")
    spec = ENGINES[engine]
    return {"engine": engine, "code": code, "timeout": timeout,
            "argv": [spec["bin"]] + list(spec["eval"]) + [code]}


def repl_plan(engine="mozjs", timeout=None):
    """Build (but never run) the argv for an interactive interpreter REPL."""
    if engine not in ENGINES:
        raise JsError(f"unknown engine {engine!r} (have: {', '.join(sorted(ENGINES))})")
    spec = ENGINES[engine]
    return {"engine": engine, "timeout": timeout,
            "argv": [spec["bin"]] + list(spec["repl"])}


def execute(plan):
    """THIN side-effecting layer: spawn the planned interpreter.

    Never invoked by self_test -- FIRST tests stay isolated from the system.
    """
    return subprocess.run(plan["argv"], capture_output=True, text=True,
                          timeout=plan.get("timeout"))


# --------------------------------------------------------------------------- #
#  Sandbox: a tiny whitelisted *expression* validator (pure)
# --------------------------------------------------------------------------- #
_WS = r"[ \t\r\n]+"
_NUM = r"\d+(?:\.\d+)?(?:[eE][+-]?\d+)?"
_STR = r"'(?:\\.|[^'\\])*'|\"(?:\\.|[^\"\\])*\""
_ID = r"[A-Za-z_$][A-Za-z0-9_$]*"
_OP = r"(?:===|!==|==|!=|<=|>=|&&|\|\||\+\+|--|=>|[-+*/%!<>=&|?:.])"
_PUNCT = r"[()\[\],]"

_TOKEN = re.compile(f"(?P<ws>{_WS})|(?P<num>{_NUM})|(?P<str>{_STR})"
                    f"|(?P<id>{_ID})|(?P<op>{_OP})|(?P<punct>{_PUNCT})")
_STR_RE = re.compile(f"(?:{_STR})")

_UNARY = {"!", "+", "-", "++", "--"}
_VALUE_KINDS = ("num", "str", "id")

# Operators of the accepted JS expression subset (no assignment, no arrows).
_ALLOWED_OPS = {"+", "-", "*", "/", "%", "!", "<", ">", "<=", ">=", "==",
                "===", "!=", "!==", "&&", "||", "?", ":", ".", "++", "--"}

# Keywords / globals that must never appear, even as a bare identifier.
_BLOCKED = {
    "eval", "function", "constructor", "prototype", "__proto__", "require",
    "module", "exports", "process", "global", "globalthis", "window",
    "document", "self", "fetch", "xmlhttprequest", "import", "export", "new",
    "delete", "void", "typeof", "with", "this", "super", "class", "return",
    "if", "else", "for", "while", "do", "switch", "case", "break", "continue",
    "throw", "try", "catch", "finally", "yield", "await", "async", "let",
    "const", "var", "settimeout", "setinterval", "queuemicrotask", "proxy",
    "reflect", "arguments", "setprototypeof", "getprototypeof",
    "defineproperty", "regeneratoruntime",
}
_BLOCKED_PROPS = {"constructor", "prototype", "__proto__"}

# Functions callable without a receiver, and per-receiver static namespaces.
_SAFE_CALLS = {"parseInt", "parseFloat", "isFinite", "isNaN", "String",
               "Number", "Boolean", "Array", "Object", "Date"}
_MEMBER_SETS = {
    "Math": {"abs", "ceil", "floor", "round", "sqrt", "cbrt", "pow", "min",
             "max", "log", "log2", "log10", "exp", "sin", "cos", "tan",
             "asin", "acos", "atan", "atan2", "sign", "trunc", "hypot",
             "random"},
    "JSON": {"parse", "stringify"},
    "Object": {"keys", "values", "entries", "freeze", "is", "fromEntries"},
    "Date": {"now", "parse", "UTC"},
    "Number": {"isInteger", "isFinite", "isNaN", "parseFloat", "parseInt"},
    "String": {"fromCharCode", "raw"},
    "Array": {"isArray", "from", "of"},
}
# Methods callable on any receiver (read-only or trivially local mutation).
_ALLOWED_METHODS = {
    "toString", "valueOf", "toFixed", "toPrecision", "toExponential",
    "slice", "substring", "substr", "indexOf", "lastIndexOf", "includes",
    "join", "split", "trim", "trimStart", "trimEnd", "concat", "repeat",
    "replace", "replaceAll", "charAt", "charCodeAt", "startsWith",
    "endsWith", "padStart", "padEnd", "toUpperCase", "toLowerCase",
    "map", "filter", "reduce", "reduceRight", "find", "findIndex", "some",
    "every", "forEach", "push", "pop", "shift", "unshift", "flat",
    "flatMap", "at", "keys", "values", "entries", "hasOwnProperty",
}


class SandboxError(JsError):
    pass


def _tokenize(code):
    pos, out = 0, []
    while pos < len(code):
        m = _TOKEN.match(code, pos)
        if not m:
            raise SandboxError(f"illegal character {code[pos]!r} at index {pos}")
        pos = m.end()
        if m.lastgroup != "ws":
            out.append((m.lastgroup, m.group()))
    return out


def sandbox_check(code):
    """Return (ok, reason). `ok` True means: inside the whitelisted subset."""
    if not isinstance(code, str) or not code.strip():
        return False, "empty expression"
    if len(code) > _MAX_CODE:
        return False, f"expression longer than {_MAX_CODE} characters"
    # Block-terminating characters are banned outside string literals.
    stripped = _STR_RE.sub("", code)
    for ch, why in ((";", "statements are not allowed"),
                    ("`", "template literals are not allowed"),
                    ("{", "blocks are not allowed"),
                    ("}", "blocks are not allowed")):
        if ch in stripped:
            return False, why
    try:
        toks = _tokenize(code)
    except SandboxError as e:
        return False, str(e)
    if not toks:
        return False, "empty expression"
    if len(toks) > _MAX_TOKENS:
        return False, f"too many tokens (max {_MAX_TOKENS})"

    # 1) operator whitelist (kills assignment, arrows, regexes, ...)
    for kind, val in toks:
        if kind == "op" and val not in _ALLOWED_OPS:
            return False, f"operator not allowed: {val}"

    # 2) blocked identifiers/properties, no bracket member access
    for i, (kind, val) in enumerate(toks):
        if kind == "id":
            if val.lower() in _BLOCKED:
                return False, f"blocked identifier: {val}"
            if i and toks[i - 1] == ("op", ".") and val in _BLOCKED_PROPS:
                return False, f"blocked property: {val}"
        if kind == "punct" and val == "[" and i:
            pk, pv = toks[i - 1]
            if pk in _VALUE_KINDS or (pk == "punct" and pv in (")", "]")):
                return False, "bracket member access is not allowed"

    # 3) balanced () / []
    stack = []
    for kind, val in toks:
        if kind != "punct":
            continue
        if val in "([":
            stack.append(val)
        elif val == ")":
            if not stack or stack.pop() != "(":
                return False, "unbalanced ')'"
        elif val == "]":
            if not stack or stack.pop() != "[":
                return False, "unbalanced ']'"
    if stack:
        return False, "unbalanced brackets"

    # 4) alternation: value, operator, value, ...
    for i, (kind, val) in enumerate(toks):
        prev = toks[i - 1] if i else None
        pk, pv = prev if prev else (None, None)
        if prev is None:
            if not (kind in _VALUE_KINDS
                    or (kind == "op" and val in _UNARY)
                    or (kind == "punct" and val in "([")):
                return False, f"expression cannot start with {val!r}"
            continue
        valish, pvalish = kind in _VALUE_KINDS, pk in _VALUE_KINDS
        if valish and pvalish:
            return False, f"missing operator before {val!r}"
        if valish and pk == "punct" and pv in (")", "]"):
            return False, f"missing operator before {val!r}"
        if kind == "op" and pk == "op" and val not in _UNARY:
            return False, f"unexpected operator: {val!r}"
        if kind == "punct" and val == "(":
            if pk == "op" and pv == ".":
                return False, "expected a property name after '.'"
            if pvalish and pk != "id":
                return False, f"unexpected '(' after {pv!r}"
            if pk == "punct" and pv not in "(,":
                return False, f"unexpected '(' after {pv!r}"
        elif kind == "punct" and val in ",)]":
            if pk == "op":
                return False, f"dangling {pv!r}"
            if pk == "punct" and pv in "(,":
                # f() is a legal call; an empty group () is not
                if val == ")" and i >= 2 and toks[i - 2][0] == "id":
                    pass  # empty argument list of a call
                else:
                    return False, "missing value"

    # 5) trailing token must produce a value
    lk, lv = toks[-1]
    if not (lk in _VALUE_KINDS or (lk == "punct" and lv in ")]")
            or (lk == "op" and lv in ("++", "--"))):
        return False, f"unexpected end after {lv!r}"

    # 6) call-target whitelist
    for i, (kind, val) in enumerate(toks):
        if kind != "punct" or val != "(" or i == 0:
            continue
        pk, pv = toks[i - 1]
        if pk != "id":
            continue  # grouping; already validated structurally
        if i >= 2 and toks[i - 2] == ("op", "."):
            base = toks[i - 3][1] if i >= 3 and toks[i - 3][0] == "id" else None
            if base in _MEMBER_SETS:
                if pv not in _MEMBER_SETS[base]:
                    return False, f"{base}.{pv}() is not allowed"
            elif pv not in _ALLOWED_METHODS:
                return False, f"method not allowed: .{pv}()"
        elif pv not in _SAFE_CALLS:
            return False, f"unknown function: {pv}"

    return True, "safe"


def is_safe(code) -> bool:
    return sandbox_check(code)[0]


# --------------------------------------------------------------------------- #
#  Module wrapper generation (pure)
# --------------------------------------------------------------------------- #
_MODULE_NAME = re.compile(r"^[A-Za-z_$][A-Za-z0-9_$]*$")


def module_text(name, fmt="cjs", entry="main"):
    """Generate a deterministic module wrapper for `name` (cjs or esm)."""
    if not isinstance(name, str) or not _MODULE_NAME.match(name):
        raise JsError(f"invalid module name: {name!r}")
    if not isinstance(entry, str) or not _MODULE_NAME.match(entry):
        raise JsError(f"invalid entry point: {entry!r}")
    if fmt not in ("cjs", "esm"):
        raise JsError(f"unknown module format {fmt!r} (cjs|esm)")
    meta = f'Object.freeze({{"name": {json.dumps(name)}}})'
    if fmt == "cjs":
        return (
            "// generated by sigma-js -- do not edit\n"
            '"use strict";\n\n'
            f"const meta = {meta};\n\n"
            f"function {entry}(args) {{\n"
            f"  return meta.name;\n"
            "}\n\n"
            f"module.exports = {{ meta, {entry} }};\n"
        )
    return (
        "// generated by sigma-js -- do not edit\n"
        f"export const meta = {meta};\n\n"
        f"export function {entry}(args) {{\n"
        f"  return meta.name;\n"
        "}\n\n"
        f"export default {{ meta, {entry} }};\n"
    )


# --------------------------------------------------------------------------- #
#  CLI
# --------------------------------------------------------------------------- #
def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog=ENGINE["name"], description=ENGINE["summary"])
    sub = p.add_subparsers(dest="cmd", required=True)
    ep = sub.add_parser("eval-plan", help="build the argv that evaluates JS code")
    ep.add_argument("code", help="JavaScript expression (safe subset)")
    ep.add_argument("--engine", default="mozjs", choices=sorted(ENGINES))
    ep.add_argument("--timeout", type=float, default=10.0)
    rp = sub.add_parser("repl-plan", help="build the argv for an interactive REPL")
    rp.add_argument("--engine", default="mozjs", choices=sorted(ENGINES))
    sb = sub.add_parser("sandbox", help="validate an expression against the safe subset")
    sb.add_argument("code")
    md = sub.add_parser("module", help="generate a module wrapper")
    md.add_argument("--name", required=True)
    md.add_argument("--format", default="cjs", choices=("cjs", "esm"))
    md.add_argument("--entry", default="main")
    return p


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.cmd == "eval-plan":
            print(shlex.join(eval_plan(args.code, args.engine, args.timeout)["argv"]))
        elif args.cmd == "repl-plan":
            print(shlex.join(repl_plan(args.engine)["argv"]))
        elif args.cmd == "sandbox":
            ok, why = sandbox_check(args.code)
            if not ok:
                print(f"error: {why}", file=sys.stderr)
                return 1
            print(why)
        elif args.cmd == "module":
            print(module_text(args.name, args.format, args.entry), end="")
        return 0
    except JsError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1


def self_test() -> None:
    # --- plan layer (pure) -------------------------------------------------
    p = eval_plan("Math.max(1, 41)", engine="mozjs")
    assert p["argv"] == ["js102", "-e", "Math.max(1, 41)"], p["argv"]
    p = eval_plan("1 + 2", engine="cjs", timeout=5)
    assert p["argv"][:2] == ["cjs", "-e"] and p["timeout"] == 5
    assert repl_plan("node")["argv"] == ["node", "-i"]
    assert repl_plan("mozjs")["argv"] == ["js102"]
    for bad in ((eval_plan, ("1 + 2", "nope-engine")),
                (eval_plan, ("require('fs')",)),
                (eval_plan, ("1;",)), (repl_plan, ("nope",))):
        try:
            bad[0](*bad[1])
        except JsError:
            pass
        else:
            raise AssertionError(f"should have rejected: {bad}")

    # --- sandbox accepts the safe subset -----------------------------------
    for good in ("1 + 2 * 3", "Math.max(1, 41)", "Math.sqrt(16) + 2",
                 "JSON.parse('{\"a\":1}').a", "parseInt('42', 10)",
                 "names.join(',')", "items.map(f).join('')", "x + 1",
                 "[1, 2, 3].length", "(1 + 2) * 3", "!flag && ready",
                 "a ? b : c", "1 +-2", "'a' + 'b'", "s.trim().toLowerCase()"):
        ok, why = sandbox_check(good)
        assert ok, f"{good!r} should be safe: {why}"

    # --- sandbox rejects everything dangerous / malformed ------------------
    for bad in ("require('fs')", "eval(1)", "process.exit()", "x.constructor",
                "a.b.constructor('x')", "foo(1)", "Math.secret()", "a; b",
                "`${x}`", "a = 1", "x[0]", "1 +", "(1)(2)", "while(1){}",
                "alert(1)", "x?.y", "new Date()", "import('os')",
                "obj.__proto__", "#!/bin/sh", "", "   ",
                "(" * 3 + ")" * 2, "a b"):
        ok, why = sandbox_check(bad)
        assert not ok, f"{bad!r} should be rejected ({why})"
    ok, why = sandbox_check("x" * 600)
    assert not ok and "longer" in why, why

    # --- module generation (pure) ------------------------------------------
    cjs = module_text("calc", "cjs")
    assert '"use strict"' in cjs and "module.exports" in cjs
    assert '"name": "calc"' in cjs and "function main(args)" in cjs
    esm = module_text("calc", "esm")
    assert "export const meta" in esm and "export default" in esm
    assert module_text("calc", "cjs") == cjs  # deterministic
    for bad in ((module_text, ("bad name",)), (module_text, ("1bad",)),
                (module_text, ("calc", "amd")), (module_text, ("calc", "cjs", "0x"))):
        try:
            bad[0](*bad[1])
        except JsError:
            pass
        else:
            raise AssertionError(f"should have rejected: {bad}")


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
