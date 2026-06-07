// Stake-attestation harness (test tool). Modes:
//   build  '{claim}'                          -> canonical message string
//   sign   '{"private_key_b58":..,"claim":{..},"timestamp":".."}' -> proof JSON
//   verify '{"proof":{..},"provenance":{..}|null,"minConfirmations":..}' -> verdict
import { Wallet } from './gc-wallet.mjs';
import {
  buildStakeMessage, signStakeAttestation, verifyStake,
} from './gc-attestation.mjs';

const mode = process.argv[2];
const arg = JSON.parse(process.argv[3]);

if (mode === 'build') {
  process.stdout.write(buildStakeMessage(arg));
} else if (mode === 'sign') {
  const w = await Wallet.fromPrivateKeyB58(arg.private_key_b58);
  const proof = await signStakeAttestation(w, arg.claim, { timestamp: arg.timestamp });
  process.stdout.write(JSON.stringify(proof));
} else if (mode === 'verify') {
  const verdict = await verifyStake(arg.proof, {
    fetchProvenance: async () => arg.provenance ?? null,
    minConfirmations: arg.minConfirmations,
  });
  process.stdout.write(JSON.stringify(verdict));
} else {
  process.stderr.write(`unknown mode: ${mode}\n`);
  process.exit(1);
}
