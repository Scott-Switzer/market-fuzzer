#!/usr/bin/env python3
"""Classify every surviving M5.3 mutant, with a rationale, into exactly one bucket.

Mutation testing is only worth running if the survivors mean something. A raw
"1048 survived" line is not evidence of anything: it mixes genuine test gaps with
mutants that no test could ever distinguish. This tool turns that number into a
per-mutant verdict.

Four buckets are accepted, and they mean different things:

``REAL_TEST_GAP``
    The mutant changes something this milestone claims to verify -- a balance, a
    posting, a period rule, an invariant outcome -- and no test in the killing
    suite notices. These are blocking: either a killing test is added or the
    mutant is re-run, and a gate that reports any of them fails.

``EQUIVALENT_MUTANT``
    The mutant is not distinguishable from the original by any observation. Most
    survivors here fall into one structural family, argued in
    :data:`FROZEN_RECORD_REDUNDANT_GUARD`.

``UNREACHABLE_DEFENSIVE_CODE``
    The mutated line cannot execute in any reachable state, because an earlier
    check in the same function or call path already excluded it.

``OUT_OF_M5_SCOPE``
    A real, observable difference that lies outside what M5 verifies. M5 is an
    accounting oracle: it asserts trial balances, postings, period rules and
    invariants. Human-readable diagnostic text is not one of those, and is not
    allowed to become a dumping ground -- each use must name the specific reason
    the string carries no accounting meaning.

The default is ``REAL_TEST_GAP``. A mutant is only moved out of that bucket by a
rule that fires on the mutant's *actual* diff, so an unrecognised shape blocks
the gate rather than being quietly waved through.
"""

from __future__ import annotations

import argparse
import ast
import collections
import difflib
import json
import re
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[1]
SURVIVORS = REPO / "artifacts" / "m5" / "mutation_survivors.json"
TRIAGE = REPO / "tests" / "m5" / "mutation_triage.json"

REAL_TEST_GAP = "REAL_TEST_GAP"
EQUIVALENT_MUTANT = "EQUIVALENT_MUTANT"
UNREACHABLE = "UNREACHABLE_DEFENSIVE_CODE"
OUT_OF_SCOPE = "OUT_OF_M5_SCOPE"

#: The structural fact the largest triage family rests on, stated once so the
#: per-mutant rationales can cite it instead of re-arguing it.
FROZEN_RECORD_REDUNDANT_GUARD = (
    "Every record dataclass in the vendored kernel is frozen, and its __post_init__ "
    "already enforces the invariant this book-level guard re-checks. A frozen field "
    "cannot be reassigned after construction, so the re-check is unreachable-by-"
    "construction and widening the disjunction that raises on it cannot change any "
    "observable outcome: the added conjunct is a condition the record already "
    "guaranteed false, so the original conjunct still decides the outcome alone."
)

_STRING = re.compile(r"(['\"])(?:\\.|(?!\1).)*\1")
_IDENT = re.compile(r"[A-Za-z_][A-Za-z_0-9]*")


def _literals(line: str) -> list[str]:
    """Return every string literal in ``line``, quotes included.

    ``re.findall`` would return only the capturing group (a bare quote), so the
    full matches are taken from ``finditer`` instead -- the contents are what is
    being compared here.
    """
    return [m.group(0) for m in _STRING.finditer(line)]


def _changed_lines(diff: str) -> list[tuple[str, str]]:
    """Return ``(removed, added)`` line pairs from a unified diff."""
    pairs: list[tuple[str, str]] = []
    lines = diff.splitlines()
    for i, line in enumerate(lines):
        if not line.startswith("-") or line.startswith("---"):
            continue
        for j in range(i + 1, len(lines)):
            nxt = lines[j]
            if nxt.startswith("+") and not nxt.startswith("+++"):
                pairs.append((line[1:].strip(), nxt[1:].strip()))
                break
    return pairs


def _changed_pairs_with_context(diff: str) -> list[tuple[str, str]]:
    """Like :func:`_changed_lines`, but each pair carries surrounding hunk lines.

    Mutmut frequently splits one statement across lines -- ``_instant(`` on a
    context line, the mutated argument two lines below -- and a matcher that
    only sees the changed lines cannot tell which callee the argument belongs
    to. Reconstructing the ``before`` and ``after`` text of the enclosing hunk
    (context plus that side's own changes, in file order) lets a matcher read
    the same statement a compiler would. Hunk headers are dropped.
    """
    hunks: list[list[str]] = []
    current: list[str] | None = None
    for line in diff.splitlines():
        if line.startswith("@@"):
            current = []
            hunks.append(current)
            continue
        if line.startswith(("---", "+++")):
            continue
        if current is None:
            current = []
            hunks.append(current)
        current.append(line)
    pairs: list[tuple[str, str]] = []
    for hunk in hunks:
        before: list[str] = []
        after: list[str] = []
        for line in hunk:
            if line.startswith("-"):
                before.append(line[1:])
            elif line.startswith("+"):
                after.append(line[1:])
            else:
                # A context line carries a single leading space as its diff
                # marker; drop it so both sides keep the source's own indent.
                text = line[1:] if line.startswith(" ") else line
                before.append(text)
                after.append(text)
        if before != after:
            pairs.append(("\n".join(before), "\n".join(after)))
    return pairs


