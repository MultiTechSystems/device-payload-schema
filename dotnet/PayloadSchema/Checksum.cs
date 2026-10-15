// Copyright (c) 2024-2026 Multitech Systems, Inc.
// SPDX-License-Identifier: MIT

using System.Globalization;
using YamlDotNet.RepresentationModel;

namespace PayloadSchema;

/// <summary>A field's <c>checksum</c> declaration (PS-585), as loaded.</summary>
public sealed record ChecksumSpec(string Algo, int Seed, int From, string OnMismatch);

/// <summary>
/// CR-2026-174 (PS-585 to PS-587): a field of an unsigned integer type may declare
/// <c>checksum: {algo, seed?, from?, on_mismatch?}</c>, a check value over the frame's bytes
/// from <c>from</c> to the byte before the field. A decoder computes it and compares it with
/// the value read (PS-586); an encoder computes it over the finished frame and writes it,
/// never taking it from the input (PS-587). Mirrors compute_checksum,
/// checksum_field_errors, checksum_mismatch and fill_checksums in
/// tools/schema_interpreter.py; the messages are the spec prototype's.
/// </summary>
public static class Checksum
{
    /// <summary>Each algorithm and the bits its result needs, in the order PS-585 lists them.</summary>
    static readonly SortedDictionary<string, int> Algos = new(StringComparer.Ordinal)
    {
        ["crc16/kermit"] = 16, ["crc8/smbus"] = 8, ["sum8"] = 8, ["xor8"] = 8,
    };

    static readonly string[] Keys = { "algo", "seed", "from", "on_mismatch" };

    /// <summary>The unsigned integer types a checksum may be declared on, by width in bits.</summary>
    internal static int? Width(string? type) => type switch
    {
        "u8" or "uint8" => 8,
        "u16" or "uint16" => 16,
        "u24" or "uint24" => 24,
        "u32" or "uint32" => 32,
        "u64" or "uint64" => 64,
        _ => null,
    };

    /// <summary>
    /// The four algorithms. The CRCs are the catalogue entries: over "123456789",
    /// crc8/smbus is 0xF4 and crc16/kermit 0x2189.
    /// </summary>
    public static ulong Compute(string algo, ReadOnlySpan<byte> data, int seed)
    {
        switch (algo)
        {
            case "sum8":
            {
                ulong sum = (ulong)seed;
                foreach (var b in data) sum += b;
                return sum & 0xFF;
            }
            case "xor8":
            {
                ulong value = (ulong)seed;
                foreach (var b in data) value ^= b;
                return value;
            }
            case "crc8/smbus":                  // poly 0x07, init 0, not reflected, no final XOR
            {
                int crc = 0;
                foreach (var b in data)
                {
                    crc ^= b;
                    for (int i = 0; i < 8; i++)
                        crc = (crc & 0x80) != 0 ? ((crc << 1) ^ 0x07) & 0xFF : (crc << 1) & 0xFF;
                }
                return (ulong)crc;
            }
            case "crc16/kermit":                // poly 0x1021 reflected (0x8408), init 0, no final XOR
            {
                int crc = 0;
                foreach (var b in data)
                {
                    crc ^= b;
                    for (int i = 0; i < 8; i++)
                        crc = (crc & 1) != 0 ? (crc >> 1) ^ 0x8408 : crc >> 1;
                }
                return (ulong)crc;
            }
        }
        throw new InvalidOperationException($"Unknown checksum algorithm '{algo}' (PS-585)");
    }

    // --- load (PS-585) ------------------------------------------------------------------

    static YamlNode? Get(YamlMappingNode map, string key)
    {
        foreach (var kv in map.Children)
            if (kv.Key is YamlScalarNode s && s.Value == key)
                return kv.Value;
        return null;
    }

    /// <summary>
    /// The declaration a decoder and an encoder follow, or null where the field declares
    /// none. Lenient: <see cref="Check"/> has rejected a malformed one before it is used.
    /// </summary>
    internal static ChecksumSpec? Parse(YamlMappingNode field)
    {
        if (Get(field, "checksum") is not YamlMappingNode spec) return null;
        var map = (Meta.FromYaml(spec) as Dictionary<string, object?>)!;
        int Int(string key, int fallback) => map.GetValueOrDefault(key) is long l ? (int)l : fallback;
        return new ChecksumSpec(map.GetValueOrDefault("algo") as string ?? "",
            Int("seed", 0), Int("from", 0), map.GetValueOrDefault("on_mismatch") as string ?? "error");
    }

