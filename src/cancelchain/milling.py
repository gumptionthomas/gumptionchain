from __future__ import annotations

import multiprocessing
from collections.abc import Callable, Generator, Iterable
from hashlib import sha256, sha512
from itertools import count
from typing import Any, Protocol


class MillableBlock(Protocol):
    target: str
    unproven_header: str

    def solve(self, proof_of_work: int) -> None: ...


class _HashProto(Protocol):
    """Structural type for the hash object returned by `hashlib.sha256()`.

    The stdlib doesn't expose a public name for this; importing the
    private `_hashlib.HASH` is fragile (and absent on OpenSSL-less Python
    builds), so this minimal Protocol captures only the surface
    `mill_hash`'s callers actually use.
    """

    def digest(self) -> bytes: ...
    def hexdigest(self) -> str: ...


def mill_hash(data: bytes | str) -> _HashProto:
    if isinstance(data, str):
        data = data.encode()
    return sha256(sha512(data).digest())


def mill_hash_bin(data: bytes | str) -> bytes:
    return mill_hash(data).digest()


def mill_hash_str(data: bytes | str) -> str:
    return mill_hash(data).hexdigest()


def mill_work(
    w: tuple[int, int, str, int],
) -> tuple[int | None, int]:
    work_start, work_stop, unproven_header, target = w
    for proof in range(work_start, work_stop):
        h = mill_hash_str(f'{unproven_header}{proof}')
        if int(h, 16) < target:
            return (proof, work_stop - proof)
    return (None, work_stop - work_start)


def mill_block(
    block: MillableBlock,
    rounds: int,
    worksize: int,
    progress_next: Callable[..., None],
) -> Generator[int | None, None, None]:
    target = int(block.target, 16)
    unproven_header = block.unproven_header
    proof_of_work: int | None = None
    proof_start = 0
    r: Iterable[int] = range(rounds) if rounds else count()
    while proof_of_work is None:
        for _i in r:
            if proof_of_work is not None:
                break
            proof, c = mill_work(
                (proof_start, proof_start + worksize, unproven_header, target)
            )
            progress_next(n=c)
            if proof is not None and proof_of_work is None:
                proof_of_work = proof
            proof_start += worksize
        yield proof_of_work


def work_generator(
    unproven_header: str,
    target: int,
    start: int,
    worksize: int,
    num: int,
) -> Generator[tuple[int, int, str, int], None, None]:
    for i in range(num):
        work_start = start + (i * worksize)
        yield (work_start, work_start + worksize, unproven_header, target)


def mill_block_mp(
    block: MillableBlock,
    rounds: int,
    worksize: int,
    progress_next: Callable[..., None],
) -> Generator[int | None, None, None]:
    cpus = multiprocessing.cpu_count()
    target = int(block.target, 16)
    unproven_header = block.unproven_header
    proof_of_work: int | None = None
    proof_start = 0
    r: Iterable[int] = range(rounds) if rounds else count()
    while proof_of_work is None:
        for _i in r:
            if proof_of_work is not None:
                break
            work = work_generator(
                unproven_header, target, proof_start, worksize, cpus
            )
            with multiprocessing.Pool(cpus) as p:
                imap = p.imap_unordered(mill_work, work)
                for proof, c in imap:
                    progress_next(n=c)
                    if proof is not None and proof_of_work is None:
                        proof_of_work = proof
            p.join()
            proof_start += worksize * cpus
        yield proof_of_work


def milling_generator(
    block: MillableBlock,
    mp: bool = False,  # noqa: FBT001
    rounds: int | None = None,
    worksize: int | None = None,
    progress: Any = None,
) -> Generator[int | None, None, None]:
    _rounds = rounds or 1
    _worksize = worksize or 100000
    progress_next: Callable[..., None] = (
        progress.next if progress else lambda n=1: None
    )
    milling_func = mill_block_mp if mp else mill_block
    miller = milling_func(block, _rounds, _worksize, progress_next)
    proof_of_work: int | None = None
    for proof_of_work in miller:
        if proof_of_work is not None:
            block.solve(proof_of_work)
        yield proof_of_work