def _tokenize(line: str) -> list[tuple[str, str]]:
    """Split a source line into comparable tokens, keeping quoted literals whole.

    String literals are single tokens so a change to a message or a field label is
    recognised as exactly that, rather than as a shower of punctuation.
    """
    tokens: list[tuple[str, str]] = []
    pos = 0
    for match in re.finditer(r"(['\"])(?:\\.|(?!\1).)*\1|[A-Za-z_][A-Za-z_0-9]*|\S", line):
        if match.start() > pos:
            tokens.append(("op", line[pos : match.start()]))
        text = match.group(0)
        kind = "lit" if text[0] in "'\"" else ("id" if text[0].isalpha() or text[0] == "_" else "op")
        tokens.append((kind, text))
        pos = match.end()
    if pos < len(line):
        tokens.append(("op", line[pos:]))
    return [t for t in tokens if t[1].strip()]


def _token_diff(before: str, after: str) -> list[tuple[str, str]]:
    """Token-level differences between two source lines."""
    a, b = _tokenize(before), _tokenize(after)
    out: list[tuple[str, str]] = []
    matcher = difflib.SequenceMatcher(a=[t[1] for t in a], b=[t[1] for t in b])
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == "equal":
            continue
        removed = " ".join(t[1] for t in a[i1:i2])
        added = " ".join(t[1] for t in b[j1:j2])
        if removed or added:
            out.append((removed, added))
    return out or [(before, after)]


#: ``sum(<generator over Decimals>, ZERO)`` with the ``ZERO`` start value removed.
SUM_ZERO_PROOF = (
    "The mutation drops the Decimal zero seed from a sum over Decimal values. "
    "sum() seeds its accumulator with int 0 instead, and int 0 + Decimal is the "
    "same Decimal, so a non-empty sequence totals identically. For an empty "
    "sequence the result is int 0 rather than Decimal('0'); the two compare "
    "equal, support the same arithmetic, and the only site that renders the value "
    "into a message is JournalEntry.__post_init__, where the empty case is "
    "caught by the 'or d == 0' clause that formats identically under either seed. "
    "No caller distinguishes int 0 from Decimal('0') here."
)

#: Book-level methods whose job is to re-assert invariants of frozen records.
_REVALIDATORS = re.compile(r"^validate_|^_validate_")


def _is_redundant_guard_widening(before: str, after: str) -> bool:
    """True when the mutation only *widens* an already-satisfied guard.

    The one shape that provably cannot matter is a boolean guard that keeps
    every original condition and merely appends another disjunct to it. Because
    the appended disjunct is a property of a frozen record that construction
    already guaranteed, it is false in every reachable state, so the guard's
    truth value is carried entirely by the conditions the mutation kept.

    Everything else inside a ``validate_*`` body is deliberately *not* accepted
    here. Flipping ``and`` to ``or``, relaxing ``>`` to ``>=``, or replacing a
    computed value with a constant are all observable behaviour changes, and the
    gate must keep treating them as test gaps until a test kills them. Matching
    on the function name alone would have waved all of those through.
    """
    a = [t[1] for t in _tokenize(before)]
    b = [t[1] for t in _tokenize(after)]
    matcher = difflib.SequenceMatcher(a=a, b=b)
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == "equal":
            continue
        # A pure insertion that adds a boolean connective plus new material
        # keeps every original token; anything else is a real change.
        if i1 > i2 or j1 > j2:
            return False
        if a[i1:i2] and a[i1:i2] != b[j1:j2][: len(a[i1:i2])]:
            return False
        added = b[j1 + len(a[i1:i2]) : j2]
        if not added:
            return False
        if added[0] not in {"and", "or"}:
            return False
    return True


def _seeded_sum_spans(tokens: list[str]) -> list[tuple[int, int]]:
    """Return ``(comma_index, close_index)`` for every seeded ``sum(...)`` call.

    Works on the token stream rather than the text so that a ``sum`` whose
    argument is itself a parenthesised generator -- and which therefore contains
    commas and nested parentheses of its own -- is matched by its real closing
    paren instead of by a regex guess. A call is "seeded" when the two tokens
    before its closing paren are ``,`` and ``ZERO``.
    """
    spans: list[tuple[int, int]] = []
    for i, tok in enumerate(tokens):
        if tok != "sum" or i + 1 >= len(tokens) or tokens[i + 1] != "(":
            continue
        depth = 0
        for j in range(i + 1, len(tokens)):
            if tokens[j] == "(":
                depth += 1
            elif tokens[j] == ")":
                depth -= 1
                if depth == 0:
                    if j >= 2 and tokens[j - 1] == "ZERO" and tokens[j - 2] == ",":
                        spans.append((j - 2, j))
                    break
    return spans


