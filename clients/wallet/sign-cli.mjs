// GumptionChain gc-sig-v1 signing CLI (test harness, not a unit test).
// Reads a JSON request from argv[2], signs it with the given b58 private key,
// and prints {address, signature} JSON to stdout. Used by the Python live
// cross-verify test to prove byte-for-byte gc-sig parity without fixtures.
import { Wallet } from './gc-wallet.mjs';
import { canonical } from './gc-sig.mjs';

const req = JSON.parse(process.argv[2]);
const w = await Wallet.fromPrivateKeyB58(req.private_key_b58);
const address = await w.address();
const bytes = await canonical({
  method: req.method,
  path: req.path,
  query: req.query,
  body: new TextEncoder().encode(req.body ?? ''),
  nodeHost: req.node_host,
  timestamp: req.timestamp,
  address,
});
const signature = await w.sign(bytes);
process.stdout.write(JSON.stringify({ address, signature }));
