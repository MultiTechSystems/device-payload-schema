// Copyright (c) 2024-2026 Multitech Systems, Inc.
// SPDX-License-Identifier: MIT

using System.Globalization;
using System.Text;
using YamlDotNet.RepresentationModel;

namespace PayloadSchema;

public static class SchemaDecoder
{
    public static Dictionary<string, object?> Decode(PayloadSchemaDefinition schema, byte[] data)
        => Run(schema, data, null, null).result;

    /// <summary>
    /// Decode with port-based schema selection.
    ///
    /// Pass <paramref name="direction"/> where the caller knows which way the message
    /// was travelling. A message travelling the way the selected entry says it does not
    /// is not decoded at all: uplink bytes read through downlink field definitions
    /// produce numbers with no relationship to what the device measured, and nothing in
    /// the output would mark them as such, so nothing is returned (PS-288). Omitting it
    /// skips the check and does not satisfy PS-021 (PS-290).
    /// </summary>
    public static Dictionary<string, object?> DecodeWithPort(PayloadSchemaDefinition schema, byte[] data, int fPort,
        string? direction = null)
        => Run(schema, data, fPort, direction).result;

    /// <summary>
    /// The interpreter output: the decoded result with its `_meta` as the last key
    /// (PS-174, PS-175, PS-180; CR-2026-096). <see cref="Decode"/> and
    /// <see cref="DecodeWithPort"/> are unchanged and never carry it.
    ///
    /// The input context (PS-495) is the FPort - selecting the port as
    /// <see cref="DecodeWithPort"/> does, null meaning none - the receive time and the
    /// device EUI. A failed decode throws exactly as the decode does and produces no
    /// `_meta` (PS-497); so does a devEUI or recvTime that is present but malformed.
    /// Mirrors interpret in tools/schema_interpreter.py.
    /// </summary>
    public static Dictionary<string, object?> Interpret(PayloadSchemaDefinition schema, byte[] payload,
        InputContext context, string? direction = null)
    {
        var (result, ctx, fields) = Run(schema, payload, context.FPort, direction);

        var meta = new Dictionary<string, object?>();
        var root = schema.Root;
        if (root != null && Meta.FromYaml(FindKey(root, "name")) is { } schemaName)
            meta["schema"] = schemaName;
        if (root != null && FindKey(root, "version") is { } version)
            meta["version"] = Meta.FromYaml(version);
        // PS-496, PS-495: a context that is present but malformed is refused, not
        // silently dropped - a missing device_eui would read as "not supplied".
        if (context.DevEUI != null)
        {
            var eui = Meta.NormaliseDevEui(context.DevEUI)
                ?? throw new InvalidOperationException(
                    $"devEUI {Meta.PyRepr(context.DevEUI)} is not 16 hexadecimal digits (PS-496)");
            meta["device_eui"] = eui;
        }
        if (context.RecvTime != null)
        {
            var rxTime = Meta.RxTimeSeconds(context.RecvTime)
                ?? throw new InvalidOperationException(
                    $"recvTime {Meta.PyRepr(context.RecvTime)} is not an "
                    + "ISO 8601 time or a number of seconds (PS-495)");
            meta["rx_time"] = rxTime;
        }
        if (context.FPort != null)
            meta["fPort"] = (long)context.FPort.Value;                 // PS-340, PS-342: as supplied

        var declared = Meta.Declarations(fields.Select(f => f.Node).OfType<YamlMappingNode>());
        var entries = new Dictionary<string, object?>();
        foreach (var key in result.Keys)
        {
            if (key.StartsWith('_')) continue;                          // `_quality`, `_warnings`
            var source = ctx.Producers.TryGetValue(key, out var producer) && producer.Node != null
                ? producer.Node
                : declared.GetValueOrDefault(key);
            entries[key] = source != null ? Meta.FieldMeta(source) : new Dictionary<string, object?>();
        }
        meta["fields"] = entries;

        return new Dictionary<string, object?>(result) { ["_meta"] = meta };
    }

    static YamlNode? FindKey(YamlMappingNode map, string key)
    {
        foreach (var kv in map.Children)
            if (kv.Key is YamlScalarNode s && s.Value == key)
                return kv.Value;
        return null;
    }

    /// <summary>
    /// One decode: the result, the context it ran in (for `_meta`'s producers) and the
    /// field list selected. With no FPort a ports schema selects nothing (PS-459, PS-460):
    /// it used to decode the empty top-level list and return {} with success.
    /// </summary>
    static (Dictionary<string, object?> result, DecodeContext ctx, List<SchemaField> fields) Run(
        PayloadSchemaDefinition schema, byte[] data, int? fPort, string? direction)
    {
        List<SchemaField> fields;
        if (fPort == null)
        {
            if (schema.Ports is { Count: > 0 })
                throw new InvalidOperationException(
                    $"no FPort was supplied, and schema '{schema.Name}' selects its fields by port (PS-459)");
            fields = schema.Fields;
        }
        else
        {
            SchemaDirection.Check(schema, fPort.Value, direction);
            fields = ResolveFields(schema, fPort.Value);
        }
        var ctx = new DecodeContext(data, schema.Endian) { RepeatOnlyNames = schema.RepeatOnlyNames };
        var result = new Dictionary<string, object?>();

        MergeTo(result, DecodeFields(fields, ctx, schema));
        ReportLeftover(ctx);

        // PS-427, PS-428: an omitted reading joins `_quality` only where it is produced.
        ctx.FinishQuality();
        if (ctx.Quality.Count > 0)
            result["_quality"] = new Dictionary<string, string>(ctx.Quality);
        // Warnings were collected and never reported, so an unknown TLV tag had nowhere
        // to surface (PS-301). Reported the way _quality is.
        if (ctx.Warnings.Count > 0)
            result["_warnings"] = new List<string>(ctx.Warnings);

        return (result, ctx, fields);
    }

    /// <summary>
    /// PS-472: bytes after the last field of the selected list are reported, not dropped
    /// in silence. The decode is reported as it would be otherwise; this only warns, with
    /// the offset of the first byte not decoded and the count to the end. A frame from
    /// newer firmware and a frame decoded with the wrong layout both used to look
    /// complete. A failed decode throws before reaching here, and a PS-302 warning is
    /// this warning for its bytes. Mirrors _report_leftover in tools/schema_interpreter.py.
    /// </summary>
    static void ReportLeftover(DecodeContext ctx)
    {
        var left = ctx.Data.Length - ctx.Offset;
        if (left <= 0 || ctx.LeftoverReported) return;
        ctx.Warnings.Add($"{left} byte(s) after the last field left undecoded, from offset {ctx.Offset} (PS-472)");
    }

    static List<SchemaField> ResolveFields(PayloadSchemaDefinition schema, int fPort)
    {
        if (schema.Ports == null)
            return schema.Fields;

        var portKey = fPort.ToString();
        if (schema.Ports.TryGetValue(portKey, out var pd))
            return pd.Fields;
        if (schema.Ports.TryGetValue("default", out var dpd))
            return dpd.Fields;

        throw new InvalidOperationException($"No port definition for fPort {fPort} and no default in schema '{schema.Name}'");
    }

    static Dictionary<string, object?> DecodeFields(List<SchemaField> fields, DecodeContext ctx, PayloadSchemaDefinition? schema)
    {
        var result = new Dictionary<string, object?>();

        foreach (var field in fields)
        {
            // PS-383: a repeat's trailer is decoded from the reserved bytes before its
            // first element, so its names are bound for the elements and later fields,
            // and it is reported beside the repeat, not inside it.
            if (field.Type == FieldType.Repeat && field.Trailer is { Count: > 0 })
            {
                var trailerAt = ctx.Data.Length - field.Reserve;
                if (trailerAt < ctx.Offset)
                    throw new InvalidOperationException($"repeat '{field.Name}' reserves {field.Reserve} "
                        + $"byte(s) but {ctx.Remaining} remain at offset {ctx.Offset} (PS-351)");
                var resume = ctx.Offset;
                ctx.Offset = trailerAt;
                MergeTo(result, DecodeFields(field.Trailer, ctx, schema));
                ctx.Offset = resume;
            }

            // PS-402, PS-403: an optional field is decoded where its bytes remain and is
            // absent where none do. Every later field is optional too (PS-404), so once one
            // is absent the list ends. Some bytes but too few is an error: an optional
            // field is never partly read.
            if (field.Optional)
            {
                var remaining = ctx.Remaining;
                if (remaining <= 0) break;
                var size = FixedElementSize(field.Type == FieldType.Object
                    ? field.Fields : new List<SchemaField> { field });
                if (size > 0 && remaining < size)
                    throw new InvalidOperationException($"Error decoding {(string.IsNullOrEmpty(field.Name) ? "?" : field.Name)}: "
                        + $"optional field takes {size} byte(s) but {remaining} remain at offset {ctx.Offset} (PS-403)");
            }

            // $ref
            if (field.Ref2 != null && schema?.Definitions != null)
            {
                var refResult = ResolveRef(field.Ref2, ctx, schema);
                MergeTo(result, refResult);
                foreach (var kv in refResult)
                    ctx.Variables[kv.Key] = kv.Value;
                continue;
            }

            // Byte group
            if (field.ByteGroup.Count > 0)
            {
                var bgResult = DecodeByteGroup(field, ctx);
                MergeTo(result, bgResult);
                foreach (var kv in bgResult)
                    ctx.Variables[kv.Key] = kv.Value;
                continue;
            }

            // TLV
            if (field.Type == FieldType.TLV)
            {
                MergeTo(result, DecodeTLV(field, ctx));
                continue;
            }

            // TLV inline
            if (field.TLVInline != null)
            {
                MergeTo(result, DecodeTLV(field.TLVInline, ctx));
                continue;
            }

            // Flagged
            if (field.Flagged != null)
            {
                var flaggedResult = DecodeFlagged(field.Flagged, ctx);
                MergeTo(result, flaggedResult);
                foreach (var kv in flaggedResult)
                    ctx.Variables[kv.Key] = kv.Value;
                continue;
            }

            // Match inline
            if (field.MatchInline != null)
            {
                var matchResult = DecodeMatch(field.MatchInline, ctx);
                if (matchResult is Dictionary<string, object?> matchMap)
                {
                    MergeTo(result, matchMap);
                    foreach (var kv in matchMap)
                        ctx.Variables[kv.Key] = kv.Value;
                }
                continue;
            }

            // Top-level match (type: match with on:) -- merge results
            if (field.Type == FieldType.Match && string.IsNullOrEmpty(field.Name))
            {
                var matchResult = DecodeMatch(field, ctx);
                if (matchResult is Dictionary<string, object?> matchMap)
                {
                    MergeTo(result, matchMap);
                    foreach (var kv in matchMap)
                        ctx.Variables[kv.Key] = kv.Value;
                }
                continue;
            }

            var value = DecodeField(field, ctx, schema);

            if (value is AbsentReading absent)
            {
                // PS-427, PS-428: no reading - not reported and not bound. An internal
                // field has no output to mark.
                if (!string.IsNullOrEmpty(field.Name) && !field.Name.StartsWith("_"))
                    ctx.MarkAbsent(field, absent.Why);
                continue;
            }

            if (ReferenceEquals(value, Omitted))
            {
                // A mapping lookup with no entry and no default: the device
                // reported nothing this schema can name (PS-269).
                continue;
            }

            if (value != null && !string.IsNullOrEmpty(field.Name))
            {
                // A leading underscore marks an internal field: it becomes a
                // variable later fields can reference, but is not reported.
                // Without this an intermediate used to combine two words appeared
                // in the decoded output.
                if (!field.Name.StartsWith("_"))
                {
                    var outputName = ResolveFieldName(field, ctx);
                    if (outputName != field.Name)
                        ctx.Produced(field, outputName);                      // PS-492
                    result[outputName] = value;
                }
                // Keyed by the schema-level name in Variables so $references keep
                // working when name_from is in play (PS-267).
                ctx.Variables[field.Name] = value;
                if (field.ValidRange is { Length: >= 2 })
                    ctx.CheckValidRange(value, field);
            }
        }

        return result;
    }