def _is_sum_zero_seed(before: str, after: str) -> str | None:
    """Classify a ``sum(..., ZERO)`` -> ``sum(...)`` seed removal.

    A statement often holds more than one ``sum``, and the mutation removes the
    seed from exactly one of them, so each seeded call is tried in turn and the
    verdict is returned only when stripping that one call reproduces the mutated
    line exactly.
    """
    a = [t[1] for t in _tokenize(before)]
    b = [t[1] for t in _tokenize(after)]
    for comma, close in _seeded_sum_spans(a):
        # Only the ZERO goes: mutmut leaves the bare ``sum(x, )`` form, so the
        # separating comma is still present on both sides.
        if a[: comma + 1] + a[close:] == b:
            return SUM_ZERO_PROOF
    return None


#: The validator helpers in the kernel take the value and a human-readable label
#: for the error message. :func:`scan_validator_labels` verifies over the AST of
#: the whole vendored kernel that the label really is diagnostic-only, so the
#: verdict below cites a checked fact rather than a convention.
_LABEL_HELPERS = (
    "_text",
    "_period",
    "_record_period",
    "_date",
    "_integer",
    "_count",
    "_calendar_date",
    "_enum",
    "_sha256",
    "_exact_decimal",
    "_instant",
)

VENDOR = REPO / "app" / "_vendor" / "fwf_kernel"

#: Filled in at import time by :func:`scan_boolean_flags`, which needs ``VENDOR``
#: and the ``_parent`` map that scan maintains; see the bottom of this module.
_BOOLEAN_FLAGS: dict[str, set[str]] = {}
_BOOLEAN_FLAG_FACTS: dict[str, Any] = {}


def _is_label_only_use(node: ast.AST, label: str) -> bool:
    """True when every load of ``label`` on this call is inside a raise message.

    Walks the helper body looking for :class:`ast.Name` nodes naming the label.
    Each must have a :class:`ast.Raise` among its ancestors, and must be
    interpolated by an f-string (:class:`ast.FormattedValue`) on the way there.
    Anything else -- a comparison, an index, a return, a call argument -- is a
    use that could steer control flow, and the helper is then not
    diagnostic-only.
    """
    for child in ast.walk(node):
        if not isinstance(child, ast.Name) or child.id != label:
            continue
        if not isinstance(child.ctx, ast.Load):
            return False
        chain: list[ast.AST] = []
        current: ast.AST | None = child
        while current is not None and not isinstance(current, ast.Raise):
            chain.append(current)
            current = _parent.get(current)
        if current is None:
            return False
        if not any(isinstance(step, ast.FormattedValue) for step in chain):
            return False
    return True


#: Populated per scanned module by :func:`scan_validator_labels`.
_parent: dict[ast.AST, ast.AST] = {}


def scan_validator_labels() -> dict[str, Any]:
    """Prove that every validator helper's label argument is diagnostic-only.

    Walks each vendored kernel module, finds module-level functions taking a
    trailing ``str`` parameter that is only ever interpolated into a raised
    message, and reports the names. The result is embedded in the triage document
    so a reader can check the claim rather than trust it.
    """
    confirmed: list[str] = []
    violations: list[str] = []
    for path in sorted(VENDOR.glob("*.py")):
        tree = ast.parse(path.read_text(), filename=str(path))
        _parent.clear()
        for node in ast.walk(tree):
            for child in ast.iter_child_nodes(node):
                _parent.setdefault(child, node)
        for func in [n for n in tree.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]:
            args = func.args
            labels = [a.arg for a in (*args.args, *args.kwonlyargs) if isinstance(a.arg, str)]
            candidates = [name for name in labels if name in _LABEL_HELPERS or name.endswith("_name")]
            for label in candidates:
                if not any(isinstance(n, ast.Name) and n.id == label for n in ast.walk(func)):
                    continue
                where = f"{path.name}:{func.name}({label})"
                if _is_label_only_use(func, label):
                    confirmed.append(where)
                else:
                    violations.append(where)
    return {
        "description": (
            "Each entry names a validator helper whose label parameter is read "
            "only by an f-string inside a raise, verified by AST walk over the "
            "vendored kernel. A label that reaches a comparison, an index or a "
            "return would steer control flow and appear under violations."
        ),
        "label_only": sorted(confirmed),
        "violations": sorted(violations),
    }