    /// <summary>Throws the first PS-585 violation anywhere in the document, test vectors skipped.</summary>
    internal static void Check(YamlMappingNode root) => Walk(root);

    // --- placement (PS-608, CR-2026-185) -------------------------------------------------

    /// <summary>The constructs a checksum field may not sit within (CHECKSUM_EXCLUDED).</summary>
    static readonly Dictionary<string, string> Excluded = new()
    {
        ["byte_group"] = "a byte_group", ["tlv"] = "a tlv entry", ["records"] = "a records entry",
    };

    /// <summary>
    /// PS-608: `from` counts from the frame's first byte (PS-585), so a checksum field is
    /// rejected at load as a byte_group member, or within a tlv entry, a records entry or a
    /// repeat's <c>fields</c> or <c>element</c>; a repeat's trailer is allowed. The spec
    /// prototype's check_checksum_placement over the header, fields and ports of the
    /// <c>$ref</c>-spliced document; the first violation only.
    /// </summary>
    internal static void CheckPlacement(YamlMappingNode root)
    {
        foreach (var key in new[] { "header", "fields", "ports" })
            if (Placement(Get(root, key), null) is { } error)
                throw new InvalidOperationException(error);
    }

    static string? Placement(YamlNode? node, string? inside)
    {
        if (node is YamlSequenceNode seq)
        {
            foreach (var item in seq.Children)
                if (Placement(item, inside) is { } e) return e;
        }
        else if (node is YamlMappingNode map)
        {
            if (Get(map, "checksum") != null && Get(map, "type") != null && inside != null)
            {
                var name = (Get(map, "name") as YamlScalarNode)?.Value ?? "None";
                return $"Field '{name}': a checksum field cannot sit within {inside}; its `from` "
                    + "counts from the frame (PS-608)";
            }
            bool repeat = (Get(map, "type") as YamlScalarNode)?.Value == "repeat";
            foreach (var kv in map.Children)
            {
                var key = (kv.Key as YamlScalarNode)?.Value ?? "";
                if (key == "checksum") continue;
                var within = Excluded.TryGetValue(key, out var w) ? w : inside;
                if (repeat && (key == "fields" || key == "element")) within = "a repeat element";
                if (Placement(kv.Value, within) is { } e) return e;
            }
        }
        return null;
    }

    static void Walk(YamlNode? node)
    {
        if (node is YamlSequenceNode seq)
        {
            foreach (var item in seq.Children) Walk(item);
        }
        else if (node is YamlMappingNode map)
        {
            if (Get(map, "checksum") is { } spec && (Get(map, "type") != null || Get(map, "name") != null)
                && FieldError(map, spec) is { } error)
                throw new InvalidOperationException(error);
            foreach (var kv in map.Children)
                if (!(kv.Key is YamlScalarNode k && k.Value == "test_vectors"))
                    Walk(kv.Value);
        }
    }

    /// <summary>The reference's checksum_field_errors: the first failure only, in its order.</summary>
    static string? FieldError(YamlMappingNode field, YamlNode specNode)
    {
        var name = Meta.FromYaml(Get(field, "name")) is { } n ? Plain(n) : "<unnamed>";
        if (Meta.FromYaml(specNode) is not Dictionary<string, object?> spec)
            return $"The checksum of '{name}' is a mapping, such as {{algo: xor8}} (PS-585)";
        foreach (var key in spec.Keys)
            if (!Keys.Contains(key) && key != "description" && !key.StartsWith("x-", StringComparison.Ordinal))
                return $"`{key}` is not a key of the checksum of '{name}' (PS-448)";
        var type = Meta.FromYaml(Get(field, "type"));
        var width = Width(type as string);
        if (width == null)
            return $"Field '{name}': a checksum is declared on an unsigned integer type "
                + $"(u8 to u64), not {Repr(type)} (PS-585)";
        var algo = spec.GetValueOrDefault("algo");
        if (algo is not string a || !Algos.ContainsKey(a))
            return $"The checksum of '{name}': `algo` is one of {string.Join(", ", Algos.Keys)}, "
                + $"not {Repr(algo)} (PS-585)";
        if (width < Algos[a])
            return $"Field '{name}': {type} cannot hold a {a} result (PS-585)";
        if (spec.ContainsKey("seed") && a is not ("sum8" or "xor8"))
            return $"The checksum of '{name}': `seed` applies to sum8 and xor8, not {a} (PS-585)";
        if (!(spec.GetValueOrDefault("seed", 0L) is long seed && seed >= 0 && seed <= 255))
            return $"The checksum of '{name}': `seed` is a byte, 0 to 255 (PS-585)";
        if (!(spec.GetValueOrDefault("from", 0L) is long from && from >= 0 && from <= int.MaxValue))
            return $"The checksum of '{name}': `from` is a byte offset, 0 or more (PS-585)";
        if (spec.GetValueOrDefault("on_mismatch", "error") is not ("error" or "warn"))
            return $"The checksum of '{name}': `on_mismatch` is `error` or `warn` (PS-586)";
        return null;
    }