    static Dictionary<string, object?> ResolveRef(string refPath, DecodeContext ctx, PayloadSchemaDefinition schema)
    {
        if (!refPath.StartsWith("#/definitions/"))
            throw new InvalidOperationException($"Unsupported $ref format: {refPath}");

        var defName = refPath["#/definitions/".Length..];
        if (schema.Definitions == null || !schema.Definitions.TryGetValue(defName, out var def))
            throw new InvalidOperationException($"Definition not found: {defName}");

        return DecodeFields(def.Fields, ctx, schema);
    }

    static Dictionary<string, object?> DecodeByteGroup(SchemaField field, DecodeContext ctx)
    {
        int size = field.Size > 0 ? field.Size : 1;
        var data = ctx.Read(size);
        var result = new Dictionary<string, object?>();

        // PS-364: the group's bytes are one value in its effective byte order - its own
        // `endian` where declared - and a member's bit positions refer to that value. A
        // bool member is one bit of it.
        var groupEndian = field.GroupEndian ?? ctx.Endian;
        ulong rawVal = Helpers.DecodeUint(data, groupEndian);

        foreach (var subfield in field.ByteGroup)
        {
            ctx.Produced(subfield);                                            // PS-490
            object raw;
            var bitRange = Helpers.ParseBitRange(subfield.RawType);
            if (bitRange != null)
                raw = ExtractRange(rawVal, subfield.SignedBits, bitRange.Value.start,
                    bitRange.Value.end - bitRange.Value.start + 1);
            else if (subfield.Type == FieldType.Bool)
                raw = ((rawVal >> subfield.Bit) & 1) == 1;
            else
                raw = (double)rawVal;

            var value = ApplyPostRead(raw, subfield, ctx);
            if (ReferenceEquals(value, Omitted) || string.IsNullOrEmpty(subfield.Name))
                continue;
            if (subfield.Name.StartsWith("_"))
            {
                // Internal: bound for later references, not reported.
                ctx.Variables[subfield.Name] = value;
                continue;
            }
            result[subfield.Name] = value;
        }

        return result;
    }

    static Dictionary<string, object?> DecodeFlagged(FlaggedDef fd, DecodeContext ctx)
    {
        if (!ctx.Variables.TryGetValue(fd.Field, out var flagsVal))
            throw new InvalidOperationException($"Flagged field reference not found: {fd.Field}");

        var (_, flags) = Helpers.ToInt(flagsVal);
        var result = new Dictionary<string, object?>();

        foreach (var group in fd.Groups)
        {
            int isPresent = (flags >> group.Bit) & 1;
            if (isPresent == 0) continue;
            // A member omitted under PS-427/PS-428 waits to learn whether `_quality` is
            // produced, as _decode_flagged's members do in the reference.
            ctx.FlaggedDepth++;
            try { MergeTo(result, DecodeFields(group.Fields, ctx, null)); }
            finally { ctx.FlaggedDepth--; }
        }

        return result;
    }

    static object? DecodeField(SchemaField field, DecodeContext ctx, PayloadSchemaDefinition? schema)
    {
        // PS-490: recorded where the field is decoded, as _decode_field does. An object's
        // members and a repeat's elements are another level of output, described by the
        // declaration's nested entries (PS-371, PS-481), not by what they wrote.
        ctx.Produced(field);
        if (field.Type is not (FieldType.Object or FieldType.Repeat))
            return DecodeFieldBody(field, ctx, schema);
        ctx.MetaDepth++;
        try { return DecodeFieldBody(field, ctx, schema); }
        finally { ctx.MetaDepth--; }
    }

    static object? DecodeFieldBody(SchemaField field, DecodeContext ctx, PayloadSchemaDefinition? schema)
    {
        // A negative Length is the `remaining` sentinel and is passed through to
        // Read, which resolves it; only an absent length infers from the type.
        int length = field.LengthRef != null && field.Type is FieldType.Bytes or FieldType.Ascii
                         or FieldType.Hex or FieldType.Base64 or FieldType.Skip
            ? ResolveLengthRef(field, ctx)
            : field.Length != 0 ? field.Length : Helpers.InferLengthFromType(field.Type);
        string endian = field.Endian ?? ctx.Endian;
        object? value = null;
        // The integer as read, before any `encoding` is decoded, for PS-427.
        object? read = null;

