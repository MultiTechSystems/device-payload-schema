#!/usr/bin/env python3
"""
generate_ts013_codec.py - Generate TS013-compliant JavaScript codec from Payload Schema YAML.

Generates a self-contained JS file implementing:
  - decodeUplink(input)   → { data, warnings, errors }
  - encodeDownlink(input)  → { bytes, fPort, warnings, errors }
  - decodeDownlink(input)  → { data, warnings, errors }

Supports all Phase 2 features: flagged, ports, tlv, bitfield_string, formula,
match, byte_group, modifiers, enum, repeat.

Usage:
    python tools/generate_ts013_codec.py schema.yaml [-o output.js]
    python tools/generate_ts013_codec.py schemas/ -o generated/
"""

import argparse
import json
import re
import sys
import yaml
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Set, Tuple


#: What a port entry declaring no `direction` means (PS-287). `both` keeps the annotation
#: opt-in: a schema that says nothing about a port keeps that port reachable from either
#: TS013 entry point, rather than being narrowed to uplink after the fact (CR-2026-010).
DEFAULT_PORT_DIRECTION = 'both'


#: The runtime encoder every generated codec carries. A port of the reference's encode
#: path; see the comment at its head.
ENCODER_RUNTIME_JS = r'''// --- Encoder ---
// The inverse of the decoder, as a port of tools/schema_interpreter.py's `encode` and
// `_encode_field_list`. It walks the schema's field list, embedded beside it as JSON,
// rather than being unrolled field by field at generation time: the unrolled encoder
// was a second hand-maintained copy of the reference's rules, and it drifted - it undid
// modifiers in key order, never undid a transform stage, a lookup or an encoding, wrote
// a float as an integer, and dropped every match, object and repeat.
//
// Each step mirrors the reference function named beside it, so a change to one should
// be carried to the other. ENC_TYPES, ENC_VARS and ENC_ENDIAN_OPAQUE are emitted by the
// generator from the reference's own tables.

function encFail(msg) { throw new Error(msg); }

function encIsNumber(v) { return typeof v === "number" && isFinite(v); }

/* Python's round(): half to even. Math.round is half up, and asymmetric for negatives. */
function encRint(v) {
  var r = Math.round(v);
  if (Math.abs(v % 1) === 0.5 && r % 2 !== 0) r -= 1;
  return r;
}

function encLabelText(label) {                       // label_text
  if (typeof label === "string") return "'" + label + "'";
  return String(label);
}

function encShared(name, label, codes) {              // shared_label_error (PS-169)
  var sorted = codes.slice().sort(function (a, b) { return a - b; });
  encFail("Field '" + name + "': the label " + encLabelText(label) + " is shared by codes [" +
          sorted.join(", ") + "], so it cannot be encoded (PS-169)");
}

function encReverseLookup(v, lookup, name) {          // reverse_lookup
  // PS-513 (same_label): === never matches true to 1 or "true" to true. Only a string
  // was looked up, so a boolean passed through and wrote its own 0 or 1.
  // PS-169 (CR-2026-146): a label two or more codes share is rejected, naming `name`;
  // with no name - a caller ranking candidate cases - the smallest code is taken.
  if (!lookup || v === null || v === undefined) return v;
  var codes = [], c;
  if (Array.isArray(lookup)) {
    for (c = 0; c < lookup.length; c++) if (lookup[c] === v) codes.push(c);
    if (codes.length > 1 && name !== undefined) encShared(name, v, codes);
    return codes.length ? codes[0] : v;
  }
  var template = null;
  for (var k in lookup) {
    if (!Object.prototype.hasOwnProperty.call(lookup, k)) continue;
    if (k === "default") {
      if (typeof lookup[k] === "string" && lookup[k].indexOf("${value}") >= 0) template = lookup[k];
      continue;
    }
    if (lookup[k] === v) {
      var n = Number(k);
      if (!(isFinite(n) && String(k).trim() !== "")) return v;
      codes.push(n);
    }
  }
  if (codes.length) {
    if (codes.length > 1 && name !== undefined) encShared(name, v, codes);
    return Math.min.apply(null, codes);
  }
  if (template !== null && typeof v === "string") {
    var cut = template.indexOf("${value}");
    var head = template.slice(0, cut), tail = template.slice(cut + 8);
    if (v.length > head.length + tail.length && v.indexOf(head) === 0 &&
        v.slice(v.length - tail.length) === tail) {
      var text = v.slice(head.length, v.length - tail.length);
      if (/^-?\d+(\.\d+)?([eE][-+]?\d+)?$/.test(text)) return Number(text);
    }
  }
  return v;
}

function encReverseStages(v, stages) {                // reverse_transform_stages
  if (!encIsNumber(v) || !stages) return v;
  for (var s = stages.length - 1; s >= 0; s--) {
    var st = stages[s];
    if (st === null || typeof st !== "object") continue;
    if ("add" in st) v = v - Number(st.add);
    else if ("mult" in st) {
      if (Number(st.mult) === 0) encFail("cannot undo 'mult: 0'");
      v = v / Number(st.mult);
    }
    else if ("div" in st) v = v * Number(st.div);
    else if ("round" in st || "op" in st) continue;
    else if ("floor" in st || "ceiling" in st || "clamp" in st) continue;
    else encFail("cannot undo transform stage: " + (Object.keys(st).sort().join(", ") || "empty stage"));
  }
  return v;
}

function encReverseCanonical(v, f) {                  // reverse_canonical_modifiers
  if (f.add !== undefined && f.add !== null) v = v - f.add;
  if (f.div !== undefined && f.div !== null) v = v * f.div;
  if (f.mult !== undefined && f.mult !== null && f.mult !== 0) v = v / f.mult;
  return v;
}

var ENC_FLOAT_TYPES = ["f16", "bf16", "f32", "f64", "udec", "sdec", "f32le16", "f32be16le",
                       "uflt16", "sflt16", "sflt24"];

/* PS-168, PS-552: the value as the input gave it, for a rejection to name (_given_value). */
var encGivenRec = null;
function encGiven(f, v) { return (encGivenRec && encGivenRec.f === f) ? encGivenRec.v : v; }

function encBounds(bits, signed) {                    // integer_bounds
  return signed ? [-Math.pow(2, bits - 1), Math.pow(2, bits - 1) - 1] : [0, Math.pow(2, bits) - 1];
}

function encFits(raw, bits, signed, f, given, rangeType) {   // check_fits
  if (bits > 48 || typeof raw !== "number") return raw;   // 64-bit: encIntBytes checks exactly
  var b = encBounds(bits, signed);
  if (raw < b[0] || raw > b[1]) {
    encFail("Field '" + f.name + "': " + String(given) + " encodes as " + raw + ", outside " +
            b[0] + " to " + b[1] + (rangeType ? " for " + rangeType : "") + " (PS-168)");
  }
  return raw;
}

function encReverseModifiers(v, f) {                  // _reverse_modifiers
  encGivenRec = { f: f, v: v };
  v = encReverseLookup(v, f.lookup, f.name);
  if (typeof v === "boolean" && f.lookup) {
    encFail(v + " is not a label with a key of its own in the lookup for " +
            JSON.stringify(f.name) + "; a boolean matches only a boolean label, and a " +
            "`default` label has no value to recover (PS-409, PS-513)");
  }
  if (typeof v === "string" && f.lookup) {
    encFail(JSON.stringify(v) + " is not a label in the lookup for " + JSON.stringify(f.name) +
            "; a `default` label matches any unmapped value, so the value that produced " +
            "it cannot be recovered");
  }
  if (!encIsNumber(v)) return v;
  if (f.encode_formula) encFail("encode_formula is not supported by the generated codec");
  v = encReverseStages(v, f.transform);
  v = encReverseCanonical(v, f);
  if (ENC_FLOAT_TYPES.indexOf(String(f.type || "u8")) >= 0) return v;
  return encRint(v);
}

/* Big-endian bytes of an integer: a number, or the decimal string the decoder reports
 * above 2^53 (PS-296). Two's complement for a negative value. */
function encIntBytes(value, size, signed) {
  var neg = false, digits;
  if (typeof value === "string") {
    var text = value.trim();
    if (text.charAt(0) === "-") { neg = true; text = text.slice(1); }
    if (!/^\d+$/.test(text)) encFail("cannot encode " + JSON.stringify(value) + " as an integer");
    digits = text.split("").map(Number);
  } else {
    if (typeof value === "boolean") value = value ? 1 : 0;
    if (!encIsNumber(value)) encFail("cannot encode " + JSON.stringify(value) + " as an integer");
    value = value < 0 ? Math.ceil(value) : Math.floor(value);    // Python int()
    if (value < 0) { neg = true; value = -value; }
    digits = String(value).indexOf("e") >= 0 ? null : String(value).split("").map(Number);
    if (digits === null) encFail(value + " is too large to encode exactly");
  }
  if (neg && !signed) encFail("can't convert negative int to unsigned");
  var bytes = [];
  for (var i = 0; i < size; i++) {
    var rem = 0, next = [];
    for (var j = 0; j < digits.length; j++) {
      var cur = rem * 10 + digits[j];
      var q = Math.floor(cur / 256);
      rem = cur % 256;
      if (next.length || q) next.push(q);
    }
    bytes.unshift(rem);
    digits = next.length ? next : [0];
  }
  if (digits.length > 1 || digits[0] !== 0) encFail("int too big to convert");
  if (neg) {
    var carry = 1;
    for (var b = size - 1; b >= 0; b--) {
      var inv = (~bytes[b] & 0xFF) + carry;
      bytes[b] = inv & 0xFF;
      carry = inv >> 8;
    }
    if ((bytes[0] & 0x80) === 0 && !bytes.every(function (x) { return x === 0; })) encFail("int too big to convert");
  } else if (signed && (bytes[0] & 0x80) !== 0) {
    encFail("int too big to convert");
  }
  return bytes;
}

function encWriteInt(out, value, size, signed, endian) {
  var bytes = encIntBytes(value, size, signed);
  if (endian === "little") bytes.reverse();
  for (var i = 0; i < bytes.length; i++) out.push(bytes[i]);
}

/* IEEE 754 binary16 from a double, rounding to nearest even, as struct.pack('e'). */
function encHalf(v) {
  var dv = new DataView(new ArrayBuffer(8));
  if (isNaN(v)) return 0x7E00;
  var sign = (v < 0 || (v === 0 && 1 / v < 0)) ? 0x8000 : 0;
  var a = Math.abs(v);
  if (a === Infinity) return sign | 0x7C00;
  if (a === 0) return sign;
  var e = Math.floor(Math.log2(a));
  if (Math.pow(2, e) > a) e -= 1;
  if (Math.pow(2, e + 1) <= a) e += 1;
  var m, h;
  if (e < -14) {
    m = encRint(a / Math.pow(2, -24));                   // subnormal
    h = m;                                               // may carry into the exponent
  } else {
    m = encRint((a / Math.pow(2, e) - 1) * 1024);
    if (m === 1024) { m = 0; e += 1; }
    if (e > 15) encFail("float too large to pack with e format");
    h = ((e + 15) << 10) | m;
  }
  if (h >= 0x7C00) encFail("float too large to pack with e format");
  return sign | h;
}

/* bfloat16 from a double (encode_bf16, PS-559): rounded once from the binary64 value, a tie to
 * the even fraction, never through a binary32; a finite value past the largest bf16 is a
 * PS-168 error naming the field. */
var ENC_BF16_MAX = (2 - Math.pow(2, -7)) * Math.pow(2, 127);
function encBf16(v, f) {
  if (isNaN(v)) return 0x7FC0;
  var sign = (v < 0 || (v === 0 && 1 / v < 0)) ? 0x8000 : 0;
  var a = Math.abs(v);
  if (a === Infinity) return sign | 0x7F80;
  var word, e = 0, m;
  if (a !== 0) {
    e = Math.floor(Math.log2(a));
    if (Math.pow(2, e) > a) e -= 1;
    if (Math.pow(2, e + 1) <= a) e += 1;
  }
  if (a === 0 || e < -126) {
    word = encRint(a * Math.pow(2, 133));             // subnormal: fraction * 2**-133
  } else {
    m = encRint(a / Math.pow(2, e) * 128);            // 128..256, the leading bit included
    if (m === 256) { m = 128; e += 1; }
    word = ((e + 127) * 128) + (m - 128);
  }
  if (word >= 0x7F80) {
    encFail("Field '" + f.name + "': " + String(v) + " is outside the range of bf16, " +
            String(-ENC_BF16_MAX) + " to " + String(ENC_BF16_MAX) + " (PS-168)");
  }
  return sign | word;
}

function encWriteFloat(out, value, type, endian) {
  var v = Number(value);
  var size = type === "f16" ? 2 : type === "f32" ? 4 : 8;
  var dv = new DataView(new ArrayBuffer(size));
  if (type === "f16") dv.setUint16(0, encHalf(v), false);
  else if (type === "f32") dv.setFloat32(0, v, false);
  else dv.setFloat64(0, v, false);
  var bytes = [];
  for (var i = 0; i < size; i++) bytes.push(dv.getUint8(i));
  if (endian === "little") bytes.reverse();
  for (var j = 0; j < size; j++) out.push(bytes[j]);
}

function encEncoding(v, enc, size) {                  // _encode_encoding
  if (enc === "sign_magnitude") {
    var half = Math.pow(2, size * 8 - 1);
    if (Math.abs(v) > half - 1) encFail(v + " has no " + size * 8 + "-bit sign-magnitude form (PS-463)");
    return v < 0 ? half + Math.abs(v) : v;
  }
  if (enc === "bcd") {
    if (v < 0 || v > Math.pow(10, size * 2) - 1) encFail(v + " has no " + size * 8 + "-bit BCD form (PS-463)");
    var out = 0, scale = 1, t = Math.abs(v);
    while (t > 0) { out += (t % 10) * scale; scale *= 16; t = Math.floor(t / 10); }
    return out;
  }
  if (enc === "gray") return v ^ Math.floor(v / 2);
  return v;
}

function encMinifloat(type, value) {                  // encode_minifloat
  var v = Number(value), mag = Math.abs(v), e, f;
  if (type === "uflt16" || type === "sflt16") {
    var signed = type === "sflt16";
    if (v < 0 && !signed) encFail(value + " is negative; uflt16 holds [0, 1) (PS-420)");
    var bits = signed ? 11 : 12;
    for (e = 0; e < 16; e++) {
      f = encRint(mag * Math.pow(2, bits) * Math.pow(2, 15 - e));
      if (f < Math.pow(2, bits)) {
        var word = e * Math.pow(2, bits) + f;
        return word + (signed && v < 0 ? 0x8000 : 0);
      }
    }
    encFail(value + " is outside the range of " + type + " (PS-420)");
  }
  var sign = v < 0 ? 0x800000 : 0;
  if (mag === 0) return sign;
  for (e = 1; e < 127; e++) {
    if (mag < Math.pow(2, e - 62)) {
      f = encRint((mag / Math.pow(2, e - 63) - 1) * 65536);
      if (f === 65536) continue;
      if (f < 0) break;
      return sign + e * 65536 + f;
    }
  }
  if (e >= 127) encFail(value + " is outside the range of sflt24 (PS-420)");
  f = encRint(mag * 65536 * Math.pow(2, 62));
  if (f >= 65536) return sign + 65536;
  return sign + f;
}

function encLength(f, natural) {                      // encode_length
  var raw = f.length === undefined ? natural : f.length;
  if (typeof raw === "string") return Math.max(0, natural);
  var n = parseInt(raw, 10);
  return isNaN(n) ? Math.max(0, natural) : Math.max(0, n);
}

function encPad(out, bytes, length) {
  for (var i = 0; i < length; i++) out.push(i < bytes.length ? bytes[i] & 0xFF : 0);
}

function encHex(text, what) {
  text = String(text).replace(/ /g, "");
  if (text.length % 2 || /[^0-9a-fA-F]/.test(text)) encFail(what + ": expected hex, got " + JSON.stringify(text));
  var out = [];
  for (var i = 0; i < text.length; i += 2) out.push(parseInt(text.substr(i, 2), 16));
  return out;
}

function encBase64(text) {
  var A = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/";
  var s = String(text).replace(/[^A-Za-z0-9+/]/g, ""), out = [], bits = 0, acc = 0;
  for (var i = 0; i < s.length; i++) {
    acc = (acc << 6) | A.indexOf(s.charAt(i)); bits += 6;
    if (bits >= 8) { bits -= 8; out.push((acc >> bits) & 0xFF); }
  }
  return out;
}

/* PS-168 for udec/sdec (check_nibble_decimal): 0 to 15.9, or -8 to 7.9, in tenths. */
function encNibbleFits(tenths, signed, f, given) {
  var lo = signed ? -80 : 0, hi = signed ? 79 : 159;
  if (tenths >= lo && tenths <= hi) return;
  var abs = Math.abs(tenths);
  var shown = (tenths < 0 ? "-" : "") + Math.floor(abs / 10) + (abs % 10 ? "." + (abs % 10) : "");
  encFail("Field '" + (f.name || "?") + "': " + String(given) + " encodes as " + shown +
          ", outside " + (signed ? "-8" : "0") + " to " + (signed ? "7.9" : "15.9") + " (PS-168)");
}

function encEnumLabel(entry) {
  return entry !== null && typeof entry === "object" && "name" in entry ? entry.name : entry;
}

function encField(out, f, value, ctx) {               // _encode_field
  var type = String(f.type);
  var endian = ctx.endian;
  if (f.endian !== undefined && ENC_ENDIAN_OPAQUE.indexOf(type) < 0) {
    if (f.endian !== "big" && f.endian !== "little") {
      encFail("field 'endian' must be 'big' or 'little', got " + JSON.stringify(f.endian));
    }
    endian = f.endian;
  }
  encFieldInner(out, f, value, endian, ctx);
}

function encFieldInner(out, f, value, endian, ctx) {  // _encode_field_inner
  var type = f.type;
  if (!type) encFail("Field '" + (f.name || "?") + "' declares no type");
  type = String(type);
  if (/[\[:<]/.test(type)) {
    // A lone bit range, rounded and checked against its width (PS-552, PS-168). It was
    // masked to a byte, silently.
    var lbt = encBitType(type), lraw = encRint(Number(value));
    encFits(lraw, lbt[2], type.charAt(0) === "s", f, encGiven(f, value), type);
    encPacked(out, encPlace({}, lraw, lbt[1], lbt[2]), Math.max(1, lbt[0]), endian);
    return;
  }
  if (ENC_WORD_ORDERED[type]) {
    if (typeof value === "number" && ENC_WORD_ORDERED[type][1] !== "f") {
      value = encFits(encRint(value), 32, type.charAt(0) === "s", f, encGiven(f, value));
    }
    var tmp = [];
    writeWordOrdered(tmp, 0, value, ENC_WORD_ORDERED[type][0], ENC_WORD_ORDERED[type][1]);
    for (var w = 0; w < 4; w++) out.push(tmp[w]);
    return;
  }
  if (ENC_TYPES[type]) {
    var size = ENC_TYPES[type][0], signed = ENC_TYPES[type][1];
    var iv = value;
    // PS-552: the nearest integer, a tie to even, before any encoding; PS-168 after it.
    if (typeof iv === "number") iv = encRint(iv);
    if (f.encoding) {
      iv = encEncoding(Number(iv), f.encoding, size);
      signed = false;
    }
    encFits(iv, size * 8, signed, f, encGiven(f, value));
    encWriteInt(out, iv, size, signed, endian);
    return;
  }
  if (type === "uflt16" || type === "sflt16" || type === "sflt24") {
    encWriteInt(out, encMinifloat(type, value), type === "sflt24" ? 3 : 2, false, endian);
    return;
  }
  if (type === "udec" || type === "sdec") {
    // PS-556: n is the value times 10, rounded half to even; n div 10 and n mod 10, floored.
    var n = encRint(Number(value) * 10);
    encNibbleFits(n, type === "sdec", f, encGiven(f, value));   // PS-168
    var whole = Math.floor(n / 10);
    out.push(((whole & 0x0F) << 4) | (n - whole * 10));
    return;
  }
  if (type === "bf16") { encWriteInt(out, encBf16(Number(value), f), 2, false, endian); return; }
  if (type === "f16" || type === "f32" || type === "f64") { encWriteFloat(out, value, type, endian); return; }
  if (type === "bool") { out.push(value ? 1 : 0); return; }
  if (type === "skip") {
    var n = typeof f.length === "string" ? 0 : Math.max(0, parseInt(f.length === undefined ? 1 : f.length, 10));
    for (var s = 0; s < n; s++) out.push(0);
    return;
  }
  if (type === "bytes") {
    var raw;
    if (Array.isArray(value)) raw = value.map(function (x) { return Number(x) & 0xFF; });
    else if (typeof value === "string" && f.format === "base64") raw = encBase64(value);
    else if (typeof value === "string") {
      var text = value.replace(/ /g, "");
      if (f.separator) text = text.split(String(f.separator)).join("");
      raw = encHex(text, "bytes field " + JSON.stringify(f.name));
    } else encFail("bytes field " + JSON.stringify(f.name) + ": cannot encode " + typeof value);
    encPad(out, raw, encLength(f, raw.length));
    return;
  }
  if (encIsLiteral(f)) return;                       // PS-359: a literal writes nothing
  if (type === "string" || type === "ascii") {        // encode_ascii (PS-537)
    var text = String(value), u = [], high = false;
    var fixed = typeof f.length === "number" && f.length >= 0 ? f.length : null;
    for (var c = 0; c < text.length; c++) {
      var code = text.charCodeAt(c);
      if (code > 0xFF) {
        encFail("Field '" + f.name + "': an ascii value holds a character above U+00FF, which has no byte (PS-537)");
      }
      if (code > 0x7F) high = true;
      u.push(code);                                  // PS-538: the byte of the same number
    }
    if (fixed !== null && u.length > fixed) {
      encFail("Field '" + f.name + "': an ascii value of " + u.length + " bytes is longer than its length of " + fixed + " (PS-537)");
    }
    if (high) ctx.wa.push("Field '" + f.name + "': a character above U+007F in an ascii value (PS-537)");
    encPad(out, u, fixed !== null ? fixed : u.length);
    return;
  }
  if (type === "hex") {
    var h = encHex(value, "hex field " + JSON.stringify(f.name));
    encPad(out, h, encLength(f, Math.floor(String(value).length / 2)));
    return;
  }
  if (type === "base64") {
    var d = encBase64(value);
    if (f.length) encPad(out, d, f.length); else encPad(out, d, d.length);
    return;
  }
  if (type === "enum") {                             // _encode_enum
    // A name back to its code, a code as given (PS-067); a name several codes share is
    // rejected (PS-169), and so is one with no code, the `default` label among them.
    var iv2;
    if (typeof value === "string") {
      var codes = encEnumCodes(f, value);
      if (!codes.length) encFail("Field '" + f.name + "': " + encLabelText(value) + " is not a name of the enum (PS-067)");
      if (codes.length > 1) encShared(f.name, value, codes);
      iv2 = codes[0];
    } else if (encIsNumber(value)) iv2 = encRint(value);              // PS-552
    else encFail("Field '" + f.name + "': " + encLabelText(value) + " is not a name or code of the enum (PS-067)");
    var base = String(f.base || "u8"), bt = ENC_TYPES[base] || [1, false];
    // PS-168, naming the enum: its base was written as a nameless field.
    encFits(iv2, bt[0] * 8, bt[1], f, encGiven(f, value));
    encWriteInt(out, iv2, bt[0], bt[1], endian);
    return;
  }
  encFail("Cannot encode type: " + type);
}

function encMissing(name) {                           // missing_field_message
  return "Missing required field: " + name;
}

function encHasDefault(f) {                           // field_default
  // PS-554: a declared `default` is a defined absence, in reported form, encoded as
  // input. An enum's is its unmapped label (PS-068), a legacy match's its fallback case.
  return "default" in f && f.type !== "enum" && f.type !== "match";
}

// declared_sources: every name a field list declares, whether some declaration reads
// payload bytes or computes from them (`reads`), and the values its literals give.
function encDeclared(fields) {
  var found = {};
  function note(name, literal, value) {
    if (!Object.prototype.hasOwnProperty.call(found, name)) found[name] = { reads: false, literals: [] };
    if (literal) found[name].literals.push(value); else found[name].reads = true;
  }
  (function walk(node) {
    if (Array.isArray(node)) { node.forEach(walk); return; }
    if (!node || typeof node !== "object") return;
    if (typeof node.name === "string" && node.name && "type" in node) {
      if (encIsLiteral(node)) note(node.name, true, node.value); else note(node.name, false);
    }
    if (node.match && typeof node.match === "object" && typeof node.match.name === "string") note(node.match.name, false);
    Object.keys(node).forEach(function (k) { var v = node[k]; if (v && typeof v === "object") walk(v); });
  })(fields);
  return found;
}

// empty_payload_error (PS-442): no bytes written, where the input gives a value only a
// field reading payload bytes reports.
function encEmptyPayloadError(fields, data) {
  var sources = null;
  for (var key in data) {
    var v = data[key];
    if (key.charAt(0) === "_" || v === null || v === undefined) continue;
    if ((typeof v === "string" || Array.isArray(v)) && v.length === 0) continue;
    if (typeof v === "object" && !Array.isArray(v) && Object.keys(v).length === 0) continue;
    if (sources === null) sources = encDeclared(fields);
    var e = Object.prototype.hasOwnProperty.call(sources, key) ? sources[key] : null;
    if (!e || !e.reads) continue;
    if (e.literals.some(function (l) { return l === v; })) continue;
    return "Encoding wrote no bytes, though the input gives '" + key + "', which the schema reads from the payload (PS-442)";
  }
  return null;
}

function encIsLiteral(f) {                            // is_literal
  return (f.type === "string" || f.type === "number" || f.type === "boolean") && "value" in f;
}

function encInternalValue(f, data) {                  // _internal_encode_value
  if ("value" in f) return f.value;
  if (Object.prototype.hasOwnProperty.call(data, f.name)) return data[f.name];
  if (encHasDefault(f)) return f["default"];         // PS-554
  encFail("internal field " + JSON.stringify(f.name) + " reads payload bytes and declares no " +
          "value, and the input does not supply it (PS-434)");
}

function encRawBits(f) {                              // raw_bits_field
  var g = {};
  for (var k in f) if (k !== "encoding") g[k] = f[k];
  return g;
}

// The field list being encoded: a reference is looked for there before the rest of the
// schema (ENC_VARS, ENC_NAMED), since each port may declare its own `code`.
var ENC_SCOPE = null;

function encFind(node, test) {                        // the walk in _field_declaring_var
  if (Array.isArray(node)) {
    for (var i = 0; i < node.length; i++) { var f = encFind(node[i], test); if (f) return f; }
    return null;
  }
  if (node && typeof node === "object") {
    if (test(node)) return node;
    for (var k in node) {
      if (!Object.prototype.hasOwnProperty.call(node, k)) continue;
      var g = encFind(node[k], test);
      if (g) return g;
    }
  }
  return null;
}

function encDeclaringVar(name) {                      // _field_declaring_var
  var f = encFind(ENC_SCOPE, function (n) { return n["var"] === name && n.name; });
  return f ? { name: f.name, lookup: f.lookup, type: f.type, values: f.values }
           : (ENC_VARS[name] || null);
}

function encNamed(name) {                             // _field_named
  var f = encFind(ENC_SCOPE, function (n) {
    return n.name === name && ((n.lookup !== undefined && n.lookup !== null) || n.type === "enum");
  });
  return f ? { name: f.name, lookup: f.lookup, type: f.type, values: f.values }
           : (ENC_NAMED[name] || null);
}

function encEnumCodes(f, value) {                     // the codes an enum names `value`
  var values = f.values || {}, codes = [], k;
  if (Array.isArray(values)) {
    for (k = 0; k < values.length; k++) if (encEnumLabel(values[k]) === value) codes.push(k);
  } else {
    for (k in values) {
      if (Object.prototype.hasOwnProperty.call(values, k) && encEnumLabel(values[k]) === value) {
        var n = Number(k);
        if (isFinite(n)) codes.push(n);
      }
    }
  }
  return codes.sort(function (a, b) { return a - b; });
}

function encReverseEnum(value, f) {                   // reverse_enum
  var codes = encEnumCodes(f, value);
  if (codes.length > 1) encShared(f.name, value, codes);           // PS-169
  if (codes.length) return codes[0];
  if (typeof value === "string" && value.indexOf("unknown(") === 0 && value.slice(-1) === ")") {
    var u = parseInt(value.slice(8, -1), 10);
    if (isFinite(u)) return u;
  }
  return value;
}

function encReference(ref, data) {                    // _encode_reference
  // PS-523: a looked-up field bound the number under a text or boolean label, and a
  // numeric label as itself; the data carries the label, so the first are reversed.
  var value, src;
  if (Object.prototype.hasOwnProperty.call(data, ref)) { value = data[ref]; src = encNamed(ref); }
  else {
    src = encDeclaringVar(ref);
    if (!src || !Object.prototype.hasOwnProperty.call(data, src.name)) return null;
    value = data[src.name];
  }
  if (src && src.lookup !== null && src.lookup !== undefined &&
      (typeof value === "string" || typeof value === "boolean")) return encReverseLookup(value, src.lookup, src.name);
  // CR-2026-132: an enum bound its code.
  if (src && src.type === "enum" && typeof value === "string") return encReverseEnum(value, src);
  return value;
}

function encResolveName(f, name, data) {              // _resolve_encode_name
  if (!f.name_from) return name;
  var missing = false;
  var resolved = String(f.name_from).replace(/\$\{(\w+)\}/g, function (_, ref) {
    var v;
    if (Object.prototype.hasOwnProperty.call(data, ref)) v = data[ref];
    else {
      var src = encDeclaringVar(ref);
      if (src && Object.prototype.hasOwnProperty.call(data, src.name)) v = data[src.name];
      else { missing = true; return ""; }
    }
    // The key holds what the field reports, its label included (PS-532).
    if (v !== null && typeof v === "object")
      encFail("template " + JSON.stringify(f.name_from) + " references " + JSON.stringify(ref) +
              ", which is not a number, string or boolean and cannot be written into a key (PS-532)");
    return String(v);
  });
  return missing ? null : resolved;
}

function encBitType(type) {                           // _parse_bitfield_type
  var m = /^[us](\d+)\[(\d+):(\d+)\]$/.exec(String(type));
  if (!m) encFail("Unknown bitfield format: " + type);
  return [Math.floor(Number(m[1]) / 8), Number(m[2]), Number(m[3]) - Number(m[2]) + 1];
}

function encIsBareBitfield(f) {                       // _is_bare_bitfield
  if (!f || typeof f !== "object" || "byte_group" in f) return false;
  var t = String(f.type === undefined ? "" : f.type);
  if (t === "bitfield_string") return false;
  return /[\[:<]/.test(t);
}

/* OR `value`, masked to `width` bits, into `bits` at `start`: the reference packs with
 * `|=`, and two ranges may read the same bits (vobo reads one nibble as a number and as
 * a label), so adding the values would carry. `bits` maps a bit index to 1, which stays
 * exact past the 32 bits JavaScript's bitwise operators reach. */
function encPlace(bits, value, start, width) {
  var span = Math.pow(2, width);
  var v = ((value % span) + span) % span;
  for (var b = 0; b < width && v > 0; b++) {
    if (v % 2) bits[start + b] = 1;
    v = Math.floor(v / 2);
  }
  return bits;
}

function encPacked(out, bits, size, endian) {
  var bytes = [];
  for (var i = 0; i < size; i++) bytes.push(0);
  for (var k in bits) {
    var at = Number(k);
    if (at >= size * 8) encFail("int too big to convert");
    bytes[size - 1 - Math.floor(at / 8)] |= 1 << (at % 8);
  }
  if (endian === "little") bytes.reverse();
  for (var j = 0; j < size; j++) out.push(bytes[j]);
}

function encLabelValue(label, f) {                    // _bitfield_label_value
  var keys = ["enum", "values"];
  for (var i = 0; i < keys.length; i++) {
    var table = f[keys[i]];
    if (Array.isArray(table)) { if (table.indexOf(label) >= 0) return table.indexOf(label); }
    else if (table && typeof table === "object") {
      for (var k in table) if (table[k] === label) return Number(k);
    }
  }
  encFail(JSON.stringify(label) + " is not a declared value of " + JSON.stringify(f.name));
}

function encBitRun(fr, run, data, ctx) {              // _encode_bitfield_run
  // The run shares one base, in its fields' effective byte order (PS-059). Each range is
  // ORed into the pending base and advances only by its own consume (CR-2026-136).
  var endian = ctx.endian;
  for (var j = 0; j < run.length; j++) if (run[j].endian) { endian = run[j].endian; break; }
  for (var i = 0; i < run.length; i++) {
    var f = run[i], name = f.name || "", v;   // a nameless range is refused at load (PS-553)
    if ("value" in f || name.charAt(0) === "_") v = encInternalValue(f, data);
    else if (!encHas(data, name) && encHasDefault(f)) v = f["default"];   // PS-554
    else if (!encHas(data, name)) {
      if (!(parseInt(f.consume || 0, 10) || 0)) {
        // PS-551 (CR-2026-141): a range that does not consume is fixed by the fields that
        // write its bits, so it is derived and writes none (FrameBuffer.place_bits).
        var dt = encBitType(f.type);
        encPlaceBits(fr, Math.max(1, dt[0]), endian, dt[1], dt[2], 0, 0, name, true);
        continue;
      }
      encFail(encMissing(name));   // PS-551
    }
    else v = data[name];
    v = encReverseModifiers(v, f);
    if (typeof v === "string") v = encLabelValue(v, f);
    if (typeof v === "boolean") v = v ? 1 : 0;
    if (!encIsNumber(v)) encFail("cannot encode " + JSON.stringify(f.name) + " into its bit range: " + JSON.stringify(v) + " is not a number");
    v = encRint(v);
    var bt = encBitType(f.type);
    // PS-064 before PS-062: rejected, never masked into a neighbour's bits.
    encFits(v, bt[2], String(f.type).charAt(0) === "s", f, encGiven(f, v), f.type);
    encPlaceBits(fr, Math.max(1, bt[0]), endian, bt[1], bt[2], v, parseInt(f.consume || 0, 10) || 0, name);
  }
}

function encGroupWidth(f) {                          // byte_group_width
  var group = f.byte_group, members = Array.isArray(group) ? group : (group.fields || []);
  var declared = Array.isArray(group) ? f["size"] : group["size"];
  var width = (declared === undefined || declared === null) ? 1 : parseInt(declared, 10);
  for (var i = 0; i < members.length; i++) {   // PS-070: the widest member's base
    var t = String((members[i] && members[i].type) || ""), r = /^[us](\d+)\[/.exec(t);
    var w = r ? Math.max(1, Math.floor(Number(r[1]) / 8)) : (ENC_TYPES[t] ? ENC_TYPES[t][0] : 1);
    if (w > width) width = w;
  }
  return width;
}

function encByteGroup(out, f, data, ctx) {            // _encode_byte_group
  var group = f.byte_group, members = Array.isArray(group) ? group : (group.fields || []);
  var size = encGroupWidth(f);
  var packed = {};
  for (var i = 0; i < members.length; i++) {
    var g = members[i];
    if (!g || typeof g !== "object") continue;
    if ("endian" in g) encFail("byte_group member '" + (g.name || "?") + "' declares endian; the group's byte order is declared on the group (PS-364)");
    if (ENC_COMPUTED.indexOf(g.type) >= 0 || encIsLiteral(g)) continue;   // no bits of its own
    var name = g.name || "", v;
    if (!name) v = g["default"] === undefined ? 0 : g["default"];
    else if ("value" in g || name.charAt(0) === "_") v = encInternalValue(g, data);
    else if (!encHas(data, name) && encHasDefault(g)) v = g["default"];   // PS-554
    else if (!encHas(data, name)) encFail(encMissing(name));   // PS-551
    else v = data[name];
    if (g.type === "bool") {                    // its `bit` of the group (PS-364)
      if (v) packed = encPlace(packed, 1, Number(g.bit || 0), 1);
      continue;
    }
    v = encReverseModifiers(v, g);
    if (typeof v === "boolean") v = v ? 1 : 0;
    if (!encIsNumber(v)) continue;
    v = encRint(v);
    if (String(g.type || "").indexOf("[") >= 0) {
      var bt = encBitType(g.type);
      encFits(v, bt[2], String(g.type).charAt(0) === "s", g, encGiven(g, v), g.type);  // PS-168
      packed = encPlace(packed, v, bt[1], bt[2]);
    } else {                                    // a whole integer is its full range (PS-070)
      var wt = ENC_TYPES[String(g.type)];
      encFits(v, wt[0] * 8, wt[1], g, encGiven(g, v));                       // PS-168
      packed = encPlace(packed, v, 0, wt[0] * 8);
    }
  }
  var endian = (!Array.isArray(group) && group.endian) ? group.endian : ctx.endian;
  encPacked(out, packed, Math.max(1, size), endian);
}

/* The bits of an unsigned digit string, least significant first, by repeated halving of
 * its digits - exact at any width, where parseInt is exact only to 2^53. Null when the
 * text is not a plain run of digits in `base`. */
function encDigitBits(text, base) {
  text = String(text).replace(/^\s+|\s+$/g, "").replace(/^\+/, "");
  if (base === 16) text = text.replace(/^0[xX]/, "");
  if (!(base === 16 ? /^[0-9a-fA-F]+$/ : /^[0-9]+$/).test(text)) return null;
  var digits = [], bits = [], i;
  for (i = 0; i < text.length; i++) digits.push(parseInt(text.charAt(i), base));
  while (digits.length) {
    var rem = 0, next = [];
    for (i = 0; i < digits.length; i++) {
      var cur = rem * base + digits[i], q = Math.floor(cur / 2);
      rem = cur % 2;
      if (next.length || q) next.push(q);
    }
    bits.push(rem);
    digits = next;
  }
  return bits;
}

function encBitfieldString(out, f, value, ctx) {      // _encode_bitfield_string
  var parts = f.parts || [], delim = f.delimiter === undefined ? "." : f.delimiter;
  var prefix = f.prefix || "", length = f.length === undefined ? 2 : f.length;
  value = String(value);
  if (prefix && value.indexOf(prefix) === 0) value = value.slice(prefix.length);
  var segs = value.split(delim), packed = {};
  for (var i = 0; i < parts.length; i++) {
    var p = parts[i];
    if (p.length < 2) continue;
    var fmt = p.length > 2 ? p[2] : "decimal";
    var seg = i < segs.length ? segs[i] : "0";
    var hexFmt = fmt === "hex" || fmt === "hex:upper";
    var v = hexFmt ? parseInt(seg, 16) : parseInt(seg, 10);
    if (isNaN(v)) encFail("invalid literal for bitfield_string segment: " + JSON.stringify(seg));
    if (Math.abs(v) > 9007199254740991) {
      // Past 2^53 a Number has already lost the low bits, so place the digits exactly.
      var bits = encDigitBits(seg, hexFmt ? 16 : 10);
      if (!bits) encFail("invalid literal for bitfield_string segment: " + JSON.stringify(seg));
      var start = Number(p[0]), width = Number(p[1]);
      for (var b = 0; b < width && b < bits.length; b++) if (bits[b]) packed[start + b] = 1;
      continue;
    }
    packed = encPlace(packed, v, Number(p[0]), Number(p[1]));
  }
  encPacked(out, packed, length, ctx.endian);
}

function encGroupGiven(group, data) {                 // _flagged_group_given
  var gfs = group.fields || [];
  if (gfs.some(function (g) { return g && g.name && Object.prototype.hasOwnProperty.call(data, g.name); })) return true;
  return encClaimable(gfs).some(function (g) { return Object.prototype.hasOwnProperty.call(data, g.name); });
}

function encFlagged(out, fg, data, ctx) {             // _encode_flagged
  // CR-2026-136: the given groups' members are one field list, laid out as a decoder
  // reads them (a bool or bit range is its bits of a shared byte; an object is encoded).
  var groups = fg.groups || [], members = [];
  for (var i = 0; i < groups.length; i++) {
    var gfs = groups[i].fields || [];
    if (!encGroupGiven(groups[i], data)) continue;
    if (gfs.some(function (g) { return g && typeof g === "object" && !g.name; })) {
      // A construct in the group (a nested flagged block, a byte_group): all of it.
      gfs.forEach(function (g) { if (g && typeof g === "object") members.push(g); });
      continue;
    }
    gfs.forEach(function (g) { if (g && typeof g === "object" && g.name) members.push(g); });
  }
  return encList(out, members, data, ctx);
}

function encCasePattern(value, pattern) {             // _match_case_pattern
  if (value === null || value === undefined) return false;
  if (typeof pattern === "string" && pattern.trim().charAt(0) === "[") {
    var list = pattern.trim().replace(/^\[|\]$/g, "").split(",").map(function (x) { return x.trim(); });
    for (var i = 0; i < list.length; i++) {
      var n = /^0x/i.test(list[i]) ? parseInt(list[i], 16) : Number(list[i]);
      if (list[i] !== "" && (value === n || value === list[i].replace(/^["']|["']$/g, ""))) return true;
    }
    return false;
  }
  if (typeof pattern === "string" && pattern.indexOf("..") >= 0) {
    var parts = pattern.split("..");
    var a = parseInt(parts[0], 10), b = parseInt(parts[1], 10);
    if (isNaN(a) || isNaN(b) || !/^\s*-?\d+\s*$/.test(parts[0]) || !/^\s*-?\d+\s*$/.test(parts[1])) return false;
    return a <= value && value <= b;
  }
  if (value === pattern) return true;
  if (typeof pattern === "string" && typeof value !== "string") {
    var text = pattern.trim(), num;
    if (/^0x[0-9a-f]+$/i.test(text)) num = parseInt(text, 16);
    else if (/^[-+]?\d+$/.test(text)) num = parseInt(text, 10);
    else return false;
    return value === num;
  }
  return false;
}

// _body_score: how many of a branch's reported names the input gives, through the
// constructs a decode reports from, or null where a literal the input contradicts, or a
// nested match with every branch ruled out and no `skip` default, rules it out.
function encBodyScore(body, data) {
  if (!Array.isArray(body)) return null;
  var score = 0, has = Object.prototype.hasOwnProperty;
  for (var i = 0; i < body.length; i++) {
    var f = body[i];
    if (!f || typeof f !== "object") continue;
    if ("byte_group" in f && !f.type) {
      var g = f.byte_group, h = encBodyScore(Array.isArray(g) ? g : (g.fields || []), data);
      if (h === null) return null;
      score += h; continue;
    }
    if ("flagged" in f && !f.type) {
      (f.flagged.groups || []).forEach(function (grp) { score += encBodyScore(grp.fields || [], data) || 0; });
      continue;
    }
    if (f.match && typeof f.match === "object" && !f.type) {
      var m = f.match, mc = m.cases || {}, bodies = [];
      if (m.name && has.call(data, m.name)) score++;
      Object.keys(mc).forEach(function (k) { if (k !== "default" && Array.isArray(mc[k])) bodies.push(mc[k]); });
      var dflt = m["default"] === undefined ? "error" : m["default"];
      if (Array.isArray(dflt)) bodies.push(dflt);
      else if (Array.isArray(mc["default"])) bodies.push(mc["default"]);
      var best = null;
      bodies.forEach(function (b) { var s2 = encBodyScore(b, data); if (s2 !== null && (best === null || s2 > best)) best = s2; });
      if (best !== null) score += best;
      else if (dflt !== "skip") return null;
      continue;
    }
    if (f.tlv && typeof f.tlv === "object" && !f.type) {
      var names = {};
      Object.keys(f.tlv.cases || {}).forEach(function (k) {
        if (Array.isArray(f.tlv.cases[k])) encCaseClaims(f.tlv.cases[k]).forEach(function (c) { names[c[0].name] = true; });
      });
      Object.keys(names).forEach(function (n) { if (has.call(data, n)) score++; });
      continue;
    }
    if (!f.name || String(f.name).charAt(0) === "_" || (ENC_COMPUTED.indexOf(f.type) >= 0 && !encIsLiteral(f))) continue;
    var key = f.name;
    if (f.name_from) { try { key = encResolveName(f, f.name, data); } catch (e) { key = null; } }
    if (key === null || !has.call(data, key)) continue;
    if (encIsLiteral(f) && f.value !== data[key]) return null;
    score++;
  }
  return score;
}

function encDefaultBody(m) {                          // _default_body
  if (Array.isArray(m["default"])) return m["default"];
  var d = (m.cases || {})["default"];
  return Array.isArray(d) ? d : null;
}

// _best_case: the case scoring highest, the first of equals, with `tied` where another
// equalled it; null where none scores, or the default list scores higher than any.
function encBestCase(m, data) {
  var cases = m.cases || {}, best = null, bestHits = 0, tied = false;
  for (var key in cases) {
    if (key === "default" || !Array.isArray(cases[key])) continue;
    var hits = encBodyScore(cases[key], data) || 0;
    if (hits > bestHits) { best = key; bestHits = hits; tied = false; }
    else if (hits && hits === bestHits) tied = true;
  }
  if (best === null) return null;
  var d = encDefaultBody(m);
  if (d !== null && (encBodyScore(d, data) || 0) > bestHits) return null;
  return { key: best, body: cases[best], tied: tied };
}

function encParseInt0(text) {                         // int(text, 0)
  text = String(text).trim();
  var neg = text.charAt(0) === "-";
  if (neg || text.charAt(0) === "+") text = text.slice(1);
  var n;
  if (/^0x[0-9a-f]+$/i.test(text)) n = parseInt(text.slice(2), 16);
  else if (/^0o[0-7]+$/i.test(text)) n = parseInt(text.slice(2), 8);
  else if (/^0b[01]+$/i.test(text)) n = parseInt(text.slice(2), 2);
  else if (/^(0|[1-9]\d*)$/.test(text)) n = parseInt(text, 10);
  else encFail("invalid literal for int() with base 0: " + JSON.stringify(text));
  return neg ? -n : n;
}

function encMatch(out, f, data, ctx, peeked) {        // _encode_match
  var m = f.match || {}, cases = m.cases || {}, length = m.length;
  var dflt = m["default"] === undefined ? "error" : m["default"];
  var disc = null;
  if (m.name && Object.prototype.hasOwnProperty.call(data, m.name)) disc = data[m.name];
  else if (m.field) disc = encReference(String(m.field).replace(/^\$+/, ""), data);
  if ((disc === null || disc === undefined) && m["var"] && encHas(data, m["var"])) disc = data[m["var"]];
  if ((disc === null || disc === undefined) && length !== undefined && length !== null &&
      peeked && peeked.length >= Number(length)) {
    // CR-2026-136: a bit range before the match peeked and filled these bytes.
    disc = 0;
    for (var pb = 0; pb < Number(length); pb++) {
      var at = ctx.endian === "little" ? Number(length) - 1 - pb : pb;
      disc = disc * 256 + peeked[at];
    }
  }
  var key = null, body = null;
  if (length !== undefined && length !== null && typeof disc === "number" && isFinite(disc) &&
      !Number.isInteger(disc)) {                      // check_written_discriminator (PS-557)
    encFail("Field " + (m.name ? "'" + m.name + "'" : "an unnamed match") + ": the discriminator " +
            String(disc) + " is not an integer, so it cannot be written (PS-557)");
  }
  if (disc !== null && disc !== undefined) {
    for (var k in cases) {
      if (k === "default") continue;
      if (encCasePattern(disc, k)) { key = k; body = cases[k]; break; }
    }
  }
  if (body === null) {
    var found = encBestCase(m, data);
    if (found) { key = found.key; body = found.body; }
  }
  if (body === null && m.remaining === true && !encBodyScore(encDefaultBody(m), data)) {
    // No case's fields given, nor the default's: the case matching 0 bytes is the one
    // writing nothing selects.
    for (var zk in cases) {
      if (zk !== "default" && Array.isArray(cases[zk]) && encCasePattern(0, zk)) { key = zk; body = cases[zk]; break; }
    }
  }
  var known = disc !== null && disc !== undefined;
  var inline = length !== undefined && length !== null;
  var label = m.name || m["var"] || "match";
  if (body === null && !known && inline) {
    // CR-2026-151: a nested match reading this discriminator fixes it (_nested_discriminator).
    var nested = encNestedDiscriminator(m, data, label);
    if (nested) {
      disc = nested[0]; key = nested[1]; body = nested[2]; known = true;
      if (m["var"]) {
        var withVar = {};
        for (var dk in data) withVar[dk] = data[dk];
        withVar[m["var"]] = disc;
        data = withVar;
      }
    }
  }
  if (body === null) {
    if (Array.isArray(dflt)) body = dflt;
    else if (Array.isArray(cases["default"])) body = cases["default"];
    else if (inline && !known) encFail(encMissing(label));   // PS-551, PS-442, named by the discriminator
    else if (dflt === "skip") body = [];
    else if (known) encFail("No matching case for value " + String(disc));   // PS-442
    else encFail(encMissing(m.name || m["var"] || String(m.field || "match").replace(/^\$+/, "")));
  }
  if (inline) {
    var value = disc;
    if (!known) {
      if (key === null) encFail(encMissing(label));
      value = encExactCase(key);
      if (value === null) encFail("match case " + JSON.stringify(key) + " names no single discriminator value");
    }
    // PS-168: a discriminator, given or derived, fits the bytes it is written in.
    encFits(encRint(Number(value)), parseInt(length, 10) * 8, false, { name: label }, value);
    encWriteInt(out, value, parseInt(length, 10), false, ctx.endian);
  }
  return encList(out, body, data, ctx);
}

// _nested_discriminator (CR-2026-151): [value, case key or null, branch fields] where a
// nested match reading this discriminator has exactly one case the input fills, its key a
// single value selecting the branch that holds it; null where none is filled.
function encNestedDiscriminator(m, data, label) {
  var names = {};
  if (m["var"]) names[m["var"]] = true;
  if (m.name) names[m.name] = true;
  if (!m["var"] && !m.name) return null;
  var cases = m.cases || {}, branches = [];
  Object.keys(cases).forEach(function (k) { if (k !== "default" && Array.isArray(cases[k])) branches.push([k, cases[k]]); });
  var fb = Array.isArray(m["default"]) ? m["default"] : (Array.isArray(cases["default"]) ? cases["default"] : null);
  if (fb) branches.push(["default", fb]);
  function namesOf(fields) {
    return fields.filter(function (f) {
      return f && f.name && String(f.name).charAt(0) !== "_" && ENC_COMPUTED.indexOf(f.type) < 0;
    }).map(function (f) { return f.name; });
  }
  var filled = [];
  function walk(fields, path) {
    (fields || []).forEach(function (f) {
      if (!f || f.type || !f.match || typeof f.match !== "object") return;
      var inner = f.match, ic = inner.cases || {};
      var reads = !!names[String(inner.field || "").replace(/^\$+/, "")];
      var bodies = [];
      Object.keys(ic).forEach(function (k) { if (k !== "default" && Array.isArray(ic[k])) bodies.push([k, ic[k]]); });
      if (Array.isArray(inner["default"])) bodies.push(["default", inner["default"]]);
      // A case is filled where the input gives every one of its fields and nothing any
      // other case of this match declares beyond them.
      var given = {};
      if (reads) bodies.forEach(function (b) {
        if (b[0] !== "default") namesOf(b[1]).forEach(function (n) { if (encHas(data, n)) given[n] = true; });
      });
      bodies.forEach(function (b) {
        if (reads && b[0] !== "default") {
          var own = namesOf(b[1]);
          var ok = own.length > 0 && own.every(function (n) { return encHas(data, n); }) &&
            Object.keys(given).every(function (n) { return own.indexOf(n) >= 0; });
          if (ok) filled.push([path, b[0]]);
        }
        walk(b[1], reads ? path.concat([[inner, b[0]]]) : path);
      });
    });
  }
  branches.forEach(function (b) { walk(b[1], [[m, b[0]]]); });
  if (!filled.length) return null;
  if (filled.length !== 1) encFail(encMissing(label));
  var value = encExactCase(filled[0][1]);
  if (value === null) encFail(encMissing(label));
  filled[0][0].forEach(function (step) {
    var ec = step[0].cases || {}, selected = "default";
    for (var k in ec) {
      if (k !== "default" && encCasePattern(value, k)) { selected = k; break; }
    }
    if (selected !== step[1]) encFail(encMissing(label));
  });
  var outer = filled[0][0][0][1], body = null;
  branches.forEach(function (b) { if (b[0] === outer) body = b[1]; });
  return [value, outer === "default" ? null : outer, body];
}

function encRecords(f, data) {                        // _repeat_records
  var r = data[f.name || ""];
  if (r === undefined || r === null) return null;
  if (Array.isArray(r)) return r;
  if (typeof r === "object") return [r];
  return null;
}

function encRepeatSize(f, key, data, siblings) {      // _repeat_size
  var size = f[key];
  if (typeof size === "string" && size.charAt(0) === "$") {
    var src = encSizeSource(siblings, size.slice(1));
    size = data[src ? src.name : size.slice(1)];
  }
  if (typeof size !== "number" || !isFinite(size) || Math.floor(size) !== size) return null;
  return size;
}

function encRepeatElements(out, f, data, records, count, ctx) {   // _encode_repeat_elements
  var name = f.name || "";
  // PS-550: a sibling identifying these values position by position is given as many
  // values as this repeat, as a decoder requires (PS-546).
  encSiblings(f).forEach(function (sib) {
    var given = data[sib];
    if (!Array.isArray(given) || given.length !== records.length) {
      encFail("Repeat '" + name + "' is given " + records.length + " values and '" + sib + "', which identifies them, " + (Array.isArray(given) ? given.length : "none") + " (PS-550)");
    }
  });
  var rf = f.fields || [];
  if (f.element && typeof f.element === "object" && !f.fields) {
    // PS-529: each value is encoded as the element, wrapped as it was decoded. PS-544: a
    // null is written as the element's first sentinel - an absent value, which encList
    // writes as the sentinel's raw bits - and is an error where it declares none.
    // PS-545: an element named with `_` is decoded and encoded under "element".
    var en = f.element.name || "element", el = {};
    if (en.charAt(0) === "_") en = "element";
    for (var ek in f.element) el[ek] = f.element[ek];
    el.name = en; rf = [el];
    records = records.map(function (v, p) {
      if (v === null || v === undefined) {
        if (!el.sentinel) encFail("Repeat '" + name + "' has null at position " + p + ", and its element declares no `sentinel` to write for it (PS-544)");
        return {};
      }
      var w = {}; w[en] = v; return w;
    });
  }
  if (encReachesRecords(rf)) {
    // PS-542: the records do not say which element they came from.
    encFail("Repeat '" + name + "' has elements that yield several records, which cannot be encoded (PS-542)");
  }
  if (f.present_if && rf.some(function (r) { return r && ENC_COMPUTED.indexOf(r.type) < 0 && !encIsLiteral(r); })) {
    encFail("repeat field " + JSON.stringify(name) + " declares present_if and its elements read payload bytes, so the elements it dropped cannot be encoded (PS-387)");
  }
  if (count !== null && records.length !== count) {
    // PS-551: the count and the list must agree; nothing is padded or cut.
    encFail("Repeat '" + name + "' is given " + records.length + " values for a count of " + count + " (PS-551)");
  }
  for (var i = 0; i < records.length; i++) {
    var rec = records[i];
    if (rec === null || typeof rec !== "object" || Array.isArray(rec)) encFail("repeat field " + JSON.stringify(name) + ": expected each record to be a mapping");
    if (f.index) { var copy = {}; for (var k in rec) copy[k] = rec[k]; copy[f.index] = i + (f.index_base || 0); rec = copy; }
    var element = [];
    encList(element, rf, rec, ctx);
    encCheckPeeks(element);           // PS-551: an element's bytes close with it
    for (var eb = 0; eb < element.length; eb++) out.push(element[eb]);
  }
}

function encRepeat(out, f, data, ctx, siblings) {     // _encode_repeat
  var name = f.name || "", raw = data[name];
  if (raw !== undefined && raw !== null && typeof raw !== "object") encFail("repeat field " + JSON.stringify(name) + ": expected a list of records, got " + typeof raw);
  var records = encRecords(f, data);
  if (records === null) encFail(encMissing(name));      // PS-551
  siblings = siblings || [];
  var count = encRepeatSize(f, "count", data, siblings);
  var body = [];
  encRepeatElements(body, f, data, records, count, ctx);
  var bl = encRepeatSize(f, "byte_length", data, siblings);
  if (bl !== null && body.length !== bl) encFail("Repeat '" + name + "' encodes to " + body.length + " bytes for a byte_length of " + bl + " (PS-551)");
  for (var i = 0; i < body.length; i++) out.push(body[i]);
  if (f.trailer) {
    var trailer = [];
    encList(trailer, f.trailer, data, ctx);
    encCheckPeeks(trailer);                              // PS-551
    for (var tb = 0; tb < trailer.length; tb++) out.push(trailer[tb]);
  }
}

function encSiblings(f) {                             // sibling_references
  // The distinct sibling names a repeat's `$name[i]` references name (PS-546): its
  // identity, and its elements' `unit` and `ipso.instance`.
  var refs = [f.identity], names = [];
  var members = (f.element && typeof f.element === "object" && !f.fields) ? [f.element] : (f.fields || []);
  members.forEach(function (m) {
    if (!m || typeof m !== "object") return;
    var ipso = (m.ipso && typeof m.ipso === "object") ? m.ipso : {};
    refs.push(m.unit, ipso.instance);
  });
  refs.forEach(function (r) {
    var mm = typeof r === "string" ? /^\$(\w+)\[(\w+)\]$/.exec(r) : null;
    if (mm && names.indexOf(mm[1]) < 0) names.push(mm[1]);
  });
  return names;
}

function encReachesRecords(fields) {                  // records_reachable
  return (fields || []).some(function (f) {
    if (!f || typeof f !== "object") return false;
    if ("records" in f && !f.type) return true;
    var m = f.match;
    if (!m || typeof m !== "object" || f.type) return false;
    var bodies = [];
    for (var k in (m.cases || {})) bodies.push(m.cases[k]);
    bodies.push(m["default"]);
    return bodies.some(function (b) { return Array.isArray(b) && encReachesRecords(b); });
  });
}

function encClaimable(fields) {                       // _claimable_fields
  var out = [];
  (fields || []).forEach(function (f) {
    if (!f || typeof f !== "object") return;
    if ("byte_group" in f && !f.type) {
      var g = f.byte_group;
      out = out.concat(encClaimable(Array.isArray(g) ? g : (g.fields || [])));
      return;
    }
    if ("flagged" in f && !f.type) {
      (f.flagged.groups || []).forEach(function (grp) { out = out.concat(encClaimable(grp.fields || [])); });
      return;
    }
    if (!f.name || String(f.name).charAt(0) === "_" || ENC_COMPUTED.indexOf(f.type) >= 0) return;
    out.push(f);
  });
  return out;
}

// FLOAT_TYPE_MAX: the largest finite magnitude of each binary float type (CR-2026-148).
var ENC_FLOAT_MAX = { f16: 65504, f32: 3.4028234663852886e38, f32le16: 3.4028234663852886e38,
                      f32be16le: 3.4028234663852886e38, f64: Infinity };

// An f16 value rounded to the nearest representable half, a tie to even (struct 'e').
function encF16Round(x) {
  if (x === 0 || !isFinite(x)) return x;
  var a = Math.abs(x), e = Math.max(Math.floor(Math.log(a) / Math.LN2), -14);
  if (Math.pow(2, e) > a) e -= 1;
  if (Math.pow(2, e + 1) <= a) e += 1;
  e = Math.max(e, -14);
  var q = Math.pow(2, e - 10), r = encRint(a / q) * q;
  return x < 0 ? -r : r;
}

// The field's forward arithmetic, as a decoder applies it (apply_canonical_modifiers, then
// _apply_transform); NaN where it has no value.
function encForward(v, f) {
  if (f.mult !== undefined && f.mult !== null) v = v * f.mult;
  if (f.div !== undefined && f.div !== null) { if (Number(f.div) === 0) return NaN; v = v / f.div; }
  if (f.add !== undefined && f.add !== null) v = v + f.add;
  (f.transform || []).forEach(function (st) {
    if (st === null || typeof st !== "object" || !isFinite(v)) return;
    if (st.sqrt) v = Math.sqrt(Math.max(0, v));
    else if (st.abs) v = Math.abs(v);
    else if ("pow" in st) v = Math.pow(v, Number(st.pow));
    else if ("floor" in st) v = Math.max(v, Number(st.floor));
    else if ("ceiling" in st) v = Math.min(v, Number(st.ceiling));
    else if ("clamp" in st) v = Math.max(Number(st.clamp[0]), Math.min(Number(st.clamp[1]), v));
    else if (st.log10) v = v > 0 ? Math.log(v) / Math.LN10 : NaN;
    else if (st.log) v = v > 0 ? Math.log(v) : NaN;
    else if ("mult" in st || "div" in st || "add" in st) {
      if ("mult" in st) v = v * Number(st.mult);
      if ("div" in st) { if (Number(st.div) === 0) { v = NaN; return; } v = v / Number(st.div); }
      if ("add" in st) v = v + Number(st.add);
    } else if (st.op === "round") v = roundDecimal(v, st.decimals || 0, st.ties === "away");
  });
  return v;
}

// _case_takes (CR-2026-148, PS-527 amended): [fits, exact]. Fits: the value with the
// field's arithmetic undone (and, for an integer type or bit range, rounded per PS-552)
// lies within the type's range (PS-168); a float takes any finite value no larger than
// its largest. Exact: the arithmetic re-applied to the raw value written, through the
// type's storage, gives the value under binary64 equality. No tolerance anywhere.
function encCaseTakes(f, value) {
  var g = encReverseLookup(value, f.lookup);
  if (typeof g !== "number" || typeof g === "boolean") return [true, true];
  var raw;
  try {
    raw = encReverseCanonical(encReverseStages(g, f.transform), f);
  } catch (e) { return [false, false]; }
  if (typeof raw !== "number" || !isFinite(raw)) return [false, false];
  var t = String(f.type || "u8"), stored, b;
  if (/\[/.test(t)) {
    var bt = encBitType(t);
    stored = encRint(raw);
    b = encBounds(bt[2], t.charAt(0) === "s");
    if (stored < b[0] || stored > b[1]) return [false, false];
  } else if (ENC_TYPES[t]) {
    var info = ENC_TYPES[t], bits = info[0] * 8;
    stored = encRint(raw);
    if (bits > 48) {
      if (stored < (info[1] ? -Math.pow(2, bits - 1) : 0) || stored >= Math.pow(2, info[1] ? bits - 1 : bits)) return [false, false];
    } else {
      b = encBounds(bits, info[1]);
      if (stored < b[0] || stored > b[1]) return [false, false];
    }
  } else if (t === "bf16") {                         // through PS-559's rounding
    try { stored = decodeBf16(encBf16(raw, f)); } catch (e) { return [false, false]; }
  } else if (has_(ENC_FLOAT_MAX, t)) {
    if (Math.abs(raw) > ENC_FLOAT_MAX[t]) return [false, false];
    stored = t === "f64" ? raw : t === "f16" ? encF16Round(raw) : Math.fround(raw);
  } else if (t === "udec" || t === "sdec") {
    var n = encRint(raw * 10);                       // PS-556
    if (n < (t === "sdec" ? -80 : 0) || n > (t === "sdec" ? 79 : 159)) return [false, false];
    var whole = Math.floor(n / 10);
    stored = whole + (n - whole * 10) * 0.1;
  } else if (t === "uflt16" || t === "sflt16" || t === "sflt24") {
    try { stored = decodeMinifloat(t, encMinifloat(t, raw)); } catch (e) { return [false, false]; }
    if (typeof stored !== "number") return [false, false];
  } else {
    return [true, true];
  }
  return [true, encForward(stored, f) === g];
}

function has_(o, k) { return Object.prototype.hasOwnProperty.call(o, k); }

function encTlvTag(out, key, tlv, ctx) {              // _encode_tlv_tag
  if (tlv.tag_fields && tlv.tag_key) {
    var text = String(key).trim();
    if (text.charAt(0) === "[") text = text.charAt(text.length - 1) === "]" ? text.slice(1, -1) : text.slice(1);
    var parts = text.split(",").map(function (p) { return p.trim().replace(/^["']|["']$/g, ""); });
    if (parts.some(function (p) { return p === "*" || p.charAt(0) === "!"; })) {
      encFail("TLV case " + JSON.stringify(key) + " matches a range of tags, so encoding cannot choose one");
    }
    var names = Array.isArray(tlv.tag_key) ? tlv.tag_key : [tlv.tag_key];
    if (parts.length !== names.length) encFail("TLV case " + JSON.stringify(key) + " does not match tag_key");
    var values = {};
    for (var i = 0; i < names.length; i++) values[names[i]] = encParseInt0(parts[i]);
    tlv.tag_fields.forEach(function (tf) {
      if (!(tf.name in values)) encFail("TLV case " + JSON.stringify(key) + " gives no value for " + JSON.stringify(tf.name));
      encField(out, tf, values[tf.name], ctx);
    });
    return;
  }
  encWriteInt(out, encParseInt0(key), tlv.tag_size === undefined ? 1 : tlv.tag_size, false, ctx.endian);
}

function encIsPeekField(f) {                         // is_peek_field
  if (!f || typeof f !== "object" || !f.name || (parseInt(f.consume || 0, 10) || 0)) return false;
  return f.type === "bool" || encIsBareBitfield(f);
}

/* The peeks a plain whole field after them writes over (covered_peeks). */
function encCoveredPeeks(fields) {
  var out = [], pending = [];
  (fields || []).forEach(function (f) {
    if (!f || typeof f !== "object") return;
    if (encIsPeekField(f)) { pending.push(f); return; }
    if (ENC_COMPUTED.indexOf(f.type) >= 0 || encIsLiteral(f)) return;
    var construct = ["match", "tlv", "byte_group", "flagged", "$ref"].some(function (k) { return k in f; });
    var bits = typeof f.type === "string" && /[\[:<]/.test(f.type);
    if (!construct && !bits && ["skip", "object", "repeat", "bool"].indexOf(f.type) < 0) out = out.concat(pending);
    pending = [];
  });
  return out;
}

function encCaseClaims(fields, optional) {             // _case_claims
  var out = [], covered = encCoveredPeeks(fields);
  (fields || []).forEach(function (f) {
    if (!f || typeof f !== "object") return;
    if ("byte_group" in f && !f.type) {
      var g = f.byte_group;
      out = out.concat(encCaseClaims(Array.isArray(g) ? g : (g.fields || []), optional));
      return;
    }
    if ("flagged" in f && !f.type) {
      (f.flagged.groups || []).forEach(function (grp) { out = out.concat(encCaseClaims(grp.fields || [], true)); });
      return;
    }
    if ("match" in f && !f.type) { out = out.concat(encMatchClaims(f.match)); return; }
    if (!f.name || String(f.name).charAt(0) === "_") return;
    // A literal or computed field claims its name, never required (no-silent-empty).
    if (encIsLiteral(f) || ENC_COMPUTED.indexOf(f.type) >= 0) { out.push([f, true]); return; }
    // PS-551 (CR-2026-141): a peek the fields after it fix need not be given.
    out.push([f, !!optional || f.optional === true || covered.indexOf(f) >= 0]);
  });
  return out;
}

// _match_claims: a nameless inline match claims its `name` and every branch's fields,
// each optional, each name once.
function encMatchClaims(m) {
  if (!m || typeof m !== "object") return [];
  var out = [], seen = {};
  if (m.name) {
    var w = parseInt(m.length || 1, 10);
    out.push([[1, 2, 3, 4, 8].indexOf(w) >= 0 ? { name: m.name, type: "u" + 8 * w } : { name: m.name }, true]);
    seen[m.name] = true;
  }
  var bodies = Object.keys(m.cases || {}).map(function (k) { return m.cases[k]; });
  if (Array.isArray(m["default"])) bodies.push(m["default"]);
  bodies.forEach(function (b) {
    if (b && !Array.isArray(b) && typeof b === "object") b = b.fields;
    if (!Array.isArray(b)) return;
    encCaseClaims(b, true).forEach(function (c) {
      // A literal is seen by its value too: branches report one name as different literals.
      var sk = encIsLiteral(c[0]) ? c[0].name + "\u0000" + JSON.stringify(c[0].value) : c[0].name;
      if (!seen[sk]) { seen[sk] = true; out.push([c[0], true]); }
    });
  });
  return out;
}

function encTlv(out, f, data, ctx) {                  // _encode_tlv
  var tlv = f.tlv || {}, cases = tlv.cases || {}, lsize = tlv.length_size || 0;
  if (tlv.merge === false) encFail("a tlv with merge: false reports its entries under 'channels', and encoding them is not supported");
  var order = Object.keys(data), has = Object.prototype.hasOwnProperty;
  // PS-525 to PS-527 (CR-2026-110, -125, -128). `rep` is tlv_repeat_modes and
  // tlv_encode_plan, resolved at generation time, with the cases' declaration order: an
  // object's integer-like keys iterate in ascending order, not the order written.
  var rep = tlv.__on_repeat || {};
  var modes = rep.modes || {}, members = rep.members || {};
  var keys = rep.order || Object.keys(cases);
  // A supplied member of a merged object that no case declares has nowhere to go.
  Object.keys(members).forEach(function (n) {
    var v = data[n];
    if (v === null || typeof v !== "object" || Array.isArray(v)) return;
    var stray = Object.keys(v).filter(function (m) { return members[n].indexOf(m) < 0; }).sort();
    if (stray.length) encFail("tlv: the merged object '" + n + "' is given [" + stray.map(function (m) { return "'" + m + "'"; }).join(", ") + "], which no case declares (PS-527)");
  });
  // Each value goes under the first case whose declared fields it supplies in full;
  // nothing is zero-filled or dropped. A case requiring more fields is tried first.
  var entries = [];
  keys.forEach(function (key) {
    if (key === "default" || !Array.isArray(cases[key])) return;
    var req = [], opt = [];
    encCaseClaims(cases[key]).forEach(function (c) { (c[1] ? opt : req).push(c[0]); });
    entries.push({ key: key, body: cases[key], req: req, opt: opt, seq: entries.length });
  });
  // An object counts its members (CR-2026-129), so a merged {adr, dr} case outranks {dr}.
  function width(list) {
    return list.reduce(function (n, c) {
      return n + (c.type === "object" ? Math.max(1, encCaseClaims(c.fields || []).length) : 1);
    }, 0);
  }
  entries.forEach(function (e) { e.width = width(e.req); });
  entries.sort(function (a, b) { return b.width - a.width || a.seq - b.seq; });
  var queues = {}, pending = {};
  entries.forEach(function (e) {
    e.req.concat(e.opt).forEach(function (c) {
      var n = c.name;
      if (!has.call(data, n) || has.call(queues, n) || modes[n] === "merge") return;
      var v = data[n];
      var collected = Array.isArray(v) && c.type !== "repeat" && !(c.type === "bytes" && c.format === "array") && (modes[n] || "collect") === "collect";
      queues[n] = collected ? v.slice() : [v];
    });
  });
  Object.keys(members).forEach(function (n) {
    var v = data[n];
    if (v !== null && typeof v === "object" && !Array.isArray(v)) pending[n] = Object.keys(v);
  });
  function membersOf(c) { return (c.fields || []).filter(function (m) { return m && m.name; }).map(function (m) { return m.name; }); }
  function fits(c, v) {                              // fits, exact_only (CR-2026-148)
    if (c.type === "object" && v !== null && typeof v === "object" && !Array.isArray(v) && modes[c.name] !== "merge") {
      var claims = encCaseClaims(c.fields || []), every = {}, ok = true;
      claims.forEach(function (x) { every[x[0].name] = true; if (!x[1] && !has.call(v, x[0].name)) ok = false; });
      Object.keys(v).forEach(function (k) { if (!every[k]) ok = false; });
      return ok;
    }
    // A mapping is no value for a scalar field, nor a non-mapping for an object (the
    // reference's `fits`): one name may be an object under one tag and a number under another.
    var isMap = v !== null && typeof v === "object" && !Array.isArray(v);
    if (isMap && c.type && c.type !== "object" && c.type !== "repeat") return false;
    if (c.type === "object" && !isMap && modes[c.name] !== "merge") return false;
    if (encIsLiteral(c)) return c.value === v;        // PS-359, PS-513
    if (ENC_COMPUTED.indexOf(c.type) >= 0) return true;   // asks for its source (PS-434)
    var took = encCaseTakes(c, v);
    return took[0] && (took[1] || !exactOnly);
  }
  // CR-2026-148: a first sweep places only values a case reproduces exactly, a second
  // the rest; the PS-527 ranking applies within each.
  var written = [], progress, exactOnly;
  [true, false].forEach(function (phase) {
  exactOnly = phase;
  progress = true;
  while (progress) {
    progress = false;
    entries.forEach(function (e) {
      for (;;) {
        var values = {}, taken = 0, complete = true;
        for (var i = 0; i < e.req.length; i++) {
          var c = e.req[i], n = c.name;
          if (modes[n] === "merge") {
            var ms = membersOf(c);
            if (!has.call(pending, n) || ms.some(function (m) { return !has.call(data[n], m); })) { complete = false; break; }
            var sub = {};
            ms.forEach(function (m) { sub[m] = data[n][m]; });
            values[n] = sub;
            if (ms.some(function (m) { return pending[n].indexOf(m) >= 0; })) taken++;
          } else {
            var q = queues[n];
            if (!q || !q.length || !fits(c, q[0])) { complete = false; break; }
            values[n] = q[0]; taken++;
          }
        }
        if (!complete) break;
        e.opt.forEach(function (c) {
          var q = queues[c.name];
          if (q && q.length && fits(c, q[0])) { values[c.name] = q[0]; taken++; }
        });
        if (!taken) break;
        var entry = {}, pos = Infinity;
        for (var k in data) entry[k] = data[k];
        Object.keys(values).forEach(function (n) {
          entry[n] = values[n];
          pos = Math.min(pos, order.indexOf(n));
          if (modes[n] === "merge") pending[n] = pending[n].filter(function (m) { return !has.call(values[n], m); });
          else queues[n].shift();
        });
        written.push({ key: e.key, body: e.body, data: entry, p: pos, seq: written.length });
        progress = true;
      }
    });
  }
  });
  Object.keys(queues).forEach(function (n) {
    if (queues[n].length) encFail("tlv: no case takes the value of '" + n + "' " + JSON.stringify(queues[n][0]) + " in full (PS-502, PS-527)");
  });
  Object.keys(pending).forEach(function (n) {
    if (pending[n].length) encFail("tlv: no case takes the members [" + pending[n].slice().sort().map(function (m) { return "'" + m + "'"; }).join(", ") + "] of the merged object '" + n + "' in full (PS-527)");
  });
  // A `name_from` key no claim can name: where no field declares it by name, no case
  // takes it, and it was dropped with nothing reported.
  var declared = null;
  keys.forEach(function (key) {
    if (!Array.isArray(cases[key])) return;
    encCaseClaims(cases[key]).forEach(function (c) {
      var t = c[0].name_from;
      if (typeof t !== "string") return;
      var re = new RegExp("^" + t.split(/\$\{\w+\}/).map(function (part) {
        return part.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
      }).join(".+") + "$");
      order.forEach(function (k) {
        if (has.call(queues, k) || has.call(pending, k) || !re.test(k)) return;
        if (declared === null) declared = encDeclared(ENC_SCOPE || []);
        if (has.call(declared, k)) return;
        encFail("tlv: no case takes the value of '" + k + "' " + JSON.stringify(data[k]) + " in full (PS-502, PS-527)");
      });
    });
  });
  // Payload order, so a payload round-trips; entries of one name keep the order taken.
  written.sort(function (a, b) { return a.p - b.p || a.seq - b.seq; });
  written.forEach(function (w) {
    encTlvTag(out, w.key, tlv, ctx);
    var value = [];
    encList(value, w.body, w.data, ctx);
    encCheckPeeks(value);              // PS-551: a tlv value is measured whole
    if (lsize > 0) encWriteInt(out, value.length, lsize, false, ctx.endian);
    for (var i = 0; i < value.length; i++) out.push(value[i]);
  });
}

/* Group a field list so bit ranges sharing a span are packed together (_bitfield_runs). */
function encRuns(fields) {
  var items = [], run = [];
  (fields || []).forEach(function (f) {
    if (!f || typeof f !== "object") return;
    if (!encIsBareBitfield(f)) {
      if (run.length) { items.push({ bits: run }); run = []; }
      items.push({ field: f });
      return;
    }
    run.push(f);
    if ((parseInt(f.consume || 0, 10) || 0) >= 1) { items.push({ bits: run }); run = []; }
  });
  if (run.length) items.push({ bits: run });
  return items;
}

function encHas(data, name) {
  return Object.prototype.hasOwnProperty.call(data, name) && data[name] !== null && data[name] !== undefined;
}

/* CR-2026-136 (FrameBuffer): OR `bytes` into what is pending, then write them. */
function encEmit(fr, bytes, carried) {
  var shared = Math.min(fr.pend.length, bytes.length), offset = fr.out.length;
  for (var s = 0; s < shared; s++) {
    // PS-551 (CR-2026-141): a field would OR over a peek's different bits.
    if (fr.pend[s] & ~bytes[s] & encCov(bytes, s)) encFail(encPeekDisagreement(fr.pk, fr.out.concat(bytes)));
  }
  for (var c = 0; c < bytes.length; c++) encCover(fr, offset + c, encCov(bytes, c));
  (bytes.pk || []).forEach(function (pk) { fr.pk.push(encShifted(pk, offset)); });
  var merged = [];
  for (var i = 0; i < bytes.length; i++) merged.push(i < shared ? (bytes[i] | fr.pend[i]) : bytes[i]);
  fr.pend.splice(0, shared);
  carried = Math.min(carried || 0, merged.length);
  // A nested list's unconsumed tail stays pending here (Frame in the reference).
  for (var o = 0; o < merged.length - carried; o++) fr.out.push(merged[o]);
  if (carried) {
    var tail = merged.slice(merged.length - carried);
    for (var t = 0; t < fr.pend.length; t++) {
      if (t < tail.length) tail[t] |= fr.pend[t]; else tail.push(fr.pend[t]);
    }
    fr.pend = tail;
  }
}

/* OR `value`, `width` bits at `start`, into the pending base of `base` bytes, then
 * consume as declared (FrameBuffer.place_bits). */
function encPlaceBits(fr, base, endian, start, width, value, consume, name, derived) {
  if (name) {
    // CR-2026-141 (PS-551): recorded for the check where the frame closes. A derived range
    // writes no bits: the fields that write them fix its value.
    var span = Math.pow(2, width);
    fr.pk.push({ p: fr.out.length, size: base, endian: endian, start: start, width: width,
                 raw: derived ? null : ((value % span) + span) % span, name: name });
    if (!derived) {
      var mask = encPeekBytes(span - 1, start, width, base, endian);
      for (var m = 0; m < base; m++) encCover(fr, fr.out.length + m, mask[m]);
    }
  }
  var bits = [];
  encPacked(bits, encPlace({}, value, start, width), base, endian);
  while (fr.pend.length < base) fr.pend.push(0);
  for (var i = 0; i < base; i++) fr.pend[i] |= bits[i];
  if (consume > 0) {
    while (fr.pend.length < consume) fr.pend.push(0);
    for (var c = 0; c < consume; c++) fr.out.push(fr.pend[c]);
    fr.pend.splice(0, consume);
  }
}

/* CR-2026-141 (PS-551). An array's positions carry the bits some field wrote, in `unc`
 * where not all of them (Frame.covered), and `pk` lists each bit range or bool placed:
 * {p, size, endian, start, width, raw (null where derived), name} (Frame.peeks). */
function encCov(arr, i) { return arr.unc && arr.unc[i] !== undefined ? arr.unc[i] : 255; }

function encCover(fr, at, mask) { fr.cov[at] = (fr.cov[at] || 0) | mask; }

function encShifted(pk, by) {
  return { p: pk.p + by, size: pk.size, endian: pk.endian, start: pk.start, width: pk.width, raw: pk.raw, name: pk.name };
}

function encPeekBytes(value, start, width, size, endian) {
  var b = [];
  encPacked(b, encPlace({}, value, start, width), size, endian);
  return b;
}

/* The range's unsigned bits as `frame` holds them; past its end, null, or zeros if `pad`. */
function encPeekFound(frame, pk, pad) {
  if (pk.p + pk.size > frame.length && !pad) return null;
  var bytes = [];
  for (var i = 0; i < pk.size; i++) bytes.push(pk.p + i < frame.length ? frame[pk.p + i] : 0);
  if (pk.endian === "little") bytes.reverse();
  var v = 0;
  for (var b = pk.start + pk.width - 1; b >= pk.start; b--) {
    v = v * 2 + ((bytes[pk.size - 1 - Math.floor(b / 8)] >> (b % 8)) & 1);
  }
  return v;
}

function encPeekDisagreement(peeks, frame) {          // peek_disagreement
  for (var i = 0; i < peeks.length; i++) {
    var pk = peeks[i];
    if (pk.raw === null) continue;
    var found = encPeekFound(frame, pk, false);
    if (found !== null && found !== pk.raw) {
      return "Field '" + pk.name + "' is given " + pk.raw + ", but the fields that share its bits write " + found + " (PS-551)";
    }
  }
  return "A field writes bits a peek before it set otherwise (PS-551)";
}

function encCheckPeeks(arr) {                         // check_peeks
  (arr.pk || []).forEach(function (pk) {
    if (pk.raw === null) {
      var mask = encPeekBytes(Math.pow(2, pk.width) - 1, pk.start, pk.width, pk.size, pk.endian);
      for (var i = 0; i < pk.size; i++) {
        var have = pk.p + i < arr.length ? encCov(arr, pk.p + i) : 0;
        if ((have & mask[i]) !== mask[i]) encFail(encMissing(pk.name));
      }
      return;
    }
    var found = encPeekFound(arr, pk, true);
    if (found !== pk.raw) encFail(encPeekDisagreement([pk], arr));
  });
}

/* PS-551: what the input omits but the rest of it fixes (_derive_inputs and helpers). */
function encSizeSource(fields, ref) {                 // _size_source
  for (var i = 0; i < (fields || []).length; i++) {
    var f = fields[i];
    if (!f || typeof f !== "object") continue;
    if (f.byte_group !== undefined) {
      var g = f.byte_group, found = encSizeSource(Array.isArray(g) ? g : (g.fields || []), ref);
      if (found) return found;
      continue;
    }
    if (f.name && (f.name === ref || f.var === ref)) return f;
  }
  return null;
}

function encDerivable(src) {                          // _derivable
  if (!src || !src.name) return false;
  if (ENC_COMPUTED.indexOf(src.type) >= 0 || encIsLiteral(src)) return false;
  var keys = ["value", "name_from", "lookup", "mult", "div", "add", "transform", "polynomial", "encoding", "compute", "ref", "guard"];
  return !keys.some(function (k) { return k in src; });
}

function encIsPeek(fields, src) {                     // _is_peek
  var items = encRuns(fields);
  for (var i = 0; i < items.length; i++) {
    var run = items[i].bits;
    if (run && run.indexOf(src) >= 0) return (parseInt(run[run.length - 1].consume || 0, 10) || 0) < 1;
  }
  return false;
}

function encDerivedSource(fields, ref) {              // _derived_source
  if (typeof ref !== "string" || ref.charAt(0) !== "$") return null;
  var src = encSizeSource(fields, ref.slice(1));
  return (encDerivable(src) && !encIsPeek(fields, src)) ? src : null;
}

function encExactCase(key) {                          // _exact_case_value
  if (typeof key === "number") return Math.floor(key) === key ? key : null;
  try { return encParseInt0(key); } catch (e) { return null; }
}

function encSoleCase(m, data) {                       // _sole_case_given
  var best = encBestCase(m, data);
  return (best === null || best.tied) ? null : encExactCase(best.key);
}

function encDerive(fields, data, ctx) {                 // _derive_inputs
  var d = {};
  for (var k in data) d[k] = data[k];
  (fields || []).forEach(function (f) {
    if (!f || typeof f !== "object") return;
    var src;
    if (f.type === "repeat") {
      var records = encRecords(f, data);
      if (records === null) return;
      ["count", "byte_length"].forEach(function (key) {
        src = encDerivedSource(fields, f[key]);
        if (!src || encHas(d, src.name)) return;
        if (key === "count") d[src.name] = records.length;
        else {
          var scratch = [];
          encRepeatElements(scratch, f, data, records, null, ctx);
          d[src.name] = scratch.length;
        }
      });
    } else if (f.match && typeof f.match === "object" && !f.type) {
      src = encDerivedSource(fields, f.match.field);
      if (!src || encHas(d, src.name)) return;
      var v = encSoleCase(f.match, data);
      if (v !== null) d[src.name] = v;
    } else if (f.flagged && typeof f.flagged === "object") {
      var mask = String(f.flagged.field || "").replace(/^\$+/, "");
      src = encSizeSource(fields, mask);
      var name = src ? src.name : mask, bits = 0, declared = 0;
      (f.flagged.groups || []).forEach(function (g) {
        declared += Math.pow(2, g.bit || 0);
        if (encGroupGiven(g, data)) bits += Math.pow(2, g.bit || 0);
      });
      var given = d[name];
      if (given === undefined || given === null) { d[name] = bits; return; }
      // The groups test what a reference to the mask binds (PS-523, CR-2026-132): an
      // enum's code, the number under a text label, as _encode_reference gives it.
      given = encReference(name, d);
      if (typeof given === "number" && Math.floor(given) === given) {
        var low = 0;
        (f.flagged.groups || []).forEach(function (g) {
          if (Math.floor(given / Math.pow(2, g.bit || 0)) % 2) low += Math.pow(2, g.bit || 0);
        });
        if (low !== bits) encFail("Flagged field '" + name + "' is given " + given + ", and the groups given set " + bits + " (PS-551)");
      }
    }
  });
  return d;
}

/* `top` selects the reference's top-level loop (encode) over _encode_field_list: the
 * name_from error is its alone. */
function encList(out, fields, data, ctx, top) {
  var omitted = null;
  data = encDerive(fields, data, ctx);
  // CR-2026-136: a frame laid out as a decoder reads it (FrameBuffer). A bit range or
  // bool that does not consume leaves its bytes pending, and the next field's are ORed in.
  var fr = { out: [], pend: [], cov: {}, pk: [] };
  encRuns(fields).forEach(function (item) {
    if (item.bits) { encBitRun(fr, item.bits, data, ctx); return; }
    var tmp = [];
    var carried = encOne(item, tmp);
    encEmit(fr, tmp, typeof carried === "number" ? carried : 0);
  });
  var base = out.length, total = fr.out.length + fr.pend.length;
  for (var fo = 0; fo < fr.out.length; fo++) out.push(fr.out[fo]);
  for (var fp = 0; fp < fr.pend.length; fp++) out.push(fr.pend[fp]);
  for (var at = 0; at < total; at++) {
    var cm = fr.cov[at] || 0;
    if (cm !== 255) { out.unc = out.unc || {}; out.unc[base + at] = cm; }
  }
  if (fr.pk.length) {
    out.pk = out.pk || [];
    fr.pk.forEach(function (pk) { out.pk.push(encShifted(pk, base)); });
  }
  return fr.pend.length;     // the bytes at the end still pending (Frame.pending)

  function encOne(item, out) {
    var f = item.field, name = f.name || "", type = f.type === undefined ? "u8" : f.type;
    if (f.optional === true && f.name) {
      var okey = encResolveName(f, f.name, data);
      if (!encHas(data, okey === null ? f.name : okey)) { omitted = omitted || f.name; return; }
      if (omitted) encFail("optional field " + JSON.stringify(f.name) + " is supplied while the earlier optional field " + JSON.stringify(omitted) + " is not (PS-405)");
    }
    if ("match" in f && !f.type) { return encMatch(out, f, data, ctx, fr.pend); }
    if ("tlv" in f && !f.type) { encTlv(out, f, data, ctx); return; }
    if ("byte_group" in f) { encByteGroup(out, f, data, ctx); return; }
    if ("flagged" in f && !f.type) { return encFlagged(out, f.flagged, data, ctx); }
    if (type === "repeat") { encRepeat(out, f, data, ctx, fields); return; }
    if (type === "object") {
      // Read back from the key a `name_from` object was reported under (CR-2026-030).
      var objKey = encResolveName(f, name, data);
      if (objKey === null) objKey = name;
      var nested = data[objKey];
      if (nested === null || typeof nested !== "object" || Array.isArray(nested)) encFail(encMissing(objKey));   // PS-551
      return encList(out, f.fields || [], nested, ctx);
    }
    if (ENC_COMPUTED.indexOf(type) >= 0) return;
    if (type === "skip") {
      var n = typeof f.length === "string" ? 0 : Math.max(0, parseInt(f.length === undefined ? 1 : f.length, 10));
      // CR-2026-141 (padding): a skip's zeros fix no peek's bits and contradict none.
      for (var i = 0; i < n; i++) { out.unc = out.unc || {}; out.unc[out.length] = 0; out.push(0); }
      return;
    }
    if (encIsLiteral(f)) return;
    if ("value" in f) { encField(out, f, f.value, ctx); return; }
    var value;
    // A nameless field other than a skip is refused at load (PS-553); it was written
    // here as zero padding.
    if (name.charAt(0) === "_") {
      value = encInternalValue(f, data);
    } else {
      var key = encResolveName(f, name, data);
      if (key === null) {
        if (top) encFail("Error encoding " + name + ": name_from " + JSON.stringify(f.name_from) + " references a field the data does not carry, so its output key cannot be rebuilt");
        key = name;
      }
      if (!encHas(data, key) && encHasDefault(f)) {
        value = f["default"];                    // PS-554: encoded as input
      } else {
        if (!encHas(data, key)) {
          if (f.sentinel) { encField(out, encRawBits(f), f.sentinel[0], ctx); return; }
          if (type === "bool" && !(parseInt(f.consume || 0, 10) || 0)) {
            // PS-551 (CR-2026-141): derived from the fields that write its bit.
            encPlaceBits(fr, 1, "big", Number(f.bit || 0), 1, 0, 0, name, true);
            return;
          }
          encFail(encMissing(key));              // PS-551: this wrote a zero
        }
        value = data[key];
      }
    }
    if (type === "bitfield_string") { encBitfieldString(out, f, value, ctx); return; }
    if (type === "bool") {                       // its bit of the byte it peeks
      encPlaceBits(fr, 1, "big", Number(f.bit || 0), 1, value ? 1 : 0,
                   parseInt(f.consume || 0, 10) || 0, name);
      return;
    }
    encField(out, f, encReverseModifiers(value, f), ctx);
  }
}

function encodeRoot(fields, data, endian) {
  // wa: PS-537's warnings, reported after the others as the reference reports them.
  var ctx = { endian: endian, w: [], wa: [] }, out = [];
  ENC_SCOPE = fields;
  encList(out, fields, data || {}, ctx, true);
  encCheckPeeks(out);                                    // PS-551 (CR-2026-141)
  if (!out.length) {
    var empty = encEmptyPayloadError(fields, data || {});   // PS-442
    if (empty) encFail(empty);
  }
  return { bytes: Array.prototype.slice.call(out), warnings: ctx.w.concat(ctx.wa) };
}
'''


