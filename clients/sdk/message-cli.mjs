// gc-msg-v1 sign/verify harness (test tool, not a unit test). Used by the
// Python parity tests to prove JS<->Python message-signing interop.
//   node message-cli.mjs sign   '{"secret":"...","message":"...","timestamp":"..."}'
//   node message-cli.mjs verify '<proof JSON>'
import { SigningKey } from './gc-signing-key.mjs';
import { signMessage, verifyMessage } from './gc-message.mjs';

const mode = process.argv[2];
const arg = JSON.parse(process.argv[3]);

if (mode === 'sign') {
  const w = await SigningKey.fromSecret(arg.secret);
  const proof = await signMessage(w, arg.message, { timestamp: arg.timestamp });
  process.stdout.write(JSON.stringify(proof));
} else if (mode === 'verify') {
  process.stdout.write(JSON.stringify(await verifyMessage(arg)));
} else {
  process.stderr.write(`unknown mode: ${mode}\n`);
  process.exit(1);
}
