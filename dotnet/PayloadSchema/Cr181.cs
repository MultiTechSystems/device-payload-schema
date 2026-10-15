// Copyright (c) 2024-2026 Multitech Systems, Inc.
// SPDX-License-Identifier: MIT

using System.Text.RegularExpressions;

namespace PayloadSchema;

/// <summary>
/// CR-2026-181 (PS-081, PS-601, PS-602; PS-527 and PS-551 amended): an encoder writes a
/// `bytes`, `hex` or `base64` value exactly as given. A string is read in the field's own
/// format - base64, padded or not, for `format: base64` and the base64 type, otherwise hex in
/// either case with the declared separator or without - and an array gives integers 0 to
/// 255. A malformed value, and one whose byte count is not a literal `length`, is rejected
/// naming the field, in the spec prototype's words. The encoder used to cut and pad
/// (<c>Pad</c>), mask array elements with <c>&amp; 0xFF</c>, read an unparseable element as
/// 0, strip ':' where the field declared no separator, and let
/// <c>Convert.FromBase64String</c>'s errors through unnamed.
/// </summary>
public static class Cr181
{
    static readonly Regex HexText = new("^(?:[0-9A-Fa-f]{2})*$", RegexOptions.CultureInvariant);
    static readonly Regex Base64Text = new("^[A-Za-z0-9+/]*={0,2}$", RegexOptions.CultureInvariant);
    const string Alphabet = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/";

    public static bool IsByteString(FieldType t) => t is FieldType.Bytes or FieldType.Hex or FieldType.Base64;

    /// <summary>byte_string_input: the bytes a value gives (PS-081), or PS-601's rejection.</summary>
    public static byte[] Input(SchemaField f, object? value)
    {
        var name = f.Name;
        if (value is byte[] raw) return raw;
        if (value is System.Collections.IList list)
        {
            var output = new byte[list.Count];
            for (int i = 0; i < list.Count; i++)
            {
                var item = list[i];
                var (ok, d) = item is bool ? (false, 0.0) : Helpers.ToFloat64(item);
                if (!ok || double.IsNaN(d) || d != Math.Round(d) || d < 0 || d > 255)
                    throw new FieldErrorException(
                        $"Field '{name}': an array element is not an integer from 0 to 255 (PS-601)");
                output[i] = (byte)d;
            }
            return output;
        }
        if (value is not string text)
            throw new FieldErrorException($"Field '{name}': a {JsonType(value)} is not a byte string value (PS-081)");
        bool base64 = f.Type == FieldType.Base64 || (f.Type == FieldType.Bytes && f.Format == "base64");
        if (base64)
        {
            var t = text.Trim();
            var padded = t + new string('=', (4 - t.Length % 4) % 4);
            var body = padded.TrimEnd('=');
            if (!Base64Text.IsMatch(padded) || body.Length % 4 == 1)
                throw new FieldErrorException($"Field '{name}': '{text}' is not valid base64 (PS-601)");
            // The alphabet is checked; trailing bits are ignored, as Python's b64decode does.
            var bytes = new List<byte>();
            int acc = 0, bits = 0;
            foreach (var c in body)
            {
                acc = ((acc << 6) | Alphabet.IndexOf(c)) & 0xFFFF;
                bits += 6;
                if (bits >= 8)
                {
                    bits -= 8;
                    bytes.Add((byte)(acc >> bits));
                }
            }
            return bytes.ToArray();
        }
        var separator = f.Type == FieldType.Bytes ? f.Separator : null;
        bool sep = !string.IsNullOrEmpty(separator);
        var hex = sep ? text.Replace(separator!, "") : text;
        if (!HexText.IsMatch(hex))
            throw new FieldErrorException($"Field '{name}': '{text}' is not well-formed hex"
                + (sep ? $" with separator '{separator}'" : "") + " (PS-601)");
        return Convert.FromHexString(hex);
    }

    static string JsonType(object? value) => value switch
    {
        null => "null",
        bool => "boolean",
        System.Collections.IDictionary => "object",
        _ when Helpers.ToFloat64(value).Item1 => "number",
        _ => value.GetType().Name,
    };

    /// <summary>A literal `length`, or null for `remaining`, a field name or none.</summary>
    public static int? LiteralLength(SchemaField f)
        => f.LengthRef == null && f.LengthDeclared && f.Length >= 0 ? f.Length : null;

    /// <summary>PS-602 for a literal length: the value's byte count, never cut or padded.</summary>
    public static byte[] CheckLength(SchemaField f, byte[] raw)
    {
        if (LiteralLength(f) is int n && raw.Length != n)
            throw new FieldErrorException($"Field '{f.Name}' is given {raw.Length} bytes for a length of {n} (PS-602)");
        return raw;
    }

    public static byte[] Encode(SchemaField f, object? value) => CheckLength(f, Input(f, value));

    /// <summary>PS-527 as amended: well formed, and of a literal length's byte count.</summary>
    public static bool Takes(SchemaField f, object? value)
    {
        try
        {
            Encode(f, value);
            return true;
        }
        catch (FieldErrorException)
        {
            return false;
        }
    }
}