def _strip_js_comments(code: str) -> str:
    """The runtime without its commentary and indentation, which are for its readers here.

    Whole-line comments and trailing ones are removed; no string or regular expression
    in the runtime contains `//`, which tests/test_ts013_encode_parity.py checks.
    """
    out = []
    in_block = False
    for line in code.splitlines():
        text = line.strip()
        if in_block:
            in_block = '*/' not in text
            continue
        if text.startswith('/*'):
            in_block = '*/' not in text
            continue
        if not text or text.startswith('//'):
            continue
        out.append(re.sub(r'\s+// .*$', '', text))
    return '\n'.join(out)


SLIM_ENCODE_DOWNLINK = '''function encodeDownlink(input) {
  return { bytes: [], fPort: input.fPort || 1, warnings: [], errors: ["this codec was generated without an encoder (--slim)"] };
}'''


def _slim(code: str) -> str:
    """A decode-only codec: encodeDownlink refuses, and whole-line comments are dropped.

    Only comment lines go, never a trailing comment: a lookup label may hold `//`, and a
    line-oriented pass cannot tell that from a comment. A line opening with `//` or `/*`
    is never inside a string - the generated code has no multi-line strings.
    """
    start = code.find('function encodeDownlink(input) {')
    if start >= 0:
        end = code.index('\n}', start) + 2
        code = code[:start] + SLIM_ENCODE_DOWNLINK + code[end:]
    # The command encoder reads the runtime this build leaves out.
    start = code.find('function encodeCommand(')
    if start >= 0:
        end = code.index('\n}', start) + 2
        code = code[:start].rstrip('\n') + code[end:]
    out = []
    in_block = False
    for line in code.splitlines():
        text = line.strip()
        if in_block:
            in_block = '*/' not in text
            continue
        if text.startswith('/*'):
            in_block = '*/' not in text
            continue
        if not text or text.startswith('//'):
            continue
        out.append(line)
    return '\n'.join(out) + '\n'