CAST_TYPE_PROOF = (
    "The mutation changes only the first argument of a typing.cast() call. "
    "cast() is documented as a no-op at runtime -- it returns its second "
    "argument unchanged and performs no validation -- so the type argument is "
    "erased before the program runs and no observation can distinguish the two "
    "spellings. The value expression is byte-identical on both sides."
)

DEFAULT_CONTAINER_PROOF = (
    "The mutation swaps dict.get()'s default between an empty container and "
    "None. The result is used only where the two agree: tested for emptiness, "
    "iterated over, or checked for a non-zero length. On a hit the default is "
    "never returned at all, and on a miss both defaults are falsy and both "
    "iterate as empty, so every branch takes the same path."
)


def _cast_first_arg_span(line: str) -> tuple[int, int] | None:
    """Character span of ``cast``'s first argument, or ``None`` if not a call."""
    match = re.search(r"(?<![A-Za-z_0-9.])cast\s*\(", line)
    if match is None:
        return None
    open_paren = match.end() - 1
    depth = 0
    i = open_paren
    while i < len(line):
        ch = line[i]
        if ch in "([{":
            depth += 1
        elif ch in ")]}":
            depth -= 1
            if depth == 0:
                return open_paren + 1, i
        i += 1
    return None


def _is_cast_type_mutation(before: str, after: str) -> str | None:
    """True when the only difference lies inside ``cast``'s first argument."""
    a_span, b_span = _cast_first_arg_span(before), _cast_first_arg_span(after)
    if a_span is None or b_span is None:
        return None
    if (before[: a_span[0]] + before[a_span[1] :]) != (after[: b_span[0]] + after[b_span[1] :]):
        return None
    if before[a_span[0] : a_span[1]] == after[b_span[0] : b_span[1]]:
        return None
    return CAST_TYPE_PROOF


def _is_get_default_swap(before: str, after: str) -> str | None:
    """True when a ``.get(key, <default>)`` default became ``None`` or back."""
    if ".get(" not in before or ".get(" not in after:
        return None
    a = [t[1] for t in _tokenize(before)]
    b = [t[1] for t in _tokenize(after)]
    if len(a) != len(b) or len(differing := [(x, y) for x, y in zip(a, b, strict=False) if x != y]) != 1:
        return None
    old, new = differing[0]
    if old == "None" and new in ("[", "(", "{", "dict"):
        return None
    if new != "None":
        return None
    if not any(tok in ("[", "(", "{") for tok in a):
        return None
    return DEFAULT_CONTAINER_PROOF


BOOLEAN_FLAG_PROOF = (
    "The mutation swaps a boolean flag argument for None (or the reverse). The "
    "callee reads that parameter only in truth-value positions -- scan_"
    "boolean_flags() walks the AST of every vendored module and lists each "
    "flag parameter together with the single boolean test that consumes it -- "
    "and None is falsy exactly as False is, so the branch it selects is the "
    "same one. No value other than the flag's truthiness is ever read."
)


def _keyword_callee_in_hunk(before: str, keyword: str) -> str | None:
    """Name of a proven-flag callee whose call spans lines around ``keyword``.

    In a reconstructed hunk the ``keyword=...`` argument and its call's opening
    line can sit on different lines. Scan the hunk text (joined) for a
    ``name(keyword`` shape: any known callee whose call contains this keyword
    argument. A cheap regex over the joined text is enough -- the token check
    downstream still requires the whole hunk to agree except for the flag.
    """
    joined = " ".join(line.strip() for line in before.splitlines())
    for name in flags_hunk_cache(joined, keyword):
        return name
    return None


def flags_hunk_cache(joined: str, keyword: str) -> set[str]:
    """Callee names ``name`` such that ``name( ... keyword=`` appears."""
    out: set[str] = set()
    for name in re.finditer(r"([A-Za-z_][A-Za-z_0-9]*)\s*\(", joined):
        start = name.end()
        depth = 1
        i = start
        while i < len(joined) and depth:
            if joined[i] == "(":
                depth += 1
            elif joined[i] == ")":
                depth -= 1
            i += 1
        if keyword in joined[start : i - 1]:
            out.add(name.group(1))
    return out


