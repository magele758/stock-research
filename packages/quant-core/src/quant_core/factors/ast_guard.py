"""Compile an allowlisted, causal factor expression to Polars."""

from __future__ import annotations

import ast
import math
import operator

import polars as pl

# Public inputs exclude forward labels, backtest signals and internal columns.
_FIELDS = frozenset(
    {
        "open",
        "high",
        "low",
        "close",
        "pre_close",
        "vwap",
        "volume_shares",
        "amount_ccy",
        "adj_factor",
        "up_limit",
        "down_limit",
        "is_suspended",
        "is_st",
        "float_market_cap",
        "free_float_market_cap",
        "industry_l1",
        "listing_id",
        "symbol",
        "trade_date",
        "market",
        "board",
        "tail_vwap",
        "tail_volume_shares",
        "turnover_rate",
    }
)
_FIELD_ALIASES = {"volume": "volume_shares", "amount": "amount_ccy", "code": "_id"}
_FORBIDDEN = frozenset({"os", "sys", "subprocess", "socket", "open", "eval", "exec", "__import__"})
_BINARY = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
    ast.Pow: operator.pow,
    ast.Mod: operator.mod,
    ast.BitAnd: operator.and_,
    ast.BitOr: operator.or_,
}
_COMPARISON = {
    ast.Eq: operator.eq,
    ast.NotEq: operator.ne,
    ast.Gt: operator.gt,
    ast.GtE: operator.ge,
    ast.Lt: operator.lt,
    ast.LtE: operator.le,
}
_POINTWISE = frozenset({"abs", "log", "log1p", "exp", "sqrt", "sign", "is_null", "is_not_null", "is_finite"})
_CROSS_SECTIONAL = frozenset({"rank", "mean", "std", "median", "sum", "min", "max"})
_ROLLING = frozenset(
    {"rolling_mean", "rolling_std", "rolling_sum", "rolling_min", "rolling_max", "rolling_median"}
)
_LAGGED = frozenset({"shift", "diff", "pct_change", "forward_fill"})
_ALIASES = {
    "ts_mean": "rolling_mean",
    "ts_std": "rolling_std",
    "ts_sum": "rolling_sum",
    "ts_min": "rolling_min",
    "ts_max": "rolling_max",
    "ts_corr": "rolling_corr",
    "lag": "shift",
    "cs_rank": "rank",
    "cs_mean": "mean",
    "cs_zscore": "zscore",
}


def _literal(node: ast.AST) -> bool | int | float | str | None:
    if isinstance(node, ast.Constant):
        value = node.value
        if value is None or type(value) in (bool, str):
            return value
        if type(value) is int and abs(value) <= 10**12:
            return value
        if type(value) is float and math.isfinite(value):
            return value
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.USub, ast.UAdd)):
        value = _literal(node.operand)
        if type(value) in (int, float):
            return -value if isinstance(node.op, ast.USub) else value
    raise ValueError("Expected a bounded literal")


def _integer(node: ast.AST, name: str, minimum: int = 1) -> int:
    value = _literal(node)
    if type(value) is not int:
        raise ValueError(f"{name} requires an integer literal")
    if value < 0:
        raise ValueError(f"{name} cannot use a negative lookback or a future index")
    if value < minimum or value > 1024:
        raise ValueError(f"{name} must be between {minimum} and 1024")
    return value


def _arguments(
    args: list[ast.expr],
    kwargs: list[ast.keyword],
    names: tuple[str, ...],
    *,
    aliases: dict[str, str] | None = None,
) -> dict[str, ast.expr]:
    if len(args) > len(names):
        raise ValueError(f"Expected arguments {', '.join(names) or '(none)'}")
    result = dict(zip(names[: len(args)], args, strict=True))
    for kw in kwargs:
        key = (aliases or {}).get(kw.arg, kw.arg)
        if key not in names or key in result:
            raise ValueError(f"Unsupported or duplicate argument {kw.arg!r}")
        result[key] = kw.value
    return result


def _column(name: str) -> pl.Expr:
    if name not in _FIELDS and name not in _FIELD_ALIASES:
        raise ValueError(f"Column {name!r} is outside the point-in-time input allowlist")
    return pl.col(_FIELD_ALIASES.get(name, name))


def _function_name(node: ast.expr) -> tuple[str, ast.expr | None]:
    if isinstance(node, ast.Name):
        return node.id, None
    if isinstance(node, ast.Attribute):
        if isinstance(node.value, ast.Name) and node.value.id in ("pl", "polars"):
            return node.attr, None
        return node.attr, node.value
    raise ValueError("Only named factor operators are allowed")


