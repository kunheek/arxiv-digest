"""Paper embeddings (fastembed, runs locally on CPU) → "similar papers" and a taste score learned
from what you liked, rated or bookmarked. Vectors live in the local DB only.

Sonnet judges a paper against your written profile; this learns from your actual picks. They
are complementary — see `python run.py --eval-taste`.
"""
from __future__ import annotations

import os
import sqlite3
import threading

import numpy as np

MODEL = "BAAI/bge-small-en-v1.5"  # 384-d, ~130 MB, ~300 papers/s on a CPU
_model = None


def _embedder():
    global _model
    if _model is None:
        from fastembed import TextEmbedding

        # fastembed caches in the system temp dir by default, which gets wiped on reboot
        cache = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "models")
        _model = TextEmbedding(MODEL, cache_dir=cache)
    return _model


def encode(texts: list[str]) -> np.ndarray:
    """L2-normalised float32 vectors, so a dot product is the cosine similarity."""
    v = np.array(list(_embedder().embed(texts)), dtype=np.float32)
    return v / np.maximum(np.linalg.norm(v, axis=1, keepdims=True), 1e-9)


def embed_missing(con: sqlite3.Connection, batch: int = 256, date: str | None = None) -> int:
    """Embed papers that have no vector yet: one day's papers (daily run), or all (backfill)."""
    q = (
        "SELECT p.id, p.title, p.abstract FROM papers p LEFT JOIN vectors v ON v.id = p.id "
        "WHERE v.id IS NULL" + (" AND p.date = ?" if date else "") + " ORDER BY p.date DESC"
    )
    rows = con.execute(q, (date,) if date else ()).fetchall()
    for i in range(0, len(rows), batch):
        chunk = rows[i : i + batch]
        vecs = encode([f"{r['title']}. {r['abstract'] or ''}"[:2000] for r in chunk])
        with con:
            con.executemany(
                "INSERT OR REPLACE INTO vectors VALUES (?,?)",
                [(r["id"], v.astype(np.float16).tobytes()) for r, v in zip(chunk, vecs)],
            )
    return len(rows)


class Index:
    """All vectors in memory: ids, an (n × 384) float32 matrix, and each paper's date."""

    def __init__(self, con: sqlite3.Connection):
        rows = con.execute(
            "SELECT v.id, v.vec, p.date FROM vectors v JOIN papers p ON p.id = v.id"
        ).fetchall()
        self.ids = [r["id"] for r in rows]
        self.dates = np.array([r["date"] for r in rows])
        self.X = (
            np.frombuffer(b"".join(r["vec"] for r in rows), np.float16).reshape(-1, 384).astype(np.float32)
            if rows
            else np.zeros((0, 384), np.float32)
        )
        self.pos = {i: n for n, i in enumerate(self.ids)}

    def rows(self, ids) -> np.ndarray:
        return self.X[[self.pos[i] for i in ids if i in self.pos]]


class Cache:
    """Index rebuilt only when the vectors change (new day embedded, papers pruned), not on
    every like or rating — for the long-running local server."""

    def __init__(self, path: str):
        self.path, self.lock, self.stamp, self.index = path, threading.Lock(), None, None

    def get(self, con: sqlite3.Connection) -> Index:
        with self.lock:
            stamp = tuple(con.execute("SELECT COUNT(*), MAX(rowid) FROM vectors").fetchone())
            if stamp != self.stamp:
                self.index, self.stamp = Index(con), stamp
            return self.index


def _knn(sim: np.ndarray, k: int) -> np.ndarray:
    """Mean of the top-k similarities along axis 1 (sim: papers × references)."""
    if sim.shape[1] == 0:
        return np.zeros(sim.shape[0], np.float32)
    k = min(k, sim.shape[1])
    return np.partition(sim, -k, axis=1)[:, -k:].mean(axis=1)


def taste_raw(cand: np.ndarray, pos: np.ndarray, neg: np.ndarray, k: int = 3) -> np.ndarray:
    """How close each candidate is to your liked papers, minus half how close it is to disliked."""
    return _knn(cand @ pos.T, k) - 0.5 * _knn(cand @ neg.T, k) if len(neg) else _knn(cand @ pos.T, k)


def _taste_loo(ix: Index, cand: list[str], pos: list[str], neg: list[str]) -> np.ndarray:
    """taste_raw for `cand`, but a reference paper is never compared with itself
    (it would be its own nearest neighbour and its score would just echo your label)."""
    P, N = ix.rows(pos), ix.rows(neg)
    pos_n = {i: n for n, i in enumerate(i for i in pos if i in ix.pos)}
    neg_n = {i: n for n, i in enumerate(i for i in neg if i in ix.pos)}
    C = ix.rows(cand)
    raw = taste_raw(C, P, N)
    for c, i in enumerate(cand):
        if i in pos_n and len(P) > 1:
            raw[c] = taste_raw(C[[c]], np.delete(P, pos_n[i], 0), N)[0]
        elif i in neg_n:
            raw[c] = taste_raw(C[[c]], P, np.delete(N, neg_n[i], 0))[0]
    return raw