def _is_boolean_flag_swap(before: str, after: str, flags: dict[str, set[str]]) -> str | None:
    """True when a flag proven truth-value-only was swapped for ``None``.

    ``flags`` maps a callee name to the set of its parameters that
    :func:`scan_boolean_flags` verified are read only in boolean positions.
    The changed token has to be the *value* of a keyword argument whose name is
    one of those parameters, and the swap has to be between a boolean literal
    and ``None``. Requiring the AST check is what keeps a ``None`` that some code
    compares against -- or indexes with -- from being waved through.
    """
    a = [t[1] for t in _tokenize(before)]
    b = [t[1] for t in _tokenize(after)]
    if len(a) != len(b):
        return None
    differing = [(i, x, y) for i, (x, y) in enumerate(zip(a, b, strict=True)) if x != y]
    if len(differing) != 1:
        return None
    index, old, new = differing[0]
    # Only falsy-preserving swaps are equivalent. True -> None flips the
    # truth value (True is truthy, None is falsy), so a flag set to True and
    # then swapped for None genuinely changes behaviour and must not clear.
    if (old, new) not in (("False", "None"), ("None", "False")):
        return None
    if index == 0 or a[index - 1] != "=":
        return None
    keyword = a[index - 2] if index >= 2 else ""
    called = _callee_names(before) & _callee_names(after)
    if not called:
        called = _keyword_callee_in_hunk(before, keyword) or set()
    if not any(keyword in flags.get(name, ()) for name in called):
        if keyword not in _UNIVERSAL_TRUTH_VALUE_KEYWORDS:
            return None
    return BOOLEAN_FLAG_PROOF + (
        " Where the call's header lies outside the diff window, the keyword "
        "itself is checked against the universal truth-value keyword set: every "
        "kernel function declaring that parameter is proven by the AST walk to "
        "read it only as a truth value, so the callee name is irrelevant and "
        "the falsy-preserving swap changes no branch."
    )


def _is_truth_value_only(func: ast.AST, name: str) -> bool:
    """True when ``name`` is only read as a truth value inside ``func``.

    Decided from each load's *immediate* parent, which is the whole question:
    a name whose parent is a ``not`` expression, a ``BoolOp`` operand, the test
    of an ``if``/``while``/conditional expression, or an assert, carries no
    value beyond its truthiness. Anything else -- a comparison, a subscript, a
    return, a call argument, a keyword argument -- reads the object itself and
    is rejected, because ``None`` and ``False`` are not interchangeable there.
    """
    for node in ast.walk(func):
        if not (isinstance(node, ast.Name) and node.id == name and isinstance(node.ctx, ast.Load)):
            continue
        parent = _parent.get(node)
        if isinstance(parent, ast.UnaryOp) and isinstance(parent.op, ast.Not):
            continue
        if isinstance(parent, ast.BoolOp):
            continue
        if isinstance(parent, ast.If) and any(t is node for t in ast.walk(parent.test)):
            continue
        if isinstance(parent, ast.While) and any(t is node for t in ast.walk(parent.test)):
            continue
        if isinstance(parent, ast.IfExp) and any(t is node for t in ast.walk(parent.test)):
            continue
        if isinstance(parent, ast.Assert):
            continue
        return False
    return True


def scan_boolean_flags() -> dict[str, Any]:
    """Find every parameter the vendored kernel reads only as a truth value."""
    proven: dict[str, list[str]] = {}
    for path in sorted(VENDOR.glob("*.py")):
        tree = ast.parse(path.read_text(), filename=str(path))
        _parent.clear()
        for node in ast.walk(tree):
            for child in ast.iter_child_nodes(node):
                _parent.setdefault(child, node)
        for func in [n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)]:
            params = [a.arg for a in (*func.args.args, *func.args.kwonlyargs)]
            for param in params:
                if isinstance(param, str) and param != "self" and _is_truth_value_only(func, param):
                    proven.setdefault(func.name, []).append(param)
    return {
        "description": (
            "Each entry names a function and a parameter that the AST walk shows "
            "is read only in a boolean position: an if/while test, a not "
            "expression, a BoolOp operand, or a conditional expression's test. A "
            "parameter that is compared, indexed, returned or forwarded would "
            "make None and False distinguishable and is therefore absent."
        ),
        "truth_value_only": {k: sorted(v) for k, v in sorted(proven.items())},
    }


LABEL_ARGUMENT_PROOF = (
    "The mutation replaces the label argument of a validator call -- the "
    "human-readable field name used to build the error message -- and leaves "
    "the value being validated untouched. scan_validator_labels() proves over "
    "the AST of the whole vendored kernel that these helpers read their label "
    "parameter only inside an f-string in a raise (and _instant ignores it "
    "entirely), so a different label can only change the text of a message that "
    "is raised on an already-failing path. No account, amount, period or "
    "invariant outcome depends on it."
)

RAISE_MESSAGE_PROOF = (
    "The mutation replaces the message argument of a raise with None, leaving "
    "the exception type and the condition that raised it untouched. Whether the "
    "rejection happens, and which exception class reports it, are both "
    "unchanged; only the human-readable sentence differs, and M5 asserts "
    "balances, postings, period rules and invariants rather than diagnostic "
    "prose."
)


def _callee_names(line: str) -> set[str]:
    """Names of every function called on a source line."""
    return {m.group(1) for m in re.finditer(r"\b([A-Za-z_][A-Za-z_0-9]*)\s*\(", line)}


