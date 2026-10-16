"""Deterministic all-members cosine clustering; structural chunks are indivisible."""

import math


def normalized(vector):
    if not vector or any(isinstance(v, bool) or not isinstance(v, (float, int)) or
                         not math.isfinite(v) for v in vector):
        raise ValueError('embedding must contain finite numbers')
    norm = math.hypot(*vector)
    if not math.isfinite(norm) or not norm:
        raise ValueError('embedding must be nonzero with finite norm')
    return [v / norm for v in vector]


def similarity_matrix(vectors):
    unit = {key: normalized(value) for key, value in vectors.items()}
    if len({len(v) for v in unit.values()}) != 1:
        raise ValueError('embedding dimensions differ')
    return {(a, b): max(-1., min(1., math.fsum(x * y for x, y in zip(unit[a], unit[b]))))
            for a in sorted(unit) for b in sorted(unit) if a <= b}


def cosine(scores, a, b):
    return scores[tuple(sorted((a, b)))]


def cluster_members(ids, scores, threshold, fits=None):
    """Greedy strongest minimum similarity, stable ties, optional exact size constraint."""
    groups = []
    for item in sorted(ids):
        eligible = []
        for i, group in enumerate(groups):
            minimum = min(cosine(scores, item, other) for other in group)
            if minimum >= threshold and (fits is None or fits(group + [item])):
                eligible.append((-minimum, tuple(group), i))
        if eligible:
            groups[min(eligible)[2]].append(item)
        else:
            if fits is not None and not fits([item]):
                raise ValueError('oversized structural chunk: ' + item)
            groups.append([item])
    return groups


def refine_clusters(groups, scores, threshold, fits, refinement_step=0.05, max_refinements=4,
                    on_progress=None):
    """Bounded stricter reclustering, then similarity-aware capacity partitioning."""
    final, diagnostics = [], []
    if on_progress:
        on_progress({'state': 'checking_semantic_clusters_fit_claim_generation',
                     'phase': 'initial', 'completed': 0, 'total': len(groups)})
    for index, group in enumerate(groups, start=1):
        within_budget = fits(group)
        if on_progress and (index == 1 or index % 25 == 0 or index == len(groups) or not within_budget):
            on_progress({'state': 'checking_semantic_clusters_fit_claim_generation',
                         'phase': 'initial', 'completed': index, 'total': len(groups)})
        if within_budget:
            final.append(group)
            continue
        if on_progress:
            on_progress({'state': 'splitting_oversized_semantic_clusters', 'status': 'started',
                         'cluster_number': index, 'total': len(groups), 'structural_chunks': len(group)})
        for item in group:
            if not fits([item]):
                raise ValueError('oversized structural chunk: ' + item)
        parts = [group]
        for attempt in range(1, max_refinements + 1):
            parts = cluster_members(group, scores, min(1., threshold + attempt * refinement_step))
            if len(parts) > 1:
                break
        bounded = []
        for part in parts:
            bounded.extend([part] if fits(part) else cluster_members(part, scores, threshold, fits))
        if len(bounded) <= 1 or any(not fits(part) for part in bounded):
            raise ValueError('semantic cluster refinement failed to make strict progress')
        diagnostics.append({'original_structural_chunk_ids': group,
                            'partitions': bounded, 'reason': 'claim_generation_input_budget_exceeded'})
        final.extend(bounded)
        if on_progress:
            on_progress({'state': 'splitting_oversized_semantic_clusters', 'status': 'complete',
                         'cluster_number': index, 'total': len(groups),
                         'structural_chunks': len(group), 'resulting_clusters': len(bounded)})
    return sorted(final, key=lambda group: tuple(group)), diagnostics