def _call(node: ast.Call, expected_scope: str | None = None) -> pl.Expr:
    name, source = _function_name(node.func)
    name = _ALIASES.get(name, name)
    if name in _FORBIDDEN or name.startswith("_"):
        raise ValueError(f"Forbidden call {name!r}")
    if name == "lead":
        raise ValueError("lead reads future rows")
    args = list(node.args)
    if name == "over" and source is not None:
        params = _arguments(args, node.keywords, ("partition_by",))
        group = _literal(params.get("partition_by", ast.Constant(None)))
        if group in ("listing_id", "symbol", "code"):
            group = "_id"
        if group not in ("_id", "trade_date") or not isinstance(source, ast.Call):
            raise ValueError("over allows only the security or same-date scope of an operator")
        return _call(source, expected_scope=group)

    if name in ("col", "lit") and source is None and expected_scope is None:
        params = _arguments(args, node.keywords, ("value",), aliases={"name": "value"})
        value = _literal(params.get("value", ast.Constant(None)))
        if name == "col":
            if not isinstance(value, str):
                raise ValueError("col requires a literal column name")
            return _column(value)
        return pl.lit(value)

    if source is None:
        if not args:
            raise ValueError(f"{name} requires an input expression")
        source = args.pop(0)
    expr = _compile(source)

    scope = None
    if name in _CROSS_SECTIONAL or name == "zscore":
        scope = "trade_date"
    if name in _ROLLING or name in _LAGGED or name == "rolling_corr":
        scope = "_id"
    if expected_scope is not None and scope != expected_scope:
        raise ValueError(f"{name} cannot run over {expected_scope!r}; its causal scope is {scope!r}")

    if name in _POINTWISE:
        _arguments(args, node.keywords, ())
        return getattr(expr, name)()
    if name == "clip":
        params = _arguments(args, node.keywords, ("lower_bound", "upper_bound"))
        return expr.clip(**{key: _compile(value) for key, value in params.items()})
    if name in ("fill_null", "fill_nan"):
        params = _arguments(args, node.keywords, ("value",))
        fill = _literal(params.get("value", ast.Constant(None)))
        if type(fill) not in (bool, int, float):
            raise ValueError(f"{name} requires a finite scalar fill value")
        return getattr(expr, name)(fill)
    if name in _CROSS_SECTIONAL or name == "zscore":
        _arguments(args, node.keywords, ())
        if name == "zscore":
            return (expr - expr.mean().over(scope)) / expr.std().over(scope)
        return getattr(expr, name)().over(scope)
    if name in _LAGGED:
        argument = "limit" if name == "forward_fill" else "n"
        params = _arguments(args, node.keywords, (argument,))
        lag = _integer(params.get(argument, ast.Constant(1)), argument, minimum=0)
        return getattr(expr, name)(lag).over(scope)
    if name in _ROLLING or name == "rolling_corr":
        names = (
            ("other", "window_size", "min_samples")
            if name == "rolling_corr"
            else (
                "window_size",
                "min_samples",
                "center",
            )
        )
        params = _arguments(
            args,
            node.keywords,
            names,
            aliases={"window": "window_size", "min_periods": "min_samples"},
        )
        if "center" in params and _literal(params["center"]) is not False:
            raise ValueError("Centered rolling windows read future rows")
        if "window_size" not in params:
            raise ValueError(f"{name} requires a fixed window_size")
        window = _integer(params["window_size"], "window_size")
        min_samples = _integer(params.get("min_samples", ast.Constant(window)), "min_samples")
        if min_samples > window:
            raise ValueError("min_samples must be no larger than window_size")
        if name == "rolling_corr":
            if "other" not in params:
                raise ValueError("rolling_corr requires two input expressions")
            result = pl.rolling_corr(
                expr,
                _compile(params["other"]),
                window_size=window,
                min_samples=min_samples,
            )
        else:
            result = getattr(expr, name)(window_size=window, min_samples=min_samples)
        return result.over(scope)
    raise ValueError(f"Operator {name!r} is outside the factor allowlist")


def _compile(node: ast.AST) -> pl.Expr:
    if isinstance(node, ast.Constant):
        return pl.lit(_literal(node))
    if isinstance(node, ast.Name):
        if node.id in _FORBIDDEN and node.id not in _FIELDS:
            raise ValueError(f"Forbidden name {node.id!r}")
        return _column(node.id)
    if isinstance(node, ast.UnaryOp):
        if isinstance(node.op, ast.UAdd):
            return _compile(node.operand)
        if isinstance(node.op, ast.USub):
            return -_compile(node.operand)
        if isinstance(node.op, (ast.Invert, ast.Not)):
            return ~_compile(node.operand)
    if isinstance(node, ast.BinOp) and type(node.op) in _BINARY:
        return _BINARY[type(node.op)](_compile(node.left), _compile(node.right))
    if isinstance(node, ast.Compare) and len(node.ops) == 1 and type(node.ops[0]) in _COMPARISON:
        return _COMPARISON[type(node.ops[0])](_compile(node.left), _compile(node.comparators[0]))
    if isinstance(node, ast.IfExp):
        return pl.when(_compile(node.test)).then(_compile(node.body)).otherwise(_compile(node.orelse))
    if isinstance(node, ast.Call):
        return _call(node)
    raise ValueError(f"Unsupported expression syntax {type(node).__name__}")


def compile_factor_expression(expression: str) -> pl.Expr:
    if not isinstance(expression, str) or not expression.strip() or len(expression) > 4096:
        raise ValueError("A factor expression must be nonempty and at most 4096 characters")
    root = ast.parse(expression.strip(), mode="eval")
    if sum(1 for _ in ast.walk(root)) > 160:
        raise ValueError("Factor expression exceeds the 160-node complexity limit")
    return _compile(root.body)


def validate_factor_ast(expression: str) -> tuple[bool, list[str]]:
    """Validate syntax, data authorization and time direction without executing source code."""
    try:
        compile_factor_expression(expression)
    except (SyntaxError, ValueError, TypeError, OverflowError, RecursionError) as error:
        return False, [str(error)]
    return True, []