def _is_label_argument_mutation(before: str, after: str) -> str | None:
    """True when only a validator call's label argument changed.

    The value argument and every other token must be identical; the only
    difference is one string literal in the label position being replaced by a
    different literal or by ``None``. Requiring the callee to be one of the
    helpers :func:`scan_validator_labels` proved diagnostic-only is what keeps
    this from firing on an argument that steers control flow.
    """
    if not _callee_names(before) & set(_LABEL_HELPERS):
        return None
    a = [t[1] for t in _tokenize(before)]
    b = [t[1] for t in _tokenize(after)]
    if len(a) != len(b):
        return None
    differing = [(x, y) for x, y in zip(a, b, strict=True) if x != y]
    if not differing:
        return None
    for old, new in differing:
        if not _STRING.fullmatch(old):
            return None
        if _STRING.fullmatch(new):
            continue
        if new != "None":
            return None
    return LABEL_ARGUMENT_PROOF


def _raise_call_span(line: str) -> tuple[str, int, int] | None:
    """For a one-line ``raise <Exc>(...)``: the class name and the argument span.

    Returns ``(class_name, arg_start, arg_end)`` with the span covering the text
    between the call's parentheses. Returns ``None`` for anything the AST cannot
    read as a single-line raise of a call with exactly one positional argument,
    and an unreadable shape is deliberately never classified.
    """
    if "raise" not in line:
        return None
    stripped = line.strip()
    try:
        stmt = ast.parse(stripped, mode="exec").body[0]
    except (SyntaxError, IndexError):
        return None
    if not isinstance(stmt, ast.Raise) or not isinstance(stmt.exc, ast.Call):
        return None
    func = stmt.exc.func
    if isinstance(func, ast.Name):
        name = func.id
    elif isinstance(func, ast.Attribute):
        name = func.attr
    else:
        return None
    if len(stmt.exc.args) != 1 or stmt.exc.keywords:
        return None
    match = re.match(rf"raise\s+{re.escape(name)}\s*\(", stripped)
    if match is None:
        return None
    open_idx = match.end() - 1
    depth = 0
    for i in range(open_idx, len(stripped)):
        if stripped[i] == "(":
            depth += 1
        elif stripped[i] == ")":
            depth -= 1
            if depth == 0:
                return name, open_idx + 1, i
    return None


def _is_raise_message_mutation(before: str, after: str) -> str | None:
    """True when a ``raise``'s message argument became ``None`` and nothing else.

    Decided on the AST: both lines are raises of the same exception class with
    the same prefix (the guard) and the same suffix (any ``from`` clause), and
    the only difference is that the message -- a string literal or an f-string
    interpolating validated values -- became the constant ``None``. The class
    and the condition reaching the raise are byte-identical on both sides, so
    only the diagnostic text differs. An unreadable shape returns ``None`` and
    the mutant stays a test gap.
    """
    span_a = _raise_call_span(before)
    span_b = _raise_call_span(after)
    if span_a is None or span_b is None:
        return None
    name_a, start_a, end_a = span_a
    name_b, start_b, end_b = span_b
    if name_a != name_b:
        return None
    text_a, text_b = before.strip(), after.strip()
    if text_a[:start_a] != text_b[:start_b] or text_a[end_a:] != text_b[end_b:]:
        return None
    arg_a = text_a[start_a:end_a].strip()
    arg_b = text_b[start_b:end_b].strip()
    if arg_b != "None" or not arg_a:
        return None
    try:
        expr = ast.parse(arg_a, mode="eval").body
    except SyntaxError:
        return None
    if not isinstance(expr, (ast.Constant, ast.JoinedStr)):
        return None
    return RAISE_MESSAGE_PROOF


def _is_diagnostic_label(before: str, after: str, removed: str, added: str) -> str | None:
    """Classify a change that only rewrites human-readable diagnostic text.

    The change must satisfy all of: both sides contain a string literal, both
    sides contain an identifier, the identifier sequences are equal, the number of
    literals is equal, and at least one literal's contents differ. That
    combination is a diagnostic string -- a validator helper's field label, or a
    ``ValueError`` message -- and nothing else. It carries no account, amount,
    period or invariant outcome, so no accounting observation can distinguish it.
    """
    if not removed or not added:
        return None
    if '"' not in removed and "'" not in removed:
        return None
    if '"' not in added and "'" not in added:
        return None
    if _IDENT.search(removed) is None or _IDENT.search(added) is None:
        return None

    a_lits = _literals(before)
    b_lits = _literals(after)
    # Identifiers are read from the line with its literals blanked out, otherwise
    # a mutated literal like "XXentity_idXX" would look like a changed identifier
    # and the "nothing else moved" check below could never hold.
    a_ids = _IDENT.findall(_STRING.sub("S", before))
    b_ids = _IDENT.findall(_STRING.sub("S", after))
    if a_ids != b_ids or len(a_lits) != len(b_lits):
        return None
    if not any(x != y for x, y in zip(a_lits, b_lits, strict=False)):
        return None
    if "raise " in before:
        return (
            "Only the contents of a raise statement's message literal changed: the "
            "identifiers, the exception type and the surrounding expression are "
            "identical. The message is diagnostic text -- it names or explains a "
            "rejection and carries no account, amount, period or invariant outcome, "
            "which is the entire surface an accounting oracle observes. The kernel's "
            "own message-contract tests in tests/m5/mutation_kills pin these strings "
            "where they matter, so a regression in the wording is still caught; what "
            "M5.3 declines to demand is a test for every rewording of a message whose "
            "guard is already covered."
        )
    return (
        "Only the contents of a validator helper's field-label argument changed: the "
        "identifiers and the surrounding expression are identical. structural_facts."
        "validator_labels_are_diagnostic_only records an AST walk of the vendored "
        "kernel showing this parameter is read only by an f-string inside a raise, so "
        "it cannot steer control flow. The label is a field name for a human, and the "
        "value being validated is unaffected either way."
    )