    /// <summary>What Python's str() writes for a scalar.</summary>
    static string Plain(object? v) => v switch
    {
        null => "None",
        bool b => b ? "True" : "False",
        double d => d.ToString("R", CultureInfo.InvariantCulture),
        _ => Convert.ToString(v, CultureInfo.InvariantCulture) ?? "",
    };

    /// <summary>What Python's repr() writes for a scalar.</summary>
    static string Repr(object? v) => v is string s ? "'" + s + "'" : Plain(v);

    // --- decode (PS-586) ----------------------------------------------------------------

    /// <summary>
    /// The declared checksum over <c>data[from..position]</c> against the value read, in
    /// the field's effective byte order and before any modifier. A mismatch is a decode error
    /// reported verbatim, or with <c>on_mismatch: warn</c> a warning. A range starting at or
    /// after the field covers nothing and is an error (PS-608, CR-2026-185).
    /// </summary>
    internal static void Verify(SchemaField field, byte[] data, int position, ulong carried,
        List<string> warnings)
    {
        var spec = field.Checksum!;
        if (spec.From >= position)
            throw new FieldErrorException($"Field '{field.Name}': the checksum range starts at byte "
                + $"{spec.From}, at or after the field at byte {position}, so it covers nothing (PS-608)");
        var computed = Compute(spec.Algo, data.AsSpan(spec.From, position - spec.From), spec.Seed);
        if (computed == carried) return;
        var message = $"Field '{field.Name}': {spec.Algo} over bytes {spec.From} to {position - 1} is "
            + $"0x{computed:X}, but the frame carries 0x{carried:X} (PS-586)";
        if (spec.OnMismatch == "warn") warnings.Add(message);
        else throw new FieldErrorException(message);
    }

    // --- encode (PS-587) ----------------------------------------------------------------

    /// <summary>A checksum whose covered range is not all written when it is reached.</summary>
    internal static string UnwrittenMessage(string? name) =>
        $"Field '{name}': the bytes its checksum covers are not all written when it is reached (PS-587)";

    /// <summary>
    /// The reference's fill_checksums: each checksum placed in <paramref name="frame"/>,
    /// earliest first so an outer checksum covers an inner one's value, computed over the
    /// finished bytes from its <c>from</c> to the byte before it. Each one placed is struck
    /// from <paramref name="open"/>, so what remains there never reached the frame.
    /// </summary>
    internal static void Fill(byte[] frame, IEnumerable<ChecksumSlot> placed, List<SchemaField> open)
    {
        foreach (var slot in placed.OrderBy(s => s.Position))
        {
            var spec = slot.Field.Checksum!;
            if (spec.From > slot.Position)
                throw new FieldErrorException(UnwrittenMessage(slot.Field.Name));
            if (spec.From == slot.Position)     // PS-608 (CR-2026-185): it covers nothing
                throw new FieldErrorException($"Field '{slot.Field.Name}': the checksum range starts "
                    + $"at byte {spec.From}, where the field is, so it covers nothing (PS-608)");
            var value = Compute(spec.Algo, frame.AsSpan(spec.From, slot.Position - spec.From), spec.Seed);
            int size = Width(slot.Field.RawType)!.Value / 8;
            for (int i = 0; i < size; i++)
            {
                int significance = slot.Little ? i : size - 1 - i;
                frame[slot.Position + i] = significance < 8 ? (byte)(value >> (8 * significance)) : (byte)0;
            }
            int at = open.FindIndex(f => ReferenceEquals(f, slot.Field));
            if (at >= 0) open.RemoveAt(at);
        }
    }
}

/// <summary>A checksum placeholder in an encoded frame: its position, field and byte order.</summary>
internal sealed record ChecksumSlot(int Position, SchemaField Field, bool Little);