        switch (field.Type)
        {
            // The cast to double used to happen here, which discarded the exact value
            // before anything could report it: a u64 of 2^64-1 came back as
            // 1.8446744073709552E+19. The integer is kept, and ApplyModifiers below
            // converts only where a modifier makes the field a `number` (PS-293, PS-294).
            // The type fixes the unit order and the byte order within a unit, so the
            // endian setting is deliberately not consulted (PS-272).
            case FieldType.U32LE16 or FieldType.S32LE16 or FieldType.F32LE16
                or FieldType.U32BE16LE or FieldType.S32BE16LE or FieldType.F32BE16LE:
            {
                value = Wave4.ReadWordOrdered(field.Type, ctx.Read(4));
                break;
            }

            case FieldType.UFlt16 or FieldType.SFlt16 or FieldType.SFlt24:
            {
                // The MCCI minifloats (CR-2026-063): a word in the field's byte order.
                var decoded = Wave4.DecodeMinifloat(field.Type,
                    Helpers.DecodeUint(ctx.Read(Wave4.MinifloatSize(field.Type)), endian));
                if (decoded == null) return Omitted;    // no value JSON can carry (PS-419)
                value = decoded.Value;
                break;
            }

            case FieldType.U8:
            case FieldType.U16:
            case FieldType.U24:
            case FieldType.U32:
            case FieldType.U64:
            {
                var data = ctx.Read(length);
                var unsigned = Helpers.DecodeUint(data, endian);
                read = unsigned;
                // PS-427: compared before the code is decoded, so a sentinel that is no
                // valid codeword (0xFF under bcd) is absent rather than an error.
                if (field.Encoding != null && field.Sentinel is { Count: > 0 } && SentinelHit(field, read))
                    return AbsentReading.Sentinel;
                value = field.Encoding != null
                    // Applied to the integer read, before the modifiers (PS-422).
                    ? Wave4.DecodeEncoding(unsigned, field.Encoding, length, field.Name)
                    : unsigned;
                break;
            }

            case FieldType.S8:
            case FieldType.S16:
            case FieldType.S24:
            case FieldType.S32:
            case FieldType.S64:
            {
                var data = ctx.Read(length);
                value = Helpers.DecodeSint(data, endian);
                break;
            }

            case FieldType.F16:
            case FieldType.F32:
            case FieldType.F64:
            {
                int size = field.Type switch
                {
                    FieldType.F16 => 2,
                    FieldType.F32 => 4,
                    FieldType.F64 => 8,
                    _ => 4
                };
                var data = ctx.Read(size);
                value = Helpers.DecodeFloat(data, size, endian);
                break;
            }

            case FieldType.Bool:
            {
                var data = ctx.Peek(1);
                value = Helpers.DecodeBits(data[0], field.Bit, 1) != 0;
                if (field.Consume > 0)
                    ctx.Read(field.Consume);
                break;
            }

            case FieldType.Bits:
            {
                // Read the whole base width, not just the first byte: a range wider
                // than a byte (u24[0:11] for a packed 12-bit humidity) decoded from
                // byte zero alone and reported a value with no error.
                int baseBytes = field.BitBaseBytes > 0 ? field.BitBaseBytes : 1;
                var data = ctx.Peek(baseBytes, field.ByteOffset);
                // PS-059 (CR-2026-052): assembled in the field's effective byte order,
                // which was big-endian whatever the schema said.
                ulong baseValue = Helpers.DecodeUint(data, endian);
                int bits = field.BitCount > 0 ? field.BitCount : 1;
                value = ExtractRange(baseValue, field.SignedBits, field.BitOffset, bits);
                // An explicit range does not advance the cursor by itself: several
                // fields share one byte and the last of them declares `consume`.
                if (field.Consume > 0)
                    ctx.Read(field.Consume);
                break;
            }

            case FieldType.String:
            {
                // A literal reads no bytes. Only a declared length is a read: `length`
                // here already carries a default inferred from the type, which made
                // `{type: string, value: "ppm"}` a one-byte read that shifted
                // everything after it.
                // `string` is only ever a literal: a string read from the payload is
                // `ascii` (PS-361), and a literal reads no bytes (PS-357).
                if (field.Value == null)
                    throw new InvalidOperationException($"field '{field.Name}': type string declares "
                        + "no value; a string read from the payload is type ascii (PS-361)");
                value = field.Value;
                break;
            }

            case FieldType.Ascii:
            {
                var data = ctx.Read(length);
                value = Encoding.ASCII.GetString(data).TrimEnd('\0');
                break;
            }

            case FieldType.Enum:
            {
                int baseLen = field.Base switch
                {
                    "u16" or "s16" => 2,
                    "u32" or "s32" => 4,
                    _ => 1
                };
                var data = ctx.Read(baseLen);
                int intVal = (int)Helpers.DecodeUint(data, endian);
                if (field.Values != null && field.Values.TryGetValue(intVal, out var enumStr))
                    value = enumStr;
                // An unmapped value takes the declared default (PS-068). Returning
                // the raw integer ignored the default the schema asked for.
                else if (field.EnumDefault != null)
                    value = field.EnumDefault;
                else
                    value = (double)intVal;
                break;
            }

            case FieldType.Hex:
            {
                var data = ctx.Read(length);
                value = Convert.ToHexString(data).ToLowerInvariant();
                break;
            }

            case FieldType.Skip:
            {
                ctx.Read(length);
                return null;
            }

            case FieldType.Bytes:
            {
                var data = ctx.Read(length);
                value = Helpers.FormatBytes(data.ToArray(), field.Format, field.Separator);
                break;
            }

            case FieldType.Repeat:
            {
                value = DecodeRepeat(field, ctx, schema);
                break;
            }

            case FieldType.BitfieldString:
            {
                var data = ctx.Read(length);
                ulong intVal = Helpers.DecodeUint(data, endian);
                string delimiter = field.Delimiter ?? ".";
                string prefix = field.Prefix ?? "";
                var partStrs = new List<string>();
                foreach (var part in field.Parts)
                {
                    if (part.Count < 2) continue;
                    var (_, bitOff) = Helpers.ToInt(part[0]);
                    var (_, bitLen) = Helpers.ToInt(part[1]);
                    string format = part.Count >= 3 && part[2] is string f ? f : "decimal";
                    ulong mask = (1UL << bitLen) - 1;
                    ulong raw = (intVal >> bitOff) & mask;
                    // PS-430: hex lower case, hex:upper upper case; any other format is
                    // refused when the schema is loaded.
                    partStrs.Add(format switch
                    {
                        "hex" => raw.ToString("x"),
                        "hex:upper" => raw.ToString("X"),
                        _ => raw.ToString(),
                    });
                }
                value = prefix + string.Join(delimiter, partStrs);
                break;
            }

            case FieldType.Number:
            {
                value = DecodeNumber(field, ctx);
                if (field.IntegerResult && value is double d && !double.IsNaN(d) && !double.IsInfinity(d))
                {
                    // PS-388: a fractional part is an error, never truncated or rounded.
                    if (d != Math.Round(d) || double.IsInfinity(d))
                        throw new InvalidOperationException(
                            $"{field.Name}: type integer but the computed value is {d}; " +
                            "add `idiv` to truncate or a {op: round} transform stage");
                    value = (long)d;
                }
                break;
            }

            case FieldType.UDec:
            case FieldType.SDec:
            {
                // Nibble-decimal (PS-330): upper nibble the whole part, a 4-bit
                // two's-complement value for sdec; lower nibble the tenths.
                int b = ctx.Read(1)[0];
                int whole = b >> 4;
                if (field.Type == FieldType.SDec && whole >= 8) whole -= 16;
                value = whole + (b & 0x0F) * 0.1;
                break;
            }

            case FieldType.Base64:
            {
                value = Convert.ToBase64String(ctx.Read(length));
                break;
            }

            case FieldType.Object:
            {
                value = DecodeFields(field.Fields, ctx, schema);
                break;
            }

            case FieldType.Match:
            {
                value = DecodeMatch(field, ctx);
                break;
            }

            case FieldType.TLV:
            {
                return DecodeTLV(field, ctx);
            }

            default:
                throw new InvalidOperationException($"Unknown field type: {field.Type} ({field.RawType})");
        }

        // PS-427: the integer read, before any modifier and before any encoding is
        // decoded, is a sentinel: the field is absent.
        if (field.Sentinel is { Count: > 0 } && SentinelHit(field, read ?? value))
            return AbsentReading.Sentinel;
        return ApplyPostRead(value, field, ctx, fieldRules: true);
    }

    /// <summary>A field read and found to carry no reading (PS-427, PS-428).</summary>
    internal sealed class AbsentReading
    {
        public string Why { get; }
        AbsentReading(string why) => Why = why;
        public static readonly AbsentReading Sentinel = new("absent");
        public static readonly AbsentReading OutOfRange = new("out_of_range");
    }

    /// <summary>Whether the integer read is one of the field's sentinels (PS-427).</summary>
    static bool SentinelHit(SchemaField field, object? raw)
    {
        long n;
        switch (raw)
        {
            case ulong u when u <= long.MaxValue: n = (long)u; break;
            case long l: n = l; break;
            case int i: n = i; break;
            // A bit range is read as a double here; it is an integer in the reference.
            case double d when field.Type == FieldType.Bits && d == Math.Floor(d): n = (long)d; break;
            default: return false;
        }
        return field.Sentinel!.Contains(n);
    }

    /// <summary>
    /// PS-464, PS-465: `length` naming a preceding field takes that field's decoded value
    /// as the count. An unresolved name is an error naming it.
    /// </summary>
    static int ResolveLengthRef(SchemaField field, DecodeContext ctx)
    {
        var name = field.LengthRef!;
        if (!ctx.Variables.TryGetValue(name, out var value))
            throw new InvalidOperationException(
                $"length names '{name}', which is not a field decoded before this one (PS-465)");
        var (ok, numeric) = value is bool ? (false, 0.0) : Helpers.ToFloat64(value);
        if (!ok || numeric < 0 || numeric != Math.Floor(numeric))
            throw new InvalidOperationException(
                $"length names '{name}', whose value {value} is not a byte count (PS-464)");
        return (int)numeric;
    }

    /// <summary>
    /// What happens to a value once it has been read: modifiers, then lookup, then the
    /// field's variable. Shared with byte_group members, which skipped all three - arwin
    /// lrs10701's temperature, u32[0:9] with div and add inside a group, came back as
    /// the raw 1023 where Python, Java and Go gave 72.3.
    /// </summary>
    static object? ApplyPostRead(object? value, SchemaField field, DecodeContext ctx,
        bool fieldRules = false)
    {
        // A failed guard's `else` is reported as declared: no modifier, stage or lookup
        // (PS-444). The lookup used to index it, so `else: 7` came out as the eighth label.
        bool asDeclared = false;
        ctx.GuardElse.Remove(field);
        ctx.PreLookup.Remove(field);
        if (value is DeclaredElse declared)
        {
            value = declared.Value;
            asDeclared = true;
            // The `else` ends the sequence (PS-443): not compared with valid_range either.
            ctx.GuardElse.Add(field);
            // `type: integer` reports an integral else as an integer, as it did before.
            if (field.IntegerResult && declared.Value == Math.Floor(declared.Value)
                && !double.IsInfinity(declared.Value))
                value = (long)declared.Value;
        }

        // Apply modifiers, skipping a Number whose value came from a ref or a
        // compute - DecodeNumber already applied its stages, so doing it again here
        // doubles them. The ref case was already skipped; compute was not, so a
        // compute field carrying a transform had it run twice, and
        // decentlab/dl-blg's voltage_ratio came out as -0.4999999996 where the
        // vendor decoder says 0.0064094: its `div: 16777216` and `add: -0.5` were
        // each applied a second time. Nothing caught it - that schema had no test
        // vectors at all.
        if (field.Type == FieldType.Number && (field.Ref != null || field.Compute != null))
        {
            // already handled in DecodeNumber
        }
        else if (ReportsAsInteger(field))
        {
            // PS-293, PS-294: an integer-typed field carrying no modifier keeps the
            // ulong or long it was read as, so a u64 above 2^53 survives.
        }
        else
        {
            var (ok, numVal) = Helpers.ToFloat64(value);
            if (ok)
            {
                numVal = ApplyModifiers(numVal, field);
                value = numVal;
            }
        }

        // A value the arithmetic could not produce - a zero divisor (PS-100), the log of
        // a non-positive number (PS-117) - is absent, and NaN is never reported (PS-282).
        // Checked before the lookup, which would read NaN as some index.
        if (value is double nd && (double.IsNaN(nd) || double.IsInfinity(nd)))
            return Omitted;

        // PS-475: valid_range compares the value after the arithmetic and before the
        // lookup. A looked-up field is compared on its number, so it is stashed for the
        // quality check; and `out_of_range: omit` is decided here, because an omitted
        // value takes no further step (PS-443) - it is not looked up, so an index its
        // sequence lacks is no error. Compared after the lookup, a label was never a
        // number: every looked-up value read "good" and none was ever omitted.
        if (!asDeclared && field.ValidRange is { Length: >= 2 } bounds
            && value is not bool && Helpers.ToFloat64(value) is (true, var rangeValue))
        {
            if (field.Lookup != null)
                ctx.PreLookup[field] = rangeValue;
            // PS-428: `out_of_range: omit` makes a value outside valid_range no reading -
            // not reported, not bound. Applied on the field-list path, not to byte_group
            // members, as in the reference; and to an internal field only where it is
            // computed.
            if (fieldRules && field.OutOfRangeOmit && bounds.Length == 2
                && (field.Type == FieldType.Number || !field.Name.StartsWith("_"))
                && !(bounds[0] <= rangeValue && rangeValue <= bounds[1]))
                return AbsentReading.OutOfRange;
        }

        // Apply lookup. A mapping is matched on its keys, which need not start at
        // zero or be contiguous (PS-268). An unmatched value omits the field rather
        // than reporting the raw integer under a name that promises a label, unless
        // a default is declared (PS-269).
        if (field.Lookup != null && !asDeclared)
        {
            var (ok, numVal) = Helpers.ToFloat64(value);
            if (ok)
            {
                // A value with a fraction matches no key: ToInt would truncate 2.5 to the
                // key 2. An integral double such as 7.0 is the key 7.
                int intVal = (int)numVal;
                bool integral = numVal == Math.Round(numVal) && !double.IsInfinity(numVal);
                // A computed value reaches the lookup after its arithmetic (PS-443), so it
                // may have a fraction. That is no index of a sequence, and an error
                // (PS-105); it used to be truncated, reporting 1.5 as index 1.
                if (!integral && field.LookupIsSequence)
                    throw new InvalidOperationException(
                        $"lookup index {numVal.ToString(CultureInfo.InvariantCulture)} is not an index "
                        + $"of a {field.Lookup.Count}-entry sequence (PS-105)");
                var template = Wave5.Template(field);
                if (integral && field.Lookup.TryGetValue(intVal, out var lookupStr))
                    value = lookupStr;
                else if (template != null)
                    // PS-406: the default names the value it could not map.
                    value = template.Replace(Wave5.ValueToken, Wave5.FormatLookupValue(numVal));
                else if (field.LookupDefault != null)
                    value = field.LookupDefault;
                else if (field.LookupIsSequence)
                    // An out-of-bounds index into a sequence is an error (PS-105), not
                    // the raw value: the payload does not match the schema's shape.
                    // A mapping gap is the different case and omits the field (PS-269).
                    throw new InvalidOperationException(
                        $"lookup index {intVal} out of bounds for {field.Lookup.Count} entries");
                else
                    return Omitted;
            }
        }

        // Store variable
        if (field.Var != null)
            ctx.Variables[field.Var] = value;

        return value;
    }

