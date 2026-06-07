// GumptionChain browser wallet: keygen, key import/export, address, sign.
// Pure Web Crypto + vanilla JS. No dependencies. Browser + Node 20+.
import {
  base58encode, base58decode, base64encode, base64decode, millHash,
} from './gc-crypto.mjs';

const ALG = { name: 'RSASSA-PKCS1-v1_5' };
const KEYGEN = {
  name: 'RSASSA-PKCS1-v1_5',
  modulusLength: 2048,
  publicExponent: new Uint8Array([0x01, 0x00, 0x01]),
  hash: 'SHA-384',
};
const IMPORT_PARAMS = { name: 'RSASSA-PKCS1-v1_5', hash: 'SHA-384' };
const ADDRESS_TAG = 'GC';
const KEY_SIZE = 2048;
const PUBLIC_EXPONENT = Uint8Array.of(0x01, 0x00, 0x01); // 65537

// Mirror the node's Wallet key-profile guard: reject any imported key that is
// not RSA-2048 with e=65537, so a client can't mint an identity/signature the
// Python node will always reject. (Web Crypto exposes both on key.algorithm.)
function assertKeyProfile(key) {
  const { modulusLength, publicExponent } = key.algorithm;
  if (modulusLength !== KEY_SIZE) {
    throw new Error(
      `unsupported RSA modulus length ${modulusLength} (want ${KEY_SIZE})`,
    );
  }
  const e = new Uint8Array(publicExponent);
  const ok =
    e.length === PUBLIC_EXPONENT.length &&
    e.every((b, i) => b === PUBLIC_EXPONENT[i]);
  if (!ok) {
    throw new Error('unsupported RSA public exponent (want 65537)');
  }
}

export class Wallet {
  #privateKey;
  #publicKey;

  constructor(privateKey, publicKey) {
    this.#privateKey = privateKey;
    this.#publicKey = publicKey;
  }

  static async generate() {
    const pair = await crypto.subtle.generateKey(KEYGEN, true, [
      'sign',
      'verify',
    ]);
    return new Wallet(pair.privateKey, pair.publicKey);
  }

  static async fromPrivateKeyB58(b58) {
    const pkcs8 = base58decode(b58);
    const priv = await crypto.subtle.importKey(
      'pkcs8',
      pkcs8,
      IMPORT_PARAMS,
      true,
      ['sign'],
    );
    assertKeyProfile(priv);
    const jwk = await crypto.subtle.exportKey('jwk', priv);
    const pubJwk = { kty: jwk.kty, n: jwk.n, e: jwk.e, ext: true };
    const pub = await crypto.subtle.importKey(
      'jwk',
      pubJwk,
      IMPORT_PARAMS,
      true,
      ['verify'],
    );
    return new Wallet(priv, pub);
  }

  static async fromPublicKeyB64(b64) {
    const pub = await crypto.subtle.importKey(
      'spki',
      base64decode(b64),
      IMPORT_PARAMS,
      true,
      ['verify'],
    );
    assertKeyProfile(pub);
    return new Wallet(null, pub);
  }

  async exportPrivateKeyB58() {
    if (!this.#privateKey) {
      throw new Error('no private key');
    }
    const pkcs8 = new Uint8Array(
      await crypto.subtle.exportKey('pkcs8', this.#privateKey),
    );
    return base58encode(pkcs8);
  }

  async #spki() {
    return new Uint8Array(await crypto.subtle.exportKey('spki', this.#publicKey));
  }

  async publicKeyB64() {
    return base64encode(await this.#spki());
  }

  async address() {
    const digest = await millHash(await this.#spki());
    return `${ADDRESS_TAG}${base58encode(digest)}${ADDRESS_TAG}`;
  }

  async sign(bytes) {
    if (!this.#privateKey) {
      throw new Error('no private key');
    }
    const sig = await crypto.subtle.sign(ALG, this.#privateKey, bytes);
    return base64encode(new Uint8Array(sig));
  }

  async verify(bytes, signatureB64) {
    return crypto.subtle.verify(ALG, this.#publicKey, base64decode(signatureB64), bytes);
  }
}
