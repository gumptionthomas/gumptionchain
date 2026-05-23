import multiprocessing
from hashlib import sha256, sha512
from itertools import count


def mill_hash(data):
    if isinstance(data, str):
        data = data.encode()
    return sha256(sha512(data).digest())


def mill_hash_bin(data):
    return mill_hash(data).digest()


def mill_hash_str(data):
    return mill_hash(data).hexdigest()


def mill_work(w):
    work_start, work_stop, unproven_header, target = w
    for proof in range(work_start, work_stop):
        h = mill_hash_str(f'{unproven_header}{proof}')
        if int(h, 16) < target:
            return (proof, work_stop - proof)
    return (None, work_stop - work_start)


def mill_block(block, rounds, worksize, progress_next):
    target = int(block.target, 16)
    unproven_header = block.unproven_header
    proof_of_work = None
    proof_start = 0
    r = range(rounds) if rounds else count()
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


def work_generator(unproven_header, target, start, worksize, num):
    for i in range(num):
        work_start = start + (i * worksize)
        yield (work_start, work_start + worksize, unproven_header, target)


def mill_block_mp(block, rounds, worksize, progress_next):
    cpus = multiprocessing.cpu_count()
    target = int(block.target, 16)
    unproven_header = block.unproven_header
    proof_of_work = None
    proof_start = 0
    r = range(rounds) if rounds else count()
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
    block, mp=False, rounds=None, worksize=None, progress=None
):
    rounds = rounds or 1
    worksize = worksize or 100000
    progress_next = progress.next if progress else lambda n=1: None
    milling_func = mill_block_mp if mp else mill_block
    miller = milling_func(block, rounds, worksize, progress_next)
    proof_of_work = None
    for proof_of_work in miller:
        if proof_of_work is not None:
            block.solve(proof_of_work)
        yield proof_of_work
