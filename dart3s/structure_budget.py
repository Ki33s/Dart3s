"""Bounded structure selection; never remove spatial cells from a candidate."""
from __future__ import annotations

import time


class StructureBudgetExceeded(RuntimeError):
    pass


def select_structure(initial, *, seed, limit, retries, count, regenerate, record,
                     check_cancel=lambda: None):
    """Return an intact candidate, or stop before expensive shape sampling."""
    if not 0 <= limit <= 32768 or not 0 <= retries <= 4:
        raise ValueError('Лимит: 0–32768; повторов: 0–4.')
    attempts = []
    for attempt in range(retries + 1):
        check_cancel()
        used_seed = (seed + attempt) % (2**64)
        started = time.monotonic()
        candidate = initial if attempt == 0 else regenerate(used_seed)
        check_cancel()
        elements = int(count(candidate))
        accepted = elements > 0 and (limit == 0 or elements <= limit)
        attempts.append(dict(seed=used_seed, elements=elements, accepted=accepted,
                             retry_seconds=time.monotonic() - started))
        record(dict(limit=limit, retries=retries, attempts=list(attempts),
                    accepted_seed=used_seed if accepted else None,
                    status='accepted' if accepted else 'retrying' if attempt < retries else 'rejected'))
        if accepted:
            return candidate
        # Do not keep a rejected retry tensor alive during the next sampler.
        del candidate
    raise StructureBudgetExceeded(
        f'Слишком сложная структура: {attempts[-1]["elements"]} элементов, лимит {limit}. '
        f'Проверено вариантов: {len(attempts)}. Геометрия и текстуры не запускались.')
