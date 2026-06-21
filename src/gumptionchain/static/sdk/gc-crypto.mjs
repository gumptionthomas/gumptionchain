// Pure Web Crypto + vanilla JS. No dependencies. Browser + Node 20+.
export function base64encode(bytes) {
  let bin = '';
  for (let i = 0; i < bytes.length; i++) bin += String.fromCharCode(bytes[i]);
  return btoa(bin);
}

export function base64decode(str) {
  const bin = atob(str);
  const out = new Uint8Array(bin.length);
  for (let i = 0; i < bin.length; i++) out[i] = bin.charCodeAt(i);
  return out;
}

export function base64urlDecode(str) {
  const pad = str.length % 4 ? '='.repeat(4 - (str.length % 4)) : '';
  return base64decode(str.replace(/-/g, '+').replace(/_/g, '/') + pad);
}

export async function millHash(bytes) {
  const inner = await crypto.subtle.digest('SHA-512', bytes);
  const outer = await crypto.subtle.digest('SHA-256', inner);
  return new Uint8Array(outer);
}

export async function sha256Hex(bytes) {
  const d = new Uint8Array(await crypto.subtle.digest('SHA-256', bytes));
  return Array.from(d, (b) => b.toString(16).padStart(2, '0')).join('');
}