    /// <summary>
    /// Applies a transform array in list order. Each stage carries exactly one
    /// operation, which the parser enforces (PS-452); a stage may name one, as
    /// {op: round, decimals: N}.
    /// </summary>

    /// <summary>
    /// Signals that a computed field is absent because its divisor was zero (PS-278).
    /// A distinct NaN payload, so it travels the existing double-valued compute path.
    /// </summary>
    internal static readonly double ComputeOmitted = BitConverter.Int64BitsToDouble(0x7ff8000000000abcL);

    internal static bool IsComputeOmitted(double v) =>
        BitConverter.DoubleToInt64Bits(v) == 0x7ff8000000000abcL;

    /// <summary>
    /// Integer division rounded toward negative infinity (PS-276).
    ///
    /// Corrected on the integers deliberately: (long)Math.Floor((double)a / b) is wrong
    /// above 2^53, where a long is not exactly representable as a double - for
    /// a = 2^53+1, b = 1 it yields 9007199254740992 rather than 9007199254740993.
    /// </summary>
    static long FloorDiv(long a, long b)
    {
        long q = a / b;
        if ((a % b != 0) && ((a < 0) != (b < 0))) q--;
        return q;
    }

    /// <summary>
    /// The remainder matching FloorDiv, so a == FloorDiv(a,b)*b + FloorMod(a,b) holds
    /// for every combination of signs (PS-277). Exact for the full long range.
    /// </summary>
    static long FloorMod(long a, long b) => ((a % b) + b) % b;

    /// <summary>
    /// Rounds to <paramref name="decimals"/> places, half-to-even, on the stored value
    /// rather than on value*10^decimals - see the note at the call site for why.
    /// </summary>
    static double RoundHalfEvenDecimal(double value, int decimals)
    {
        if (double.IsNaN(value) || double.IsInfinity(value)) return value;
        if (decimals < 0) decimals = 0;
        var text = value.ToString("F" + decimals, System.Globalization.CultureInfo.InvariantCulture);
        return double.TryParse(text, System.Globalization.NumberStyles.Float,
            System.Globalization.CultureInfo.InvariantCulture, out var rounded)
            ? rounded
            : value;
    }

    /// <summary>
    /// Round with a tie going away from zero (PS-390 `ties: away`), on the stored value.
    /// The long fixed expansion is exact, so a tie is recognised only where there is one:
    /// 78.125 is a tie, while 2.355 is stored just below one.
    /// </summary>
    /// <summary>The bytes one element always takes, or 0 where it varies (PS-344a).</summary>
    /// <summary>
    /// The bytes one tag component takes: its <c>length</c> where declared, else its type's
    /// width. This read <c>length</c> defaulting to 1, so a u16 component was read as one
    /// byte where the encoder, Python and the generated codec take two.
    /// </summary>
    internal static int TagFieldWidth(SchemaField tf) =>
        tf.Length > 0 ? tf.Length : Helpers.InferLengthFromType(tf.Type);

    internal static int FixedElementSize(List<SchemaField> fields)
    {
        int total = 0;
        foreach (var f in fields)
        {
            switch (f.Type)
            {
                case FieldType.Number:
                    continue;
                case FieldType.String when f.Value != null:
                    continue;
                case FieldType.Bits or FieldType.Bool:
                    total += f.Consume;
                    continue;
                case FieldType.Bytes or FieldType.Ascii or FieldType.Hex or FieldType.Base64 or FieldType.Skip:
                    if (f.Length <= 0) return 0;
                    total += f.Length;
                    continue;
                case FieldType.UDec or FieldType.SDec:
                    total += 1;
                    continue;
                case FieldType.U8 or FieldType.U16 or FieldType.U24 or FieldType.U32 or FieldType.U64
                    or FieldType.S8 or FieldType.S16 or FieldType.S24 or FieldType.S32 or FieldType.S64
                    or FieldType.U32LE16 or FieldType.S32LE16 or FieldType.F16 or FieldType.F32 or FieldType.F64:
                    total += Helpers.InferLengthFromType(f.Type);
                    continue;
                default:
                    return 0;
            }
        }
        return total;
    }

    /// <summary>The PS-344 error: the repeat, reported as a ragged tail.</summary>
    static InvalidOperationException RaggedTailError(SchemaField field, int remaining, int elementSize, int offset)
        => new($"repeat '{field.Name}' ends in a ragged tail: {remaining} byte(s) at offset {offset}"
            + (elementSize > 0 ? $", fewer than the {elementSize} an element takes" : "") + " (PS-343)");

    /// <summary>The PS-396 error: the repeat, its limit and the payload left unparsed.</summary>
    static InvalidOperationException RepeatLimitError(SchemaField field, int limit, string mode, int offset, int end)
        => new($"repeat '{field.Name}' exceeds its max of {limit} element(s) ({mode}); "
            + $"{end - offset} byte(s) at offset {offset} left unparsed (PS-396)");

    /// <summary>Bits of a base value, sign-extended from the range's width for an sN base (PS-353).</summary>
    static double ExtractRange(ulong baseValue, bool signed, int start, int bits)
    {
        ulong mask = bits >= 64 ? ulong.MaxValue : (1UL << bits) - 1;
        ulong v = (baseValue >> start) & mask;
        if (signed && bits < 64 && v >= (1UL << (bits - 1)))
            return (double)((long)v - (1L << bits));
        return v;
    }

    static double RoundHalfAwayDecimal(double value, int decimals)
    {
        if (double.IsNaN(value) || double.IsInfinity(value)) return value;
        if (decimals < 0) decimals = 0;
        var inv = System.Globalization.CultureInfo.InvariantCulture;
        var abs = Math.Abs(value);
        var longText = abs.ToString("F" + Math.Min(decimals + 40, 99), inv);
        var frac = longText[(longText.IndexOf('.') + 1)..];
        bool tie = frac[decimals] == '5' && frac[(decimals + 1)..].All(c => c == '0');
        double rounded;
        if (tie)
        {
            var scale = Math.Pow(10, decimals);
            rounded = (Math.Floor(abs * scale) + 1) / scale;
        }
        else
        {
            rounded = double.Parse(abs.ToString("F" + decimals, inv), inv);
        }
        return value < 0 ? -rounded : rounded;
    }

