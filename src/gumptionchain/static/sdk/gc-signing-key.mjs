// GumptionChain browser signing key: Ed25519 keygen/import/export, bech32m
// address, gcsec secret, sign/verify. Pure Web Crypto + vanilla JS. No
// dependencies. Browser + Node 20+.
import { base64encode, base64decode, base64urlDecode } from './gc-crypto.mjs';
import { encodeAddress, encodeSecret, decodeSecret } from './gc-bech32.mjs';

const ALG = 'Ed25519';
// RFC 8410 Ed25519 PKCS8 prefix for a bare 32-byte seed (16 bytes); WebCrypto
// derives the public key from the seed on import, so no `x` is needed.
const PKCS8_PREFIX = Uint8Array.of(
  0x30, 0x2e, 0x02, 0x01, 0x00, 0x30, 0x05, 0x06, 0x03, 0x2b, 0x65, 0x70,
  0x04, 0x22, 0x04, 0x20,
);

export class SigningKey {
  #privateKey;
  #publicKey;

  constructor(privateKey, publicKey) {
    this.#privateKey = privateKey;
    this.#publicKey = publicKey;
  }

  // Feature-detect WebCrypto Ed25519. Some browsers/webviews (e.g. Chrome
  // before v137) lack it; without this probe, keygen/import/sign reject with
  // an opaque NotSupportedError. Returns true only if Ed25519 keygen works.
  static async isSupported() {
    try {
      if (typeof crypto === 'undefined' || !crypto?.subtle) return false;
      await crypto.subtle.generateKey(ALG, false, ['sign', 'verify']);
      return true;
    } catch {
      return false;
    }
  }

  static async generate() {
    const pair = await crypto.subtle.generateKey(ALG, true, ['sign', 'verify']);
    return new SigningKey(pair.privateKey, pair.publicKey);
  }

  static async fromSecret(gcsec) {
    const seed = decodeSecret(gcsec);
    if (seed === null) {
      throw new Error('invalid gcsec secret (bad checksum or HRP)');
    }
    const pkcs8 = new Uint8Array(PKCS8_PREFIX.length + 32);
    pkcs8.set(PKCS8_PREFIX, 0);
    pkcs8.set(seed, PKCS8_PREFIX.length);
    const priv = await crypto.subtle.importKey('pkcs8', pkcs8, ALG, true, [
      'sign',
    ]);
    // Derive the matching public key: the private JWK exposes `x` (the pubkey).
    const jwk = await crypto.subtle.exportKey('jwk', priv);
    const pub = await crypto.subtle.importKey(
      'jwk',
      { kty: 'OKP', crv: 'Ed25519', x: jwk.x, ext: true },
      ALG,
      true,
      ['verify'],
    );
    return new SigningKey(priv, pub);
  }

  static async fromMnemonic(mnemonic) {
    const { mnemonicToSeed } = await import('./gc-bip39.mjs');
    const seed = await mnemonicToSeed(mnemonic);
    return SigningKey.fromSecret(encodeSecret(seed));
  }

  async mnemonic() {
    const { seedToMnemonic } = await import('./gc-bip39.mjs');
    const gcsec = await this.exportSecret();
    return seedToMnemonic(decodeSecret(gcsec));
  }

  static async fromPublicKeyB64(b64) {
    const pub = await crypto.subtle.importKey(
      'spki',
      base64decode(b64),
      ALG,
      true,
      ['verify'],
    );
    return new SigningKey(null, pub);
  }

  async exportSecret() {
    if (!this.#privateKey) throw new Error('no private key');
    const jwk = await crypto.subtle.exportKey('jwk', this.#privateKey);
    return encodeSecret(base64urlDecode(jwk.d));
  }

  async #rawPublic() {
    return new Uint8Array(await crypto.subtle.exportKey('raw', this.#publicKey));
  }

  async publicKeyB64() {
    const spki = new Uint8Array(
      await crypto.subtle.exportKey('spki', this.#publicKey),
    );
    return base64encode(spki);
  }

  async address() {
    return encodeAddress(await this.#rawPublic());
  }

  async sign(bytes) {
    if (!this.#privateKey) throw new Error('no private key');
    const sig = await crypto.subtle.sign(ALG, this.#privateKey, bytes);
    return base64encode(new Uint8Array(sig));
  }

  async verify(bytes, signatureB64) {
    return crypto.subtle.verify(
      ALG,
      this.#publicKey,
      base64decode(signatureB64),
      bytes,
    );
  }
}
