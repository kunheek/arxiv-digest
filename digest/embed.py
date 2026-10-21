"""Paper embeddings (fastembed, runs locally on CPU) → "similar papers" and a taste score learned
from what you liked, rated or bookmarked. Vectors live in the local DB only.

Sonnet judges a paper against your written profile; this learns from your actual picks. They
are complementary — see `python run.py --eval-taste`.
"""
from __future__ import annotations

import os
import sqlite3

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


def embed_missing(con: sqlite3.Connection, batch: int = 256, limit: int = 0) -> int:
    """Embed every paper that has no vector yet (new papers each day, or the whole backlog)."""
    q = (
        "SELECT p.id, p.title, p.abstract FROM papers p LEFT JOIN vectors v ON v.id = p.id "
        "WHERE v.id IS NULL ORDER BY p.date DESC"
    )
    rows = con.execute(q + (f" LIMIT {int(limit)}" if limit else "")).fetchall()
    for i in range(0, len(rows), batch):
        chunk = rows[i : i + batch]
        vecs = encode([f"{r['title']}. {r['abstract'] or ''}"[:2000] for r in chunk])
        with con:
            con.executemany(
                "INSERT OR REPLACE INTO vectors VALUES (?,?)",
                [(r["id"], v.astype(np.float16).tobytes()) for r, v in zip(chunk, vecs)],
            )
    return len(rows)


def load(con: sqlite3.Connection, where: str = "", args: tuple = ()) -> tuple[list[str], np.ndarray]:
    rows = con.execute(
        "SELECT v.id, v.vec FROM vectors v JOIN papers p ON p.id = v.id " + where, args
    ).fetchall()
    if not rows:
        return [], np.zeros((0, 384), np.float32)
    ids = [r["id"] for r in rows]
    return ids, np.stack([np.frombuffer(r["vec"], np.float16) for r in rows]).astype(np.float32)


def _knn(sim: np.ndarray, k: int) -> np.ndarray:
    """Mean of the top-k similarities along axis 1 (sim: papers × references)."""
    if sim.shape[1] == 0:
        return np.zeros(sim.shape[0], np.float32)
    k = min(k, sim.shape[1])
    return np.sort(np.partition(sim, -k, axis=1)[:, -k:], axis=1).mean(axis=1)


def taste_raw(cand: np.ndarray, pos: np.ndarray, neg: np.ndarray, k: int = 3) -> np.ndarray:
    """How close each candidate is to your liked papers, minus half how close it is to disliked."""
    return _knn(cand @ pos.T, k) - 0.5 * _knn(cand @ neg.T, k) if len(neg) else _knn(cand @ pos.T, k)


def reference_ids(con: sqlite3.Connection, min_rating: int = 4) -> tuple[list[str], list[str]]:
    """(positives, negatives): liked ∪ bookmarked ∪ rated ≥ min_rating; rated ≤ 2."""
    pos = {r[0] for r in con.execute(
        "SELECT id FROM likes UNION SELECT id FROM bookmarks UNION SELECT id FROM ratings WHERE rating >= ?",
        (min_rating,))}
    neg = {r[0] for r in con.execute("SELECT id FROM ratings WHERE rating <= 2")} - pos
    return sorted(pos), sorted(neg)