    static double ApplyTransformStages(double numVal, List<TransformStage> stages)
    {
        foreach (var stage in stages)
        {
            if (stage.Op == "round")
            {
                // Half-to-even AND decimal-correct, matching Python, Java and Go.
                //
                // Math.Round(double, int, ToEven) is documented to lose precision this
                // way, and it does: it gives 2.36 for 2.355 and 2.68 for 2.675, where
                // the stored doubles are 2.35499999999999998 and 2.67499999999999982 and
                // the other implementations give 2.35 and 2.67. Rounding through
                // `decimal` is no better - it treats the literal as exact, so it agrees
                // on those two and then gives 2.34 for 2.345.
                //
                // Formatting to a fixed number of decimals is correctly rounded on the
                // stored value and breaks ties to even, which is both jobs at once.
                numVal = stage.Ties == "away"
                    ? RoundHalfAwayDecimal(numVal, stage.Decimals)
                    : RoundHalfEvenDecimal(numVal, stage.Decimals);
                continue;
            }
            if (stage.Floor.HasValue) { numVal = Math.Max(numVal, stage.Floor.Value); continue; }
            if (stage.Ceiling.HasValue) { numVal = Math.Min(numVal, stage.Ceiling.Value); continue; }
            if (stage.Clamp is { Length: 2 } bounds)
            {
                numVal = Math.Max(bounds[0], Math.Min(bounds[1], numVal));
                continue;
            }
            // Unary maths stages, each exclusive of the others and of the arithmetic
            // ops, in the order the Python interpreter checks them. sqrt clamps a
            // negative input at 0 (PS-116). The log of a non-positive number has no
            // value, so the field is absent (PS-117): NaN, which ApplyPostRead omits.
            // The logs clamped at 1e-10 before, reporting log10(0) as -10.
            if (stage.Sqrt) { numVal = Math.Sqrt(Math.Max(0.0, numVal)); continue; }
            if (stage.Abs) { numVal = Math.Abs(numVal); continue; }
            if (stage.Pow.HasValue) { numVal = Math.Pow(numVal, stage.Pow.Value); continue; }
            if (stage.Log10) { if (!(numVal > 0)) return double.NaN; numVal = Math.Log10(numVal); continue; }
            if (stage.Log) { if (!(numVal > 0)) return double.NaN; numVal = Math.Log(numVal); continue; }
            if (stage.Mult.HasValue) numVal *= stage.Mult.Value;
            if (stage.Div.HasValue)
            {
                if (stage.Div.Value == 0) return double.NaN;   // PS-100: the field is absent
                numVal /= stage.Div.Value;
            }
            if (stage.Add.HasValue) numVal += stage.Add.Value;
        }
        return numVal;
    }

    /// <summary>
    /// Applies a field's bare modifiers and then its transform stages. Both run
    /// when both are present - a field may scale with `mult` and then round with a
    /// stage - which an either/or chain here used to get wrong, dropping the
    /// modifier. The modifiers run in the canonical order mult, div, add, whatever
    /// order the keys were written in (PS-101).
    /// </summary>
    /// <summary>
    /// Whether this field's declared type selects `integer` in the clause 1 table and
    /// nothing in the field turns it into a `number` (PS-279, PS-293).
    /// </summary>
    static bool ReportsAsInteger(SchemaField field)
    {
        var integerType = field.Type is FieldType.U8 or FieldType.U16 or FieldType.U24
            or FieldType.U32 or FieldType.U64 or FieldType.S8 or FieldType.S16
            or FieldType.S24 or FieldType.S32 or FieldType.S64
            or FieldType.U32LE16 or FieldType.S32LE16;
        if (!integerType) return false;

        return field.Mult == null && field.Div == null && field.Add == null
            && (field.Transform == null || field.Transform.Count == 0)
            && string.IsNullOrEmpty(field.Formula);
    }

    static double ApplyModifiers(double numVal, SchemaField field)
    {
        if (field.Mult.HasValue) numVal *= field.Mult.Value;
        if (field.Div.HasValue)
        {
            // A zero divisor omits the field (PS-100): NaN, which ApplyPostRead turns
            // into an omission. Skipping the division reported the undivided value.
            if (field.Div.Value == 0) return double.NaN;
            numVal /= field.Div.Value;
        }
        if (field.Add.HasValue) numVal += field.Add.Value;
        return ApplyTransformStages(numVal, field.Transform);
    }

    /// <summary>
    /// A failed guard's `else`: the value as declared (PS-444), which ApplyPostRead
    /// reports without passing it through the lookup.
    /// </summary>
    internal sealed class DeclaredElse
    {
        public double Value { get; }
        public DeclaredElse(double value) => Value = value;
    }

    /// <summary>
    /// A computed field's value (PS-443): its source - the `ref` value with its
    /// `polynomial`, or the `compute` result - then the bare modifiers in the canonical
    /// order (PS-101), then the transform stages. The lookup follows in ApplyPostRead, as
    /// for a field read from the payload. The `compute` path used to take the stages and
    /// drop the modifiers, so `mult: 10` on a compute of 2 reported 2.
    ///
    /// PS-444: the guard is evaluated before the source, so a failed one never resolves a
    /// reference it was guarding, and its `else` is reported exactly as declared.
    /// </summary>
    static object? DecodeNumber(SchemaField field, DecodeContext ctx)
    {
        if (field.Ref == null && field.Compute == null)
        {
            // A literal (PS-357). PS-445 refuses a guard here at load.
            var (ok, v) = Helpers.ToFloat64(field.Value);
            return ok ? v : 0.0;
        }

        if (field.Guard != null && !EvaluateGuardConditions(field.Guard, ctx))
            return new DeclaredElse(field.Guard.ElseValue);

        double source;
        if (field.Ref != null)
        {
            var refName = field.Ref.TrimStart('$');
            if (!ctx.Variables.TryGetValue(refName, out var refVal))
            {
                ctx.CheckUnbound(refName);    // PS-368
                throw new InvalidOperationException($"Ref field not found: {refName}");
            }
            var (ok, rv) = Helpers.ToFloat64(refVal);
            source = ok ? rv : 0;
            if (field.Polynomial is { Length: > 0 })
                source = Helpers.EvaluatePolynomial(field.Polynomial, source);
        }
        else
        {
            source = EvaluateCompute(field.Compute!, ctx);
            // A zero divisor omits the field (PS-278), short-circuiting before the
            // arithmetic so it never sees the sentinel.
            if (IsComputeOmitted(source)) return null;
        }

        // Applied here exactly once; ApplyPostRead skips a computed field's arithmetic.
        return ApplyModifiers(source, field);
    }

    static double EvaluateCompute(ComputeDef cd, DecodeContext ctx)
    {
        double a = ResolveOperand(cd.A, ctx);
        double b = ResolveOperand(cd.B, ctx);

        return cd.Op switch
        {
            // PS-278: a zero divisor omits the field. Throwing, as this used to,
            // abandoned the decode entirely where other implementations carried on.
            "div" => b == 0 ? ComputeOmitted : a / b,
            "mul" => a * b,
            "add" => a + b,
            "sub" => a - b,
            // PS-277 floored: the remainder takes the divisor's sign, so mod(a, 8)
            // stays in 0..7. C#'s native % truncates, giving -1 where the floored
            // answer is 2. PS-284: operands truncate toward zero first.
            "mod" => b == 0 ? ComputeOmitted : (double)FloorMod((long)a, (long)b),
            // PS-276 floored: rounds toward negative infinity, not toward zero.
            "idiv" => b == 0 ? ComputeOmitted : (double)FloorDiv((long)a, (long)b),
            _ => throw new InvalidOperationException($"Unknown compute op: {cd.Op}")
        };
    }

    static double ResolveOperand(string op, DecodeContext ctx)
    {
        if (op.StartsWith('$'))
        {
            var name = op[1..];
            if (ctx.Variables.TryGetValue(name, out var val))
            {
                var (ok, f) = Helpers.ToFloat64(val);
                if (ok) return f;
            }
            else ctx.CheckUnbound(name);    // PS-368
            throw new InvalidOperationException($"Operand field not found: {op}");
        }
        return double.Parse(op, CultureInfo.InvariantCulture);
    }

    internal static bool EvaluateGuardConditions(GuardDef gd, DecodeContext ctx)
    {
        foreach (var cond in gd.When)
        {
            var fieldName = cond.Field.TrimStart('$');
            if (!ctx.Variables.TryGetValue(fieldName, out var fieldVal))
            {
                ctx.CheckUnbound(fieldName);    // PS-368
                return false;
            }
            var (ok, fv) = Helpers.ToFloat64(fieldVal);
            if (!ok) return false;
            if (cond.Gt.HasValue && !(fv > cond.Gt.Value)) return false;
            if (cond.Gte.HasValue && !(fv >= cond.Gte.Value)) return false;
            if (cond.Lt.HasValue && !(fv < cond.Lt.Value)) return false;
            if (cond.Lte.HasValue && !(fv <= cond.Lte.Value)) return false;
            if (cond.Eq.HasValue && fv != cond.Eq.Value) return false;
            if (cond.Ne.HasValue && fv == cond.Ne.Value) return false;
        }
        return true;
    }

    static object? DecodeMatch(SchemaField field, DecodeContext ctx)
    {
        int matchValue;
        // What this construct contributes on its own: the discriminator under `name`,
        // where it was read here and named. Null where there is nothing to report.
        Dictionary<string, object?>? inline = null;

        if (field.MatchRemaining)
        {
            // PS-414: the bytes from here to the end of the region - a reserving repeat's
            // buffer already stops short of the reserve. Nothing is read.
            matchValue = ctx.Remaining;
        }
        else if (!string.IsNullOrEmpty(field.On))
        {
            var varName = field.On.TrimStart('$');
            if (!ctx.Variables.TryGetValue(varName, out var val))
                throw new InvalidOperationException($"Variable not found: ${varName}");
            var (_, iv) = Helpers.ToInt(val);
            matchValue = iv;
        }
        else
        {
            int length = field.Length > 0 ? field.Length : 1;
            var data = ctx.Read(length);
            matchValue = (int)Helpers.DecodeUint(data, ctx.Endian);

            // A discriminator read from the payload is reported where `name` asks for it
            // and stored where `var` does (CR-2026-020). Both were parsed and then
            // discarded, so a schema naming its discriminator decoded one field fewer and
            // a later `$ref` to the variable resolved to nothing. One taken from `field`
            // needs neither: it is already in the output under its own name.
            if (!string.IsNullOrEmpty(field.Var))
                ctx.Variables[field.Var!] = matchValue;
            if (!string.IsNullOrEmpty(field.Name))
            {
                inline = new Dictionary<string, object?> { [field.Name!] = matchValue };
                ctx.Variables[field.Name!] = matchValue;
            }
        }

        // A default case is sorted last by the parser, so reaching one here means no
        // explicit case matched.
        foreach (var c in field.Cases)
        {
            if (c.IsDefault)
                return MergeMatch(inline, DecodeFields(c.Fields, ctx, null));

            var caseVal = c.CaseValue;
            if (caseVal == null) continue;

            if (MatchesCase(matchValue, caseVal))
                return MergeMatch(inline, DecodeFields(c.Fields, ctx, null));
        }

