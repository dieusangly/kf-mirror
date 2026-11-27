"""Validation and pairwise cosine similarity for embeddings."""

import math


def normalized(vector):
    if not vector or any(isinstance(v, bool) or not isinstance(v, (float, int)) or
                         not math.isfinite(v) for v in vector):
        raise ValueError('embedding must contain finite numbers')
    norm = math.hypot(*vector)
    if not math.isfinite(norm) or not norm:
        raise ValueError('embedding must be nonzero with finite norm')
    return [v / norm for v in vector]


def similarity_matrix(vectors, *, groups=None):
    """Compute all pairs, or just the distinct pairs within saved groups.

    Normalize every vector in either mode, including singleton groups, so
    limiting the comparisons does not weaken vector validation.
    """
    unit = {key: normalized(value) for key, value in vectors.items()}
    if len({len(v) for v in unit.values()}) != 1:
        raise ValueError('embedding dimensions differ')

    def grouped_pairs():
        for group in groups:
            ids = sorted(group)
            for index, a in enumerate(ids):
                for b in ids[index + 1:]:
                    yield a, b

    pairs = grouped_pairs() if groups is not None else (
        (a, b) for a in sorted(unit) for b in sorted(unit) if a <= b)
    return {(a, b): max(-1., min(1., math.fsum(x * y for x, y in zip(unit[a], unit[b]))))
            for a, b in pairs}


def cosine(scores, a, b):
    return scores[tuple(sorted((a, b)))]