#: Identifiers a field must not be staged under: the generated functions' own locals,
#: JavaScript's reserved words and the globals a codec relies on. A field named `d`
#: became `var d = readU(...)` and replaced the output object, so decodeUplink returned
#: `data: 0`; `w` did the same to the warnings. A clashing name gets a trailing `_`,
#: and `_restore_output_keys` reports it under its own name again.
_CODEC_LOCALS = {
    'pos', 'd', 'vars', 'w', 'aqr', 'aqp', 'endian', 'buf', 'bgVal', 'bgStart', 'q',
    'qn', 'r', 'port', 'bytes', 'cmdName', 'cmdId', 'input', 'e', 'i', 'k',
    '_si', '_tlvTag', '_tlvStart', '_tlvSpan', '_tlvLen', '_mv', '_mr', '_k', '_a', '_p',
    '_i', '_flags', 'lv', 'lbl',
}
_JS_RESERVED = {
    'arguments', 'await', 'break', 'case', 'catch', 'class', 'const', 'continue',
    'debugger', 'default', 'delete', 'do', 'else', 'enum', 'eval', 'export', 'extends',
    'false', 'finally', 'for', 'function', 'if', 'implements', 'import', 'in',
    'instanceof', 'interface', 'let', 'new', 'null', 'package', 'private', 'protected',
    'public', 'return', 'static', 'super', 'switch', 'this', 'throw', 'true', 'try',
    'typeof', 'var', 'void', 'while', 'with', 'yield', 'undefined', 'NaN', 'Infinity',
    'Math', 'Number', 'String', 'Array', 'Object', 'JSON', 'Error', 'Date', 'Boolean',
    'DataView', 'ArrayBuffer', 'isFinite', 'isNaN', 'parseInt', 'parseFloat', 'module',
    'exports', 'require', 'unescape', 'encodeURIComponent',
}
_CODEC_GLOBALS: Optional[set] = None