        // `default` decides what an unmatched value means and defaults to "error".
        // Nothing read the key, so every value of it behaved as "skip" and a schema
        // declaring a fallback got none.
        if (field.MatchDefault is List<SchemaField> fallbackFields)
            return MergeMatch(inline, DecodeFields(fallbackFields, ctx, null));
        if (field.MatchDefault is null || (field.MatchDefault as string) != "skip")
            throw new InvalidOperationException($"No matching case for value {matchValue}");
        return inline;
    }

    /// <summary>
    /// Whether a discriminator satisfies one case key, matching what the Python
    /// interpreter's <c>_match_case_pattern</c> accepts.
    ///
    /// The string range spelling <c>"2..5"</c> was missing: only numbers and lists were
    /// compared, so a range key matched nothing at all and the construct decoded silently
    /// empty (CR-2026-020).
    /// </summary>
    static bool MatchesCase(int matchValue, object caseVal)
    {
        switch (caseVal)
        {
            case int iv:
                return matchValue == iv;
            case double dv:
                return matchValue == (int)dv;
            case string text when Helpers.ParseListCaseKey(text) is { } listed:
                // A quoted flow sequence, "[1, 2, 3]", matches any element (PS-398).
                return listed.Contains(matchValue);
            case string text:
                var separator = text.IndexOf("..", StringComparison.Ordinal);
                if (separator > 0)
                {
                    return int.TryParse(text[..separator].Trim(), out var lo)
                           && int.TryParse(text[(separator + 2)..].Trim(), out var hi)
                           && matchValue >= lo && matchValue <= hi;
                }
                return int.TryParse(text.Trim(), out var exact) && matchValue == exact;
            case List<object?> list:
                return list.Any(item =>
                {
                    var (ok, itemInt) = Helpers.ToInt(item);
                    return ok && matchValue == itemInt;
                });
            case Dictionary<string, object?> rangeMap:
                var low = rangeMap.TryGetValue("min", out var minNode)
                    ? Helpers.ToInt(minNode).Item2 : int.MinValue;
                var high = rangeMap.TryGetValue("max", out var maxNode)
                    ? Helpers.ToInt(maxNode).Item2 : int.MaxValue;
                return matchValue >= low && matchValue <= high;
        }
        return false;
    }

    /// <summary>
    /// Folds a case's fields onto the discriminator this construct reported, so
    /// <c>name</c> survives whichever branch decoded.
    /// </summary>
    static object? MergeMatch(Dictionary<string, object?>? inline, object? decoded)
    {
        if (inline is null)
            return decoded;
        if (decoded is Dictionary<string, object?> decodedMap)
        {
            foreach (var kv in decodedMap)
                inline[kv.Key] = kv.Value;
        }
        return inline;
    }

    static Dictionary<string, object?> DecodeTLV(SchemaField field, DecodeContext ctx)
    {
        // PS-471: the loop stops `reserve` bytes before the payload ends, and the fields
        // after the tlv decode from them. A buffer that stops there keeps every entry
        // read inside the region.
        if (field.Reserve > 0)
        {
            if (ctx.Remaining < field.Reserve)
                throw new InvalidOperationException($"tlv reserves {field.Reserve} byte(s) but "
                    + $"{ctx.Remaining} remain at offset {ctx.Offset} (PS-471)");
            return ctx.WithRegion(ctx.Data.Length - field.Reserve, () => DecodeTLVEntries(field, ctx));
        }
        return DecodeTLVEntries(field, ctx);
    }

    static Dictionary<string, object?> DecodeTLVEntries(SchemaField field, DecodeContext ctx)
    {
        int tagSize = field.TagSize > 0 ? field.TagSize : 1;
        int lengthSize = field.LengthSize;
        bool merge = field.Merge ?? true;
        string unknownMode = field.UnknownMode ?? "skip";

        var result = new Dictionary<string, object?>();
        var channels = new List<Dictionary<string, object?>>();

        while (ctx.Remaining > 0)
        {
            // Where this entry begins, so an abandoned remainder is counted from the
            // unknown tag itself rather than from after it (PS-302).
            var entryStart = ctx.Offset;

            var tag = new List<int>();

            // PS-477: a tag is never partly read. Fewer bytes than the tag at the start of
            // an entry is an error identifying the tlv; the plain tag_size path threw a
            // bare buffer underflow, naming neither the tlv nor the tag. A composite tag's
            // width is its tag_fields', as they are read below.
            var width = field.TagFields.Count > 0
                ? field.TagFields.Sum(TagFieldWidth)
                : tagSize;
            if (ctx.Remaining < width)
                throw new InvalidOperationException($"tlv entry at offset {ctx.Offset}: "
                    + $"{ctx.Remaining} byte(s) remain, fewer than its {width}-byte tag (PS-477)");

            if (field.TagFields.Count > 0)
            {
                var tagValues = new Dictionary<string, int>();
                foreach (var tf in field.TagFields)
                {
                    // The reference reads a composite tag's components as fields, which
                    // records them (PS-490); a case member of the same name overwrites it.
                    if (field.TagKey != null) ctx.Produced(tf);
                    var data = ctx.Read(TagFieldWidth(tf));
                    int val = (int)Helpers.DecodeUint(data, ctx.Endian);
                    if (!string.IsNullOrEmpty(tf.Name))
                        tagValues[tf.Name] = val;
                }

                if (field.TagKey is List<object?> tkList)
                {
                    foreach (var k in tkList)
                        if (k is string ks && tagValues.TryGetValue(ks, out var tv))
                            tag.Add(tv);
                }
                else if (field.TagKey is string tks)
                {
                    if (tagValues.TryGetValue(tks, out var tv))
                        tag.Add(tv);
                }
                else if (field.TagFields.Count > 0 && !string.IsNullOrEmpty(field.TagFields[0].Name))
                {
                    if (tagValues.TryGetValue(field.TagFields[0].Name, out var tv))
                        tag.Add(tv);
                }
            }
            else
            {
                var data = ctx.Read(tagSize);
                tag.Add((int)Helpers.DecodeUint(data, ctx.Endian));
            }

            int dataLength = -1;
            if (lengthSize > 0)
            {
                // PS-486: an entry is never partly read. A length cut short is an error,
                // not leftover bytes for PS-472: the schema describes them.
                if (ctx.Remaining < lengthSize)
                    throw new InvalidOperationException($"tlv entry at offset {entryStart}: "
                        + $"{ctx.Remaining} byte(s) remain after its tag, fewer than its "
                        + $"{lengthSize}-byte length (PS-486)");
                var lenData = ctx.Read(lengthSize);
                dataLength = (int)Helpers.DecodeUint(lenData, ctx.Endian);
                // Nor may its value run past the end (the context already stops at a
                // `reserve`), known tag or skipped: a known one read what was there and
                // reported success, a skipped one failed with a bare underflow.
                if (dataLength > ctx.Remaining)
                    throw new InvalidOperationException($"tlv entry at offset {entryStart}: its length "
                        + $"declares {dataLength} byte(s), {ctx.Remaining} remain (PS-486)");
            }

            string? caseKey = FindTLVCaseKey(field.TLVCases, tag);

            if (caseKey != null && field.TLVCases != null)
            {
                var caseFields = field.TLVCases[caseKey];
                var caseResult = DecodeFields(caseFields, ctx, null);

                if (merge)
                {
                    foreach (var kv in caseResult)
                    {
                        if (result.ContainsKey(kv.Key))
                        {
                            if (result[kv.Key] is List<object?> arr)
                                arr.Add(kv.Value);
                            else
                                result[kv.Key] = new List<object?> { result[kv.Key], kv.Value };
                        }
                        else
                        {
                            result[kv.Key] = kv.Value;
                        }
                    }
                }
                else
                {
                    var entry = new Dictionary<string, object?> { ["tag"] = tag };
                    foreach (var kv in caseResult)
                        entry[kv.Key] = kv.Value;
                    channels.Add(entry);
                }
            }
            else
            {
                // A tag the schema does not describe. Whatever the mode, the fact is
                // reported: silence cannot be told from a device that sent fewer fields
                // (PS-301, PS-302).
                var label = string.Join(", ", tag.Select(part => $"0x{part:X2}"));

                if (unknownMode == "error")
                    throw new InvalidOperationException($"Unknown TLV tag: {label}");

                if (unknownMode == "raw")
                {
                    var span = dataLength >= 0 ? dataLength : ctx.Remaining;
                    var bytes = ctx.Read(span);
                    var entry = new Dictionary<string, object?>
                    {
                        ["tag"] = tag.ToList(),
                        ["raw"] = Convert.ToHexString(bytes).ToLowerInvariant(),
                    };
                    // PS-303: reported either way. Merged output has no channel list, so
                    // it goes under `unknown_tags`.
                    if (merge)
                    {
                        if (result.TryGetValue("unknown_tags", out var existing)
                            && existing is List<Dictionary<string, object?>> list)
                            list.Add(entry);
                        else
                            result["unknown_tags"] = new List<Dictionary<string, object?>> { entry };
                    }
                    else
                    {
                        channels.Add(entry);
                    }

                    if (dataLength < 0)
                    {
                        ctx.Warnings.Add($"unknown TLV tag ({label}) captured raw; "
                                         + $"{span} byte(s) after it could not be delimited");
                        break;
                    }
                    continue;
                }

                // skip, the default
                if (dataLength >= 0)
                {
                    ctx.Warnings.Add($"unknown TLV tag ({label}) skipped, "
                                     + $"{dataLength} byte(s) discarded");
                    ctx.Read(dataLength);
                    continue;
                }

                // Nothing to skip over, so decoding stops and everything from the tag
                // onwards is lost (PS-302).
                ctx.Warnings.Add($"unknown TLV tag ({label}) at offset {entryStart}: "
                                 + $"{ctx.Data.Length - entryStart} of {ctx.Data.Length} byte(s) left undecoded");
                // This warning is PS-472's for these bytes; no second one is reported.
                ctx.LeftoverReported = true;
                break;
            }
        }

        if (!merge)
            result["channels"] = channels;

        return result;
    }

