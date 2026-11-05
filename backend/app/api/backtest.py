"""Backtesting endpoints."""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from ..services import backtest as bt
from ..services import prices

router = APIRouter(prefix="/api/backtest")


class CostsIn(BaseModel):
    commission_pct: float = Field(0.0005, ge=0, le=0.05)
    commission_fixed: float = Field(0.0, ge=0, le=100)
    slippage_pct: float = Field(0.0005, ge=0, le=0.05)


class RunIn(BaseModel):
    ticker: str
    strategy: str = "sma_cross"
    params: dict[str, Any] = {}
    start: str | None = None
    end: str | None = None
    initial: float = Field(10_000, gt=0)
    costs: CostsIn = CostsIn()
    code: str | None = None


class SweepIn(RunIn):
    x_param: str
    x_values: list[float]
    y_param: str
    y_values: list[float]
    metric: str = "sharpe"


class WalkIn(SweepIn):
    train_frac: float = Field(0.7, ge=0.4, le=0.9)
    folds: int = Field(4, ge=2, le=8)


def _data(ticker: str):
    df = prices.daily(ticker)
    if df.empty:
        raise HTTPException(404, f"No price data for {ticker}")
    return df


def _bench():
    return prices.daily("SPY")


def _vals(v: list[float]) -> list[Any]:
    return [int(x) if float(x).is_integer() else x for x in v]


@router.get("/presets")
def presets():
    return {"presets": bt.presets_payload(), "label": bt.LABEL,
            "metrics": ["sharpe", "sortino", "cagr_pct", "total_return_pct", "max_drawdown_pct", "win_rate_pct", "profit_factor"]}


@router.post("/run")
def run(body: RunIn):
    t = body.ticker.upper()
    try:
        out = bt.run(_data(t), body.strategy, body.params, start=body.start, end=body.end,
                     costs=bt.Costs(**body.costs.model_dump()), initial=body.initial, code=body.code, benchmark=_bench())
    except bt.StrategyError as e:
        raise HTTPException(400, str(e))
    return {"ticker": t, **out, "as_of": prices.as_of(t)}


@router.post("/sweep")
def sweep(body: SweepIn):
    t = body.ticker.upper()
    try:
        out = bt.sweep(_data(t), body.strategy, body.x_param, _vals(body.x_values), body.y_param, _vals(body.y_values),
                       body.params, start=body.start, end=body.end, costs=bt.Costs(**body.costs.model_dump()),
                       metric=body.metric, code=body.code)
    except bt.StrategyError as e:
        raise HTTPException(400, str(e))
    return {"ticker": t, **out}


@router.post("/walkforward")
def walkforward(body: WalkIn):
    t = body.ticker.upper()
    try:
        out = bt.walk_forward(_data(t), body.strategy, body.x_param, _vals(body.x_values), body.y_param,
                              _vals(body.y_values), body.params, start=body.start, end=body.end,
                              costs=bt.Costs(**body.costs.model_dump()), train_frac=body.train_frac, folds=body.folds,
                              metric=body.metric, code=body.code, benchmark=_bench())
    except bt.StrategyError as e:
        raise HTTPException(400, str(e))
    return {"ticker": t, **out}