def _codec_globals() -> set:
    """Every top-level function and var a generated codec declares."""
    global _CODEC_GLOBALS
    if _CODEC_GLOBALS is None:
        text = TS013Generator._gen_helpers(None) + ENCODER_RUNTIME_JS  # type: ignore
        names = set(re.findall(r'^function ([A-Za-z_$][\w$]*)', text, re.M))
        names |= set(re.findall(r'^var ([A-Za-z_$][\w$]*)', text, re.M))
        names |= {'ENC_TYPES', 'ENC_WORD_ORDERED', 'ENC_ENDIAN_OPAQUE', 'ENC_COMPUTED',
                  'ENC_VARS', 'ENC_NAMED', 'OUTPUT_KEYS', 'COMMANDS', 'decodeUplink', 'decodeDownlink',
                  'encodeDownlink', 'decodePayload', 'encodePayload', 'decodeCommand',
                  'encodeCommand'}
        _CODEC_GLOBALS = names
    return _CODEC_GLOBALS


def _tlv_repeat_modes(tlv):
    """tlv_repeat_modes() for a tlv that declares `on_repeat` on itself or on a case
    field (PS-525); otherwise every name collects (PS-157), and the codec carries no
    table for it."""
    from schema_interpreter import tlv_repeat_modes, reported_fields
    declared = 'on_repeat' in tlv or any(
        'on_repeat' in field_def
        for body in (tlv.get('cases') or {}).values() if isinstance(body, list)
        for field_def in reported_fields(body).values())
    return tlv_repeat_modes(tlv) if declared else ({}, {}, [])


def _annotate_on_repeat(node):
    """A copy of an encoder plan with each tlv carrying `__on_repeat`: its names' modes
    (PS-525), each merged object's declared members (PS-527) and the cases' declaration
    order, which a JSON object with integer-like keys does not keep."""
    from schema_interpreter import tlv_encode_plan
    if isinstance(node, list):
        return [_annotate_on_repeat(item) for item in node]
    if not isinstance(node, dict):
        return node
    out = {k: _annotate_on_repeat(v) for k, v in node.items()}
    tlv = node.get('tlv')
    if isinstance(tlv, dict) and isinstance(tlv.get('cases'), dict):
        modes, last_members, _ = _tlv_repeat_modes(tlv)
        plan = tlv_encode_plan(tlv, modes, last_members)
        out['tlv'] = dict(out['tlv'], __on_repeat={
            'modes': {k: v for k, v in modes.items() if v != 'collect'},
            'members': {k: sorted(v) for k, v in plan['members'].items()},
            'order': [str(key) for key in tlv['cases']],
        })
    return out


#: Per-generation spellings for names whose plain mangling collides with another
#: name's. `TS013Generator.generate` fills it for the schema it is generating and
#: restores it afterwards; `to_js_name` consults it first, so every site that stages,
#: reads or references a field - `$name`, `ref:`, compute operands, a match's field,
#: name_from templates, guards, lengths - agrees on one spelling without each call
#: site having to know about collisions.
_JS_NAME_OVERRIDES: Dict[str, str] = {}


def to_js_name(name: str) -> str:
    override = _JS_NAME_OVERRIDES.get(name)
    if override is not None:
        return override
    return _mangle_js_name(name)


def unique_js_names(names: Iterable[str]) -> Dict[str, str]:
    """Give every name a JS spelling no other name in the set shares.

    A name keeps its plain mangling unless another name mangles to the same spelling
    (vendor keys `ER+P-1_s` and `ER-P-1_s` both mangle to `ER_P_1_s`), so a schema with
    no collision generates exactly the codec it always did. Within a colliding group a
    name that is already its own spelling keeps it - it is the only one a reader would
    expect there - and the rest follow in sorted order, the first keeping the plain
    spelling and each later one taking `_2`, `_3`, ... A suffix is skipped where it is
    some other name's plain spelling or already taken, so `a+b`, `a-b` and `a_b_2`
    give `a_b`, `a_b_3` and `a_b_2`. The output keys are restored from this mapping at
    the codec's entry points, so the suffix never reaches a caller.
    """
    unique = sorted(set(names))
    plain = {name: _mangle_js_name(name) for name in unique}
    reserved = set(plain.values())
    groups: Dict[str, List[str]] = {}
    for name in unique:
        groups.setdefault(plain[name], []).append(name)
    taken: Set[str] = set()
    result: Dict[str, str] = {}
    for spelling in sorted(groups):
        members = sorted(groups[spelling], key=lambda n: (n != spelling, n))
        result[members[0]] = spelling
        taken.add(spelling)
        suffix = 2
        for name in members[1:]:
            while True:
                candidate = '%s_%d' % (spelling, suffix)
                suffix += 1
                if candidate in reserved or candidate in taken:
                    continue
                if _mangle_js_name(candidate) != candidate:   # a reserved identifier
                    continue
                break
            result[name] = candidate
            taken.add(candidate)
    return result


def _mangle_js_name(name: str) -> str:
    mangled = re.sub(r'[^a-zA-Z0-9_]', '_', name)
    # An identifier cannot start with a digit: Watteco's `4_20_mA` emitted
    # `var 4_20_mA`, a syntax error that stopped the whole codec loading. The key is
    # restored at the entry points like any other mangled name, as are wholly numeric
    # names (mutelcor's "1" to "12"). Not an `_` prefix: the codec drops `_` keys from
    # its output as internal fields - which is also why a name whose first character
    # mangles to `_` (mutelcor's "[06] Button Totals") gets the prefix: it was
    # reported as `_06__Button_Totals` and then deleted as internal.
    if mangled[:1].isdigit() or (mangled[:1] == '_' and str(name)[:1] != '_'):
        mangled = 'n' + mangled
    if mangled in _CODEC_LOCALS or mangled in _JS_RESERVED or mangled in _codec_globals() \
            or re.match(r'^(decodePort|encodePort)', mangled):
        mangled += '_'
    return mangled


#: Byte width of every numeric type spelling clause 2 defines, canonical or alias
#: (PS-049). The list is closed (PS-326) and case-sensitive (PS-333): this used to
#: fall back to any `[usif]` followed by digits, so `float` read four bytes and `u17`
#: two, where every interpreter rejects both.
NUMERIC_TYPE_SIZES = {
    'u8': 1, 'uint8': 1, 's8': 1, 'i8': 1, 'int8': 1,
    'u16': 2, 'uint16': 2, 's16': 2, 'i16': 2, 'int16': 2,
    'u24': 3, 'uint24': 3, 's24': 3, 'i24': 3, 'int24': 3,
    'u32': 4, 'uint32': 4, 's32': 4, 'i32': 4, 'int32': 4,
    'u32le16': 4, 's32le16': 4, 'f32le16': 4,
    'u32be16le': 4, 's32be16le': 4, 'f32be16le': 4,
    'u64': 8, 'uint64': 8, 's64': 8, 'i64': 8, 'int64': 8,
    'f16': 2, 'bf16': 2, 'f32': 4, 'f64': 8,
}


def type_size(t: str) -> Optional[int]:
    return NUMERIC_TYPE_SIZES.get(str(t))


def parse_bit_slice_type(t: str) -> Optional[Tuple[str, int, int]]:
    """Parse bit-slice type syntax.

    Supports:
      - u8[3:4]   -> start=3, width=2

    The bracket range is the only bitfield spelling; the Verilog part-select
    `u8[3+:2]` was withdrawn by CR-2026-006.

    Returns (base_type, bit_start, bit_width) or None.
    """
    m = re.match(r'^([us]\d+)\[(\d+):(\d+)\]$', t)
    if m:
        base_t = m.group(1)
        lo = int(m.group(2))
        hi = int(m.group(3))
        if hi < lo:
            return None
        return base_t, lo, (hi - lo + 1)

    return None


#: (unit order, kind) of each word-ordered type (PS-271, PS-362, PS-363).
WORD_ORDERED = {
    'u32le16': ('le16', 'u'), 's32le16': ('le16', 's'), 'f32le16': ('le16', 'f'),
    'u32be16le': ('be16le', 'u'), 's32be16le': ('be16le', 's'), 'f32be16le': ('be16le', 'f'),
}


def is_word_ordered(t: str) -> bool:
    """Whether the type carries its 32 bits as two 16-bit units in an order it fixes."""
    return t in WORD_ORDERED


def is_signed(t: str) -> bool:
    clean = str(t)
    return clean.startswith('s') or clean.startswith('i')


def is_float(t: str) -> bool:
    # Exactly the three float spellings: a prefix test admitted `float` and `float16`,
    # which are not types (CR-2026-037), and read them as four bytes.
    return str(t) in ('f16', 'bf16', 'f32', 'f64')


def field_endian_override(t: str, field: Optional[Dict[str, Any]] = None) -> Optional[str]:
    """The byte order this field reads in, or None to use the schema's.

    A field's own `endian:` wins. The generator only ever derived byte order from a
    `le_`/`be_` type prefix, so a schema using the key generated a codec that read the
    schema's order instead. The prefixes are withdrawn (PS-053a, CR-2026-039): a type
    carrying one is unknown and refused.
    """
    if field is not None:
        declared = field.get('endian')
        if declared in ('big', 'little'):
            return declared
    return None


def formula_to_js(formula: str) -> str:
    """Convert formula syntax to JavaScript (DEPRECATED)."""
    js = formula
    js = re.sub(r'\bpow\b', 'Math.pow', js)
    js = re.sub(r'\babs\b', 'Math.abs', js)
    js = re.sub(r'\bsqrt\b', 'Math.sqrt', js)
    js = re.sub(r'\bmin\b', 'Math.min', js)
    js = re.sub(r'\bmax\b', 'Math.max', js)
    js = re.sub(r'\bround\b', 'roundHalfEven', js)  # half-to-even, as the interpreters use
    js = re.sub(r'\bfloor\b', 'Math.floor', js)
    js = re.sub(r'\bceil\b', 'Math.ceil', js)
    js = re.sub(r'\blog\b', 'Math.log', js)
    js = re.sub(r'\bexp\b', 'Math.exp', js)
    js = re.sub(r'\bPI\b', 'Math.PI', js)
    js = re.sub(r'\band\b', '&&', js)
    js = re.sub(r'\bor\b', '||', js)
    js = re.sub(r'\bnot\b', '!', js)
    # $field_name → d.field_name
    js = re.sub(r'\$([a-zA-Z_][a-zA-Z0-9_]*)', r'vars.\1', js)  # see ref_to_js
    # C-style ternary: cond ? a : b (already JS)
    return js


def polynomial_to_js(coeffs: List[float], input_expr: str) -> str:
    """Generate JS for polynomial evaluation using Horner's method."""
    if not coeffs:
        return '0'
    if len(coeffs) == 1:
        return str(coeffs[0])
    # Horner's method: ((a_n * x + a_{n-1}) * x + ...) * x + a_0
    result = str(coeffs[0])
    for coef in coeffs[1:]:
        if coef >= 0:
            result = f'({result} * {input_expr} + {coef})'
        else:
            result = f'({result} * {input_expr} - {abs(coef)})'
    return result


def compute_to_js(compute: Dict[str, Any]) -> str:
    """Generate JS for cross-field binary operation."""
    op = compute.get('op', 'add')
    a = compute.get('a', 0)
    b = compute.get('b', 0)
    
    def operand_to_js(spec):
        # `vars`, not `d` - see ref_to_js.
        if isinstance(spec, str) and spec.startswith('$'):
            return f'vars.{to_js_name(spec[1:])}'
        return str(spec)
    
    a_js = operand_to_js(a)
    b_js = operand_to_js(b)
    
    if op == 'add':
        return f'({a_js} + {b_js})'
    elif op == 'sub':
        return f'({a_js} - {b_js})'
    elif op == 'mul':
        return f'({a_js} * {b_js})'
    elif op == 'div':
        # PS-278: a zero divisor omits the field. `undefined` rather than NaN, because
        # JSON.stringify drops an undefined property but writes NaN as `null`, and
        # neither NaN nor null is what an absent field means.
        return f'({b_js} !== 0 ? {a_js} / {b_js} : undefined)'
    elif op == 'mod':
        # PS-277 floored: the remainder takes the divisor's sign. JavaScript's `%`
        # truncates, so `((a % b) + b) % b` is needed - the generator emitted the bare
        # `%` and therefore disagreed with the floored interpreters.
        # PS-284: operands truncate toward zero first.
        return (f'({b_js} !== 0 ? ((Math.trunc({a_js}) % Math.trunc({b_js})) '
                f'+ Math.trunc({b_js})) % Math.trunc({b_js}) : undefined)')
    elif op == 'idiv':
        # PS-276 floored: Math.floor, not Math.trunc, so it rounds toward negative
        # infinity. Operands truncate first (PS-284), matching the interpreters.
        return (f'({b_js} !== 0 ? Math.floor(Math.trunc({a_js}) / Math.trunc({b_js})) '
                f': undefined)')
    return '0'


def operand_ok_js(field_js: str) -> str:
    """A condition's operand can be compared: present (PS-540) and not a boolean (PS-549)."""
    return f"({field_js} != null && typeof {field_js} !== 'boolean')"


def condition_to_js(cond: Dict[str, Any]) -> str:
    """One guard condition, {field: $x, <op>: value}, as a JS boolean (PS-386)."""
    field_ref = cond.get('field', '')
    field_js = f'vars.{to_js_name(field_ref[1:])}'  # see ref_to_js
    for op, js in (('gt', '>'), ('gte', '>='), ('lt', '<'), ('lte', '<='),
                   ('eq', '==='), ('ne', '!==')):
        if op in cond:
            # PS-540: an absent operand fails the condition. JS would compare
            # `undefined !== 3` as true and `null >= 0` as true. PS-549: so does a
            # boolean, which JS would compare as 1 or 0.
            return f'({operand_ok_js(field_js)} && {field_js} {js} {cond[op]})'
    if 'in' in cond or 'not_in' in cond:
        # PS-540: an absent operand fails `not_in` too.
        return f'({operand_ok_js(field_js)} && {set_condition_to_js(field_js, cond)})'
    return 'true'


def set_condition_to_js(field_js: str, cond: Dict[str, Any]) -> str:
    """`in` / `not_in` (PS-543): the value equals one of a list of numbers, or none."""
    op = 'in' if 'in' in cond else 'not_in'
    values = json.dumps([v for v in cond[op]])
    return f'{values}.indexOf({field_js}) {">=" if op == "in" else "<"} 0'


def guard_to_js(guard: Dict[str, Any], value_expr: str, else_hook: str = '') -> str:
    """Generate JS for guard conditional evaluation.

    `else_hook` is an expression evaluated before the `else` is reported, for a field
    that has to know its guard failed (PS-475: an `else` is not compared with the range).
    """
    when_conditions = guard.get('when', [])
    else_value = guard.get('else', 'NaN')
    
    conditions = []
    for cond in when_conditions:
        field_ref = cond.get('field', '')
        if isinstance(field_ref, str) and field_ref.startswith('$'):
            field_js = f'vars.{to_js_name(field_ref[1:])}'  # see ref_to_js
        else:
            continue
        # PS-540: an absent operand fails the condition; JS would pass `undefined !== 3`
        # and `null >= 0`.
        if any(op in cond for op in ('gt', 'gte', 'lt', 'lte', 'eq', 'ne', 'in', 'not_in')):
            conditions.append(operand_ok_js(field_js))                # PS-540, PS-549
        if 'gt' in cond:
            conditions.append(f'{field_js} > {cond["gt"]}')
        elif 'gte' in cond:
            conditions.append(f'{field_js} >= {cond["gte"]}')
        elif 'lt' in cond:
            conditions.append(f'{field_js} < {cond["lt"]}')
        elif 'lte' in cond:
            conditions.append(f'{field_js} <= {cond["lte"]}')
        elif 'eq' in cond:
            conditions.append(f'{field_js} === {cond["eq"]}')
        elif 'ne' in cond:
            conditions.append(f'{field_js} !== {cond["ne"]}')
        elif 'in' in cond or 'not_in' in cond:                          # PS-543
            conditions.append(set_condition_to_js(field_js, cond))
    
    if not conditions:
        return value_expr
    
    cond_js = ' && '.join(conditions)
    if else_hook:
        return f'({cond_js}) ? {value_expr} : ({else_hook}, {else_value})'
    return f'({cond_js}) ? {value_expr} : {else_value}'


def canonical_modifiers_to_js(field: Dict[str, Any], input_expr: str) -> str:
    """Emit JS applying the bare modifiers in the canonical order: mult, div, add.

    Both call sites previously iterated the field dict, so the generated codec
    followed the source key order while the C interpreter applied mult, div, add.
    The generated JavaScript and the reference interpreter therefore disagreed for
    any field carrying `add` before `div` -- the shared conformance fixture
    examples/canonical-modifier-order.yaml pins this (PS-101).
    """
    expr = input_expr
    for key in ('mult', 'div', 'add'):
        operand = field.get(key)
        if operand is None:
            continue
        if key == 'mult':
            expr = f'({expr} * {operand})'
        elif key == 'div':
            # A zero divisor omits the field (PS-100): undefined, which the output
            # pass drops. Skipping the division reported the undivided value.
            expr = f'({expr} / {operand})' if operand != 0 else 'undefined'
        else:
            expr = f'({expr} + {operand})'
    return expr


def transform_to_js(transform_ops: List[Dict[str, Any]], input_expr: str) -> str:
    """Generate JS for transform array operations."""
    result = input_expr
    for op in transform_ops:
        if 'sqrt' in op and op['sqrt']:
            result = f'Math.sqrt(Math.max(0, {result}))'
        elif 'abs' in op and op['abs']:
            result = f'Math.abs({result})'
        elif 'pow' in op:
            result = f'Math.pow({result}, {op["pow"]})'
        elif 'floor' in op:  # Clamp lower bound
            result = f'Math.max({result}, {op["floor"]})'
        elif 'ceiling' in op:  # Clamp upper bound
            result = f'Math.min({result}, {op["ceiling"]})'
        elif 'clamp' in op:
            bounds = op['clamp']
            if isinstance(bounds, list) and len(bounds) >= 2:
                result = f'Math.max({bounds[0]}, Math.min({bounds[1]}, {result}))'
        elif 'log10' in op and op['log10']:
            # A non-positive input has no logarithm: the field is absent (PS-117).
            # Math.log10 gives -Infinity or NaN there, which the output pass drops.
            result = f'Math.log10({result})'
        elif 'log' in op and op['log']:
            result = f'Math.log({result})'
        elif 'add' in op:
            result = f'({result} + {op["add"]})'
        elif 'mult' in op:
            result = f'({result} * {op["mult"]})'
        elif 'div' in op:
            result = f'({result} / {op["div"]})' if op['div'] != 0 else 'undefined'
        elif 'op' in op:
            # PS-390: `round` is the one named operation, with `ties` even (the default)
            # or away from zero. `floor`/`ceil` here meant rounding down and up, which the
            # specification never defined; an unknown operation is rejected, not skipped.
            if op['op'] != 'round':
                raise ValueError(
                    f"transform stage names an unknown operation {op['op']!r} (PS-390)")
            decimals = op.get('decimals', 0)
            ties = op.get('ties', 'even')
            if ties not in ('even', 'away'):
                raise ValueError(f"round `ties` must be 'even' or 'away', got {ties!r} (PS-390)")
            helper = 'roundHalfEven' if ties == 'even' else 'roundHalfAway'
            result = f'{helper}({result}, {int(decimals)})'
        elif 'round' in op:
            raise ValueError(
                "`{round: n}` is not a transform stage; write {op: round, decimals: n} (PS-390)")
        else:
            raise ValueError(
                f"transform stage {op!r} names no operation of the PS-115 table (PS-390)")
    return result


def enum_label(entry):
    """What an enum value reports: its `name` where it is the description form (PS-394)."""
    if isinstance(entry, dict) and 'name' in entry:
        return entry['name']
    return entry


def is_remaining_length(declared):
    """True when a `length` means "to the end of the payload" (PS-014).

    The spelling is `remaining`; a negative integer is the internal sentinel the
    parsers map it to.
    """
    if isinstance(declared, str):
        return declared.strip().lower() == 'remaining'
    return isinstance(declared, int) and declared < 0


def length_to_js(field: Dict[str, Any], default: Any = 1) -> str:
    """A field's byte count as JS: an integer, `remaining`, or a preceding field (PS-464).

    A name, with or without `$`, resolves through `vars` as `repeat`'s byte_length does;
    unbound, it throws naming it (PS-465). Interpolated as written before, so `$n` was a
    JS syntax error and a bare `n` resolved to whatever local happened to share the name.
    """
    declared = field.get('length', default)
    if is_remaining_length(declared):
        return 'buf.length - pos'
    if isinstance(declared, str) and not re.fullmatch(r'-?\d+', declared.strip()):
        ref = to_js_name(declared.strip().lstrip('$'))
        return (f'(vars.{ref} === undefined ? (function () {{ throw new Error("length names '
                f'{json.dumps(ref)[1:-1]}, which is not a field decoded before this one '
                f'(PS-465)"); }})() : vars.{ref})')
    return str(int(declared))


def name_from_to_js(field: Dict[str, Any], js_name: str) -> Tuple[List[str], str]:
    """Build the JS for a field's output key, honouring `name_from` (PS-265..PS-267).

    Returns (guard statements, key expression). The key expression indexes `d`, so a
    field whose key varies with the payload can be reported at all - a device with a
    variable number of active instances puts the instance in the key
    ("region_3_avg_dwell"), and enumerating every possible instance as its own field is
    not an alternative because the count is a device configuration rather than a
    property of the schema.

    References resolve from `vars`, which is keyed by the schema-level `name` - a field
    with `name_from` still declares one, and that is what `$references`, test vectors and
    encoding use (PS-267).

    A reference that has not been decoded throws, which the generated entry point turns
    into an `errors` entry, matching PS-266 and the interpreter. Concatenation writes a
    value as PS-532 asks - `3` not `3.0`, `1e-7`, `true` - because it is ECMAScript's own
    Number::toString; an object or array is refused.
    """
    template = field.get('name_from')
    if not template:
        return [], f'd.{js_name}'

    parts: List[str] = []
    refs: List[str] = []
    last = 0
    for match in re.finditer(r'\$\{(\w+)\}', str(template)):
        literal = str(template)[last:match.start()]
        if literal:
            parts.append(json.dumps(literal))
        reference = to_js_name(match.group(1))
        # A template writes the value reported, a looked-up field's label (PS-532), not
        # the number a reference binds (PS-523).
        parts.append(f'("{reference}" in lbl ? lbl.{reference} : vars.{reference})')
        refs.append(reference)
        last = match.end()
    tail = str(template)[last:]
    if tail:
        parts.append(json.dumps(tail))
    if not parts:
        parts.append(json.dumps(str(template)))
    # Force string concatenation even when the template starts with a reference.
    if not parts[0].startswith('"'):
        parts.insert(0, '""')

    guards = []
    for reference in refs:
        message = (f"name_from for '{field.get('name')}' references "
                   f"'{reference}', which has not been decoded")
        guards.append(f'if (vars.{reference} === undefined) '
                      f'throw new Error({json.dumps(message)});')
        omitted = (f"name_from for '{field.get('name')}' references {reference}, which "
                   "reported no value (PS-266, PS-532)")
        guards.append(f'if ("{reference}" in lbl && lbl.{reference} === undefined) '
                      f'throw new Error({json.dumps(omitted)});')
        # PS-532: concatenation writes a number, string or boolean as the key wants it,
        # and an object or array has no text to write.
        shape = (f"template {str(template)!r} references {reference!r}, which is not a "
                 "number, string or boolean and cannot be written into a key (PS-532)")
        guards.append(f'if (vars.{reference} !== null && typeof vars.{reference} === '
                      f'"object" && !("{reference}" in lbl)) throw new Error({json.dumps(shape)});')
    return guards, 'd[' + ' + '.join(parts) + ']'


def ref_to_js(ref: str) -> str:
    """Convert a `$field` reference to a JS expression.

    Resolves against `vars`, which records every field - internal or not - after its
    modifiers, and a looked-up field as PS-523 binds it, matching the interpreters'
    variable table. `d` holds only the reported
    fields, so a reference to an internal name (one beginning with an underscore)
    resolved to undefined: rakwireless/qingping computes both humidity and temperature
    from internal bitfields and reported neither.

    This only works because all five `vars` emitters record the post-modifier value. An
    earlier attempt switched the references alone and lost 15 comparisons, because
    `vars` then held the raw read and every reference to a scaled field resolved to the
    unscaled one.
    """
    if isinstance(ref, str) and ref.startswith('$'):
        return f'vars.{to_js_name(ref[1:])}'
    return str(ref)