    static string? FindTLVCaseKey(Dictionary<string, List<SchemaField>>? cases, List<int> tag)
    {
        if (cases == null) return null;

        if (tag.Count == 1)
        {
            var key = tag[0].ToString();
            if (cases.ContainsKey(key)) return key;
        }

        var tagJson = $"[{string.Join(",", tag)}]";
        if (cases.ContainsKey(tagJson)) return tagJson;

        // Compare composite keys numerically. The rendering above has no space
        // after the comma while schemas are written "[1, 117]", so the exact
        // comparison missed every composite key. This also handles keys that
        // exclude a value with `!` or ignore a tag field with `*`, taking exact
        // keys first, then negated, then wildcard (PS-270).
        for (int wanted = 0; wanted <= 2; wanted++)
        {
            foreach (var candidate in cases.Keys)
            {
                var (matched, specificity) = MatchCompositeCaseKey(candidate, tag);
                if (matched && specificity == wanted) return candidate;
            }
        }

        return null;
    }

    /// <summary>Sentinel for a field that produced no value and is left out.</summary>
    internal static readonly object Omitted = new();

    static readonly System.Text.RegularExpressions.Regex NameFromPattern =
        new(@"\$\{(\w+)\}");

    /// <summary>Resolves a field's output key, honouring name_from (PS-265, PS-266).</summary>
    static string ResolveFieldName(SchemaField field, DecodeContext ctx)
    {
        if (string.IsNullOrEmpty(field.NameFrom)) return field.Name;

        var missing = new List<string>();
        var resolved = NameFromPattern.Replace(field.NameFrom, match =>
        {
            var reference = match.Groups[1].Value;
            if (!ctx.Variables.TryGetValue(reference, out var value))
            {
                missing.Add(reference);
                return string.Empty;
            }
            var (ok, intVal) = Helpers.ToInt(value);
            return ok ? intVal.ToString() : value?.ToString() ?? string.Empty;
        });
        if (missing.Count > 0)
            throw new InvalidOperationException(
                $"name_from for '{field.Name}' references {string.Join(", ", missing)}, " +
                "which has not been decoded");
        return resolved;
    }

    /// <summary>
    /// Matches a composite TLV case key against a tag, allowing `!value` to exclude
    /// and `*` to ignore a tag field. Specificity is 0 exact, 1 negated, 2 wildcard.
    /// </summary>
    static (bool, int) MatchCompositeCaseKey(string key, List<int> tag)
    {
        var trimmed = key.Trim();
        if (!trimmed.StartsWith("[")) return (false, 0);
        trimmed = trimmed.TrimStart('[').TrimEnd(']');
        var parts = trimmed.Split(',');
        if (parts.Length != tag.Count) return (false, 0);

        int specificity = 0;
        for (int i = 0; i < parts.Length; i++)
        {
            var part = parts[i].Trim().Trim('"', '\'');
            if (part == "*")
            {
                specificity = Math.Max(specificity, 2);
                continue;
            }
            bool negated = part.StartsWith("!");
            var text = negated ? part.Substring(1).Trim() : part;
            int expected;
            if (text.StartsWith("0x") || text.StartsWith("0X"))
            {
                if (!int.TryParse(text.Substring(2),
                        System.Globalization.NumberStyles.HexNumber, null, out expected))
                    return (false, 0);
            }
            else if (!int.TryParse(text, out expected))
            {
                return (false, 0);
            }
            if (negated)
            {
                specificity = Math.Max(specificity, 1);
                if (tag[i] == expected) return (false, 0);
            }
            else if (tag[i] != expected)
            {
                return (false, 0);
            }
        }
        return (true, specificity);
    }

    static List<object?> DecodeRepeat(SchemaField field, DecodeContext ctx, PayloadSchemaDefinition? schema)
    {
        int maxIterations = field.Max > 0 ? field.Max : 1000;
        int minIterations = field.Min;
        var result = new List<object?>();
        int iterations = 0;

        // PS-378, PS-380: a carried field starts again from its `carry` value each time
        // the repeat begins, including each iteration of an enclosing repeat.
        var carryState = new Dictionary<string, object?>();
        foreach (var member in field.Fields)
            if (member.Carry != null && !string.IsNullOrEmpty(member.Name))
                carryState[member.Name] = CarryInitial(member, ctx);
        var elementNames = new HashSet<string>(
            field.Fields.Select(f => f.Name).Where(n => !string.IsNullOrEmpty(n)));

        // One element, in a scope of its own: its names are gone after it (PS-368).
        // Appended unless present_if drops it; a dropped element still counts, still
        // advances the index and still consumed its bytes (PS-386).
        void Element()
        {
            var outer = new Dictionary<string, object?>(ctx.Variables);
            var savedNames = ctx.ElementNames;
            ctx.ElementNames = elementNames;
            if (!string.IsNullOrEmpty(field.Index))
                ctx.Variables[field.Index!] = iterations;                          // PS-366
            foreach (var kv in carryState)
                ctx.Variables[kv.Key] = kv.Value;                                  // PS-379
            Dictionary<string, object?> element;
            bool keep;
            try
            {
                element = DecodeFields(field.Fields, ctx, schema);
                foreach (var name in carryState.Keys.ToList())
                    if (ctx.Variables.TryGetValue(name, out var carried))
                        carryState[name] = carried;
                keep = field.PresentIf == null
                       || EvaluateGuardConditions(new GuardDef { When = { field.PresentIf } }, ctx);
            }
            finally
            {
                ctx.Variables.Clear();
                foreach (var kv in outer)
                    ctx.Variables[kv.Key] = kv.Value;
                ctx.ElementNames = savedNames;
            }
            iterations++;
            if (keep)
                result.Add(element);
        }

        if (field.Count != null)
        {
            int count;
            if (field.Count is int ci) count = ci;
            else if (field.Count is string cs)
            {
                var varName = cs.TrimStart('$');
                if (ctx.Variables.TryGetValue(varName, out var val))
                {
                    var (_, iv) = Helpers.ToInt(val);
                    count = iv;
                }
                else throw new InvalidOperationException($"Repeat count variable not found: {cs}");
            }
            else count = 0;

            // PS-396: more elements than `max` is an error, not a silent truncation. The
            // count was clamped here, so the next field read from inside an element the
            // clamp had discarded.
            if (count > maxIterations)
                throw RepeatLimitError(field, maxIterations, $"count {count}", ctx.Offset, ctx.Data.Length);
            for (int i = 0; i < count; i++)
                Element();
        }
        else if (field.ByteLength != null)
        {
            int byteLength;
            if (field.ByteLength is int bli) byteLength = bli;
            else if (field.ByteLength is string bls)
            {
                var varName = bls.TrimStart('$');
                if (ctx.Variables.TryGetValue(varName, out var val))
                {
                    var (_, iv) = Helpers.ToInt(val);
                    byteLength = iv;
                }
                else throw new InvalidOperationException($"Repeat byte_length variable not found: {bls}");
            }
            else byteLength = 0;

            int endOffset = ctx.Offset + byteLength;
            while (ctx.Offset < endOffset && iterations < maxIterations)
                Element();

            // PS-088: the members must divide the span exactly. This implementation had
            // no check at all, so it accepted both ways they do not - an iteration
            // starting inside the span and finishing past it, and a ceiling stopping the
            // loop early - and left the read position somewhere other than the span's
            // end. Every field after the repeat then came from the wrong offset with
            // nothing reported: a 2-byte member over a 5-byte span produced a third
            // record holding the following field's byte, and the following field read
            // past the payload (CR-2026-022).
            if (ctx.Offset != endOffset)
            {
                if (iterations >= maxIterations && ctx.Offset < endOffset)
                    throw RepeatLimitError(field, maxIterations, $"byte_length {byteLength}",
                        ctx.Offset, endOffset);
                throw new InvalidOperationException(
                    $"Repeat byte_length mismatch: expected end at {endOffset}, got {ctx.Offset}");
            }
        }
        else if (field.Until == "end")
        {
            // PS-350, PS-351: the region ends `reserve` bytes before the payload does, and
            // the ragged-tail rule applies to that region. Its elements decode from a
            // buffer that stops there, so nothing inside one can read the reserved bytes.
            int regionEnd = ctx.Data.Length - field.Reserve;
            if (regionEnd < ctx.Offset)
                throw new InvalidOperationException($"repeat '{field.Name}' reserves {field.Reserve} "
                    + $"byte(s) but {ctx.Remaining} remain at offset {ctx.Offset} (PS-351)");
            // PS-343 to PS-344a: a tail too short for a whole element is an error naming
            // the repeat as a ragged tail, tested before the element begins where its
            // size is fixed. This began the element and failed part-way with the
            // underflow of whichever member ran out.
            var elementSize = FixedElementSize(field.Fields);
            ctx.WithRegion(regionEnd, () =>
            {
                while (ctx.Remaining > 0 && iterations < maxIterations)
                {
                    var start = ctx.Offset;
                    if (elementSize > 0 && ctx.Remaining < elementSize)
                        throw RaggedTailError(field, ctx.Remaining, elementSize, start);
                    try
                    {
                        Element();
                    }
                    catch (InvalidOperationException e) when (e.Message.Contains("Buffer underflow"))
                    {
                        throw RaggedTailError(field, ctx.Data.Length - start, 0, start);
                    }
                    // An element that read nothing would never end the loop.
                    if (ctx.Offset == start) break;
                }
                if (iterations >= maxIterations && ctx.Remaining > 0)
                    throw RepeatLimitError(field, maxIterations, "until: end", ctx.Offset, ctx.Data.Length);
                return 0;
            });
            // PS-383: the trailer was decoded from the reserved bytes before the first
            // element, so nothing after the repeat reads them again.
            if (field.Trailer is { Count: > 0 })
                ctx.Offset = ctx.Data.Length;
        }
        else
        {
            throw new InvalidOperationException("Repeat field must specify count, byte_length, or until");
        }

        if (result.Count < minIterations)
            throw new InvalidOperationException($"Repeat produced {result.Count} elements, but minimum is {minIterations}");

        // PS-367: the number of elements reported (CR-2026-080), bound for later fields only.
        if (!string.IsNullOrEmpty(field.CountAs))
            ctx.Variables[field.CountAs!] = result.Count;

        return result;
    }