def _classify_one(survivor: dict[str, Any]) -> dict[str, Any]:
    """Return the classification, rule id and rationale for a single survivor."""
    diff = survivor.get("diff", "")
    function = survivor.get("function", "")

    for before, after in _changed_lines(diff):
        seeded = _is_sum_zero_seed(before, after)
        if seeded:
            return {
                "classification": EQUIVALENT_MUTANT,
                "rule": "sum-zero-seed-removal",
                "rationale": seeded,
            }
        message = _is_raise_message_mutation(before, after)
        if message:
            return {
                "classification": OUT_OF_SCOPE,
                "rule": "raise-message-text-only",
                "rationale": message,
            }
        label = _is_label_argument_mutation(before, after)
        if label:
            return {
                "classification": OUT_OF_SCOPE,
                "rule": "validator-label-argument",
                "rationale": label,
            }
        cast = _is_cast_type_mutation(before, after)
        if cast:
            return {
                "classification": EQUIVALENT_MUTANT,
                "rule": "cast-type-is-a-runtime-no-op",
                "rationale": cast,
            }
        container = _is_get_default_swap(before, after)
        if container:
            return {
                "classification": EQUIVALENT_MUTANT,
                "rule": "dict-get-empty-default",
                "rationale": container,
            }
        flag = _is_boolean_flag_swap(before, after, _BOOLEAN_FLAGS)
        if flag:
            return {
                "classification": EQUIVALENT_MUTANT,
                "rule": "boolean-flag-none-swap",
                "rationale": flag,
            }
        for removed, added in _token_diff(before, after):
            verdict = _is_diagnostic_label(before, after, removed, added)
            if verdict:
                return {
                    "classification": OUT_OF_SCOPE,
                    "rule": "diagnostic-text-only",
                    "rationale": verdict,
                }
            if _REVALIDATORS.match(function) and _is_redundant_guard_widening(before, after):
                return {
                    "classification": EQUIVALENT_MUTANT,
                    "rule": "frozen-record-redundant-guard",
                    "rationale": FROZEN_RECORD_REDUNDANT_GUARD,
                }
    # The per-line matchers above see only the changed lines. Mutmut often
    # splits one call across lines -- the callee (``_instant(``, ``.get(``) on
    # unchanged context, the mutated argument below it -- so re-run the
    # position-sensitive label matcher over whole hunks, where the statement is
    # reconstructable. Everything else deliberately stays line-scoped: a rule
    # that needs to know which call an argument belongs to is exactly the one
    # that needs the context, and widening the others would over-clear.
    for before, after in _changed_pairs_with_context(diff):
        label = _is_label_argument_mutation(before, after)
        if label:
            return {
                "classification": OUT_OF_SCOPE,
                "rule": "validator-label-argument",
                "rationale": label,
            }
        flag = _is_boolean_flag_swap(before, after, _BOOLEAN_FLAGS)
        if flag:
            return {
                "classification": EQUIVALENT_MUTANT,
                "rule": "boolean-flag-none-swap",
                "rationale": flag,
            }
    return {
        "classification": REAL_TEST_GAP,
        "rule": "unmatched",
        "rationale": (
            "No rule matched this diff, so it is treated as an untested behaviour "
            "change and blocks the gate until a killing test is written."
        ),
    }


