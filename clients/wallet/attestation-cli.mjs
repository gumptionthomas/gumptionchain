// Stake-attestation + social-binding harness (test tool). Modes:
//   build         '{claim}'                                          -> canonical message string
//   sign          '{"private_key_b58":..,"claim":{..},"timestamp":".."}' -> proof JSON
//   verify        '{"proof":{..},"provenance":{..}|null,"minConfirmations":..}' -> verdict
//   build-binding '{claim}'                                          -> canonical binding message
//   sign-binding  '{"private_key_b58":..,"claim":{..},"timestamp":".."}' -> proof JSON
//   verify-binding '{"proof":{..},"maxAge":..}'                     -> verdict JSON
import { Wallet } from './gc-wallet.mjs';
import {
  buildStakeMessage, signStakeAttestation, verifyStake,
  buildBindingMessage, signSocialBinding, verifyBinding,
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
} else if (mode === 'build-binding') {
  process.stdout.write(buildBindingMessage(arg));
} else if (mode === 'sign-binding') {
  const w = await Wallet.fromPrivateKeyB58(arg.private_key_b58);
  const proof = await signSocialBinding(w, arg.claim, { timestamp: arg.timestamp });
  process.stdout.write(JSON.stringify(proof));
} else if (mode === 'verify-binding') {
  const verdict = await verifyBinding(arg.proof, { maxAge: arg.maxAge });
  process.stdout.write(JSON.stringify(verdict));
} else {
  process.stderr.write(`unknown mode: ${mode}\n`);
  process.exit(1);
}
