// Parity-exact reconstruction of a GumptionChain transaction's canonical
// form, txid, and signing payload — byte-identical to the Python
// implementation (src/gumptionchain/transaction.py + payload.py). Locked by
// Python-generated test vectors (tests/fixtures/txn_signing_vectors.json).
//
// The node builds an *unsigned, sealed* transaction from a public key and
// returns it as JSON (omitting None fields via asdict_sans_none). The browser
// independently recomputes the txid to catch a dishonest node, then signs
// `signing_data` with the imported key. This module is the only client-side
// port of the canonical serialization.
import { millHash } from './gc-crypto.mjs';

// Inflow.data_csv = f"{outflow_txid},{outflow_idx}"
const inflowCsv = (i) =>
  [String(i.outflow_txid), String(i.outflow_idx)].join(',');

// Outflow.data_csv = 6 comma-joined fields, '' where the field is absent.
// The server JSON omits None fields, so reconstruct each missing one as ''.
const outflowCsv = (o) =>
  [
    String(o.amount),
    o.address ?? '',
    o.opposition ?? '',
    o.rescind ?? '',
    o.support ?? '',
    o.rescind_kind ?? '',
  ].join(',');

// Transaction.data_csv = timestamp, address, public_key, <inflows>,
// <outflows>, version (+ prev_hash ONLY for coinbases — never produced here).
export function dataCsv(txn) {
  const fields = [
    String(txn.timestamp),
    String(txn.address),
    String(txn.public_key),
    (txn.inflows ?? []).map(inflowCsv).join(','),
    (txn.outflows ?? []).map(outflowCsv).join(','),
    String(txn.version),
  ];
  if (txn.prev_hash != null) {
    fields.push(String(txn.prev_hash));
  }
  return fields.join(',');
}

const hex = (bytes) =>
  [...bytes].map((b) => b.toString(16).padStart(2, '0')).join('');

// txid = sha256(sha512(data_csv)).hexdigest(); millHash does both digests.
export async function txid(txn) {
  const bytes = new TextEncoder().encode(dataCsv(txn));
  return hex(await millHash(bytes));
}

// signing_data = (data_csv + "," + txid).encode()
export function signingData(txn) {
  return new TextEncoder().encode(`${dataCsv(txn)},${txn.txid}`);
}

// Verify the node-built txn's self-reported txid against a fresh recompute
// (honesty check), then sign and return a txn ready to POST. Throws on
// mismatch so a dishonest node can't get a signature over fields the client
// didn't actually authorize.
export async function signUnsignedTxn(unsigned, wallet) {
  const recomputed = await txid({ ...unsigned, txid: undefined });
  if (recomputed !== unsigned.txid) {
    throw new Error(
      'txid mismatch: node-built txn does not match its fields',
    );
  }
  const signature = await wallet.sign(signingData(unsigned));
  return {
    ...unsigned,
    public_key: await wallet.publicKeyB64(),
    address: await wallet.address(),
    signature,
  };
}
