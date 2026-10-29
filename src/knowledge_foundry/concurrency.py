"""Run a bounded number of independent requests with coordinator-owned results."""

from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait


def validate_workers(workers):
    if type(workers) is not int or workers < 1:
        raise ValueError('workers must be a positive integer')
    return workers


def run_bounded(function, items, workers, on_result):
    """Consume results as they finish; keep at most workers tasks outstanding.

    Callbacks run on the caller's thread. On interruption, cancel queued work
    and wait for in-flight requests before returning control to the caller.
    """
    validate_workers(workers)
    if workers == 1:
        for item in items:
            on_result(function(item))
        return
    items = iter(items)
    executor = ThreadPoolExecutor(max_workers=workers)
    pending = set()
    try:
        for item in items:
            pending.add(executor.submit(function, item))
            if len(pending) == workers:
                break
        while pending:
            done, _ = wait(pending, return_when=FIRST_COMPLETED)
            for future in done:
                pending.remove(future)
                on_result(future.result())
                try:
                    item = next(items)
                except StopIteration:
                    continue
                pending.add(executor.submit(function, item))
    finally:
        executor.shutdown(wait=True, cancel_futures=True)