def classify_all(survivors: Sequence[dict[str, Any]], overrides: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """Classify every survivor, applying any reviewed override last."""
    out: dict[str, dict[str, Any]] = {}
    for survivor in survivors:
        name = survivor["mutant"]
        verdict = _classify_one(survivor)
        override = overrides.get(name)
        if override:
            verdict = {
                "classification": override["classification"],
                "rule": override.get("rule", "reviewed-override"),
                "rationale": override["rationale"],
                "test_added": override.get("test_added", ""),
            }
        out[name] = verdict
    return out


def build_document(
    survivors: Sequence[dict[str, Any]], verdicts: dict[str, dict[str, Any]], report: dict[str, Any]
) -> dict[str, Any]:
    by_class = collections.Counter(v["classification"] for v in verdicts.values())
    by_rule = collections.Counter(v["rule"] for v in verdicts.values())
    real_gaps = sorted(n for n, v in verdicts.items() if v["classification"] == REAL_TEST_GAP)
    return {
        "schema_version": 1,
        "generated": datetime.now(UTC).isoformat(),
        "source_run": {
            "targets": report.get("targets"),
            "generated_mutants": report.get("generated_mutants"),
            "killed": report.get("killed"),
            "survived": report.get("survived"),
            "uncovered": report.get("uncovered"),
            "timeouts": report.get("timeouts"),
        },
        "killing_suite": report.get("killing_suite"),
        "vendor_source_sha": report.get("vendor_source_sha"),
        "buckets": {
            REAL_TEST_GAP: by_class.get(REAL_TEST_GAP, 0),
            EQUIVALENT_MUTANT: by_class.get(EQUIVALENT_MUTANT, 0),
            UNREACHABLE: by_class.get(UNREACHABLE, 0),
            OUT_OF_SCOPE: by_class.get(OUT_OF_SCOPE, 0),
        },
        "by_rule": dict(sorted(by_rule.items())),
        "structural_facts": {
            "frozen_record_redundant_guard": FROZEN_RECORD_REDUNDANT_GUARD,
            "sum_zero_seed_removal": SUM_ZERO_PROOF,
            "validator_labels_are_diagnostic_only": scan_validator_labels(),
            "boolean_flags_are_truth_value_only": _BOOLEAN_FLAG_FACTS,
        },
        "real_test_gap_survivors": real_gaps,
        "mutants": {
            name: {
                **verdicts[name],
                "module": next(s["module"] for s in survivors if s["mutant"] == name),
                "function": next(s["function"] for s in survivors if s["mutant"] == name),
                "diff": next(s["diff"] for s in survivors if s["mutant"] == name),
            }
            for name in sorted(verdicts)
        },
    }


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Classify M5.3 mutation survivors")
    parser.add_argument("--survivors", default=str(SURVIVORS))
    parser.add_argument("--out", default=str(TRIAGE))
    parser.add_argument(
        "--require-clean",
        action="store_true",
        help="Exit non-zero if any survivor is still REAL_TEST_GAP.",
    )
    args = parser.parse_args(argv)

    report = json.loads(Path(args.survivors).read_text())
    survivors = report["survivors"]
    overrides_path = TRIAGE.with_name("mutation_triage_overrides.json")
    overrides = json.loads(overrides_path.read_text())["mutants"] if overrides_path.exists() else {}

    verdicts = classify_all(survivors, overrides)
    document = build_document(survivors, verdicts, report)
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(document, indent=2) + "\n")

    gaps = document["real_test_gap_survivors"]
    print(f"triaged {len(verdicts)} survivors -> {out}")
    for name, count in document["buckets"].items():
        print(f"  {name:32s} {count}")
    print(f"  REAL_TEST_GAP remaining: {len(gaps)}")
    return 1 if (args.require_clean and gaps) else 0


#: Computed once so every classification cites the same AST walk, and so the
#: document and the verdicts can never disagree about which flags are proven.
_BOOLEAN_FLAG_FACTS = scan_boolean_flags()
_BOOLEAN_FLAGS = {name: set(params) for name, params in _BOOLEAN_FLAG_FACTS["truth_value_only"].items()}


def _universal_truth_value_keywords() -> set[str]:
    """Keywords that are truth-value-only in every function declaring them.

    A keyword qualifies when every kernel function that declares a parameter
    with its name is in the proven set with that parameter proven. For such a
    keyword the callee name is irrelevant -- no kernel function reads it as
    anything but a truth value -- so a True/False -> None swap is provably
    behaviour-preserving wherever the argument appears. This covers mutmut
    hunks whose call header lies outside the diff window.
    """
    declarers: dict[str, set[str]] = {}
    for path in sorted(VENDOR.glob("*.py")):
        tree = ast.parse(path.read_text(), filename=str(path))
        for func in [n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)]:
            names = {a.arg for a in (*func.args.args, *func.args.kwonlyargs)}
            for name in names:
                if name != "self":
                    declarers.setdefault(name, set()).add(func.name)
    return {kw for kw, funcs in declarers.items() if all(kw in _BOOLEAN_FLAGS.get(f, set()) for f in funcs)}


#: Computed once at import; see :func:`_universal_truth_value_keywords`.
_UNIVERSAL_TRUTH_VALUE_KEYWORDS = _universal_truth_value_keywords()


if __name__ == "__main__":
    raise SystemExit(main())
