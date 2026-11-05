"""JSON responses that understand numpy/pandas scalars and turn NaN/inf into null."""
from __future__ import annotations

import json
import math
from datetime import date, datetime
from typing import Any

import numpy as np
from fastapi.responses import JSONResponse


def clean(o: Any) -> Any:
    if isinstance(o, dict):
        return {str(k): clean(v) for k, v in o.items()}
    if isinstance(o, (list, tuple, set)):
        return [clean(v) for v in o]
    if isinstance(o, (bool, np.bool_)):
        return bool(o)
    if isinstance(o, (np.integer,)):
        return int(o)
    if isinstance(o, (float, np.floating)):
        f = float(o)
        return None if math.isnan(f) or math.isinf(f) else f
    if isinstance(o, (datetime, date)):
        return o.isoformat()
    if hasattr(o, "isoformat"):
        return o.isoformat()
    return o


class SafeJSONResponse(JSONResponse):
    def render(self, content: Any) -> bytes:
        return json.dumps(clean(content), ensure_ascii=False, separators=(",", ":"), default=str).encode("utf-8")


def register_numpy_encoders() -> None:
    """Teach FastAPI's jsonable_encoder about numpy scalars (it runs before the response class)."""
    from fastapi import encoders

    for t in (np.bool_, np.float64, np.float32, np.float16, np.int64, np.int32, np.int16, np.int8, np.uint64,
              np.uint32, np.uint8):
        encoders.ENCODERS_BY_TYPE[t] = clean


register_numpy_encoders()
