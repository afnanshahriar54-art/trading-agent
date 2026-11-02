"""AI forecast with Kronos, an open-source model for price candles (MIT licence,
github.com/shiyu-coder/Kronos; model code in trader/kronos_model).

Kronos reads the last 400 candles and draws possible next candles. We draw several
separate paths, so the app can say how sure it is: "8 of 10 paths end higher".
The model (Kronos-small, ~100 MB) is downloaded once, into the app's ai_model folder.
"""
from __future__ import annotations

import os
import threading

import numpy as np
import pandas as pd

from .paths import HOME

MODEL_ID, TOKENIZER_ID = "NeoQuasar/Kronos-small", "NeoQuasar/Kronos-Tokenizer-base"
LOOKBACK = 400        # candles of history fed to the model (its limit is 512)
MAX_BATCH = 40        # sequences per model call, to keep memory use modest on a normal PC
_lock = threading.Lock()
_predictor = None


def available() -> tuple[bool, str]:
    try:
        import torch  # noqa: F401
        return True, ""
    except ImportError:
        return False, "The AI engine (PyTorch) isn't installed in this copy of the app."


def predictor():
    """Load Kronos once (first use downloads it)."""
    global _predictor
    with _lock:
        if _predictor is None:
            os.environ.setdefault("HF_HOME", str(HOME / "ai_model"))
            import torch
            torch.set_num_threads(max(1, (os.cpu_count() or 2) - 1))
            from .kronos_model import Kronos, KronosPredictor, KronosTokenizer
            tokenizer = KronosTokenizer.from_pretrained(TOKENIZER_ID)
            model = Kronos.from_pretrained(MODEL_ID)
            _predictor = KronosPredictor(model, tokenizer, device="cpu", max_context=512)
    return _predictor


def future_times(index: pd.DatetimeIndex, horizon: int, market: str) -> pd.DatetimeIndex:
    """Timestamps of the candles to forecast (Kronos uses the calendar: weekday, hour...)."""
    step = pd.Series(index).diff().median()
    last = index[-1]
    if step >= pd.Timedelta(hours=20):  # daily candles
        return pd.date_range(last + pd.Timedelta(days=1), periods=horizon, freq="D" if market == "crypto" else "B")
    return pd.date_range(last + step, periods=horizon, freq=step)


def forecast_many(items: list[tuple[str, pd.DataFrame, str]], horizon: int = 5, paths: int = 10,
                  seed: int | None = None) -> dict[str, dict]:
    """items: (key, OHLCV DataFrame with a DatetimeIndex, market). Returns {key: forecast}, where a
    forecast has the median path, a 10-90% range, and how many paths end above today's price."""
    import torch

    pred = predictor()
    jobs = []
    for key, df, market in items:
        ctx = df[["open", "high", "low", "close", "volume"]].iloc[-LOOKBACK:].astype(float)
        if len(ctx) < LOOKBACK:
            continue  # Kronos needs the same history length for every series in a batch
        jobs.append((key, ctx.reset_index(drop=True), pd.Series(df.index[-LOOKBACK:]),
                     pd.Series(future_times(df.index, horizon, market)), float(ctx.close.iloc[-1])))
    if seed is not None:
        torch.manual_seed(seed)
    # Each job becomes `paths` copies; each copy samples its own future.
    seqs = [(j, job) for j, job in enumerate(jobs) for _ in range(paths)]
    closes: dict[int, list[np.ndarray]] = {}
    for start in range(0, len(seqs), MAX_BATCH):
        chunk = seqs[start:start + MAX_BATCH]
        outs = pred.predict_batch([job[1] for _, job in chunk], [job[2] for _, job in chunk],
                                  [job[3] for _, job in chunk], pred_len=horizon, T=1.0, top_p=0.9,
                                  sample_count=1, verbose=False)
        for (j, _), out in zip(chunk, outs):
            closes.setdefault(j, []).append(out["close"].to_numpy())
    results = {}
    for j, (key, _, _, y_ts, last) in enumerate(jobs):
        c = np.array(closes[j])
        final = c[:, -1]
        results[key] = {
            "last": last, "times": [t.isoformat() for t in y_ts],
            "median": np.median(c, axis=0).tolist(),
            "low": np.percentile(c, 10, axis=0).tolist(), "high": np.percentile(c, 90, axis=0).tolist(),
            "up_paths": int((final > last).sum()), "paths": len(final),
            "exp_return": float(np.median(final) / last - 1),
        }
    return results