def reference_ids(con: sqlite3.Connection, min_rating: int = 4) -> tuple[list[str], list[str]]:
    """(positives, negatives): liked ∪ bookmarked ∪ rated ≥ min_rating; rated ≤ 2."""
    pos = {r[0] for r in con.execute(
        "SELECT id FROM likes UNION SELECT id FROM bookmarks UNION SELECT id FROM ratings WHERE rating >= ?",
        (min_rating,))}
    neg = {r[0] for r in con.execute("SELECT id FROM ratings WHERE rating <= 2")} - pos
    return sorted(pos), sorted(neg)


def taste_scores(
    con: sqlite3.Connection, index: Index | None = None, days: int = 60,
    min_positives: int = 3, min_rating: int = 4,
) -> dict[str, float]:
    """0–10 taste score (percentile among recent papers) for papers of the last `days` days.
    Empty until you have `min_positives` picks."""
    ix = index or Index(con)
    pos, neg = reference_ids(con, min_rating)
    pos = [i for i in pos if i in ix.pos]
    if len(pos) < min_positives or not ix.ids:
        return {}
    newest = max(ix.dates)
    since = (np.datetime64(newest) - np.timedelta64(days, "D")).astype(str)
    cand = [i for i, d in zip(ix.ids, ix.dates) if d >= since]
    raw = _taste_loo(ix, cand, pos, neg)
    rank = raw.argsort().argsort() / max(len(raw) - 1, 1)
    return {i: round(float(10 * rank[n]), 1) for n, i in enumerate(cand)}


def similar(con: sqlite3.Connection, pid: str, k: int = 15, index: Index | None = None) -> list[tuple[str, float]]:
    ix = index or Index(con)
    if pid not in ix.pos:
        return []
    s = ix.X @ ix.X[ix.pos[pid]]
    k1 = min(k + 1, len(s))
    top = np.argpartition(-s, k1 - 1)[:k1]
    top = top[np.argsort(-s[top])]
    return [(ix.ids[i], round(float(s[i]), 3)) for i in top if ix.ids[i] != pid][:k]


def auc(pos: np.ndarray, neg: np.ndarray) -> float:
    """P(random positive outranks random negative); 0.5 = chance."""
    if not len(pos) or not len(neg):
        return float("nan")
    allv = np.concatenate([pos, neg])
    r = allv.argsort().argsort() + 1.0
    return float((r[: len(pos)].sum() - len(pos) * (len(pos) + 1) / 2) / (len(pos) * len(neg)))


def evaluate(con: sqlite3.Connection, min_positives: int = 8, seed: int = 0, min_rating: int = 4) -> str:
    """Leave-one-out: can each score rank a pick above papers you never picked?
    Compares Sonnet's `final` with the embedding taste score and a blend."""
    ix = Index(con)
    pos, neg = reference_ids(con, min_rating)
    pos = [i for i in pos if i in ix.pos]
    if len(pos) < min_positives:
        return (f"Only {len(pos)} picks (liked / bookmarked / rated ≥ {min_rating}); need ≥ {min_positives} "
                "before the comparison means anything. Keep liking papers on the local site.")
    final = {r["id"]: r["final"] for r in con.execute("SELECT id, final FROM papers")}
    rng = np.random.default_rng(seed)
    pos_set = set(pos)
    others = [i for i in ix.ids if i not in pos_set]
    others = [str(i) for i in rng.choice(others, min(len(others), 2000), replace=False)]
    f_pos = np.array([final[i] for i in pos], np.float32)
    f_oth = np.array([final[i] for i in others], np.float32)
    t_pos = _taste_loo(ix, pos, pos, neg)
    t_oth = _taste_loo(ix, others, pos, neg)  # a disliked paper among `others` is scored without itself
    z = lambda a, b: ((a - b.mean()) / (b.std() + 1e-9), (b - b.mean()) / (b.std() + 1e-9))
    fp, fo = z(f_pos, f_oth); tp, to = z(t_pos, t_oth)
    return (f"{len(pos)} picks vs {len(others)} random other papers (AUC, 0.5 = chance, 1 = perfect)\n"
            f"  Sonnet final score : {auc(f_pos, f_oth):.3f}\n"
            f"  embedding taste    : {auc(t_pos, t_oth):.3f}   (leave-one-out)\n"
            f"  blend (equal)      : {auc(fp + tp, fo + to):.3f}")