class TS013Generator:
    def __init__(self, schema: Dict[str, Any], source: str = '', slim: bool = False):
        # `slim` builds a decode-only codec: no encoder, and no commentary. A generated
        # codec is mostly a way to test a schema; where one is deployed, size can matter
        # more than encoding, and the encoder runtime is most of a small codec.
        self.slim = slim
        # Every `$ref` is spliced up front, as the interpreter does (PS-346, PS-347), and
        # a schema whose references are invalid is refused (PS-345, PS-348, PS-349).
        from schema_interpreter import expand_refs
        schema, ref_errors = expand_refs(schema)
        if ref_errors:
            raise ValueError(ref_errors[0])
        # The repeat iterator's and reserve's schema rules (PS-350 to PS-387, PS-471).
        from schema_interpreter import case_body_errors, schema_iterator_errors
        # PS-441: a case body is a field list; a bare string was iterated by character.
        iterator_problems = case_body_errors(schema)
        iterator_problems += schema_iterator_errors(schema)
        from schema_interpreter import internal_name_errors, optional_errors
        iterator_problems += optional_errors(schema.get('fields')) + internal_name_errors(schema)
        for port_entry in (schema.get('ports') or {}).values():
            group = port_entry.get('fields') if isinstance(port_entry, dict) else port_entry
            iterator_problems += optional_errors(group)
        # PS-445, PS-452: a guard only on a computed field, one operation per stage.
        from schema_interpreter import arithmetic_schema_errors, meta_declaration_errors
        iterator_problems += arithmetic_schema_errors(schema)
        # PS-510: ipso and senml are mappings, and ipso carries `object`.
        from schema_interpreter import annotation_schema_errors
        iterator_problems += annotation_schema_errors(schema)
        from schema_interpreter import reference_errors
        iterator_problems += reference_errors(schema)            # PS-539
        iterator_problems += meta_declaration_errors(schema)    # PS-491
        # PS-525, PS-526: on_repeat's values, merge on objects only, one mode per name.
        from schema_interpreter import on_repeat_schema_errors
        iterator_problems += on_repeat_schema_errors(schema)
        # PS-358, PS-519, PS-520: a malformed literal, as the interpreter refuses it.
        from schema_interpreter import literal_errors, typed_field_dicts
        for field_def in typed_field_dicts(schema):
            iterator_problems += literal_errors(field_def)
        if iterator_problems:
            raise ValueError(iterator_problems[0])
        self.schema = schema
        self.source = source
        self.name = schema.get('name', 'unknown')
        self.version = schema.get('version', 1)
        self.endian = schema.get('endian', 'big')
        self.has_ports = 'ports' in schema
        self.has_commands = 'downlink_commands' in schema
        self.downlink_commands = schema.get('downlink_commands', {})
        self.indent = 0
        # Constructs this generator declined to emit, surfaced as `// TODO:` comments in
        # the generated codec rather than dropped silently.
        self.gaps: List[str] = []

    def _i(self) -> str:
        return '  ' * self.indent

    def generate(self) -> str:
        global _JS_NAME_OVERRIDES
        saved = _JS_NAME_OVERRIDES
        _JS_NAME_OVERRIDES = {
            name: js for name, js in unique_js_names(self._field_names()).items()
            if js != _mangle_js_name(name)
        }
        try:
            return self._generate()
        finally:
            _JS_NAME_OVERRIDES = saved

    def _generate(self) -> str:
        lines = []
        lines.append(f'// TS013 Payload Codec — {self.name}')
        lines.append(f'// Schema version: {self.version}')
        if self.source:
            lines.append(f'// Generated from: {self.source}')
        lines.append(f'// DO NOT EDIT — regenerate from schema')
        lines.append('')
        lines.append(self._gen_helpers())
        lines.append('')
        if not self.slim:
            lines.append(self._gen_encoder_runtime())
            lines.append('')
        lines.append(self._gen_decode_fields())
        encode_fields = '' if self.slim else self._gen_encode_fields()
        if encode_fields:
            lines.append('')
            lines.append(encode_fields)
        if self.has_commands:
            lines.append('')
            lines.append(self._gen_command_functions())
        lines.append('')
        lines.append(self._gen_entry_points())
        code = '\n'.join(lines)
        if self.slim:
            code = _slim(code)
        return code

    def _gen_helpers(self) -> str:
        return '''// --- Sequence lookup ---
// PS-104 indexes a sequence from zero; PS-105 makes an out-of-bounds index an error,
// not the raw index. Reporting the raw value let a payload that does not match its
// schema decode as though it did - every implementation did that, silently, until
// PS-105 was implemented. A mapping gap is the different case: it omits the field
// (PS-269), because a gap is a known unknown rather than a shape mismatch.
function seqLookup(table, index) {
  if (index >= 0 && index < table.length && table[index] !== undefined) {
    return table[index];
  }
  throw new Error("lookup index " + index + " out of bounds for " + table.length + " entries");
}

// --- What a reference to a looked-up field binds (PS-523) ---
// A numeric label is bound as itself, so a table of sizes feeds arithmetic; a text or
// boolean label, or no entry at all, binds the number before the lookup.
function refValue(label, number) {
  return typeof label === "number" ? label : number;
}

// --- Lookup default template ---
// PS-406: a mapping default carrying ${value} names the value it could not map, written
// in decimal. String() gives the shortest round-tripping decimal with no fraction where
// the value is integral, which is what the reference interpreter writes too.
function lookupTemplate(template, value) {
  return template.split("${value}").join(String(value));
}

// --- Range quality (valid_range -> _quality) ---
// PS-131/PS-182. Checked after all arithmetic, on the reported value, and the value
// is passed through unchanged whatever the verdict (PS-132). Boundaries are `good`.
// A non-numeric value is reported `good` rather than skipped, matching the
// interpreters - the flag says "not out of range", not "was checked".
function checkRange(q, w, name, value, lo, hi) {
  if (typeof value !== "number" || !isFinite(value)) { q[name] = "good"; return; }
  if (value < lo || value > hi) {
    w.push(name + ": value " + value + " outside valid range [" + lo + ", " + hi + "]");
    q[name] = "out_of_range";
  } else {
    q[name] = "good";
  }
}

// --- Unknown TLV tags ---
// PS-301 to PS-304. A generated codec has to report an undescribed tag the way an
// interpreter reading the same schema does, or the two conformance paths disagree:
// before this the generator ignored `unknown` entirely, so `unknown: error` errored on
// one path and skipped in silence on the other.
function tlvTagLabel(parts) {
  var out = [];
  for (var n = 0; n < parts.length; n++) {
    var hex = (parts[n] & 0xFF).toString(16).toUpperCase();
    out.push("0x" + (hex.length < 2 ? "0" + hex : hex));
  }
  return out.join(", ");
}

// The captured bytes of an unknown tag, under the key PS-303 names. Generated codecs
// merge a TLV case into the parent, so this is always the merged form.
function tlvUnknownRaw(d, tag, buf, from, span, separate) {
  var hex = "";
  for (var n = from; n < from + span && n < buf.length; n++) {
    var part = (buf[n] & 0xFF).toString(16);
    hex += part.length < 2 ? "0" + part : part;
  }
  // PS-303: with `merge: false` the captured entry is one of the channels, as the
  // interpreters report it; merged output has no channel list, so `unknown_tags`.
  var key = separate ? "channels" : "unknown_tags";
  if (!d[key]) { d[key] = []; }
  d[key].push({ tag: tag, raw: hex });
}

// --- A name reported again (PS-157, PS-525) ---
// One tlv entry's output folded into the tlv's. A name already reported is combined as
// its on_repeat says: collected into an array by default, kept from the last entry
// under `last`, combined member by member under `merge`. `collected` holds the names
// already turned into arrays: a value that is itself a list is one element, not
// appended into. The generated codec kept the last value for every name, so where two
// entries reported one name the interpreters gave an array and this gave one value.
function tlvFold(outer, entry, modes, lastMembers, collected) {
  for (var k in entry) {
    if (!Object.prototype.hasOwnProperty.call(entry, k)) continue;
    var v = entry[k];
    if (v === undefined) continue;
    var mode = modes[k] || "collect";
    if (!Object.prototype.hasOwnProperty.call(outer, k) || outer[k] === undefined) {
      outer[k] = v;
    } else if (mode === "last") {
      outer[k] = v;
    } else if (mode === "merge") {
      var combined = {}, before = outer[k];
      for (var a in before) if (Object.prototype.hasOwnProperty.call(before, a)) combined[a] = before[a];
      for (var m in v) {
        if (!Object.prototype.hasOwnProperty.call(v, m)) continue;
        if (Object.prototype.hasOwnProperty.call(combined, m) && !(lastMembers[k] && lastMembers[k][m])) {
          throw new Error("tlv: the merged object '" + k + "' reports its member '" + m + "' twice (PS-525)");
        }
        combined[m] = v[m];
      }
      outer[k] = combined;
    } else if (collected[k]) {
      outer[k].push(v);
    } else {
      outer[k] = [outer[k], v];
      collected[k] = true;
    }
  }
}

// --- Absent values ---
// A field with no value to report is absent (PS-278, PS-100, PS-117), and NaN and the
// infinities are not JSON values (PS-282). An omitted step yields undefined, which
// later arithmetic turns into NaN, so both are dropped here, once, on the way out -
// the generated codec reported null for log10(0) and the undivided value for div 0.
// Mirrors normalize_output in the reference interpreter.
function omitAbsent(v) {
  if (typeof v === 'number') return isFinite(v) ? v : undefined;
  if (Array.isArray(v)) {
    var list = [];
    for (var i = 0; i < v.length; i++) {
      var item = omitAbsent(v[i]);
      if (item !== undefined) list.push(item);
    }
    return list;
  }
  if (v === null || typeof v !== 'object') return v;
  var out = {};
  for (var k in v) {
    if (Object.prototype.hasOwnProperty.call(v, k)) {
      var x = omitAbsent(v[k]);
      if (x !== undefined) out[k] = x;
    }
  }
  return out;
}

// --- Rounding ---
// Half-to-even (banker's rounding), matching the Python, Java and C# interpreters.
//
// Two traps this avoids. First, JavaScript's Math.round is half-UP and asymmetric
// for negatives (Math.round(-2.5) is -2 but Math.round(2.5) is 3), so a codec using
// it disagreed with the interpreters on any exact half - mclimate/vicki reported a
// humidity of 78.13 where the interpreters say 78.12.
//
// Second, rounding v*10^d is not the same as rounding v at d decimals: 2.355 is
// stored as 2.35499999999999998, but 2.355*100 lands on exactly 235.5, so the
// multiply turns a value below the half into a tie and rounds it up. toFixed is
// correctly rounded on the exact stored value, so it is used for the ordinary case
// and a genuine tie is detected from the long expansion.
function roundHalfEven(v, decimals) {
  return roundDecimal(v, decimals, false);
}

/* `ties: away` (PS-390): a tie rounds away from zero, as JavaScript's toFixed does in
 * the vendor decoders this reproduces. Every other value rounds as roundHalfEven. */
function roundHalfAway(v, decimals) {
  return roundDecimal(v, decimals, true);
}

function roundDecimal(v, decimals, away) {
  if (typeof v !== 'number' || !isFinite(v)) return v;
  var d = Math.min(Math.max(decimals || 0, 0), 90);
  var neg = v < 0;
  var a = Math.abs(v);
  // A genuine tie is a 5 at the cut with nothing but zeros beyond it in the exact
  // expansion. Testing Number(s) === a instead would be wrong: the shortest
  // representation of 2.345 round-trips exactly while the stored value is above it.
  var long = a.toFixed(Math.min(d + 19, 100));
  var frac = long.slice(long.indexOf('.') + 1);
  var out;
  if (frac.charAt(d) === '5' && /^0*$/.test(frac.slice(d + 1))) {
    var f = Math.pow(10, d);
    var lower = Math.floor(a * f);
    out = ((!away && lower % 2 === 0) ? lower : lower + 1) / f;
  } else {
    out = Number(a.toFixed(d));
  }
  if (out === 0) return 0;   // never report -0
  return neg ? -out : out;
}

// --- Binary helpers ---
/* A JavaScript number holds integers exactly to 2^53-1, so a 7- or 8-byte field can
 * carry a value this codec cannot represent. PS-295 forbids reporting the rounded
 * number, and PS-296 records the limit: above it the exact value is reported as a
 * decimal string. The digits are accumulated in base 10 rather than through BigInt,
 * which not every network server's codec sandbox provides. */
var SAFE_INTEGER = 9007199254740991;

/* A read past the end of the payload is an error, as it is in every interpreter. The
 * readers below used to substitute 0 for a missing byte, so a truncated payload decoded
 * to plausible values with no error at all.
 *
 * PS-535 (CR-2026-115): the error identifies the field. Each field's code sets `_fld` to
 * its label before it reads - `'name'`, or `an unnamed skip` - so the innermost field
 * reading is the one named, and the text is the reference's exactly. */
var _fld = null;
function underflow(detail) {
  return new Error(_fld === null ? detail : "Field " + _fld + ": " + detail + " (PS-535)");
}
function need(buf, pos, size) {
  if (pos + size > buf.length) {
    throw underflow("Buffer underflow: need " + size + " bytes at offset " + pos +
                    ", but only " + Math.max(0, buf.length - pos) + " remaining");
  }
}
/* A bit range or a bool reads its byte(s) without consuming them. */
function needPeek(buf, pos, size) {
  if (pos + size > buf.length) throw underflow("Buffer underflow at peek offset " + pos);
}

function orderedBytes(buf, pos, size, endian) {
  need(buf, pos, size);
  var out = [];
  var i;
  if (endian === 'big') {
    for (i = 0; i < size; i++) out.push(buf[pos + i] || 0);
  } else {
    for (i = size - 1; i >= 0; i--) out.push(buf[pos + i] || 0);
  }
  return out;
}

/* Exact base-256 to decimal, most significant byte first. */
function decimalOf(bytes) {
  var digits = [0];
  for (var k = 0; k < bytes.length; k++) {
    var carry = bytes[k];
    for (var d = 0; d < digits.length; d++) {
      var cur = digits[d] * 256 + carry;
      digits[d] = cur % 10;
      carry = Math.floor(cur / 10);
    }
    while (carry > 0) {
      digits.push(carry % 10);
      carry = Math.floor(carry / 10);
    }
  }
  var text = '';
  for (var j = digits.length - 1; j >= 0; j--) text += digits[j];
  return text;
}

/* `width` bits from bit `off` of the value whose bytes are `bytes`, most significant
 * first, as a byte array of their own, most significant first. Bits are moved one at a
 * time, so a range above bit 31 - or wider than 53 bits - is as exact as the
 * reference's arbitrary-precision integer. A shift and mask on the whole value works on
 * JavaScript's 32-bit integers, so bits 40..47 of a six-byte firmware word came out as
 * some other byte, and a range of a 64-bit base read 0. */
function bitBytes(bytes, off, width) {
  var n = Math.ceil(width / 8), out = [];
  for (var i = 0; i < n; i++) out.push(0);
  var top = bytes.length * 8;
  for (var b = 0; b < width; b++) {
    var at = off + b;
    if (at >= top) break;
    if ((bytes[bytes.length - 1 - Math.floor(at / 8)] >> (at % 8)) & 1) {
      out[n - 1 - Math.floor(b / 8)] |= 1 << (b % 8);
    }
  }
  return out;
}

/* One part of a bitfield_string, rendered in `fmt` (PS-430). */
function bitfieldPart(bytes, off, width, fmt) {
  var out = bitBytes(bytes, off, width);
  if (fmt === 'decimal') return decimalOf(out);
  var hex = '';
  for (var k = 0; k < out.length; k++) hex += (out[k] < 16 ? '0' : '') + out[k].toString(16);
  hex = hex.replace(/^0+/, '') || '0';
  return fmt === 'hex:upper' ? hex.toUpperCase() : hex;
}

/* A bit range of a base wider than six bytes, sign-extended from the range's width when
 * `signed` (PS-352). A number where it is exact, else the decimal string (PS-296). */
function bitRange(bytes, off, width, signed) {
  var out = bitBytes(bytes, off, width), neg = false, k;
  if (signed && width > 0 && (out[0] >> ((width - 1) % 8)) & 1) {
    // Two's complement within `width` bits: invert, mask the top byte, add one.
    neg = true;
    var spare = out.length * 8 - width;
    for (k = 0; k < out.length; k++) out[k] = (~out[k]) & 0xFF;
    out[0] &= 0xFF >> spare;
    for (k = out.length - 1; k >= 0; k--) { out[k] = (out[k] + 1) & 0xFF; if (out[k]) break; }
  }
  var text = decimalOf(out), v = Number(text);
  if (v <= SAFE_INTEGER) return neg ? -v : v;
  return neg ? '-' + text : text;
}

function readU(buf, pos, size, endian) {
  if (size >= 7) {
    var text = decimalOf(orderedBytes(buf, pos, size, endian));
    var n = Number(text);
    return n <= SAFE_INTEGER ? n : text;
  }
  need(buf, pos, size);
  var v = 0;
  if (endian === 'big') {
    for (var i = 0; i < size; i++) v = (v * 256) + (buf[pos + i] || 0);
  } else {
    for (var i = size - 1; i >= 0; i--) v = (v * 256) + (buf[pos + i] || 0);
  }
  return v;
}

/* Two 16-bit big-endian units, least significant unit first (PS-271). The type fixes
 * both orders, so the endian argument is deliberately absent (PS-272). */
function readU32LE16(buf, pos) {
  need(buf, pos, 4);
  var low = ((buf[pos] || 0) << 8) | (buf[pos + 1] || 0);
  var high = ((buf[pos + 2] || 0) << 8) | (buf[pos + 3] || 0);
  return low + high * 65536;
}

function readS32LE16(buf, pos) {
  var v = readU32LE16(buf, pos);
  return v >= 2147483648 ? v - 4294967296 : v;
}

function readS(buf, pos, size, endian) {
  if (size >= 7) {
    var bytes = orderedBytes(buf, pos, size, endian);
    var negative = (bytes[0] & 0x80) !== 0;
    if (negative) {
      /* Two's complement: invert and add one, then report the magnitude. Math.pow was
       * used for the sign extension before, which is inexact above 2^53. */
      var carry = 1;
      for (var i = bytes.length - 1; i >= 0; i--) {
        var inv = (~bytes[i] & 0xFF) + carry;
        bytes[i] = inv & 0xFF;
        carry = inv >> 8;
      }
      var magnitude = decimalOf(bytes);
      var negated = Number('-' + magnitude);
      return negated >= -SAFE_INTEGER ? negated : '-' + magnitude;
    }
    var text = decimalOf(bytes);
    var n = Number(text);
    return n <= SAFE_INTEGER ? n : text;
  }
  var v = readU(buf, pos, size, endian);
  var sign = Math.pow(2, size * 8 - 1);
  if (v >= sign) v -= sign * 2;
  return v;
}

/* PS-388: a computed `integer` with a fractional part is an error, never rounded. */
function asInteger(v, name) {
  if (typeof v === 'number' && isFinite(v) && v !== Math.floor(v)) {
    throw new Error(name + ': type integer but the computed value is ' + v);
  }
  return v;
}

/* RFC 4648 base64 of a byte range. Written out because btoa is not available in every
 * JavaScript runtime a TS013 codec runs under. */
function toBase64(buf, pos, n) {
  var A = 'ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/';
  var out = '';
  for (var i = 0; i < n; i += 3) {
    var b0 = buf[pos + i], b1 = buf[pos + i + 1], b2 = buf[pos + i + 2];
    var t = (b0 << 16) | ((i + 1 < n ? b1 : 0) << 8) | (i + 2 < n ? b2 : 0);
    out += A.charAt((t >> 18) & 63) + A.charAt((t >> 12) & 63);
    out += i + 1 < n ? A.charAt((t >> 6) & 63) : '=';
    out += i + 2 < n ? A.charAt(t & 63) : '=';
  }
  return out;
}

/* The named encodings (PS-422 to PS-425), applied to the unsigned integer read and before
 * the modifiers. A BCD group above 9 is an error, never a number (PS-424). */
function decodeEncoding(v, enc, size) {
  var bits = size * 8, i, out;
  if (enc === 'sign_magnitude') {
    var half = Math.pow(2, bits - 1);
    if (v >= half) { out = -(v - half); return out === 0 ? 0 : out; }
    return v;
  }
  if (enc === 'bcd') {
    out = 0;
    for (i = size * 2 - 1; i >= 0; i--) {
      var digit = Math.floor(v / Math.pow(16, i)) % 16;
      if (digit > 9) throw new Error("Invalid BCD digit: " + digit + " (PS-424)");
      out = out * 10 + digit;
    }
    return out;
  }
  out = v;
  for (var shift = 1; shift < bits; shift *= 2) out = out ^ Math.floor(out / Math.pow(2, shift));
  return out >>> 0;
}

/* The MCCI minifloats (PS-417 to PS-419). Decoded by their formulas, never as IEEE half
 * precision (PS-421); an sflt24 with exponent 127 has no value and is left undefined, which
 * the output pass drops (PS-282). */
function decodeBf16(word) {
  var dv = new DataView(new ArrayBuffer(4));
  dv.setUint16(0, word, false);
  return dv.getFloat32(0, false);
}

function decodeMinifloat(type, w) {
  var sign, e, f, v;
  if (type === 'uflt16') {
    e = Math.floor(w / 4096); f = w % 4096;
    return f / 4096 * Math.pow(2, e - 15);
  }
  if (type === 'sflt16') {
    sign = w >= 32768 ? -1 : 1; e = Math.floor(w / 2048) % 16; f = w % 2048;
    v = sign * f / 2048 * Math.pow(2, e - 15);
    return v === 0 ? 0 : v;
  }
  sign = w >= 8388608 ? -1 : 1; e = Math.floor(w / 65536) % 128; f = w % 65536;
  if (e === 127) return undefined;
  v = e === 0 ? sign * f / 65536 * Math.pow(2, -62) : sign * (1 + f / 65536) * Math.pow(2, e - 63);
  return v === 0 ? 0 : v;
}

/* IEEE 754 binary16. Generated codecs read an f16 with readF64 before, so eight bytes
 * were taken for a two-byte field. */
function readF16(buf, pos, endian) {
  need(buf, pos, 2);
  var h = endian === 'little'
    ? ((buf[pos + 1] || 0) << 8) | (buf[pos] || 0)
    : ((buf[pos] || 0) << 8) | (buf[pos + 1] || 0);
  var sign = (h & 0x8000) ? -1 : 1;
  var exp = (h >> 10) & 0x1F;
  var frac = h & 0x3FF;
  if (exp === 0) return sign * Math.pow(2, -14) * (frac / 1024);
  if (exp === 31) return frac ? NaN : sign * Infinity;
  return sign * Math.pow(2, exp - 15) * (1 + frac / 1024);
}

/* bfloat16 (PS-558): the word is the upper half of a binary32 whose lower half is zero. */
function readBf16(buf, pos, endian) {
  var w = readU(buf, pos, 2, endian);
  var dv = new DataView(new ArrayBuffer(4));
  dv.setUint16(0, w, false);
  return dv.getFloat32(0, false);
}

function readF32(buf, pos, endian) {
  need(buf, pos, 4);
  var b = buf.slice(pos, pos + 4);
  if (endian === 'little') b = [b[3], b[2], b[1], b[0]];
  var ab = new ArrayBuffer(4);
  var dv = new DataView(ab);
  for (var i = 0; i < 4; i++) dv.setUint8(i, b[i] || 0);
  return dv.getFloat32(0, false);
}

function readF64(buf, pos, endian) {
  need(buf, pos, 8);
  var b = buf.slice(pos, pos + 8);
  if (endian === 'little') b = [b[7], b[6], b[5], b[4], b[3], b[2], b[1], b[0]];
  var ab = new ArrayBuffer(8);
  var dv = new DataView(ab);
  for (var i = 0; i < 8; i++) dv.setUint8(i, b[i] || 0);
  return dv.getFloat64(0, false);
}

function writeU(buf, pos, size, value, endian) {
  value = Math.round(value) >>> 0;
  if (endian === 'big') {
    for (var i = size - 1; i >= 0; i--) { buf[pos + i] = value & 0xFF; value = (value >>> 8); }
  } else {
    for (var i = 0; i < size; i++) { buf[pos + i] = value & 0xFF; value = (value >>> 8); }
  }
}

/* The word-ordered 32-bit types (PS-271, PS-362, PS-363): `le16` is the low 16-bit unit
 * first, each unit big-endian; `be16le` the high unit first, each unit little-endian.
 * kind is u, s or f (an IEEE 754 binary32 of the assembled word). */
function readWordOrdered(buf, pos, layout, kind) {
  need(buf, pos, 4);
  var b0 = buf[pos] || 0, b1 = buf[pos + 1] || 0, b2 = buf[pos + 2] || 0, b3 = buf[pos + 3] || 0;
  var word = layout === 'le16'
    ? ((b2 << 8) | b3) * 65536 + ((b0 << 8) | b1)
    : ((b1 << 8) | b0) * 65536 + ((b3 << 8) | b2);
  if (kind === 'f') {
    var dv = new DataView(new ArrayBuffer(4));
    dv.setUint32(0, word, false);
    return dv.getFloat32(0, false);
  }
  if (kind === 's' && word >= 2147483648) return word - 4294967296;
  return word;
}

function writeWordOrdered(buf, pos, value, layout, kind) {
  var word;
  if (kind === 'f') {
    var dv = new DataView(new ArrayBuffer(4));
    dv.setFloat32(0, value, false);
    word = dv.getUint32(0, false);
  } else {
    word = Math.round(value);
    if (word < 0) word += 4294967296;
    word = word % 4294967296;
  }
  var high = Math.floor(word / 65536), low = word % 65536;
  var first = layout === 'le16' ? low : high, second = layout === 'le16' ? high : low;
  if (layout === 'le16') {
    buf[pos] = (first >> 8) & 0xFF; buf[pos + 1] = first & 0xFF;
    buf[pos + 2] = (second >> 8) & 0xFF; buf[pos + 3] = second & 0xFF;
  } else {
    buf[pos] = first & 0xFF; buf[pos + 1] = (first >> 8) & 0xFF;
    buf[pos + 2] = second & 0xFF; buf[pos + 3] = (second >> 8) & 0xFF;
  }
}

/* The inverse of readU32LE16: low 16-bit unit first, each unit big-endian (PS-271). */
function writeU32LE16(buf, pos, value) {
  var v = Math.round(value);
  if (v < 0) v += 4294967296;
  v = v % 4294967296;
  var low = v % 65536;
  var high = Math.floor(v / 65536);
  buf[pos] = (low >> 8) & 0xFF;
  buf[pos + 1] = low & 0xFF;
  buf[pos + 2] = (high >> 8) & 0xFF;
  buf[pos + 3] = high & 0xFF;
}

function writeS(buf, pos, size, value, endian) {
  if (value < 0) value += (1 << (size * 8));
  writeU(buf, pos, size, value, endian);
}'''

    def _gen_encoder_runtime(self) -> str:
        """The runtime encoder and the reference tables it reads.

        The tables come from tools/schema_interpreter.py rather than being restated, so a
        type the reference learns is a type the codec writes.
        """
        from schema_interpreter import (
            COMPUTED_TYPES, INTEGER_TYPE_INFO, SchemaInterpreter, WORD_ORDERED_TYPES)
        runtime = _strip_js_comments(ENCODER_RUNTIME_JS)
        types = {k: [size, signed] for k, (size, signed) in INTEGER_TYPE_INFO.items()}
        return '\n'.join([
            f'var ENC_TYPES = {json.dumps(types, sort_keys=True)};',
            f'var ENC_WORD_ORDERED = {json.dumps(WORD_ORDERED_TYPES, sort_keys=True)};',
            f'var ENC_ENDIAN_OPAQUE = {json.dumps(list(SchemaInterpreter._ENDIAN_OPAQUE_TYPES))};',
            f'var ENC_COMPUTED = {json.dumps(list(COMPUTED_TYPES))};',
            runtime,
        ])

    def _enc_vars(self) -> Dict[str, Dict[str, Any]]:
        """var name -> the field declaring it, first in document order (_field_declaring_var)."""
        found: Dict[str, Dict[str, Any]] = {}

        def visit(node):
            if isinstance(node, dict):
                var = node.get('var')
                if isinstance(var, str) and node.get('name') and var not in found:
                    found[var] = {'name': node['name'], 'lookup': node.get('lookup'),
                                  'type': node.get('type'), 'values': node.get('values')}
                for value in node.values():
                    visit(value)
            elif isinstance(node, list):
                for item in node:
                    visit(item)

        visit(self.schema)
        return found

    def _enc_named(self) -> Dict[str, Dict[str, Any]]:
        """field name -> the first field of that name carrying a lookup (_field_named)."""
        found: Dict[str, Dict[str, Any]] = {}

        def visit(node):
            if isinstance(node, dict):
                name = node.get('name')
                if isinstance(name, str) and name not in found and (
                        node.get('lookup') is not None or node.get('type') == 'enum'):
                    found[name] = {'name': name, 'lookup': node.get('lookup'),
                                   'type': node.get('type'), 'values': node.get('values')}
                for value in node.values():
                    visit(value)
            elif isinstance(node, list):
                for item in node:
                    visit(item)

        visit(self.schema)
        return found

    def _next_uid(self) -> int:
        """A number for a generated variable that must not collide with a nested one."""
        self._uid = getattr(self, '_uid', 0) + 1
        return self._uid

    def _gen_decode_fields(self) -> str:
        parts = []
        if self.has_ports:
            for port_key, port_def in self.schema['ports'].items():
                fname = f'decodePort{port_key}'
                fields = port_def.get('fields', [])
                parts.append(self._gen_decode_fn(fname, fields))
        else:
            fields = self.schema.get('fields', [])
            parts.append(self._gen_decode_fn('decodePayload', fields))
        return '\n\n'.join(parts)

    def _expand_refs(self, fields: List[Dict], depth: int = 0) -> List[Dict]:
        """Splice `$ref: '#/definitions/name'` entries into the list they appear in.

        Resolved at generation time, and the referenced definition's `fields:` are
        spliced rather than nested - a nested container with no `type: object` is never
        descended into, so every field inside it would go missing.

        Unhandled until now, so a `$ref` emitted nothing and every field after it read
        from the wrong offset: ref-header.yaml reported a reading of 515 rather than 772.
        Only local `#/definitions/...` references resolve, matching the interpreters;
        cross-file references are a pre-step (tools/schema_preprocessor.py).
        """
        out: List[Dict] = []
        if depth > 16:   # a definition that refers to itself, directly or in a cycle
            return list(fields)
        definitions = self.schema.get('definitions') or {}
        for field in fields:
            if not isinstance(field, dict):
                continue
            ref = field.get('$ref')
            if not isinstance(ref, str):
                out.append(field)
                continue
            prefix = '#/definitions/'
            target = None
            if ref.startswith(prefix):
                definition = definitions.get(ref[len(prefix):])
                if isinstance(definition, dict):
                    target = definition.get('fields')
            if not isinstance(target, list):
                # Unresolvable: keep the entry so it produces nothing, rather than
                # dropping it and shifting every later offset.
                out.append(field)
                continue
            out.extend(self._expand_refs(target, depth + 1))
        return out

    def _valid_range_fields(self, fields: List[Dict]) -> List[Tuple[str, str, Any, Any]]:
        """Fields whose value carries a `valid_range`, as (outputName, schemaName, lo, hi).

        Mirrors where the interpreters actually check: the main field loop (which
        covers plain and computed fields) and `flagged` group members. It deliberately
        does NOT descend into `repeat`, `object`, TLV `cases` or `match` cases, because
        the interpreters do not check there either - a field inside a TLV case gets no
        quality flag even when it declares a range. Emitting one here would invent a
        divergence in the opposite direction. No schema in the corpus declares a range
        in those positions, so this costs nothing today; it is about which behaviour
        this generator is copying.
        """
        found: List[Tuple[str, str, Any, Any]] = []
        for field in self._expand_refs(fields):
            if not isinstance(field, dict):
                continue
            flagged = field.get('flagged')
            groups = flagged.get('groups') if isinstance(flagged, dict) else None
            if isinstance(groups, list):
                for group in groups:
                    if isinstance(group, dict) and isinstance(group.get('fields'), list):
                        found.extend(self._valid_range_fields(group['fields']))
            # A match case body is decoded by the interpreters' field-list decoder, whose
            # `_quality` belongs to the decode (wave 6b), so its ranges are checked too.
            match = field.get('match')
            if isinstance(match, dict):
                cases = match.get('cases')
                for body in (cases.values() if isinstance(cases, dict) else []):
                    if isinstance(body, list):
                        found.extend(self._valid_range_fields(body))
                if isinstance(match.get('default'), list):
                    found.extend(self._valid_range_fields(match['default']))
            valid_range = field.get('valid_range')
            name = field.get('name')
            if not name or not isinstance(valid_range, (list, tuple)) or len(valid_range) < 2:
                continue
            lo, hi = valid_range[0], valid_range[1]
            if not isinstance(lo, (int, float)) or not isinstance(hi, (int, float)):
                continue
            if isinstance(lo, bool) or isinstance(hi, bool):
                continue
            if field.get('name_from'):
                # The output key is decided at run time, so a static table cannot name
                # it. Nothing in the corpus combines the two; skipped loudly rather
                # than emitting a check against the wrong key.
                self.gaps.append(
                    f"valid_range on '{name}' with name_from is not generated")
                continue
            if name.startswith('_'):
                # Internal fields are stripped before reporting and get no flag.
                continue
            found.append((to_js_name(name), name, lo, hi))
        return found

    def _gen_decode_fn(self, fname: str, fields: List[Dict]) -> str:
        lines = [f'function {fname}(buf, endian) {{']
        lines.append(f'  var pos = 0, d = {{}}, vars = {{}}, w = [], wn = [];')
        lines.append('  _fld = null;')
        # Readings omitted as "no reading" (PS-427, PS-428), for `_quality` if produced.
        lines.append('  var aqr = {}, aqp = {};')
        # rv: a looked-up field's value before its lookup, which valid_range compares
        # (PS-475); rvs: fields whose guard reported `else`, which it does not. _leftDone:
        # a PS-302 warning already reported the bytes left over (PS-472).
        # rcs: the records the current repeat element yields (PS-541); null outside one.
        lines.append('  var rv = {}, rvs = {}, _leftDone = false, rcs = null;')
        # lv: a looked-up field's number before its lookup, which a reference binds under
        # a text or boolean label (PS-523); lbl: the value it reported, by the names it
        # binds, which a key template writes (PS-532) - undefined where it had no entry.
        lines.append('  var lv = {}, lbl = {};')
        lines.append(f'  endian = endian || "{self.endian}";')
        self.indent = 1
        for field in self._expand_refs(fields):
            lines.extend(self._gen_decode_field(field))
        # A leading underscore marks an internal field: a variable later fields can
        # reference, but not reported. `d` doubles as the working scope here - a `$ref`
        # resolves against it - so the internal keys are stripped at the end rather than
        # never written, which would break every reference to one. mclimate/vicki
        # reported four intermediates before this.
        lines.append("  for (var _k in d) { if (_k.charAt(0) === '_' && _k !== '_quality') delete d[_k]; }")
        # valid_range runs last, on the reported values, after the internal fields are
        # stripped - a `_`-prefixed field is not reported and gets no flag, matching the
        # interpreters. Each check is guarded on the key being present so a field in an
        # untaken conditional branch does not acquire a flag.
        ranges = self._valid_range_fields(fields)
        for gap in self.gaps:
            lines.append(f'  // TODO: {gap}')
        lines.append('  var q = {}, qn = 0;')
        for js_name, schema_name, lo, hi in ranges:
            key = json.dumps(schema_name)
            value = (f'(Object.prototype.hasOwnProperty.call(rv, {key}) ? rv[{key}] : '
                     f'd.{js_name})')
            lines.append(
                f'  if (Object.prototype.hasOwnProperty.call(d, {json.dumps(js_name)})) '
                f'{{ if (rvs[{key}]) q[{key}] = "good"; else checkRange(q, w, {key}, '
                f'{value}, {json.dumps(lo)}, {json.dumps(hi)}); qn++; }}'
            )
        # PS-427, PS-428: an omitted reading is recorded where `_quality` is produced: a
        # field that declares a range produces it, and then the others join it.
        lines.append('  for (var _a in aqr) { q[_a] = aqr[_a]; qn++; }')
        lines.append('  if (qn) for (var _p in aqp) { if (!(_p in q)) q[_p] = aqp[_p]; }')
        # PS-182: `_quality` appears only when a field actually carried a range.
        lines.append('  if (qn) d._quality = q;')
        # PS-472, PS-473: bytes after the last field are reported, not dropped.
        lines.append('  if (!_leftDone && pos < buf.length) w.push((buf.length - pos) + '
                     '" byte(s) after the last field left undecoded, from offset " + pos + '
                     '" (PS-472)");')
        lines.append('  return { data: d, pos: pos, warnings: w.concat(wn) };')
        lines.append('}')
        return '\n'.join(lines)

    def _gen_decode_field(self, field: Dict) -> List[str]:
        if isinstance(field, dict) and field.get('optional') is True:
            # PS-402, PS-403: decoded where its bytes remain, absent where none do, an
            # error where some do but too few. An absent field consumes nothing, so every
            # later field - optional too, by PS-404 - finds no bytes and is absent as well.
            from schema_interpreter import optional_field_size
            i = self._i()
            size = optional_field_size(field)
            inner = dict(field)
            del inner['optional']
            lines = [f'{i}  if (pos < buf.length) {{']
            if size:
                lines.append(f'{i}    if (buf.length - pos < {size}) throw new Error("Error decoding '
                             f'{field.get("name", "?")}: optional field takes {size} byte(s) but " + '
                             f'(buf.length - pos) + " remain at offset " + pos + " (PS-403)");')
            self.indent += 1
            lines.extend(self._gen_decode_field(inner))
            self.indent -= 1
            lines.append(f'{i}  }}')
            return lines
        lines = []
        i = self._i()
        # PS-422, PS-426: `encoding` only on uN and only a named code; no `match_value`.
        from schema_interpreter import encoding_errors
        problems = encoding_errors(field) if isinstance(field, dict) and 'type' in field else []
        if problems:
            raise ValueError(problems[0])
        if isinstance(field, dict) and field.get('type'):
            # PS-535: the field about to read, for a short read's error. A member of an
            # object or a repeat element sets its own, so the innermost field is named.
            from schema_interpreter import underflow_label
            label = underflow_label(field.get('name'), field.get('type'))
            lines.append(f'{i}  _fld = {json.dumps(label)};')

        # PS-541: each field list yields one record of the enclosing repeat element,
        # decoded into a fresh `d` and collected in `rcs`; the element reports them.
        if 'records' in field and not field.get('type'):
            lines.append(f'{i}  if (rcs === null) throw new Error("`records` belongs '
                         f'in a repeat\'s element, or a match case within it '
                         f'(PS-541)");')
            for body in field['records']:
                outer = f'_ro{self._next_uid()}'
                lines.append(f'{i}  var {outer} = d; d = {{}};')
                for member in body:
                    if isinstance(member, dict):
                        lines.extend(self._gen_decode_field(member))
                lines.append(f"{i}  for (var _k in d) {{ if (_k.charAt(0) === '_') "
                             f"delete d[_k]; }}")
                lines.append(f'{i}  rcs.push(d); d = {outer};')
            return lines

        # byte_group
        if 'byte_group' in field:
            bg = field['byte_group']
            bg_fields = bg if isinstance(bg, list) else bg.get('fields', bg)
            # PS-070 (CR-2026-131): the widest member's base, widened by any `size`. The
            # array form's `size` beside the group was ignored here, and a group with
            # no `size` read one byte.
            # PS-397 and PS-070: overlapping members, a member of another kind and a size
            # narrower than a member are schema errors, raised here as in the interpreter.
            from schema_interpreter import (byte_group_endian, byte_group_member_range,
                                            byte_group_width)
            bg_size = byte_group_width(field)
            byte_group_endian(field, None)        # PS-364: a member's endian is refused
            group_endian = f'"{bg["endian"]}"' if isinstance(bg, dict) and bg.get('endian') else 'endian'
            lines.append(f'{i}  // byte_group')
            lines.append(f'{i}  var bgStart = pos;')
            # PS-535: the group is read as one value (CR-2026-131), so the failing read is
            # the group's, in the peek form, and it is identified by its members.
            from schema_interpreter import byte_group_label
            members = bg_fields if isinstance(bg_fields, list) else []
            glabel = json.dumps(byte_group_label(members))
            lines.append(f'{i}  if (buf.length - pos < {int(bg_size)}) {{ _fld = {glabel}; '
                         f'needPeek(buf, pos, {int(bg_size)}); }}')
            wide_group = int(bg_size) >= 7
            if wide_group:
                # Past 2^53 readU gives a decimal string; take each range from the bytes.
                lines.append(f'{i}  var bgVal = orderedBytes(buf, pos, {bg_size}, {group_endian});')
            else:
                lines.append(f'{i}  var bgVal = readU(buf, pos, {bg_size}, {group_endian});')
            for bf in (bg_fields if isinstance(bg_fields, list) else []):
                bname = bf.get('name', '_')
                btype = bf.get('type', 'u8')
                if btype == 'bool':
                    # PS-364: a bool member reads its `bit` of the group's value. Members
                    # other than bit ranges were skipped here with no error, so a group's
                    # bool flags were missing from the generated codec's output.
                    bjs = to_js_name(bname)
                    lines.append(f'{i}  var {bjs}_out = (Math.floor(bgVal / {2 ** int(bf.get("bit", 0))}) % 2) === 1;')
                    lines.append(f'{i}  vars.{bjs} = {bjs}_out;')
                    if not bname.startswith('_'):
                        guards, target = name_from_to_js(bf, bjs)
                        for guard in guards:
                            lines.append(f'{i}  {guard}')
                        lines.append(f'{i}  {target} = {bjs}_out;')
                    continue
                # PS-070: a bit range, or a whole integer as its full range (`u16` is
                # `u16[0:15]`); either reads from the group's start.
                _, lo, hi, signed = byte_group_member_range(bf)
                width = hi - lo + 1
                bjs = to_js_name(bname)
                if wide_group:
                    lines.append(f'{i}  var {bjs} = bitRange(bgVal, {lo}, {width}, '
                                 f'{json.dumps(signed)});')
                else:
                    lines.append(f'{i}  var {bjs} = Math.floor(bgVal / {2 ** lo}) % {2 ** width};')
                if signed and not wide_group:
                    lines.append(f'{i}  if ({bjs} >= {2 ** (width - 1)}) {bjs} -= {2 ** width};')
                # `vars` records the post-modifier value - see _apply_modifiers_expr.
                bval = self._apply_modifiers_expr(bjs, bf)
                lines.append(f'{i}  var {bjs}_out = {bval};')
                lines.extend(f'{i}  {s}' for s in self._bind_js(bf, bjs, f'{bjs}_out', False))
                if not bname.startswith('_'):
                    guards, target = name_from_to_js(bf, bjs)
                    for guard in guards:
                        lines.append(f'{i}  {guard}')
                    lines.append(f'{i}  {target} = {bjs}_out;')
            lines.append(f'{i}  pos = bgStart + {bg_size};')
            return lines

        # flagged
        if 'flagged' in field:
            fg = field['flagged']
            flag_field = fg['field']
            # The mask is a reference like any other, so it reads the PS-523 binding in
            # `vars` (refValue): the number under a text or boolean label, a numeric label
            # itself, an enum's code. Written `$mask` it read `vars._mask`, which is never
            # bound, so every group was skipped with no error.
            mask_js = to_js_name(str(flag_field).lstrip('$'))
            lines.append(f'{i}  // flagged on {flag_field}')
            lines.append(f'{i}  if (vars.{mask_js} === undefined) throw new Error('
                         f'{json.dumps("Flagged field reference not found: " + str(flag_field))});')
            for group in fg.get('groups', []):
                bit = group['bit']
                lines.append(f'{i}  if (vars.{mask_js} & (1 << {bit})) {{')
                self.indent += 1
                for gf in group.get('fields', []):
                    lines.extend(self._gen_decode_field(gf))
                self.indent -= 1
                lines.append(f'{i}  }}')
            return lines

        # tlv
        if 'tlv' in field:
            reserve = field['tlv'].get('reserve') if isinstance(field['tlv'], dict) else None
            if not reserve:
                return self._gen_decode_tlv(field['tlv'])
            # PS-471: the loop reads a buffer that stops `reserve` bytes early, so no
            # entry reaches the trailer, and the fields after the tlv read it.
            uid = self._next_uid()
            lines.append(f'{i}  if (buf.length - pos < {int(reserve)}) throw new Error("tlv reserves '
                         f'{int(reserve)} byte(s) but " + (buf.length - pos) + " remain at offset " '
                         f'+ pos + " (PS-471)");')
            lines.append(f'{i}  var _tlvBuf{uid} = buf; buf = buf.slice(0, buf.length - {int(reserve)});')
            lines.extend(self._gen_decode_tlv(field['tlv']))
            lines.append(f'{i}  buf = _tlvBuf{uid};')
            return lines

        # match
        if 'match' in field:
            return self._gen_decode_match(field['match'])

        # repeat - a sequence of records, bounded by a count, a byte length, or the
        # end of the payload.
        #
        # Unimplemented until now despite the module docstring claiming otherwise, so
        # `type: repeat` fell through to the integer path: it read a single byte as a
        # number, emitted no array, and left the read position short. repeat-byte-length
        # reported a `tail` of 10 rather than 255 because of it.
        #
        # Members decode into the flat `d`, then each iteration lifts them into a record
        # and deletes them, the same approach the nested `object` support uses - so their
        # modifiers, lookups and bitfields keep working through the existing generators.
        from schema_interpreter import index_base, records_reachable, repeat_members
        # PS-528: `element` decodes as a one-field record under its name (or `element`),
        # and the repeat reports the record's value - null where it was omitted.
        element_members, element_name = (repeat_members(field) if field.get('type') == 'repeat'
                                         else ([], None))
        if field.get('type') == 'repeat' and element_members:
            key = to_js_name(field.get('name', '_items'))
            # The list's own variable is numbered, not named after the field: a member
            # sharing the repeat's name (an `altitude` list of records each holding an
            # `altitude`) declared the same `var`, and the list became that member's
            # number - "altitude.push is not a function".
            arr = f'_rep{self._next_uid()}'
            # Every member, constructs included: a bare `match` or `tlv` inside an
            # element has no name, and filtering on one dropped it from the codec.
            members = [m for m in element_members if isinstance(m, dict)]
            lines.append(f'{i}  var {arr} = [];')
            # Iterations, not reported elements: present_if drops an element and it still
            # advances the index and counts toward `count` and `max` (PS-386).
            lines.append(f'{i}  var {arr}_i = 0;')
            # PS-350, PS-351, PS-383: the region ends `reserve` bytes early. A trailer is
            # decoded from those bytes first, into the enclosing scope, and the elements
            # then read a buffer that stops at the region's end.
            reserve = field.get('reserve')
            if reserve:
                lines.append(f'{i}  var {arr}_end = buf.length - {int(reserve)};')
                lines.append(f'{i}  if ({arr}_end < pos) throw new Error("repeat \'" + '
                             f'{json.dumps(str(field.get("name", "?")))} + "\' reserves {int(reserve)} '
                             f'byte(s) but " + (buf.length - pos) + " remain at offset " + pos + " (PS-351)");')
                if field.get('trailer'):
                    lines.append(f'{i}  var {arr}_at = pos; pos = {arr}_end;')
                    for trailer_field in field['trailer']:
                        lines.extend(self._gen_decode_field(trailer_field))
                    lines.append(f'{i}  pos = {arr}_at;')
                lines.append(f'{i}  var {arr}_buf = buf; buf = buf.slice(0, {arr}_end);')
            # PS-378 to PS-380: a carried field starts from its `carry` value each time the
            # repeat begins, and its own name resolves to its previous value.
            carried = [(m['name'], m['carry']) for m in members if 'carry' in m]
            if carried:
                initial = ', '.join(
                    f'{json.dumps(to_js_name(name))}: '
                    + (ref_to_js(value) if isinstance(value, str) else json.dumps(value))
                    for name, value in carried)
                lines.append(f'{i}  var {arr}_carry = {{{initial}}};')
            lines.append(f'{i}  var {arr}_start = pos;')

            count = field.get('count')
            byte_length = field.get('byte_length')
            until = field.get('until')
            if count is not None:
                bound = ref_to_js(count) if isinstance(count, str) else str(int(count))
                lines.append(f'{i}  var {arr}_n = {bound};')
                condition = f'{arr}_i < {arr}_n'
            elif byte_length is not None:
                bound = (ref_to_js(byte_length) if isinstance(byte_length, str)
                         else str(int(byte_length)))
                lines.append(f'{i}  var {arr}_len = {bound};')
                condition = f'pos < {arr}_start + {arr}_len'
            elif until == 'end':
                condition = 'pos < buf.length'
            else:
                # No bound declared. Emit nothing rather than guess, and say so in the
                # generated source so it is visible where it matters.
                lines.append(f'{i}  // repeat {field.get("name")}: no count, '
                             f'byte_length or until - nothing to iterate')
                lines.append(f'{i}  d.{key} = {arr};')
                return lines

            # `max` is the iteration ceiling the four interpreters enforce: they clamp a
            # `count` to it and guard the byte_length and until-end loops with it, so a
            # payload holding more records than it allows yields exactly that many. This
            # generator had only the no-progress guard below, which stops a zero-width
            # member but is not a ceiling, so it ran to the end of the payload and
            # produced more records than any interpreter would (CR-2026-021). The default
            # is the interpreters' 1000.
            ceiling = field.get('max')
            ceiling = int(ceiling) if isinstance(ceiling, int) and ceiling > 0 else 1000
            lines.append(f'{i}  var {arr}_max = {ceiling};')
            # PS-396: more elements than `max` is an error naming the repeat, the limit
            # and the payload left unparsed - not a quiet truncation, after which the next
            # field read from inside an element the ceiling had discarded.
            rname = json.dumps(str(field.get('name', '?')))
            limit_error = (f'throw new Error("repeat \'" + {rname} + "\' exceeds its max of " + '
                           f'{arr}_max + " element(s) (" + MODE + "); " + (END - pos) + '
                           f'" byte(s) at offset " + pos + " left unparsed (PS-396)");')
            if count is not None:
                mode = json.dumps('count ') + f' + {arr}_n'
                lines.append(f'{i}  if ({arr}_n > {arr}_max) {{ '
                             + limit_error.replace('MODE', mode).replace('END', 'buf.length')
                             + ' }')
            condition = f'({condition}) && {arr}_i < {arr}_max'

            # A member set that consumes nothing would spin forever; bound the loop by
            # the payload as well and stop if the position does not advance.
            lines.append(f'{i}  while ({condition}) {{')
            lines.append(f'{i}    var {arr}_before = pos;')
            # Each record decodes into a fresh `d` (see `object`): lifting members out of
            # the enclosing one deleted any enclosing field sharing a member's name.
            lines.append(f'{i}    var {arr}_outer = d; d = {{}};')
            # PS-368: an element's names do not outlive it; `vars` is restored after it.
            lines.append(f'{i}    var {arr}_vars = Object.assign({{}}, vars), '
                         f'{arr}_lbl = Object.assign({{}}, lbl);')
            # PS-541: an element reaching `records` collects them in `rcs`, and reports
            # them in its place. Emitted only where the element can reach one.
            yields_records = records_reachable(members)
            if yields_records:
                lines.append(f'{i}    var {arr}_rcs = rcs; rcs = [];')
            if field.get('index'):
                # PS-366, PS-530: the position counted from index_base.
                base = index_base(field)    # the field's 'index_base' (PS-530)
                lines.append(f'{i}    vars.{to_js_name(field["index"])} = {arr}_i'
                             + (f' + {base}' if base else '') + ';')
            for name, _ in carried:
                lines.append(f'{i}    vars.{to_js_name(name)} = {arr}_carry.{to_js_name(name)};')
            ragged = None
            if until == 'end' and count is None and byte_length is None:
                # PS-343 to PS-344a: a tail too short for a whole element is an error
                # naming the repeat as a ragged tail. The readers here return 0 past the
                # end of the buffer, so without this a short tail decoded as a final
                # element of zeros and reported success.
                from schema_interpreter import fixed_element_size
                size = fixed_element_size(members)
                ragged = ('throw new Error("repeat \'" + ' + rname + ' + "\' ends in a ragged tail: " + '
                          f'(buf.length - {arr}_before) + " byte(s) at offset " + {arr}_before'
                          + (f' + ", fewer than the {size} an element takes (PS-343)");' if size
                             else ' + " (PS-343)");'))
                if size:
                    lines.append(f'{i}    if (buf.length - pos < {size}) {{ {ragged} }}')
            self.indent += 1
            for member in members:
                lines.extend(self._gen_decode_field(member))
            self.indent -= 1
            lines.append(f'{i}    var {arr}_rec = d; d = {arr}_outer;')
            for member in members:
                if str(member.get('name', '')).startswith('_'):
                    # Internal members stay out of the record.
                    lines.append(f'{i}    delete {arr}_rec.{to_js_name(member["name"])};')
            if yields_records:
                lines.append(f'{i}    var {arr}_rs = rcs; rcs = {arr}_rcs;')
            if ragged:
                lines.append(f'{i}    if (pos > buf.length) {{ {ragged} }}')
            for name, _ in carried:
                lines.append(f'{i}    {arr}_carry.{to_js_name(name)} = vars.{to_js_name(name)};')
            keep = condition_to_js(field['present_if']) if field.get('present_if') else 'true'
            lines.append(f'{i}    var {arr}_keep = {keep};')
            lines.append(f'{i}    vars = {arr}_vars; lbl = {arr}_lbl; {arr}_i++;')
            if yields_records:
                # PS-541: each record, after the element's other reported fields.
                r = f'{arr}_r'
                lines.append(f'{i}    if ({arr}_keep && {arr}_rs.length) {{ '
                             f'for (var {r} = 0; {r} < {arr}_rs.length; {r}++) '
                             f'{arr}.push(Object.assign({{}}, {arr}_rec, {arr}_rs[{r}])); }}')
                lines.append(f'{i}    else if ({arr}_keep) {arr}.push({arr}_rec);')
            elif element_name is None:
                lines.append(f'{i}    if ({arr}_keep) {arr}.push({arr}_rec);')
            else:
                # PS-528: the record's one value, or null where it was omitted, so the
                # values after it keep their positions.
                lines.append(f'{i}    if ({arr}_keep) {{ var {arr}_v = null;')
                lines.append(f'{i}      for (var {arr}_k in {arr}_rec) {{ if ({arr}_k !== "_quality" '
                             f'&& {arr}_k !== "_warnings") {{ {arr}_v = {arr}_rec[{arr}_k]; break; }} }}')
                lines.append(f'{i}      if ({arr}_v === undefined || (typeof {arr}_v === "number" '
                             f'&& !isFinite({arr}_v))) {arr}_v = null;')
                lines.append(f'{i}      {arr}.push({arr}_v); }}')
            # The zero-progress guard is for the loops bounded by bytes. A counted one
            # ends by its count, and elements reading no bytes - computed fields, bit
            # ranges with no consume - are still `count` elements; it stopped at one.
            if count is None:
                lines.append(f'{i}    if (pos <= {arr}_before) break;')
            lines.append(f'{i}    if (pos >= buf.length && !({condition})) break;')
            lines.append(f'{i}  }}')
            # PS-088: a byte_length span must be divided exactly by the members. There was
            # no check, so this codec accepted both ways it is not - an iteration starting
            # inside the span and finishing past it, and the ceiling stopping the loop
            # early - and left `pos` somewhere other than the span's end. Every field after
            # the repeat then came from the wrong offset with nothing reported: a 2-byte
            # member over a 5-byte span produced a third record holding the following
            # field's byte, and the following field read past the payload (CR-2026-022).
            if until == 'end' and count is None and byte_length is None:
                lines.append(f'{i}  if ({arr}_i >= {arr}_max && pos < buf.length) {{ '
                             + limit_error.replace('MODE', '"until: end"').replace('END', 'buf.length')
                             + ' }')
            if byte_length is not None:
                end = f'{arr}_start + {arr}_len'
                lines.append(f'{i}  if (pos !== {end}) {{')
                lines.append(f'{i}    if ({arr}_i >= {arr}_max && pos < {end}) {{')
                lines.append(f'{i}      '
                             + limit_error.replace('MODE', '"byte_length " + ' + f'{arr}_len')
                             .replace('END', f'({end})'))
                lines.append(f'{i}    }}')
                # Parenthesised: `"..." + a + b` concatenates left to right, so the sum
                # rendered as "05" rather than 5.
                lines.append(f'{i}    throw new Error("repeat byte_length mismatch:'
                             f' expected end at " + ({end}) + ", got " + pos);')
                lines.append(f'{i}  }}')
            # `min` is the fewest iterations that count as a valid decode, and fewer is an
            # error in all four interpreters. This generator returned the short array
            # instead, so a payload the interpreters refused decoded here as a schema that
            # simply found less (CR-2026-021). Emitted only where declared: the default is
            # zero, which nothing can fall below.
            floor = field.get('min')
            if isinstance(floor, int) and not isinstance(floor, bool) and floor > 0:
                lines.append(f'{i}  if ({arr}.length < {floor}) {{')
                lines.append(f'{i}    throw new Error("repeat produced " + {arr}.length'
                             f' + " elements, but minimum is {floor}");')
                lines.append(f'{i}  }}')
            if reserve:
                lines.append(f'{i}  buf = {arr}_buf;')
                if field.get('trailer'):
                    lines.append(f'{i}  pos = buf.length;')   # the trailer was read already
            if field.get('count_as'):
                lines.append(f'{i}  vars.{to_js_name(field["count_as"])} = {arr}.length;')  # PS-367
            # PS-546: a sibling identifying these values reported as many as this repeat.
            from schema_interpreter import sibling_references
            for sib in dict.fromkeys(n for _, n, _ in sibling_references(field)):
                sv = f'vars.{to_js_name(sib)}'
                lines.append(f'{i}  if (!Array.isArray({sv}) || {sv}.length !== {arr}.length) '
                             f'throw new Error("Repeat \'{field.get("name")}\' has " + {arr}.length'
                             f' + " values and \'{sib}\', which identifies them, has " + '
                             f'(Array.isArray({sv}) ? {sv}.length : "none") + " (PS-546)");')
            lines.append(f'{i}  vars.{key} = {arr};')
            if not str(field.get('name', '')).startswith('_'):
                lines.append(f'{i}  d.{key} = {arr};')
            return lines

        # object - a nested group reported under its own key.
        #
        # Unhandled until now, so a schema using one produced no key at all: the
        # milesight ct30x current-alarm channels reported none of their four members,
        # which failed the generated-codec gate and held three otherwise
        # vendor-correct schemas below Silver.
        #
        # The members decode into the same flat `d` and are then lifted into a
        # sub-object, so nested modifiers, lookups and bitfields all keep working
        # without duplicating their generators.
        # An empty `fields: []` is an object too, reported as {} (quandify's response 0);
        # it fell through to the type switch as an unknown type and no codec was made.
        if field.get('type') == 'object' and isinstance(field.get('fields'), list):
            obj_name = to_js_name(field.get('name', '_object'))
            lines.append(f'{i}  // object {field.get("name")}')
            # The members decode into a fresh `d`, which then becomes the object. They
            # used to decode into the enclosing `d` and be lifted out and deleted, which
            # deleted an enclosing field of the same name: a top-level `value` vanished
            # beside an object with a `value` member.
            outer = f'{obj_name}_outer{self._next_uid()}'   # id() varied per run
            lines.append(f'{i}  var {outer} = d; d = {{}};')
            # The members are a field list (PS-347, PS-441), so a nameless construct -
            # a `match` or a group of bit ranges - is a member like any other. Members
            # without a name were skipped, so neither was decoded: a match's fields were
            # missing and a group's byte was never consumed, shifting every later field.
            for member in field['fields']:
                if isinstance(member, dict):
                    lines.extend(self._gen_decode_field(member))
            # An internal member is bound in `vars` and not reported, as at the top level.
            lines.append(f"{i}  for (var _k in d) {{ if (_k.charAt(0) === '_') delete d[_k]; }}")
            # A `name_from` names the object as it names any field (PS-265); the object
            # was always reported under its declared `name`, so rakwireless's
            # `accelerometer_${ch}` came out as `accelerometer`. The template resolves
            # against `vars` once `d` is the enclosing object again.
            guards, target = name_from_to_js(field, obj_name)
            if guards:
                lines.append(f'{i}  var {obj_name}_obj = d; d = {outer};')
                for guard in guards:
                    lines.append(f'{i}  {guard}')
                lines.append(f'{i}  {target} = {obj_name}_obj;')
            else:
                lines.append(f'{i}  {outer}.{obj_name} = d; d = {outer};')
            return lines

        # type: number or integer (computed, decode-only). `integer` reports its value
        # as an integer and fails on a fractional one (PS-283, PS-388).
        if field.get('type') in ('number', 'integer'):
            name = to_js_name(field['name'])
            as_int = field.get('type') == 'integer'

            def integral(expr):
                return f'asInteger({expr}, {json.dumps(field["name"])})' if as_int else expr
            
            # Deprecated: formula field
            if 'formula' in field:
                js_formula = formula_to_js(field['formula'])
                lines.append(f'{i}  d.{name} = {js_formula};')
                lines.append(f'{i}  vars.{name} = {js_formula};')
                return lines
            
            # ref or compute (PS-443): the source - the `ref` value with its `polynomial`,
            # or the `compute` result - then the modifiers, the stages and the lookup, as
            # for a field read from the payload. `compute` used to drop the modifiers,
            # and both dropped the lookup. The guard wraps the whole chain, so a failed
            # one reports its `else` as declared (PS-444).
            if 'ref' in field or 'compute' in field:
                if 'ref' in field:
                    value_expr = ref_to_js(field['ref'])
                    if 'polynomial' in field:
                        value_expr = polynomial_to_js(field['polynomial'], value_expr)
                else:
                    value_expr = compute_to_js(field['compute'])
                value_expr = self._apply_modifiers_expr(value_expr, field)
                if 'guard' in field:
                    hook = ''
                    if field.get('valid_range'):
                        # A failed guard's `else` ends the sequence (PS-443): not compared.
                        key = json.dumps(field.get('name', ''))
                        hook = f'rvs[{key}] = 1'
                        value_expr = f'(delete rvs[{key}], {value_expr})'
                    value_expr = guard_to_js(field['guard'], value_expr, hook)
                value_expr = integral(value_expr)
                lines.extend(self._computed_tail(i, field, name, value_expr))
                return lines

            # Literal value. JSON, not Python's repr: `value: true` emitted `True`,
            # which is not JavaScript.
            if 'value' in field:
                literal = json.dumps(field['value'])
                lines.append(f'{i}  d.{name} = {literal};')
                lines.append(f'{i}  vars.{name} = {literal};')
                return lines
            
            # Default to 0 if no source specified. `vars` is mirrored below for every
            # computed field: this path writes only `d`, so once a `$ref` resolves
            # against `vars` an internal computed field would be invisible to it -
            # mclimate/vicki's motorPosition is computed from two such helpers.
            lines.append(f'{i}  d.{name} = 0;')
            lines.append(f'{i}  vars.{name} = 0;')
            return lines

        # regular field
        name = field.get('name', '_unknown')
        ftype = field.get('type')
        js_name = to_js_name(name)
        if 'object' in field and not ftype:
            raise ValueError("the `object:` key is withdrawn; write `type: object` with "
                             f"`name: {field['object']}` and `fields` (PS-466)")

        # A string literal (spec "Literal Types"): a constant, read from no bytes.
        # `string` fell through to the integer path, which emitted a TODO and nothing,
        # so the key was silently missing from every generated codec.
        if ftype == 'string' and 'value' not in field:
            raise ValueError(f"Field '{name}': type string declares no value; a string read "
                             f"from the payload is type ascii (PS-361)")
        # A boolean literal likewise (PS-357, PS-519); its value is checked at load.
        if (ftype == 'string' and 'value' in field and not field.get('length')) or (
                ftype == 'boolean' and 'value' in field):
            literal = json.dumps(field['value'])
            lines.append(f'{i}  vars.{js_name} = {literal};')
            if not name.startswith('_'):
                lines.append(f'{i}  d.{js_name} = {literal};')
            return lines

        # bitfield_string
        if ftype == 'bitfield_string':
            return self._gen_decode_bitfield_string(field)

        # bit-slice integer (e.g., u8[0:6], u8[7:7])
        bit_slice = parse_bit_slice_type(ftype)
        if bit_slice is not None:
            base_type, bit_start, bit_width = bit_slice
            base_size = type_size(base_type)
            if base_size is None:
                lines.append(f'{i}  // TODO: unsupported base type for {ftype}')
                return lines

            eo = field_endian_override(base_type, field)
            endian_arg = f'"{eo}"' if eo else 'endian'
            mask = (1 << bit_width) - 1
            # PS-060: a bitfield extraction does not advance the read position unless
            # `consume` says so, and it advances by `consume` bytes. This defaulted to
            # 1 and advanced by the base width, so several bitfields sharing one byte
            # each moved on a byte and read different bytes entirely - the ct30x alarm
            # object reported bits 0 and 1 of the right byte and then two bits of
            # whatever followed. The interpreter has always defaulted to no advance.
            consume = field.get('consume', 0)

            # The base is assembled in the field's effective byte order (PS-059). The
            # extraction is arithmetic rather than `>>`, which converts to a signed 32-bit
            # integer and corrupts a u32 base with its top bit set. An `sN` base is
            # sign-extended from the range's width (PS-352, PS-353).
            signed = base_type.startswith('s')
            lines.append(f'{i}  needPeek(buf, pos, {base_size});')         # PS-535
            if base_size >= 7:
                # readU gives a seven- or eight-byte base as a decimal string past 2^53,
                # and the division below then read every range of it as 0.
                lines.append(f'{i}  var {js_name}_raw = orderedBytes(buf, pos, {base_size}, {endian_arg});')
                lines.append(f'{i}  var {js_name} = bitRange({js_name}_raw, {bit_start}, '
                             f'{bit_width}, {json.dumps(signed)});')
            else:
                lines.append(f'{i}  var {js_name}_raw = readU(buf, pos, {base_size}, {endian_arg});')
                lines.append(f'{i}  var {js_name} = Math.floor({js_name}_raw / {2 ** bit_start}) % {2 ** bit_width};')
                if signed:
                    lines.append(f'{i}  if ({js_name} >= {2 ** (bit_width - 1)}) {js_name} -= {2 ** bit_width};')
            if consume:
                lines.append(f'{i}  need(buf, pos, {int(consume)});')            # PS-535
                lines.append(f'{i}  pos += {int(consume)};')

            val_expr = self._apply_modifiers_expr(js_name, field)
            lines.append(f'{i}  var {js_name}_out = {val_expr};')
            self._emit_tail(lines, i, field, js_name, name, bits_var=js_name)

            return lines

        # enum
        if ftype == 'enum':
            base = field.get('base', 'u8')
            sz = type_size(base) or 1
            signed = is_signed(base)
            read_fn = 'readS' if signed else 'readU'
            lines.append(f'{i}  var {js_name}_raw = {read_fn}(buf, pos, {sz}, endian);')
            lines.append(f'{i}  pos += {sz};')
            vals = field.get('values', {})
            # The description form reports its `name` (PS-394); the mapping was emitted
            # whole and reported as the value.
            if isinstance(vals, list):
                vals = dict(enumerate(vals))
            val_json = json.dumps({str(k): enum_label(v) for k, v in vals.items()})
            # PS-068 (CR-2026-132): with no `default` an unmatched code is omitted, as a
            # lookup miss is (PS-269); `undefined` is dropped from the output, and the
            # reference still binds the code below. This reported the code itself.
            default_js = json.dumps(field['default']) if 'default' in field else 'undefined'
            lines.append(f'{i}  var {js_name}_map = {val_json};')
            lines.append(f'{i}  var {js_name}_out = {js_name}_map[{js_name}_raw] !== undefined ? {js_name}_map[{js_name}_raw] : {default_js};')
            # PS-523 (CR-2026-132): an enum is a lookup by another spelling, so a reference
            # binds its code and a template reads its name.
            lines.extend(f'{i}  {s}' for s in self._bind_js(
                field, js_name, f'{js_name}_out', number=f'{js_name}_raw'))
            guards, target = name_from_to_js(field, js_name)
            for guard in guards:
                lines.append(f'{i}  {guard}')
            lines.append(f'{i}  {target} = {js_name}_out;')
            return lines

        # bool (PS-065, PS-066): one bit of the current byte as a JSON boolean, with no
        # advance unless `consume` says so. This emitted a TODO and no value, so a
        # schema using the specified spelling had no generated path, and conversions
        # fell back to a bit range with a {0: false, 1: true} lookup - outside what
        # PS-106 lists (numbers or strings), and Go dropped it silently.
        if ftype == 'bool':
            bit = int(field.get('bit', 0))
            consume = field.get('consume', 0)
            lines.append(f'{i}  needPeek(buf, pos, 1);')
            lines.append(f'{i}  var {js_name}_out = ((buf[pos] >> {bit}) & 1) === 1;')
            if consume:
                lines.append(f'{i}  need(buf, pos, {int(consume)});')            # PS-535
                lines.append(f'{i}  pos += {int(consume)};')
            if field.get('var'):
                lines.append(f'{i}  vars.{to_js_name(field["var"])} = {js_name}_out;')
            lines.append(f'{i}  vars.{js_name} = {js_name}_out;')
            if not name.startswith('_'):
                guards, target = name_from_to_js(field, js_name)
                for guard in guards:
                    lines.append(f'{i}  {guard}')
                lines.append(f'{i}  {target} = {js_name}_out;')
            return lines

        # skip
        if ftype == 'skip':
            # A skip past the end is a short read, as in every interpreter; the codec
            # advanced past the payload and reported success.
            lines.append(f'{i}  var _skipN = {length_to_js(field)};')
            lines.append(f'{i}  need(buf, pos, _skipN);')
            lines.append(f'{i}  pos += _skipN;')
            return lines

        # base64 (clause 2 string types): this had no case and fell through to the
        # integer path, which then had no size for it.
        if ftype == 'base64':
            lines.append(f'{i}  var {js_name}_n = {length_to_js(field)};')
            lines.append(f'{i}  need(buf, pos, {js_name}_n);')
            lines.append(f'{i}  var {js_name} = toBase64(buf, pos, {js_name}_n);')
            lines.append(f'{i}  pos += {js_name}_n;')
            if not name.startswith('_'):
                guards, target = name_from_to_js(field, js_name)
                for guard in guards:
                    lines.append(f'{i}  {guard}')
                lines.append(f'{i}  {target} = {js_name};')
            lines.append(f'{i}  vars.{js_name} = {js_name};')
            return lines

        # string types
        if ftype in ('ascii', 'hex', 'bytes'):
            # `length: remaining` consumes to the end of the payload (PS-014); a name is a
            # preceding field (PS-464).
            lines.append(f'{i}  var {js_name} = "";')
            lines.append(f'{i}  var {js_name}_n = {length_to_js(field)};')
            # A declared length the payload cannot supply is an error, as in every
            # interpreter; the loops below stopped at the end and reported what was left.
            lines.append(f'{i}  need(buf, pos, {js_name}_n);')
            if ftype == 'ascii':
                lines.append(f'{i}  for (var _si = 0; _si < {js_name}_n && pos < buf.length; _si++)'
                             f' {{ {js_name} += String.fromCharCode(buf[pos++]); }}')
                # Trailing NUL padding is not part of the text, as in the reference
                # (`rstrip` of NUL); the codec used to report "HC" followed by two NULs.
                # PS-538: each byte is its ISO 8859-1 character, with PS-073's warning
                # for one above 0x7F. Collected apart and reported after the decode's
                # own warnings, where the reference reports a nested one.
                from schema_interpreter import ASCII_HIGH_BYTE_WARNING
                warning = json.dumps(ASCII_HIGH_BYTE_WARNING.format(field.get('name')))
                lines.append(f'{i}  if (/[\\u0080-\\u00ff]/.test({js_name})) wn.push({warning});')
                lines.append(f'{i}  {js_name} = {js_name}.replace(/\\u0000+$/, "");')
            elif ftype == 'bytes' and field.get('format', 'hex') in ('base64', 'array'):
                # PS-079: the declared format. Ignored before, so every bytes field was
                # rendered as lowercase hex whatever the schema asked for.
                if 'separator' in field:
                    raise ValueError(f"Field '{name}': `separator` applies only to the hex "
                                     f"formats (PS-391)")
                if field['format'] == 'base64':
                    lines.append(f'{i}  {js_name} = toBase64(buf, pos, Math.min({js_name}_n, buf.length - pos));')
                    lines.append(f'{i}  pos += Math.min({js_name}_n, buf.length - pos);')
                else:
                    lines.append(f'{i}  {js_name} = [];')
                    lines.append(f'{i}  for (var _si = 0; _si < {js_name}_n && pos < buf.length; _si++)'
                                 f' {{ {js_name}.push(buf[pos++]); }}')
            else:
                # PS-281: `bytes` and `hex` both report a lowercase hexadecimal string,
                # unless a bytes field declares `hex:upper`, and a `separator` goes
                # between the bytes (PS-079, PS-391).
                fmt = field.get('format', 'hex') if ftype == 'bytes' else 'hex'
                if fmt not in ('hex', 'hex:upper'):
                    raise ValueError(f"Field '{name}': bytes format {fmt!r} is not one of "
                                     f"hex, hex:upper, base64, array (PS-079)")
                sep = json.dumps(str(field.get('separator', ''))) if ftype == 'bytes' else '""'
                upper = '.toUpperCase()' if fmt == 'hex:upper' else ''
                lines.append(f'{i}  for (var _si = 0; _si < {js_name}_n && pos < buf.length; _si++)'
                             f' {{ {js_name} += (_si ? {sep} : "") + ("0" + buf[pos++].toString(16)).slice(-2){upper}; }}')
            if not name.startswith('_'):
                guards, target = name_from_to_js(field, js_name)
                for guard in guards:
                    lines.append(f'{i}  {guard}')
                lines.append(f'{i}  {target} = {js_name};')
            lines.append(f'{i}  vars.{js_name} = {js_name};')
            return lines

        # float
        if is_float(ftype):
            sz = type_size(ftype) or 4
            eo = field_endian_override(ftype, field)
            endian_arg = f'"{eo}"' if eo else 'endian'
            if ftype == 'bf16':
                lines.append(f'{i}  var {js_name} = readBf16(buf, pos, {endian_arg});')
            elif sz == 2:
                lines.append(f'{i}  var {js_name} = readF16(buf, pos, {endian_arg});')
            elif sz == 4:
                lines.append(f'{i}  var {js_name} = readF32(buf, pos, {endian_arg});')
            else:
                lines.append(f'{i}  var {js_name} = readF64(buf, pos, {endian_arg});')
            lines.append(f'{i}  pos += {sz};')
            val_expr = self._apply_modifiers_expr(js_name, field)
            lines.append(f'{i}  var {js_name}_out = {val_expr};')
            lines.extend(f'{i}  {s}' for s in self._bind_js(field, js_name, f'{js_name}_out', False))
            if not name.startswith('_'):
                guards, target = name_from_to_js(field, js_name)
                for guard in guards:
                    lines.append(f'{i}  {guard}')
                lines.append(f'{i}  {target} = {js_name}_out;')
            return lines

        # The MCCI minifloats (CR-2026-063): a word in the field's effective byte order.
        if ftype in ('uflt16', 'sflt16', 'sflt24'):
            size = 3 if ftype == 'sflt24' else 2
            eo = field_endian_override(ftype, field)
            endian_arg = f'"{eo}"' if eo else 'endian'
            lines.append(f'{i}  var {js_name} = decodeMinifloat("{ftype}", readU(buf, pos, {size}, {endian_arg}));')
            lines.append(f'{i}  pos += {size};')
            val_expr = self._apply_modifiers_expr(js_name, field)
            lines.append(f'{i}  var {js_name}_out = {js_name} === undefined ? undefined : {val_expr};')
            lines.extend(f'{i}  {s}' for s in self._bind_js(field, js_name, f'{js_name}_out', False))
            if not name.startswith('_'):
                guards, target = name_from_to_js(field, js_name)
                for guard in guards:
                    lines.append(f'{i}  {guard}')
                lines.append(f'{i}  {target} = {js_name}_out;')
            return lines

        # Nibble-decimal (PS-329, PS-330): upper nibble whole, lower nibble tenths, the
        # upper nibble a 4-bit two's-complement value for sdec.
        if ftype in ('udec', 'sdec'):
            lines.append(f'{i}  need(buf, pos, 1);')
            if ftype == 'sdec':
                lines.append(f'{i}  var {js_name}_w = buf[pos] >> 4; if ({js_name}_w >= 8) {js_name}_w -= 16;')
            else:
                lines.append(f'{i}  var {js_name}_w = buf[pos] >> 4;')
            lines.append(f'{i}  var {js_name} = {js_name}_w + (buf[pos] & 0x0F) * 0.1;')
            lines.append(f'{i}  pos += 1;')
            val_expr = self._apply_modifiers_expr(js_name, field)
            lines.append(f'{i}  var {js_name}_out = {val_expr};')
            lines.extend(f'{i}  {s}' for s in self._bind_js(field, js_name, f'{js_name}_out', False))
            if not name.startswith('_'):
                guards, target = name_from_to_js(field, js_name)
                for guard in guards:
                    lines.append(f'{i}  {guard}')
                lines.append(f'{i}  {target} = {js_name}_out;')
            return lines

        # integer
        sz = type_size(ftype)
        if sz is None:
            # PS-327/PS-328. This emitted a TODO comment and no read, so the field
            # vanished and every later field came from the wrong offset.
            if not ftype:
                raise ValueError(f"Field '{name}' declares no type")
            raise ValueError(f"Field '{name}': unknown type: {ftype}")

        signed = is_signed(ftype)
        read_fn = 'readS' if signed else 'readU'
        eo = field_endian_override(ftype, field)
        endian_arg = f'"{eo}"' if eo else 'endian'

        if is_word_ordered(ftype):
            # Its own reader: the layout is not a size-and-endianness pair (PS-271).
            layout, kind = WORD_ORDERED[ftype]
            lines.append(f'{i}  var {js_name} = readWordOrdered(buf, pos, "{layout}", "{kind}");')
            lines.append(f'{i}  pos += 4;')
        else:
            lines.append(f'{i}  var {js_name} = {read_fn}(buf, pos, {sz}, {endian_arg});')
            lines.append(f'{i}  pos += {sz};')
            if field.get('sentinel'):
                # PS-427: the sentinel is the bit pattern, before any encoding.
                lines.append(f'{i}  var {js_name}_bits = {js_name};')
            if field.get('encoding'):
                lines.append(f'{i}  {js_name} = decodeEncoding({js_name}, "{field["encoding"]}", {sz});')

        val_expr = self._apply_modifiers_expr(js_name, field)
        lines.append(f'{i}  var {js_name}_out = {val_expr};')
        self._emit_tail(lines, i, field, js_name, name,
                        bits_var=f'{js_name}_bits' if field.get('sentinel') else js_name)
        return lines

    def _computed_tail(self, i: str, field: Dict, name: str, value_expr: str) -> List[str]:
        """A computed field's report and binding, unless `out_of_range: omit` drops it."""
        bounds = field.get('valid_range')
        if field.get('out_of_range') != 'omit' or not isinstance(bounds, (list, tuple)) \
                or len(bounds) != 2:
            return [f'{i}  d.{name} = {value_expr};'] + \
                [f'{i}  {s}' for s in self._bind_js(field, name, f'd.{name}', False)]
        tmp = f'_c_{to_js_name(name)}'
        key = json.dumps(name)
        # PS-475: compared before the lookup; a guard's `else` is not compared at all.
        v = f'rv[{key}]' if 'lookup' in field else tmp
        return [f'{i}  var {tmp} = {value_expr};',
                f'{i}  if (!rvs[{key}] && typeof {v} === "number" && ({v} < {bounds[0]} || {v} > {bounds[1]})) '
                f'{{ aqr[{json.dumps(name)}] = "out_of_range"; }}',
                f'{i}  else {{ d.{name} = {tmp}; '
                + ' '.join(self._bind_js(field, name, tmp, False)) + ' }']

    @staticmethod
    def _bind_js(field: Dict, js_name: str, value: str, with_var: bool = True,
                 number: Optional[str] = None) -> List[str]:
        """Statements binding a field's names in `vars`, as PS-523 says.

        A looked-up field binds its label where that is a number and otherwise the number
        before the lookup (`lv`), a lookup miss included; `lbl` keeps the reported value,
        which a `name_from` template writes (PS-532). Every name used to hold the label,
        so `$code` in a `compute` read "report_rsp" and a case keyed 129 never matched.
        """
        names = [js_name]
        if with_var and field.get('var'):
            names.insert(0, to_js_name(field['var']))
        if number is None and 'lookup' not in field:
            return [f'vars.{n} = {value};' for n in names]
        if number is None:
            number = f'lv[{json.dumps(field.get("name", ""))}]'
        return [f'vars.{n} = refValue({value}, {number}); lbl.{n} = {value};'
                for n in names]

    def _emit_tail(self, lines: List[str], i: str, field: Dict, js_name: str, name: str,
                   bits_var: str = '') -> None:
        """Bind and report a numeric field's value, unless it is "no reading".

        PS-427: a sentinel compares the raw bits, before any modifier or encoding.
        PS-428: `out_of_range: omit` drops a value outside `valid_range`. Either way the
        field is absent - not bound, not reported - and `_quality` records why where it
        is produced (`aqr` for a field declaring a range, `aqp` otherwise).
        """
        body = self._bind_js(field, js_name, f'{js_name}_out')
        if not name.startswith('_'):
            guards, target = name_from_to_js(field, js_name)
            body.extend(guards)
            body.append(f'{target} = {js_name}_out;')
        conditions = []
        bucket = 'aqr' if field.get('valid_range') else 'aqp'
        key = json.dumps(name)
        if field.get('sentinel') and bits_var:
            conditions.append((f'{json.dumps(list(field["sentinel"]))}.indexOf({bits_var}) >= 0',
                               '"absent"'))
        bounds = field.get('valid_range')
        if field.get('out_of_range') == 'omit' and isinstance(bounds, (list, tuple)) \
                and len(bounds) == 2:
            # PS-475: a looked-up field is compared before its lookup.
            v = f'rv[{key}]' if 'lookup' in field else f'{js_name}_out'
            conditions.append((f'(typeof {v} === "number" && ({v} < '
                               f'{bounds[0]} || {v} > {bounds[1]}))',
                               '"out_of_range"'))
        if not conditions:
            lines.extend(f'{i}  {line}' for line in body)
            return
        keyword = 'if'
        for condition, why in conditions:
            lines.append(f'{i}  {keyword} ({condition}) {{ {bucket}[{key}] = {why}; }}')
            keyword = 'else if'
        lines.append(f'{i}  else {{ ' + ' '.join(body) + ' }')

    def _apply_modifiers_expr(self, raw_var: str, field: Dict) -> str:
        # Deprecated formula takes precedence
        if 'formula' in field:
            js = formula_to_js(field['formula'])
            js = re.sub(r'\bx\b', raw_var, js)
            return js
        
        expr = canonical_modifiers_to_js(field, raw_var)
        
        # Apply transform array if present (new declarative approach)
        if 'transform' in field:
            expr = transform_to_js(field['transform'], expr)
        
        # PS-475: valid_range compares the value before the lookup. The value is kept in
        # `rv` as it passes - the comma operator keeps this an expression - and the range
        # checks read it from there rather than the label.
        stash = None
        if 'lookup' in field and field.get('valid_range'):
            stash = (json.dumps(field.get('name', '')), expr)
            expr = f'rv[{stash[0]}]'

        if 'lookup' in field:
            lookup = field['lookup']
            # PS-523: the number before the lookup, for a reference to bind under a
            # text or boolean label. `lv` is read through below, so it is evaluated once.
            number_key = json.dumps(field.get('name', ''))
            pre_lookup = expr
            expr = f'lv[{number_key}]'
            if isinstance(lookup, dict):
                # `default` is a fallback, not a key. It used to be serialized into the
                # table, so a miss found nothing and fell through to the raw integer -
                # `mode` reported 9 where the schema says `unknown`.
                default = None
                has_default = False
                table = {}
                for key, mapped in lookup.items():
                    if str(key) == 'default':
                        default, has_default = mapped, True
                    else:
                        table[key] = mapped
                lk = json.dumps(table)
                from schema_interpreter import lookup_template, lookup_template_errors
                problems = lookup_template_errors(field)
                if problems:
                    raise ValueError(problems[0])      # PS-407
                if lookup_template(lookup) is not None:
                    fallback = f'lookupTemplate({json.dumps(default)}, {expr})'
                elif has_default:
                    fallback = json.dumps(default)
                else:
                    # PS-269: an unmatched mapping omits the field rather than reporting
                    # the raw integer under a name that promises a label. `undefined` is
                    # dropped by JSON.stringify, which is what absent means here.
                    fallback = 'undefined'
                expr = f'(({lk})[{expr}] !== undefined ? ({lk})[{expr}] : {fallback})'
            else:
                # PS-104/PS-105: a sequence is indexed from zero, and an out-of-bounds
                # index is an error rather than the raw value. Routed through a helper so
                # the index expression is evaluated once instead of up to three times.
                lk = json.dumps(lookup)
                expr = f'seqLookup({lk}, {expr})'
            expr = f'(lv[{number_key}] = {pre_lookup}, {expr})'
        if stash:
            bounds = field.get('valid_range')
            if field.get('out_of_range') == 'omit' and isinstance(bounds, (list, tuple)) \
                    and len(bounds) == 2:
                # An omitted value is not looked up (PS-443): its index may be no entry.
                v = f'rv[{stash[0]}]'
                expr = f'(({v} < {bounds[0]} || {v} > {bounds[1]}) ? {v} : {expr})'
            expr = f'(rv[{stash[0]}] = {stash[1]}, {expr})'
        return expr

    def _reverse_modifiers_expr(self, val_var: str, field: Dict) -> str:
        """The decode chain undone: value -> the integer the payload holds.

        Delegates to the runtime encoder's `encReverseModifiers`, the port of the
        reference's `_reverse_modifiers`: the lookup, the transform stages last first,
        then add, div, mult (PS-101, PS-102). This used to undo only the bare modifiers,
        in reverse *key* order, so `{add: -40, div: 10}` encoded 25 as 290 where
        `{div: 10, add: -40}` gave 650.
        """
        return f'encReverseModifiers({val_var}, {json.dumps(field, default=str)})'

    def _gen_decode_bitfield_string(self, field: Dict) -> List[str]:
        i = self._i()
        lines = []
        name = to_js_name(field['name'])
        length = field.get('length', 2)
        parts = field.get('parts', [])
        delim = field.get('delimiter', '.')
        prefix = field.get('prefix', '')

        # The value's bytes, most significant first. Reading it as one number and
        # shifting broke above bit 31, where JavaScript's bit operators wrap.
        lines.append(f'{i}  var {name}_raw = orderedBytes(buf, pos, {length}, endian);')
        lines.append(f'{i}  pos += {length};')
        seg_exprs = []
        for part in parts:
            offset = int(part[0])
            width = int(part[1])
            fmt = part[2] if len(part) > 2 else 'decimal'
            # PS-430: hex lower case, hex:upper upper case, anything else refused.
            if fmt not in ('decimal', 'hex', 'hex:upper'):
                raise ValueError(f"bitfield_string part format {fmt!r} is not one of "
                                 f"decimal, hex, hex:upper (PS-430)")
            seg_exprs.append(
                f'bitfieldPart({name}_raw, {offset}, {width}, {json.dumps(fmt)})'
            )
        joined = f' + "{delim}" + '.join(seg_exprs)
        if prefix:
            joined = f'"{prefix}" + {joined}'
        lines.append(f'{i}  d.{name} = {joined};')
        lines.append(f'{i}  vars.{name} = {joined};')
        return lines

    def _gen_decode_tlv(self, tlv: Dict) -> List[str]:
        i = self._i()
        lines = []
        tag_fields = tlv.get('tag_fields', [])
        cases = tlv.get('cases', {})

        # Two block shapes exist. A composite block names its tag components in
        # `tag_fields`; a plain block declares only `tag_size` and reads one tag value.
        #
        # This handled the first alone: with no `tag_fields`, `tag_size` summed to 0, no
        # tag was ever read, and the key expression collapsed to a constant that matched
        # nothing - so elsys/ers decoded to {} entirely. Go had the identical defect for
        # the identical reason.
        if tag_fields:
            # The bytes a tag takes, as the reference counts them: a bit range advances
            # by its `consume` and no further (PS-060).
            from schema_interpreter import fixed_element_size
            tag_size = fixed_element_size(tag_fields) or sum(
                type_size(tf.get('type', 'u8')) or 1 for tf in tag_fields)
        else:
            tag_size = int(tlv.get('tag_size', 1))
        length_size = int(tlv.get('length_size', 0))

        lines.append(f'{i}  // TLV loop')
        lines.append(f'{i}  _fld = null;')
        # PS-477: a tag is never partly read - fewer bytes than the tag at the start of an
        # entry is an error identifying the tlv. The loop used to require tag and length
        # to fit and stop silently otherwise. PS-486 extends it to the rest of the entry:
        # a length cut short, or a length declaring more bytes than remain, is an error
        # too, whether or not the tag is known.
        lines.append(f'{i}  while (pos < buf.length) {{')
        lines.append(f'{i}    var _tlvStart = pos;')
        lines.append(f'{i}    if (buf.length - pos < {tag_size}) throw new Error("tlv entry at '
                     f'offset " + pos + ": " + (buf.length - pos) + " byte(s) remain, fewer '
                     f'than its {tag_size}-byte tag (PS-477)");')
        if length_size:
            lines.append(f'{i}    if (buf.length - pos < {tag_size + length_size}) throw new Error('
                         f'"tlv entry at offset " + pos + ": " + (buf.length - pos - {tag_size}) + '
                         f'" byte(s) remain after its tag, fewer than its {length_size}-byte '
                         f'length (PS-486)");')

        # Read tag fields
        for tf in tag_fields:
            tfname = to_js_name(tf['name'])
            tf_slice = parse_bit_slice_type(str(tf.get('type', 'u8')))
            if tf_slice is not None:
                # A bit range in a composite tag reads its base where it stands and
                # advances only by `consume` (PS-060), as anywhere else. It was read as a
                # whole byte and consumed one, so comtac lpn-td1's `[u8, u8[0:3]]` tag -
                # the count is the low nibble of the next byte - took two bytes and
                # every record after it read from the wrong offset.
                base_type, bit_start, bit_width = tf_slice
                base_size = type_size(base_type) or 1
                if base_size >= 7:
                    lines.append(f'{i}    var {tfname} = bitRange(orderedBytes(buf, pos, '
                                 f'{base_size}, endian), {bit_start}, {bit_width}, '
                                 f'{json.dumps(base_type.startswith("s"))});')
                else:
                    lines.append(f'{i}    var {tfname} = Math.floor(readU(buf, pos, {base_size}, '
                                 f'endian) / {2 ** bit_start}) % {2 ** bit_width};')
                    if base_type.startswith('s'):
                        lines.append(f'{i}    if ({tfname} >= {2 ** (bit_width - 1)}) '
                                     f'{tfname} -= {2 ** bit_width};')
                consume = tf.get('consume', 0)
                if consume:
                    lines.append(f'{i}    pos += {int(consume)};')
            else:
                tfsz = type_size(tf.get('type', 'u8')) or 1
                lines.append(f'{i}    var {tfname} = readU(buf, pos, {tfsz}, endian);')
                lines.append(f'{i}    pos += {tfsz};')
            # A component is read as a field, so its `var:` is bound for the case body -
            # a `name_from: "voc_${ch}"` with `ch` on the channel component found nothing
            # (rakwireless standardized payload). Its name is bound too: a component is
            # a field decoded earlier in the payload, so `ref: $rsn` in a case reads it
            # (PS-265), where the codec read an undefined value.
            # PS-523 (CR-2026-132): both bind as any field's do - through its modifiers,
            # and a lookup's number under a text label, the label kept for templates. The
            # tag is still matched on the value read.
            lines.append(f'{i}    var {tfname}_out = {self._apply_modifiers_expr(tfname, tf)};')
            lines.extend(f'{i}    {s}' for s in self._bind_js(tf, tfname, f'{tfname}_out'))
        if not tag_fields:
            lines.append(f'{i}    var _tlvTag = readU(buf, pos, {tag_size}, endian);')
            lines.append(f'{i}    pos += {tag_size};')
        if length_size:
            lines.append(f'{i}    var _tlvLen = readU(buf, pos, {length_size}, endian);')
            lines.append(f'{i}    pos += {length_size};')
            lines.append(f'{i}    if (_tlvLen > buf.length - pos) throw new Error("tlv entry at '
                         f'offset " + _tlvStart + ": its length declares " + _tlvLen + " byte(s), " '
                         f'+ (buf.length - pos) + " remain (PS-486)");')

        # Build tag key for matching
        if not tag_fields:
            tag_key_expr = '_tlvTag'
            tag_is_composite = False
            tag_parts = ['_tlvTag']
        elif len(tag_fields) == 1:
            tag_key_expr = to_js_name(tag_fields[0]['name'])
            tag_is_composite = False
            tag_parts = [tag_key_expr]
        else:
            parts = [to_js_name(tf['name']) for tf in tag_fields]
            tag_key_expr = '"[" + ' + ' + ", " + '.join(parts) + ' + "]"'
            tag_is_composite = True
            tag_parts = parts
        #: The tag as the interpreters carry it - a list of its components, in key order -
        #: so a warning naming it reads the same on both paths.
        tag_parts_expr = '[' + ', '.join(tag_parts) + ']'

        separate = tlv.get('merge', True) is False
        # PS-157, PS-525: each entry of a merged tlv decodes into an object of its own,
        # folded into the output by tlvFold, so a name two entries report is combined
        # as its on_repeat says. The tables are keyed as the output is staged.
        repeat_modes, repeat_last, _ = _tlv_repeat_modes(tlv)
        fold_modes = json.dumps({to_js_name(k): v for k, v in repeat_modes.items()
                                 if v != 'collect'}, sort_keys=True)
        fold_last = json.dumps({to_js_name(k): {to_js_name(m): True for m in sorted(ms)}
                                for k, ms in repeat_last.items() if ms}, sort_keys=True)
        fold_id = self._tlv_fold_seq = getattr(self, '_tlv_fold_seq', 0) + 1
        if not separate:
            lines.insert(1, f'{i}  var _tlvC{fold_id} = {{}};')
        # PS-270: exact keys are tried first, then negated (`!n`), then wildcard (`*`), so
        # a broad key never shadows a specific one; within a rank, schema order. A
        # composite key is compared component by component, as the interpreters do. It
        # was compared as one string against "[" + a + ", " + b + "]", so `!` and `*` never
        # matched, and nor did an exact key written without the space after its comma.
        ranked = []
        for order, (case_key, case_fields) in enumerate(cases.items()):
            cond, rank = self._tlv_case_condition(case_key, tag_parts)
            if cond is None:
                continue
            ranked.append((rank, order, cond, case_fields))
        ranked.sort(key=lambda item: (item[0], item[1]))
        first = True
        for _rank, _order, cond, case_fields in ranked:

            kw = 'if' if first else '} else if'
            first = False
            lines.append(f'{i}    {kw} ({cond}) {{')

            if separate:
                # `merge: false` (Clause 4): each entry is its own object, carrying its
                # tag, in a list under `channels`. The case's fields are decoded into it
                # by swapping it in as `d` for their length. This was not generated at
                # all, so `channels` was missing from the codec's output with no error.
                lines.append(f'{i}      var _tlvOuter = d; d = {{ tag: {tag_parts_expr} }};')
            else:
                lines.append(f'{i}      var _tlvO{fold_id} = d; d = {{}};')
            self.indent += 2
            for cf in case_fields:
                lines.extend(self._gen_decode_field(cf))
            self.indent -= 2
            if separate:
                lines.append(f'{i}      if (!_tlvOuter.channels) {{ _tlvOuter.channels = []; }}')
                lines.append(f'{i}      _tlvOuter.channels.push(d); d = _tlvOuter;')
            else:
                lines.append(f'{i}      tlvFold(_tlvO{fold_id}, d, {fold_modes}, {fold_last}, '
                             f'_tlvC{fold_id}); d = _tlvO{fold_id};')

        if not first:
            lines.append(f'{i}    }} else {{')
            lines.extend(self._gen_decode_tlv_unknown(
                tlv, i, tag_parts_expr, length_size))
            lines.append(f'{i}    }}')

        lines.append(f'{i}  }}')
        return lines

    @staticmethod
    def _tlv_case_condition(case_key: Any, tag_parts: List[str]):
        """A JS condition matching one tlv case key, and its PS-270 rank.

        Rank 0 is an exact key, 1 a key with a negated component (`!n`), 2 a key with a
        wildcard (`*`). Returns (None, None) for a key that names no tag, such as
        `default`, which the unknown-tag path handles.
        """
        text = str(case_key).strip()
        if text == 'default':
            return None, None
        if not text.startswith('['):
            return '%s === %s' % (tag_parts[0], text), 0
        body = text[1:-1] if text.endswith(']') else text[1:]
        parts = [p.strip().strip('"\'') for p in body.split(',')]
        if len(parts) != len(tag_parts):
            raise ValueError("tlv case key %s has %d components; the tag has %d"
                             % (text, len(parts), len(tag_parts)))
        terms, rank = [], 0
        for part, expr in zip(parts, tag_parts):
            if part == '*':
                rank = max(rank, 2)
                continue
            if part.startswith('!'):
                rank = max(rank, 1)
                terms.append('%s !== %d' % (expr, int(part[1:].strip(), 0)))
            else:
                terms.append('%s === %d' % (expr, int(part, 0)))
        return (' && '.join(terms) or 'true'), rank

    def _gen_decode_tlv_unknown(self, tlv: Dict, i: str, tag_parts_expr: str,
                                length_size: int) -> List[str]:
        """What a generated codec does with a tag its schema does not describe.

        PS-304: it has to be what an interpreter reading the same schema does, because a
        schema is conformant through either path and the two must not differ. Before this
        the generator ignored `unknown`, so every mode came out as a silent `break` -
        including `error`, which the interpreters raise on.

        With `merge: false` a raw capture is one of the `channels`, as the interpreters
        report it (PS-303); merged output puts it under `unknown_tags`.
        """
        mode = tlv.get('unknown', 'skip')
        lines = [f'{i}      var _tlvLabel = tlvTagLabel({tag_parts_expr});']

        if mode == 'error':
            lines.append(f'{i}      throw new Error("Unknown TLV tag: " + _tlvLabel);')
            return lines

        if mode == 'raw':
            span = '_tlvLen' if length_size else 'buf.length - pos'
            lines.append(f'{i}      var _tlvSpan = {span};')
            separate = 'true' if tlv.get('merge', True) is False else 'false'
            lines.append(
                f'{i}      tlvUnknownRaw(d, {tag_parts_expr}, buf, pos, _tlvSpan, {separate});')
            lines.append(f'{i}      pos += _tlvSpan;')
            if not length_size:
                # Nothing delimits the entry, so the capture ran to the end of the buffer
                # and there is no next tag to read.
                lines.append(
                    f'{i}      w.push("unknown TLV tag (" + _tlvLabel + ") captured raw; "'
                    f' + _tlvSpan + " byte(s) after it could not be delimited");')
                lines.append(f'{i}      break;')
            return lines

        # skip, the default
        if length_size:
            lines.append(
                f'{i}      w.push("unknown TLV tag (" + _tlvLabel + ") skipped, "'
                f' + _tlvLen + " byte(s) discarded");')
            lines.append(f'{i}      pos += _tlvLen;')
            return lines

        # PS-302: with no length there is nothing to skip over, so decoding stops here
        # and everything from the tag onwards is lost. The count is what makes that
        # distinguishable from a device that sent fewer fields.
        lines.append(
            f'{i}      w.push("unknown TLV tag (" + _tlvLabel + ") at offset " + _tlvStart'
            f' + ": " + (buf.length - _tlvStart) + " of " + buf.length'
            f' + " byte(s) left undecoded");')
        lines.append(f'{i}      _leftDone = true;')
        lines.append(f'{i}      break;')
        return lines

    def _gen_decode_match(self, match: Dict) -> List[str]:
        """An Option B `match`, to parity with the interpreters (CR-2026-020).

        Three things were missing and each failed differently:

        - **An inline discriminator.** `length` and `name` were not read at all, so a
          match with no `field:` emitted `vars.` - a syntax error - and never consumed the
          discriminator's bytes.
        - **A range case key.** The key was interpolated straight into a comparison, so
          `"2..5"` emitted `vars.kind === 2..5`. That is not valid JavaScript: the whole
          codec failed to parse, leaving the schema no generated path at all rather than a
          wrong one.
        - **`default`.** Unmatched values silently decoded nothing whatever the schema
          said, where the interpreters fall back, skip, or fail.
        """
        i = self._i()
        lines = []
        cases = match.get('cases', {}) or {}
        width = match.get('length')
        # PS-399: exactly one discriminator source; a schema with both or neither is
        # invalid. With neither, this read a one-byte discriminator nobody declared.
        if sum(key in match for key in ('field', 'length', 'remaining')) != 1:
            raise ValueError("a match must declare exactly one of 'field', 'length' and "
                             "'remaining' (PS-399, PS-416)")

        if 'remaining' in match:
            # PS-414: the bytes left, less any enclosing reserve - which the sliced buffer
            # already excludes. Nothing is read.
            if match['remaining'] is not True:
                raise ValueError("a match's remaining must be true (PS-414)")
            lines.append(f'{i}  var _mr = buf.length - pos;')
            discriminator = '_mr'
        elif match.get('field'):
            reference = match['field']
            discriminator = f'vars.{to_js_name(reference.lstrip("$"))}'
            lines.append(f'{i}  // match on {discriminator}')
            # A name nothing bound - a sentinel or an omitted range left it unbound
            # (PS-523) - is an error, as in every interpreter; the codec took `default`.
            message = (f"Match field {reference} has no value: no earlier field or var: "
                       "binds it")
            lines.append(f'{i}  if ({discriminator} === undefined) '
                         f'throw new Error({json.dumps(message)});')
        else:
            # Read it here, and consume it: the bytes belong to this construct.
            span = int(width) if width else 1
            lines.append(f'{i}  // match on an inline {span}-byte discriminator')
            from schema_interpreter import underflow_label
            mlabel = underflow_label(match.get('name'), 'match')
            lines.append(f'{i}  _fld = {json.dumps(mlabel)};')       # PS-535
            lines.append(f'{i}  var _mv = readU(buf, pos, {span}, endian);')
            lines.append(f'{i}  pos += {span};')
            discriminator = '_mv'
            if match.get('var'):
                lines.append(f'{i}  vars.{to_js_name(match["var"])} = _mv;')
            if match.get('name'):
                reported = to_js_name(match['name'])
                lines.append(f'{i}  d.{reported} = _mv;')
                lines.append(f'{i}  vars.{reported} = _mv;')

        # `default` inside `cases` is the fallback, not a case keyed by the string, and it
        # is tried only once every explicit case has failed.
        explicit = [(key, body) for key, body in cases.items() if key != 'default']
        fallback = cases.get('default', match.get('default'))

        first = True
        for case_key, case_fields in explicit:
            condition = self._match_condition(discriminator, case_key)
            if condition is None:
                continue
            kw = 'if' if first else '} else if'
            first = False
            lines.append(f'{i}  {kw} ({condition}) {{')
            self.indent += 1
            if isinstance(case_fields, list):
                for cf in case_fields:
                    lines.extend(self._gen_decode_field(cf))
            self.indent -= 1

        fallback_lines: List[str] = []
        if isinstance(fallback, list):
            self.indent += 1
            for cf in fallback:
                fallback_lines.extend(self._gen_decode_field(cf))
            self.indent -= 1
        elif fallback is None or fallback == 'error':
            # `error` is the default default, and the interpreters raise on it.
            fallback_lines = [
                f'{i}    throw new Error("No matching case for value " + {discriminator});'
            ]

        if first:
            # No usable case at all, so the fallback is unconditional.
            lines.extend(fallback_lines)
            return lines

        if fallback_lines:
            lines.append(f'{i}  }} else {{')
            lines.extend(fallback_lines)
        lines.append(f'{i}  }}')
        return lines

    def _match_condition(self, discriminator: str, case_key) -> Optional[str]:
        """The test for one case key, or None where no value could ever satisfy it.

        Mirrors the interpreters' `_match_case_pattern`: an integer compares equal,
        `"2..5"` is an inclusive range, `"[1, 2]"` matches any element (PS-398), and any
        other string compares equal as a string (a lookup label). A key no value could
        satisfy yields no branch at all rather than an expression that is never true.
        """
        if isinstance(case_key, bool):
            return None
        if isinstance(case_key, int):
            return f'{discriminator} === {case_key}'
        if isinstance(case_key, str):
            text = case_key.strip()
            if text.startswith('['):
                from schema_interpreter import parse_list_case_key
                values = parse_list_case_key(text)
                if not values:
                    return None
                return '(' + ' || '.join(f'{discriminator} === {v}' for v in values) + ')'
            if '..' in text:
                low, _, high = text.partition('..')
                try:
                    return (f'{discriminator} >= {int(low.strip(), 0)}'
                            f' && {discriminator} <= {int(high.strip(), 0)}')
                except ValueError:
                    return None
            try:
                return f'{discriminator} === {int(text, 0)}'
            except ValueError:
                # A string key matches a string discriminator by equality, as the
                # interpreters compare it - an `ascii` field's text. Not a lookup label:
                # a reference to a looked-up field binds a number (PS-523), so a label
                # key matches nothing there (PS-524). These keys yielded no branch, so
                # every such case body was silently missing from the codec's output.
                return f'{discriminator} === {json.dumps(case_key)}'
        return None

    # ---------------------------------------------------------------
    # Encoder generation
    # ---------------------------------------------------------------
    def _gen_encode_fields(self) -> str:
        parts = [f'var ENC_VARS = {json.dumps(self._enc_vars(), default=str)};',
                 f'var ENC_NAMED = {json.dumps(self._enc_named(), default=str)};']
        if self.has_ports:
            for port_key, port_def in self.schema['ports'].items():
                fname = f'encodePort{port_key}'
                fields = port_def.get('fields', [])
                parts.append(self._gen_encode_fn(fname, fields))
        else:
            # A schema with neither ports nor downlink_commands used to get no encoder at
            # all, so its generated codec could not build a downlink even where the
            # interpreter encoded the same schema without complaint - encodeDownlink
            # returned "No downlink encoding defined". PS-287 reads an entry that declares
            # no direction as accepting either, so refusing to encode contradicts it.
            fields = self.schema.get('fields', [])
            parts.append(self._gen_encode_fn('encodePayload', fields))
        return '\n\n'.join(parts)

    def _gen_encode_fn(self, fname: str, fields: List[Dict]) -> str:
        """An encoder: the field list as data, walked by the runtime encoder.

        The field list was unrolled into code at generation time, a second hand-kept copy
        of the reference's encode rules, and it had drifted on most constructs - see
        ENCODER_RUNTIME_JS. The list is embedded with its references spliced,
        exactly as the decoder sees it.
        """
        plan = self._expand_refs(list(fields)) if isinstance(fields, list) else fields
        plan = _annotate_on_repeat(plan)
        return '\n'.join([
            f'var {fname}_FIELDS = {json.dumps(plan, default=str, separators=(",", ":"))};',
            f'function {fname}(d, endian) {{',
            f'  return encodeRoot({fname}_FIELDS, d, endian || "{self.endian}");',
            '}',
        ])

    # ---------------------------------------------------------------
    # Downlink Commands
    # ---------------------------------------------------------------
    def _gen_command_functions(self) -> str:
        """Generate functions for encoding/decoding downlink commands."""
        lines = []
        
        # Command ID lookup table
        cmd_ids = {}
        for cmd_name, cmd_def in self.downlink_commands.items():
            cmd_id = cmd_def.get('command_id', 0)
            if isinstance(cmd_id, str) and cmd_id.startswith('0x'):
                cmd_id = int(cmd_id, 16)
            cmd_ids[cmd_name] = cmd_id
        
        lines.append('// --- Downlink Commands ---')
        lines.append(f'var COMMANDS = {json.dumps(cmd_ids)};')
        lines.append('')
        
        # Generate encodeCommand function
        lines.append('function encodeCommand(cmdName, data, endian) {')
        lines.append('  var buf = [];')
        lines.append('  var pos = 0;')
        lines.append('  if (!(cmdName in COMMANDS)) {')
        lines.append('    throw new Error("Unknown command: " + cmdName);')
        lines.append('  }')
        lines.append('  var cmdId = COMMANDS[cmdName];')
        lines.append('  buf[pos++] = cmdId & 0xFF;')
        lines.append('')
        
        # Generate case for each command
        first = True
        for cmd_name, cmd_def in self.downlink_commands.items():
            prefix = 'if' if first else '} else if'
            first = False
            lines.append(f'  {prefix} (cmdName === "{cmd_name}") {{')
            
            fields = cmd_def.get('fields', [])
            for field in fields:
                fname = field.get('name', '')
                ftype = field.get('type', 'u8')
                if fname and not fname.startswith('_'):
                    sz = type_size(ftype) or 1
                    signed = is_signed(ftype)
                    write_fn = 'writeS' if signed else 'writeU'
                    js_name = to_js_name(fname)
                    
                    # Handle modifiers
                    # PS-551: a missing value is rejected, naming the field, as the
                    # reference's encode_command does. It was written as 0.
                    src = f'data.{js_name}'
                    rev = self._reverse_modifiers_expr(f'({src})', field)
                    from schema_interpreter import field_default
                    has_default, default = field_default(field)
                    if has_default:
                        # PS-554: an omitted value takes the declared default.
                        lines.append(f'    if (data.{js_name} === undefined || data.{js_name} === null) '
                                     f'data.{js_name} = {json.dumps(default)};')
                    else:
                        lines.append(f'    if (data.{js_name} === undefined || data.{js_name} === null) '
                                     f'throw new Error({json.dumps("Missing required field: " + fname)});')
                    lines.append(f'    {write_fn}(buf, pos, {sz}, {rev}, endian);')
                    lines.append(f'    pos += {sz};')
        
        if self.downlink_commands:
            lines.append('  }')
        
        lines.append('  return buf;')
        lines.append('}')
        lines.append('')
        
        # Generate decodeCommand function
        lines.append('function decodeCommand(buf, endian) {')
        lines.append('  var d = {};')
        lines.append('  var pos = 0;')
        lines.append('  if (buf.length < 1) {')
        lines.append('    throw new Error("Payload too short for command_id");')
        lines.append('  }')
        lines.append('  var cmdId = buf[pos++];')
        lines.append('  d._command_id = cmdId;')
        lines.append('')
        
        # Generate case for each command
        first = True
        for cmd_name, cmd_def in self.downlink_commands.items():
            cmd_id = cmd_def.get('command_id', 0)
            if isinstance(cmd_id, str) and cmd_id.startswith('0x'):
                cmd_id = int(cmd_id, 16)
            
            prefix = 'if' if first else '} else if'
            first = False
            lines.append(f'  {prefix} (cmdId === {cmd_id}) {{')
            lines.append(f'    d._command = "{cmd_name}";')
            
            fields = cmd_def.get('fields', [])
            for field in fields:
                fname = field.get('name', '')
                ftype = field.get('type', 'u8')
                if fname and not fname.startswith('_'):
                    sz = type_size(ftype) or 1
                    signed = is_signed(ftype)
                    read_fn = 'readS' if signed else 'readU'
                    js_name = to_js_name(fname)
                    
                    lines.append(f'    d.{js_name} = {read_fn}(buf, pos, {sz}, endian);')
                    lines.append(f'    pos += {sz};')
                    
                    # Apply modifiers
                    if any(k in field for k in ('mult', 'add', 'div')):
                        mod_expr = f'd.{js_name}'
                        for key in ['mult', 'add', 'div']:
                            if key in field:
                                if key == 'mult':
                                    mod_expr = f'({mod_expr} * {field[key]})'
                                elif key == 'add':
                                    mod_expr = f'({mod_expr} + {field[key]})'
                                elif key == 'div':
                                    mod_expr = f'({mod_expr} / {field[key]})'
                        lines.append(f'    d.{js_name} = {mod_expr};')
        
        if self.downlink_commands:
            lines.append('  } else {')
            lines.append('    d._command = "unknown";')
            lines.append('  }')
        
        lines.append('  return { data: d, pos: pos };')
        lines.append('}')
        
        return '\n'.join(lines)

    # ---------------------------------------------------------------
    # TS013 Entry Points
    # ---------------------------------------------------------------
    def _gen_entry_points(self) -> str:
        lines = []

        if self.has_ports:
            # A `default` entry is not an fPort comparison: it is the fallback for every
            # port the schema does not list (PS-024), emitted after them below. It used to
            # be refused here, by int('default').
            port_map = {}
            for port_key, port_def in self.schema['ports'].items():
                if str(port_key) == 'default':
                    continue
                port_map[int(port_key)] = {
                    'direction': port_def.get('direction', DEFAULT_PORT_DIRECTION),
                }

            # decodeUplink and decodeDownlink. Each entry point decodes the ports
            # declared for its direction, and refuses the ports declared for the other
            # one - naming the declared direction, because "unknown fPort" describes a
            # port the schema does not define and sends an implementer looking for a
            # missing definition (PS-288, CR-2026-010).
            for fn_name, direction in (('decodeUplink', 'uplink'),
                                       ('decodeDownlink', 'downlink')):
                lines.append(f'function {fn_name}(input) {{')
                lines.append('  try {')
                lines.append(f'    var endian = "{self.endian}";')
                # PS-459, PS-460: with no FPort there is nothing to select by, and saying
                # so is a different fault from an FPort the schema does not describe.
                lines.append('    if (input.fPort === undefined || input.fPort === null) {')
                lines.append('      return { data: {}, warnings: [], errors: ["no FPort was supplied, '
                             'and this schema selects its fields by port (PS-459)"] };')
                lines.append('    }')
                for port_key in self.schema['ports']:
                    declared = self.schema['ports'][port_key].get(
                        'direction', DEFAULT_PORT_DIRECTION)
                    if str(port_key) == 'default':
                        continue
                    lines.append(f'    if (input.fPort === {port_key}) {{')
                    if declared in (direction, 'both'):
                        lines.append(f'      var r = decodePort{port_key}(input.bytes, endian);')
                        lines.append(f'      return {{ data: omitAbsent(r.data), warnings: r.warnings || [], errors: [] }};')
                    else:
                        message = (f'fPort {port_key} is declared direction:{declared}; '
                                   f'message direction is {direction}')
                        lines.append(f'      return {{ data: {{}}, warnings: [], errors: ["{message}"] }};')
                    lines.append(f'    }}')
                fallback = self.schema['ports'].get('default')
                if isinstance(fallback, dict):
                    # PS-024: every port not listed falls through to `default`.
                    declared = fallback.get('direction', DEFAULT_PORT_DIRECTION)
                    if declared == 'both' or declared == direction:
                        lines.append('    var rd = decodePortdefault(input.bytes, endian);')
                        lines.append('    return { data: omitAbsent(rd.data), warnings: rd.warnings || [], errors: [] };')
                    else:
                        message = (f'the default port entry is declared direction:{declared}; '
                                   f'message direction is {direction}')
                        lines.append(f'    return {{ data: {{}}, warnings: [], errors: ["{message}"] }};')
                # PS-025: an FPort the schema does not describe is an error. It was a
                # warning beside an empty result, which reads as a successful decode.
                lines.append(f'    return {{ data: {{}}, warnings: [], errors: ["No port definition for fPort " + input.fPort] }};')
                lines.append('  } catch (e) {')
                lines.append('    return { data: {}, warnings: [], errors: [e.message] };')
                lines.append('  }')
                lines.append('}')
                lines.append('')

            # encodeDownlink
            lines.append('function encodeDownlink(input) {')
            lines.append('  try {')
            lines.append(f'    var endian = "{self.endian}";')
            # The port the caller names, where it has one (PS-292: never an entry that
            # declares itself uplink-only); else the first entry declaring downlink. This
            # used to ignore input.fPort and always encode the first downlink entry.
            ports = self.schema['ports']
            encodable = [pk for pk, pd in ports.items()
                         if isinstance(pd, dict) and pd.get('direction') != 'uplink']
            dl_ports = [pk for pk, pd in ports.items()
                        if isinstance(pd, dict) and pd.get('direction') == 'downlink']
            lines.append('    var port = input.fPort;')
            lines.append('    var r;')
            first = True
            for pk in encodable:
                if not str(pk).isdigit():
                    continue
                kw = 'if' if first else '} else if'
                first = False
                lines.append(f'    {kw} (port === {pk}) {{')
                lines.append(f'      r = encodePort{pk}(input.data, endian);')
            uplink_only = [pk for pk, pd in ports.items()
                           if isinstance(pd, dict) and pd.get('direction') == 'uplink'
                           and str(pk).isdigit()]
            for pk in uplink_only:
                kw = 'if' if first else '} else if'
                first = False
                lines.append(f'    {kw} (port === {pk}) {{')
                lines.append(f'      return {{ bytes: [], fPort: {pk}, warnings: [], errors: '
                             f'["fPort {pk} declares direction uplink, so it is not encoded as a '
                             f'downlink (PS-292)"] }};')
            if 'default' in encodable:
                # PS-024: an unlisted port encodes through the `default` entry.
                kw = 'if' if first else '} else if'
                first = False
                lines.append(f'    {kw} (typeof port === "number") {{')
                lines.append('      r = encodePortdefault(input.data, endian);')
            kw = 'if' if first else '} else if'
            if dl_ports:
                lines.append(f'    {kw} (port === undefined || port === null) {{')
                lines.append(f'      port = {dl_ports[0]};')
                lines.append(f'      r = encodePort{dl_ports[0]}(input.data, endian);')
                lines.append('    } else {')
            else:
                lines.append(f'    {kw} (true) {{' if first else '    } else {')
            lines.append('      return { bytes: [], fPort: port || 1, warnings: [], errors: '
                         '["No encodable entry for fPort " + port] };')
            lines.append('    }')
            lines.append('    return { bytes: r.bytes, fPort: port, warnings: r.warnings, errors: [] };')
            lines.append('  } catch (e) {')
            lines.append('    return { bytes: [], fPort: 1, warnings: [], errors: [e.message] };')
            lines.append('  }')
            lines.append('}')

        else:
            # No ports - single decode/encode. A schema-level `direction` is enforced here
            # as the `ports` branch enforces an entry's (PS-288, PS-292); the message is
            # the reference interpreter's. No declaration accepts both (PS-287).
            declared_dir = self.schema.get('direction')

            def guard(direction, encode=False):
                if declared_dir is None or declared_dir in ('both', direction):
                    return
                message = json.dumps(
                    f"schema '{self.schema.get('name')}' is declared "
                    f"direction:{declared_dir}; message direction is {direction}")
                if encode:
                    lines.append(f'  if (true) {{ return {{ bytes: [], fPort: input.fPort || 1, '
                                 f'warnings: [], errors: [{message}] }}; }}')
                else:
                    lines.append(f'  if (true) {{ return {{ data: {{}}, warnings: [], '
                                 f'errors: [{message}] }}; }}')

            lines.append('function decodeUplink(input) {')
            guard('uplink')
            lines.append('  try {')
            lines.append(f'    var r = decodePayload(input.bytes, "{self.endian}");')
            lines.append('    return { data: omitAbsent(r.data), warnings: r.warnings || [], errors: [] };')
            lines.append('  } catch (e) {')
            lines.append('    return { data: {}, warnings: [], errors: [e.message] };')
            lines.append('  }')
            lines.append('}')
            lines.append('')

            if self.has_commands:
                # Use command decoding for downlinks
                lines.append('function decodeDownlink(input) {')
                guard('downlink')
                lines.append('  try {')
                lines.append(f'    var r = decodeCommand(input.bytes, "{self.endian}");')
                lines.append('    return { data: omitAbsent(r.data), warnings: r.warnings || [], errors: [] };')
                lines.append('  } catch (e) {')
                lines.append('    return { data: {}, warnings: [], errors: [e.message] };')
                lines.append('  }')
                lines.append('}')
                lines.append('')

                lines.append('function encodeDownlink(input) {')
                guard('downlink', encode=True)
                lines.append('  try {')
                lines.append('    var cmdName = input.data._command;')
                lines.append('    if (!cmdName) {')
                lines.append('      return { bytes: [], fPort: 1, warnings: ["No _command specified"], errors: [] };')
                lines.append('    }')
                lines.append(f'    var bytes = encodeCommand(cmdName, input.data, "{self.endian}");')
                lines.append('    return { bytes: bytes, fPort: 1, warnings: [], errors: [] };')
                lines.append('  } catch (e) {')
                lines.append('    return { bytes: [], fPort: 1, warnings: [], errors: [e.message] };')
                lines.append('  }')
                lines.append('}')
            else:
                # No ports and no command table: the schema's own fields are the payload,
                # in both directions.
                lines.append('function decodeDownlink(input) {')
                guard('downlink')
                lines.append('  try {')
                lines.append(f'    var endian = "{self.endian}";')
                lines.append('    var r = decodePayload(input.bytes, endian);')
                lines.append('    return { data: omitAbsent(r.data), warnings: r.warnings || [], errors: [] };')
                lines.append('  } catch (e) {')
                lines.append('    return { data: {}, warnings: [], errors: [e.message] };')
                lines.append('  }')
                lines.append('}')
                lines.append('')

                lines.append('function encodeDownlink(input) {')
                guard('downlink', encode=True)
                lines.append('  try {')
                lines.append(f'    var r = encodePayload(input.data, "{self.endian}");')
                lines.append('    return { bytes: r.bytes, fPort: input.fPort || 1, warnings: r.warnings, errors: [] };')
                lines.append('  } catch (e) {')
                lines.append('    return { bytes: [], fPort: input.fPort || 1, warnings: [], errors: [e.message] };')
                lines.append('  }')
                lines.append('}')

        return self._restore_output_keys('\n'.join(lines))

    def _field_names(self) -> Set[str]:
        """Every literal field name the schema declares, in any field list."""
        names: Set[str] = set()

        def walk(node):
            if isinstance(node, dict):
                name = node.get('name')
                if isinstance(name, str) and '${' not in name:
                    names.add(name)
                for value in node.values():
                    walk(value)
            elif isinstance(node, list):
                for value in node:
                    walk(value)

        walk(self.schema.get('fields', []))
        walk(self.schema.get('ports', {}))
        walk(self.schema.get('definitions', {}))
        return names

    def _output_key_renames(self) -> Dict[str, str]:
        """Map each JS spelling back to the schema name it came from.

        Fields are staged under `to_js_name(name)`, which is right for a local variable
        and wrong for an output key: `pm1.0` was reported as `pm1_0`, so a vendor key
        with a dot or a space never survived generation (arwin lrs10701). Two names
        that mangle to one spelling used to be a generation error; `unique_js_names`
        now gives them distinct spellings, so each is restored to its own name.
        """
        renames: Dict[str, str] = {}
        for name, js in sorted(unique_js_names(self._field_names()).items()):
            if js != name:
                renames[js] = name
        return renames

    def _restore_output_keys(self, code: str) -> str:
        """Report output under the schema's own names, and accept them on encode."""
        renames = self._output_key_renames()
        if not renames:
            return code
        entry = code.find('function decodeUplink(')
        if entry < 0:
            return code
        helper = '\n'.join([
            'var OUTPUT_KEYS = %s;' % json.dumps(renames, sort_keys=True),
            'function renameKeys(v, table) {',
            '  if (Array.isArray(v)) return v.map(function (x) { return renameKeys(x, table); });',
            '  if (v === null || typeof v !== "object") return v;',
            '  var out = {};',
            '  for (var k in v) {',
            '    if (Object.prototype.hasOwnProperty.call(v, k)) {',
            '      out[Object.prototype.hasOwnProperty.call(table, k) ? table[k] : k] = renameKeys(v[k], table);',
            '    }',
            '  }',
            '  return out;',
            '}',
            '',
        ])
        tail = code[entry:]
        # omitAbsent still runs first: dropping it reported an omitted reading as null.
        # Every entry point's result, whatever its local is called: matching `r.data`
        # alone missed a `default` port's `rd.data`, so a schema decoding through one
        # reported `d_` and `e_`, at the top level and in every nested object.
        tail = re.sub(r'data: omitAbsent\((\w+)\.data\),',
                      r'data: renameKeys(omitAbsent(\1.data), OUTPUT_KEYS),', tail)
        return code[:entry] + helper + tail


