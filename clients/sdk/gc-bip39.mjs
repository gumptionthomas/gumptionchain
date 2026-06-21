// BIP-39 codec over a raw 32-byte seed: seed <-> 24-word mnemonic with an
// 8-bit SHA-256 checksum. This is the standard entropy<->mnemonic mapping
// (NOT BIP-39's PBKDF2 seed-stretching) — the words ARE the seed. Pure Web
// Crypto + vanilla JS. No dependencies.
import { WORDLIST } from './gc-bip39-wordlist.mjs';

async function sha256(bytes) {
  return new Uint8Array(await crypto.subtle.digest('SHA-256', bytes));
}

export async function seedToMnemonic(seed) {
  if (!(seed instanceof Uint8Array) || seed.length !== 32) {
    throw new Error('seedToMnemonic requires a 32-byte seed');
  }
  const hash = await sha256(seed);
  const bits = [];
  for (const b of seed) {
    for (let i = 7; i >= 0; i--) bits.push((b >> i) & 1);
  }
  for (let i = 7; i >= 0; i--) bits.push((hash[0] >> i) & 1);
  const words = [];
  for (let i = 0; i < bits.length; i += 11) {
    let idx = 0;
    for (let j = 0; j < 11; j++) idx = (idx << 1) | bits[i + j];
    words.push(WORDLIST[idx]);
  }
  return words.join(' ');
}

export async function mnemonicToSeed(mnemonic) {
  const words = String(mnemonic).trim().split(/\s+/);
  if (words.length !== 24) {
    throw new Error('expected a 24-word recovery phrase');
  }
  const bits = [];
  for (const w of words) {
    const idx = WORDLIST.indexOf(w);
    if (idx < 0) throw new Error(`invalid recovery word: ${w}`);
    for (let j = 10; j >= 0; j--) bits.push((idx >> j) & 1);
  }
  const seed = new Uint8Array(32);
  for (let i = 0; i < 256; i++) seed[i >> 3] |= bits[i] << (7 - (i % 8));
  const hash = await sha256(seed);
  for (let i = 0; i < 8; i++) {
    if (bits[256 + i] !== ((hash[0] >> (7 - i)) & 1)) {
      throw new Error('recovery phrase checksum failed');
    }
  }
  return seed;
}