def taste_scores(con: sqlite3.Connection, days: int = 60, min_positives: int = 3) -> dict[str, float]:
    """0–10 taste score (percentile among recent papers) for papers of the last `days` days.
    Empty until you have `min_positives` picks."""
    pos_ids, neg_ids = reference_ids(con)
    if len(pos_ids) < min_positives:
        return {}
    ids, X = load(con, "WHERE p.date >= date((SELECT MAX(date) FROM papers), ?)", (f"-{days} day",))
    if not ids:
        return {}
    index = {i: n for n, i in enumerate(ids)}
    refs = pos_ids + neg_ids
    allids, A = load(con, f"WHERE v.id IN ({','.join('?' * len(refs))})", tuple(refs))
    a = {i: n for n, i in enumerate(allids)}
    pos = [i for i in pos_ids if i in a]
    P, N = A[[a[i] for i in pos]], A[[a[i] for i in neg_ids if i in a]]
    if not len(P):
        return {}
    raw = taste_raw(X, P, N)
    for n, i in enumerate(pos):  # a pick is its own nearest neighbour: score it without itself
        if i in index and len(P) > 1:
            raw[index[i]] = taste_raw(X[[index[i]]], np.delete(P, n, 0), N)[0]
    rank = raw.argsort().argsort() / max(len(raw) - 1, 1)
    return {i: round(float(10 * rank[n]), 1) for i, n in index.items()}


def similar(con: sqlite3.Connection, pid: str, k: int = 15) -> list[tuple[str, float]]:
    row = con.execute("SELECT vec FROM vectors WHERE id=?", (pid,)).fetchone()
    if not row:
        return []
    ids, X = load(con)
    s = X @ np.frombuffer(row["vec"], np.float16).astype(np.float32)
    top = np.argsort(-s)[: k + 1]
    return [(ids[i], round(float(s[i]), 3)) for i in top if ids[i] != pid][:k]


def auc(pos: np.ndarray, neg: np.ndarray) -> float:
    """P(random positive outranks random negative); 0.5 = chance."""
    if not len(pos) or not len(neg):
        return float("nan")
    allv = np.concatenate([pos, neg])
    r = allv.argsort().argsort() + 1.0
    return float((r[: len(pos)].sum() - len(pos) * (len(pos) + 1) / 2) / (len(pos) * len(neg)))


def evaluate(con: sqlite3.Connection, min_positives: int = 8, seed: int = 0) -> str:
    """Leave-one-out: can each score rank a pick above papers you never picked?
    Compares Sonnet's `final` with the embedding taste score and a blend."""
    pos_ids, neg_ids = reference_ids(con)
    ids, X = load(con)
    pos_ids = [i for i in pos_ids if i in set(ids)]
    if len(pos_ids) < min_positives:
        return (f"Only {len(pos_ids)} picks (liked / bookmarked / rated ≥ 4); need ≥ {min_positives} "
                "before the comparison means anything. Keep liking papers on the local site.")
    pos_set, idx = set(pos_ids), {i: n for n, i in enumerate(ids)}
    final = {r["id"]: r["final"] for r in con.execute("SELECT id, final FROM papers")}
    rng = np.random.default_rng(seed)
    others = [i for i in ids if i not in pos_set]
    others = list(rng.choice(others, min(len(others), 2000), replace=False))
    P = X[[idx[i] for i in pos_ids]]
    N = X[[idx[i] for i in neg_ids if i in idx]] if neg_ids else np.zeros((0, X.shape[1]), np.float32)
    O = X[[idx[i] for i in others]]
    f_pos = np.array([final[i] for i in pos_ids], np.float32)
    f_oth = np.array([final[i] for i in others], np.float32)
    t_pos = np.array([taste_raw(P[[n]], np.delete(P, n, 0), N)[0] for n in range(len(P))])
    t_oth = taste_raw(O, P, N)
    z = lambda a, b: ((a - b.mean()) / (b.std() + 1e-9), (b - b.mean()) / (b.std() + 1e-9))
    fp, fo = z(f_pos, f_oth); tp, to = z(t_pos, t_oth)
    return (f"{len(pos_ids)} picks vs {len(others)} random other papers (AUC, 0.5 = chance, 1 = perfect)\n"
            f"  Sonnet final score : {auc(f_pos, f_oth):.3f}\n"
            f"  embedding taste    : {auc(t_pos, t_oth):.3f}   (leave-one-out)\n"
            f"  blend (equal)      : {auc(fp + tp, fo + to):.3f}")
