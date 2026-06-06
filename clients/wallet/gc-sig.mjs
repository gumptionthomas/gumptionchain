// GumptionChain gc-sig-v1 canonical string + GC-* signing headers.
// Pure Web Crypto + vanilla JS. No dependencies. Browser + Node 20+.
import { sha256Hex } from './gc-crypto.mjs';

const SIG_SCHEME = 'gc-sig-v1';
const SIG_VERSION = '1';

export async function canonical({
  method,
  path,
  query,
  body,
  nodeHost,
  timestamp,
  address,
}) {
  const bodyDigest = await sha256Hex(body ?? new Uint8Array());
  const lines = [
    SIG_SCHEME,
    method.toUpperCase(),
    path,
    query,
    bodyDigest,
    nodeHost,
    timestamp,
    address,
  ];
  return new TextEncoder().encode(lines.join('\n'));
}

export async function signHeaders(
  wallet,
  { method, path, query, body, nodeHost, timestamp },
) {
  const address = await wallet.address();
  const bytes = await canonical({
    method,
    path,
    query,
    body,
    nodeHost,
    timestamp,
    address,
  });
  return {
    'GC-Sig-Version': SIG_VERSION,
    'GC-Address': address,
    'GC-Public-Key': await wallet.publicKeyB64(),
    'GC-Timestamp': String(timestamp),
    'GC-Signature': await wallet.sign(bytes),
  };
}