def fix_yaml_booleans(obj):
    if isinstance(obj, dict):
        return {('on' if k is True else 'off' if k is False else k): fix_yaml_booleans(v) for k, v in obj.items()}
    elif isinstance(obj, list):
        return [fix_yaml_booleans(i) for i in obj]
    return obj


def load_schema(path: Path) -> Dict[str, Any]:
    with open(path) as f:
        schema = yaml.safe_load(f)
    return fix_yaml_booleans(schema)


def main():
    parser = argparse.ArgumentParser(description='Generate TS013 JS codec from Payload Schema YAML')
    parser.add_argument('input', help='Schema file or directory')
    parser.add_argument('-o', '--output', help='Output file or directory')
    parser.add_argument('--slim', action='store_true',
                        help='decode-only codec without commentary: no encoder runtime, '
                             'and encodeDownlink reports that it was left out')
    args = parser.parse_args()

    input_path = Path(args.input)
    if input_path.is_file():
        files = [input_path]
    else:
        files = sorted(list(input_path.glob('*.yaml')) + list(input_path.glob('*.yml')))
        files = [f for f in files if f.stem.lower() != 'readme']

    output_path = Path(args.output) if args.output else None

    for schema_path in files:
        try:
            schema = load_schema(schema_path)
            if not isinstance(schema, dict):
                continue
            if 'fields' not in schema and 'ports' not in schema:
                continue

            gen = TS013Generator(schema, schema_path.name, slim=args.slim)
            js = gen.generate()

            if output_path:
                if output_path.suffix == '.js':
                    out = output_path
                else:
                    output_path.mkdir(parents=True, exist_ok=True)
                    out = output_path / (schema_path.stem.replace('-', '_') + '_codec.js')
                out.write_text(js)
                print(f'Generated: {out}')
            else:
                print(js)
        except Exception as e:
            print(f'Error: {schema_path.name}: {e}', file=sys.stderr)


if __name__ == '__main__':
    main()
