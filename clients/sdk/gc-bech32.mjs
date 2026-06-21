// Copyright (c) 2017, 2021 Pieter Wuille
//
// Permission is hereby granted, free of charge, to any person obtaining a copy
// of this software and associated documentation files (the "Software"), to deal
// in the Software without restriction, including without limitation the rights
// to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
// copies of the Software, and to permit persons to whom the Software is
// furnished to do so, subject to the following conditions:
//
// The above copyright notice and this permission notice shall be included in
// all copies or substantial portions of the Software.
//
// THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
// IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
// FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
// AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
// LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
// OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
// SOFTWARE.
const CHARSET = 'qpzry9x8gf2tvdw0s3jn54khce6mua7l';
const GENERATOR = [0x3b6a57b2, 0x26508e6d, 0x1ea119fa, 0x3d4233dd, 0x2a1462b3];
const BECH32M = 'bech32m';
const BECH32M_CONST = 0x2bc830a3;

function getEncodingConst(enc) {
  return enc === BECH32M ? BECH32M_CONST : 1;
}
function polymod(values) {
  let chk = 1;
  for (let p = 0; p < values.length; ++p) {
    const top = chk >> 25;
    chk = ((chk & 0x1ffffff) << 5) ^ values[p];
    for (let i = 0; i < 5; ++i) if ((top >> i) & 1) chk ^= GENERATOR[i];
  }
  return chk;
}
function hrpExpand(hrp) {
  const ret = [];
  let p;
  for (p = 0; p < hrp.length; ++p) ret.push(hrp.charCodeAt(p) >> 5);
  ret.push(0);
  for (p = 0; p < hrp.length; ++p) ret.push(hrp.charCodeAt(p) & 31);
  return ret;
}
function verifyChecksum(hrp, data, enc) {
  return polymod(hrpExpand(hrp).concat(data)) === getEncodingConst(enc);
}
function createChecksum(hrp, data, enc) {
  const values = hrpExpand(hrp).concat(data).concat([0, 0, 0, 0, 0, 0]);
  const mod = polymod(values) ^ getEncodingConst(enc);
  const ret = [];
  for (let p = 0; p < 6; ++p) ret.push((mod >> (5 * (5 - p))) & 31);
  return ret;
}
function bech32Encode(hrp, data, enc) {
  const combined = data.concat(createChecksum(hrp, data, enc));
  let ret = `${hrp}1`;
  for (let p = 0; p < combined.length; ++p) ret += CHARSET.charAt(combined[p]);
  return ret;
}
function bech32Decode(bechString, enc) {
  let hasLower = false;
  let hasUpper = false;
  let p;
  for (p = 0; p < bechString.length; ++p) {
    const c = bechString.charCodeAt(p);
    if (c < 33 || c > 126) return null;
    if (c >= 97 && c <= 122) hasLower = true;
    if (c >= 65 && c <= 90) hasUpper = true;
  }
  if (hasLower && hasUpper) return null;
  bechString = bechString.toLowerCase();
  const pos = bechString.lastIndexOf('1');
  if (pos < 1 || pos + 7 > bechString.length || bechString.length > 90) {
    return null;
  }
  const hrp = bechString.substring(0, pos);
  const data = [];
  for (p = pos + 1; p < bechString.length; ++p) {
    const d = CHARSET.indexOf(bechString.charAt(p));
    if (d === -1) return null;
    data.push(d);
  }
  if (!verifyChecksum(hrp, data, enc)) return null;
  return { hrp, data: data.slice(0, data.length - 6) };
}
function convertbits(data, frombits, tobits, pad) {
  let acc = 0;
  let bits = 0;
  const ret = [];
  const maxv = (1 << tobits) - 1;
  for (let p = 0; p < data.length; ++p) {
    const value = data[p];
    if (value < 0 || value >> frombits !== 0) return null;
    acc = (acc << frombits) | value;
    bits += frombits;
    while (bits >= tobits) {
      bits -= tobits;
      ret.push((acc >> bits) & maxv);
    }
  }
  if (pad) {
    if (bits > 0) ret.push((acc << (tobits - bits)) & maxv);
  } else if (bits >= frombits || ((acc << (tobits - bits)) & maxv)) {
    return null;
  }
  return ret;
}

// --- GumptionChain helpers: gc1… addresses, gcsec1… secrets (bech32m of 32B).
const HRP_ADDRESS = 'gc';
const HRP_SECRET = 'gcsec';

function encode32(hrp, bytes32) {
  if (bytes32.length !== 32) {
    throw new Error(`expected 32 bytes, got ${bytes32.length}`);
  }
  const data = convertbits([...bytes32], 8, 5, true);
  if (data === null) throw new Error('convertbits failed');
  return bech32Encode(hrp, data, BECH32M);
}
function decode32(hrp, str) {
  const dec = bech32Decode(str, BECH32M);
  if (dec === null || dec.hrp !== hrp) return null;
  const bytes = convertbits(dec.data, 5, 8, false);
  if (bytes === null || bytes.length !== 32) return null;
  return Uint8Array.from(bytes);
}

export const encodeAddress = (pubkey) => encode32(HRP_ADDRESS, pubkey);
export const decodeAddress = (addr) => decode32(HRP_ADDRESS, addr);
export const encodeSecret = (seed) => encode32(HRP_SECRET, seed);
export const decodeSecret = (s) => decode32(HRP_SECRET, s);
