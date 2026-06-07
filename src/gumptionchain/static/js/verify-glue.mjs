// Base verify glue: run verifyStake (from the vendored wallet module) over a
// proof, fetching provenance from the node's public endpoint. Pure logic in
// runVerify (fetchProvenance injectable for tests); renderVerdict lights the
// DOM verdict.
import { verifyStake } from '../wallet/gc-attestation.mjs';

// Adapter: node public provenance endpoint. 404 -> null (unknown txn); other
// failures propagate so they are NOT misreported as 'txn-not-found'.
export function nodeFetchProvenance(origin = '') {
  return async (txid) => {
    // Encode the path segment so a malformed txid (containing /, ?, #, …)
    // can't reshape the request into an unintended path/query.
    const resp = await fetch(
      `${origin}/transaction/${encodeURIComponent(txid)}/provenance.json`,
    );
    if (resp.status === 404) return null;
    if (!resp.ok) {
      throw new Error(`provenance fetch failed: ${resp.status}`);
    }
    return resp.json();
  };
}

export async function runVerify(proof, { fetchProvenance, minConfirmations } = {}) {
  return verifyStake(proof, {
    fetchProvenance: fetchProvenance ?? nodeFetchProvenance(),
    minConfirmations,
  });
}

// Light up the three checks + overall seal in the DOM. Expects elements with
// data-check="signature|onchain|consistent" and id="verdict-seal".
export function renderVerdict(verdict, root = document) {
  for (const key of ['signature', 'onchain', 'consistent']) {
    const el = root.querySelector(`[data-check="${key}"]`);
    if (el) {
      el.classList.toggle('check-pass', !!verdict.checks[key]);
      el.classList.toggle('check-fail', !verdict.checks[key]);
    }
  }
  const seal = root.querySelector('#verdict-seal');
  if (seal) seal.classList.toggle('verified', verdict.valid);
}