    /// <summary>A carried field's value before the first element (PS-378).</summary>
    static object? CarryInitial(SchemaField member, DecodeContext ctx)
    {
        if (member.Carry is string text && text.StartsWith('$'))
        {
            if (!ctx.Variables.TryGetValue(text[1..], out var value))
                throw new InvalidOperationException($"carry of '{member.Name}' names {text}, which "
                    + "was not decoded before the repeat (PS-381)");
            return value;
        }
        return member.Carry;
    }

    static void MergeTo(Dictionary<string, object?> target, Dictionary<string, object?> source)
    {
        foreach (var kv in source)
            target[kv.Key] = kv.Value;
    }
}

// Semantic output formatters

public static class SemanticFormatter
{
    /// <summary>
    /// Convert decoded data to SenML format (RFC 8428): one record per field the output
    /// reports, wherever it is declared, named by its <c>senml.name</c> or else its
    /// reported name, with <c>senml.unit</c> or else <c>unit</c> (PS-478). A member of an
    /// object with no <c>senml.name</c> is named by the object's reported name and its own,
    /// joined by <c>/</c> (PS-479). This looked fields up among the top-level list and the
    /// plain nested lists only, so a port entry's, a case's or a group's fields had no
    /// name or unit, and an object came out as one record holding the whole mapping.
    /// <paramref name="fPort"/> selects a port entry's fields; without it every entry's
    /// are searched. A record's unit is the one <see cref="SenmlUnits.Resolve"/> derives:
    /// a secondary unit re-expressed in its primary unit (PS-487), an unregistered one
    /// left out with a warning added to <paramref name="warnings"/> (PS-488).
    /// </summary>
    public static List<Dictionary<string, object?>> ToSenML(
        PayloadSchemaDefinition schema,
        Dictionary<string, object?> decoded,
        int? fPort = null,
        List<string>? warnings = null)
    {
        var fields = new List<SchemaField>(schema.Fields);
        if (schema.Ports != null)
        {
            if (fPort.HasValue && schema.Ports.TryGetValue(fPort.Value.ToString(), out var port))
                fields = new List<SchemaField>(port.Fields);
            else
                foreach (var entry in schema.Ports.Values) fields.AddRange(entry.Fields);
        }
        var records = new List<Dictionary<string, object?>>();
        AddSenML(records, decoded, fields, "", warnings ?? new List<string>());
        return records;
    }

    static void AddSenML(List<Dictionary<string, object?>> records,
        Dictionary<string, object?> decoded, List<SchemaField> fields, string prefix,
        List<string> warnings)
    {
        var declared = ReportedFields(fields);
        foreach (var kv in decoded)
        {
            if (kv.Key.StartsWith("_")) continue;   // `_quality`, `_warnings`: not fields
            declared.TryGetValue(kv.Key, out var field);
            var path = prefix.Length > 0 ? prefix + "/" + kv.Key : kv.Key;
            if (kv.Value is Dictionary<string, object?> nested)
            {
                AddSenML(records, nested, field == null ? new() : Members(field), path, warnings);
                continue;
            }
            var record = new Dictionary<string, object?>
            {
                ["n"] = string.IsNullOrEmpty(field?.Senml?.Name) ? path : field!.Senml!.Name
            };
            var declaredUnit = string.IsNullOrEmpty(field?.Senml?.Unit) ? field?.Unit : field!.Senml!.Unit;
            var (value, unit, unregistered) = SenmlUnits.Resolve(kv.Value, declaredUnit);
            if (unregistered != null)
                warnings.Add($"{path}: unit '{unregistered}' is not a registered SenML unit, "
                    + "so its record has no 'u' (PS-488)");
            if (value is bool b)
                record["vb"] = b;
            else if (value is string s)
                record["vs"] = s;
            else if (value is byte[] bytes)
                record["vd"] = Convert.ToBase64String(bytes);
            else
                record["v"] = value;        // a repeat's elements: PS-377, unchanged here
            if (unit != null)
                record["u"] = unit;
            records.Add(record);
        }
    }

    /// <summary>The fields one level of nested output holds: an object's, or a
    /// <c>merge: false</c> tlv's cases'.</summary>
    static List<SchemaField> Members(SchemaField field)
    {
        var tlv = field.TLVInline ?? field;
        if (tlv.TLVCases != null && tlv.Merge == false)
            return tlv.TLVCases.Values.SelectMany(c => c).ToList();
        return field.Fields;
    }

    /// <summary>Reported key to declaring field for one level of output. A construct that
    /// merges into its parent (PS-156, PS-163) contributes its fields at this level; an
    /// object or repeat its own name. The first declaration of a name wins.</summary>
    static Dictionary<string, SchemaField> ReportedFields(List<SchemaField> fields)
    {
        var found = new Dictionary<string, SchemaField>();
        void Visit(IEnumerable<SchemaField> list)
        {
            foreach (var f in list)
            {
                Visit(f.ByteGroup);
                if (f.Flagged != null)
                    foreach (var g in f.Flagged.Groups) Visit(g.Fields);
                foreach (var c in f.Cases) Visit(c.Fields);
                if (f.MatchDefault is List<SchemaField> fallback) Visit(fallback);
                if (f.MatchInline != null) Visit(new[] { f.MatchInline });
                var tlv = f.TLVInline ?? f;
                if (tlv.TLVCases != null && tlv.Merge != false)
                    foreach (var body in tlv.TLVCases.Values) Visit(body);
                if (f.TLVInline != null && f.TLVInline.Merge == false
                    && !string.IsNullOrEmpty(f.TLVInline.Name))
                    found.TryAdd(f.TLVInline.Name, f);
                if (!string.IsNullOrEmpty(f.Name))
                    found.TryAdd(f.Name, f);
            }
        }
        Visit(fields);
        return found;
    }

    /// <summary>
    /// Convert decoded data to IPSO Smart Object format.
    /// </summary>
    public static Dictionary<string, object?> ToIPSO(
        PayloadSchemaDefinition schema, 
        Dictionary<string, object?> decoded)
    {
        var result = new Dictionary<string, object?>();
        var fields = GetAllFields(schema);

        foreach (var kv in decoded)
        {
            if (kv.Key.StartsWith("_")) continue; // Skip internal fields
            
            var field = FindField(fields, kv.Key);
            
            if (field?.Ipso != null)
            {
                // Use IPSO object ID as key
                var objKey = $"/{field.Ipso.Object}";
                if (field.Ipso.Instance > 0)
                    objKey += $"/{field.Ipso.Instance}";
                
                var obj = new Dictionary<string, object?> { ["value"] = kv.Value };
                
                var unit = field.Unit;
                if (!string.IsNullOrEmpty(unit))
                    obj["unit"] = unit;
                
                result[objKey] = obj;
            }
            else
            {
                // No IPSO mapping, use field name
                result[kv.Key] = kv.Value;
            }
        }
        
        return result;
    }

    /// <summary>
    /// Get field metadata including units for all decoded fields.
    /// </summary>
    public static Dictionary<string, FieldMetadata> GetMetadata(
        PayloadSchemaDefinition schema,
        Dictionary<string, object?> decoded)
    {
        var result = new Dictionary<string, FieldMetadata>();
        var fields = GetAllFields(schema);

        foreach (var kv in decoded)
        {
            if (kv.Key.StartsWith("_")) continue;
            
            var field = FindField(fields, kv.Key);
            if (field != null)
            {
                result[kv.Key] = new FieldMetadata
                {
                    Unit = field.Unit,
                    UNECE = field.UNECE,
                    ValidRange = field.ValidRange,
                    Resolution = field.Resolution,
                    SemanticId = field.SemanticId,
                    IpsoObject = field.Ipso?.Object,
                    SenmlName = field.Senml?.Name,
                    SenmlUnit = field.Senml?.Unit
                };
            }
        }
        
        return result;
    }

    static List<SchemaField> GetAllFields(PayloadSchemaDefinition schema)
    {
        var all = new List<SchemaField>();
        all.AddRange(schema.Fields);
        
        // Include fields from definitions
        if (schema.Definitions != null)
        {
            foreach (var def in schema.Definitions.Values)
                all.AddRange(def.Fields);
        }
        
        // Flatten nested fields
        return FlattenFields(all);
    }

    static List<SchemaField> FlattenFields(List<SchemaField> fields)
    {
        var result = new List<SchemaField>();
        foreach (var f in fields)
        {
            result.Add(f);
            if (f.Fields.Count > 0)
                result.AddRange(FlattenFields(f.Fields));
            if (f.ByteGroup.Count > 0)
                result.AddRange(FlattenFields(f.ByteGroup));
            if (f.Flagged != null)
            {
                foreach (var g in f.Flagged.Groups)
                    result.AddRange(FlattenFields(g.Fields));
            }
            foreach (var c in f.Cases)
                result.AddRange(FlattenFields(c.Fields));
        }
        return result;
    }

    static SchemaField? FindField(List<SchemaField> fields, string name)
    {
        return fields.FirstOrDefault(f => f.Name == name);
    }
}

public class FieldMetadata
{
    public string? Unit { get; set; }
    public string? UNECE { get; set; }
    public double[]? ValidRange { get; set; }
    public double? Resolution { get; set; }
    public string? SemanticId { get; set; }
    public int? IpsoObject { get; set; }
    public string? SenmlName { get; set; }
    public string? SenmlUnit { get; set; }
}
