// Pendrive upload, all in the browser (her phone): fingerprint each file (SHA-1, the checksum
// Immich itself uses), ask Immich which ones it already has, upload the rest, and put
// everything in the pendrive's album. Re-selecting the same files after an interruption
// skips whatever is already in Immich.

// Streaming SHA-1: hashes multi-GB videos in 4 MB slices without loading them into memory
// (crypto.subtle.digest only takes a whole buffer).
export class Sha1 {
  constructor() {
    this.h = new Int32Array([0x67452301, 0xefcdab89, 0x98badcfe, 0x10325476, 0xc3d2e1f0]);
    this.block = new Uint8Array(64);
    this.used = 0;
    this.length = 0;
    this.w = new Int32Array(80);
  }

  update(bytes) {
    this.length += bytes.length;
    let i = 0;
    if (this.used) {
      const n = Math.min(64 - this.used, bytes.length);
      this.block.set(bytes.subarray(0, n), this.used);
      this.used += n;
      i = n;
      if (this.used < 64) return;
      this.compress(this.block, 0);
      this.used = 0;
    }
    for (; i + 64 <= bytes.length; i += 64) this.compress(bytes, i);
    if (i < bytes.length) {
      this.block.set(bytes.subarray(i), 0);
      this.used = bytes.length - i;
    }
  }

  compress(bytes, at) {
    const w = this.w;
    for (let t = 0; t < 16; t++) {
      const p = at + t * 4;
      w[t] = (bytes[p] << 24) | (bytes[p + 1] << 16) | (bytes[p + 2] << 8) | bytes[p + 3];
    }
    for (let t = 16; t < 80; t++) {
      const x = w[t - 3] ^ w[t - 8] ^ w[t - 14] ^ w[t - 16];
      w[t] = (x << 1) | (x >>> 31);
    }
    let [a, b, c, d, e] = this.h;
    for (let t = 0; t < 80; t++) {
      let f, k;
      if (t < 20) { f = (b & c) | (~b & d); k = 0x5a827999; }
      else if (t < 40) { f = b ^ c ^ d; k = 0x6ed9eba1; }
      else if (t < 60) { f = (b & c) | (b & d) | (c & d); k = 0x8f1bbcdc; }
      else { f = b ^ c ^ d; k = 0xca62c1d6; }
      const next = (((a << 5) | (a >>> 27)) + f + e + k + w[t]) | 0;
      e = d; d = c; c = (b << 30) | (b >>> 2); b = a; a = next;
    }
    this.h[0] += a; this.h[1] += b; this.h[2] += c; this.h[3] += d; this.h[4] += e;
  }

  hex() {
    const bits = this.length * 8;
    const pad = new Uint8Array(((this.used < 56 ? 56 : 120) - this.used) + 8);
    pad[0] = 0x80;
    const view = new DataView(pad.buffer);
    view.setUint32(pad.length - 8, Math.floor(bits / 2 ** 32));
    view.setUint32(pad.length - 4, bits >>> 0);
    this.update(pad);
    return Array.from(this.h, (x) => (x >>> 0).toString(16).padStart(8, '0')).join('');
  }
}

export async function sha1File(file, onBytes) {
  const hash = new Sha1();
  const SLICE = 4 * 1024 * 1024;
  for (let at = 0; at < file.size; at += SLICE) {
    const chunk = new Uint8Array(await file.slice(at, at + SLICE).arrayBuffer());
    hash.update(chunk);
    onBytes?.(chunk.length);
  }
  return hash.hex();
}

// WhatsApp, Android camera, Pixel and screenshot names carry the real date:
// IMG-20190512-WA0001.jpg, VID_20200101_183000.mp4, PXL_20210630_101112345.jpg,
// Screenshot_2022-03-04-10-11-12.png ... Used only when the file has no date inside it
// (Immich prefers the photo's own EXIF date).
export function dateFromName(name, now = new Date()) {
  const m = name.match(/(?:^|[^0-9])((?:19|20)\d{2})[-_.]?(\d{2})[-_.]?(\d{2})(?:(?:[-_ T.]| at )?(\d{2})[-_.:]?(\d{2})[-_.:]?(\d{2})(?:\d{3})?)?(?!\d)/);
  if (!m) return null;
  const [y, mo, d, h = '12', mi = '00', s = '00'] = m.slice(1).map((v) => v ?? undefined);
  const date = new Date(Date.UTC(+y, +mo - 1, +d, +h, +mi, +s));
  const valid = +mo >= 1 && +mo <= 12 && +d >= 1 && +d <= 31 && +h < 24 && +mi < 60 && +s < 60
    && date.getUTCMonth() === +mo - 1 && date <= now && +y >= 1995;
  return valid ? date : null;
}

export const MEDIA = /\.(jpe?g|png|heic|heif|webp|gif|bmp|tiff?|dng|cr2|cr3|nef|arw|raf|orf|rw2|mp4|mov|m4v|3gp|mkv|avi|mts|m2ts|webm|wmv|mpe?g)$/i;
