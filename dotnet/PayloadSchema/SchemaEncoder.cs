// Copyright (c) 2024-2026 Multitech Systems, Inc.
// SPDX-License-Identifier: MIT

namespace PayloadSchema;

/// <summary>
/// The outcome of encoding a data dictionary to payload bytes.
/// </summary>
/// <remarks>
/// Mirrors the reference interpreter's EncodeResult: a field that cannot be encoded is
/// recorded against the payload rather than aborting it, so one unrecoverable value does
/// not cost every other field its bytes. A field the data does not carry is a warning and
/// encodes as zero; a value that cannot have produced any legal bytes is an error.
///
/// The decoder throws, and still does. Encoding differs because it has inherently lossy
/// cases - a `lookup` default label stands for every unmapped value, a rounding stage
/// discarded precision - and a caller needs to know which fields those were.
/// </remarks>
public class EncodeResult
{
    public byte[] Payload { get; internal set; } = Array.Empty<byte>();
    public List<string> Warnings { get; } = new();
    public List<string> Errors { get; } = new();

    /// <summary>True when no field failed to encode. Warnings do not clear success.</summary>
    public bool Success => Errors.Count == 0;
}

/// <summary>
/// Encodes a data dictionary back to payload bytes - the inverse of <see cref="SchemaDecoder"/>.
/// </summary>
/// <remarks>
/// <para>This binding had no encoder at all: Helpers.EncodeUint and its siblings existed,
/// nothing that used them. It is ported from tools/schema_interpreter.py, whose round-trip
/// corpus is what found the gaps worth having: encoding has to undo a field's lookup, then
/// its transform chain, then its canonical modifiers, in that order, and it has to rebuild
/// the framing of every construct rather than emitting bare values.</para>
///
/// <para>Two properties of the constructs need saying, because they are not obvious from
/// the decode side. A TLV payload is flattened by decoding, so the channels are recovered
/// from which field names the data carries; their order is the order those names appear in
/// the dictionary, which for output straight from Decode is the order they were read. And a
/// `match` on `field: $var` read no bytes of its own - the variable came from an earlier
/// field the main loop encodes - so only the inline form with `length` writes a
/// discriminator.</para>
///
/// <para>encode(decode(payload)) == payload cannot hold everywhere. A `skip` field's bytes
/// are not recoverable from output that omits them, a rounding stage discards precision, and
/// a `lookup` `default` label stands for every value the table does not list (PS-269).
/// Those cases are reported through <see cref="EncodeResult"/>, not papered over with wrong
/// bytes.</para>
/// </remarks>
public static class SchemaEncoder
{
    public static EncodeResult Encode(PayloadSchemaDefinition schema,
        Dictionary<string, object?> data, IReadOnlyList<string>? order = null)
        => new Encoding(schema, schema.Fields, order).Run(data);

    /// <summary>
    /// Encode with port-based schema selection.
    ///
    /// Pass <paramref name="direction"/> where the caller knows which way the message
    /// will travel. The mirror of the decode check (PS-292): encoding for an entry that
    /// disclaims this direction produces bytes the far end reads against different field
    /// definitions, so nothing is encoded.
    /// </summary>
    public static EncodeResult EncodeWithPort(PayloadSchemaDefinition schema,
        Dictionary<string, object?> data, int fPort, IReadOnlyList<string>? order = null,
        string? direction = null)
    {
        SchemaDirection.Check(schema, fPort, direction);
        return new Encoding(schema, ResolveFieldsForEncode(schema, fPort), order).Run(data);
    }

    static List<SchemaField> ResolveFieldsForEncode(PayloadSchemaDefinition schema, int fPort)
    {
        if (schema.Ports == null)
            return schema.Fields;
        if (schema.Ports.TryGetValue(fPort.ToString(), out var pd))
            return pd.Fields;
        if (schema.Ports.TryGetValue("default", out var dpd))
            return dpd.Fields;
        throw new InvalidOperationException(
            $"No port definition for fPort {fPort} and no default in schema '{schema.Name}'");
    }

    /// <summary>One encode call's state: the schema, the endianness, and what went wrong.</summary>
    sealed class Encoding
    {
        readonly PayloadSchemaDefinition _schema;
        readonly List<SchemaField> _fields;
        readonly IReadOnlyList<string>? _order;
        readonly string _endian;
        readonly EncodeResult _result = new();

        internal Encoding(PayloadSchemaDefinition schema, List<SchemaField> fields,
            IReadOnlyList<string>? order)
        {
            _schema = schema;
            _fields = fields;
            _order = order;
            _endian = string.IsNullOrEmpty(schema.Endian) ? "big" : schema.Endian;
        }

        // --- entry point -------------------------------------------------------------

        internal EncodeResult Run(Dictionary<string, object?> data)
        {
            data ??= new Dictionary<string, object?>();
            var output = new List<byte>();

            // A `flagged` block's flags byte is not in the decoded output as a number a
            // caller would supply: it is implied by which groups are present. Computed
            // here so the plain field carrying it gets the right value when its turn comes.
            var flagsPatches = new Dictionary<string, int>();
            foreach (var field in _fields)
            {
                if (field.Flagged == null) continue;
                int flags = 0;
                foreach (var group in field.Flagged.Groups)
                    if (GroupHasData(group, data))
                        flags |= 1 << group.Bit;
                if (!string.IsNullOrEmpty(field.Flagged.Field))
                    flagsPatches[field.Flagged.Field] = flags;
            }

            // Bit ranges sharing a byte are packed together rather than a byte apiece
            // (CR-2026-024), so the whole list is walked at once: a run cannot be found
            // by looking at one field in isolation.
            output.AddRange(EncodeWithBitfieldRuns(_fields, data, flagsPatches,
                                                   topLevel: true));

            _result.Payload = output.ToArray();
            return _result;
        }

        static string Describe(SchemaField field)
        {
            if (!string.IsNullOrEmpty(field.Name)) return field.Name;
            if (field.TLVInline != null || field.Type == FieldType.TLV) return "tlv";
            if (field.MatchInline != null) return "match";
            if (field.ByteGroup.Count > 0) return "byte_group";
            if (field.Flagged != null) return "flagged group";
            if (field.Ref2 != null) return $"$ref {field.Ref2}";
            return field.Type.ToString();
        }

        // --- field dispatch ----------------------------------------------------------

        /// <summary>
        /// Encode one entry of a field list. Shared by the top-level loop and by the bodies
        /// of the constructs, so a case body may itself hold a construct.
        /// </summary>
        byte[] EncodeOne(SchemaField field, Dictionary<string, object?> data,
            Dictionary<string, int> flagsPatches, bool topLevel)
        {
            if (field.Ref2 != null)
            {
                // Decoding splices the referenced definition's fields in place. Encoding
                // has to as well, or the whole referenced header collapses to nothing.
                var def = ResolveDefinition(field.Ref2);
                return EncodeFieldList(def, data);
            }
            if (field.ByteGroup.Count > 0)
                return EncodeByteGroup(field, data);
            if (field.TLVInline != null)
                return EncodeTLV(field.TLVInline, data);
            if (field.Type == FieldType.TLV && field.TLVCases != null)
                return EncodeTLV(field, data);
            if (field.MatchInline != null)
                return EncodeMatch(field.MatchInline, data);
            if (field.Flagged != null)
                return EncodeFlagged(field.Flagged, data);
            if (field.Type == FieldType.Match && string.IsNullOrEmpty(field.Name))
                return EncodeMatch(field, data);

            switch (field.Type)
            {
                case FieldType.Repeat:
                    return EncodeRepeat(field, data);
                case FieldType.Object:
                    // A nested object's fields are written in place; decoding reports them
                    // flattened, so they are looked up by their own names.
                    return EncodeFieldList(field.Fields, data);
                case FieldType.Number:
                    // Derived from other fields: no bytes of its own.
                    return Array.Empty<byte>();
                case FieldType.Skip:
                    // `remaining` gives no count to pad on encode (PS-014).
                    return new byte[field.Length > 0 ? field.Length : 0];
            }

            if (field.Type == FieldType.BitfieldString)
            {
                data.TryGetValue(field.Name, out var text);
                return EncodeBitfieldString(field, text?.ToString() ?? "");
            }

            object? value;
            if (string.IsNullOrEmpty(field.Name) || field.Name.StartsWith("_"))
                value = 0.0;
            else if (flagsPatches.TryGetValue(field.Name, out var flags))
                value = (double)flags;
            else if (data.TryGetValue(field.Name, out var supplied))
                value = supplied;
            else
            {
                if (topLevel)
                    _result.Warnings.Add($"Missing field: {field.Name}");
                value = 0.0;
            }

            return EncodeField(field, ReverseModifiers(value, field));
        }


        /// <summary>
        /// Whether a plain field reads a bit range out of a byte it may share with others.
        /// A byte_group member is excluded: that construct packs its own.
        /// </summary>
        static bool IsBareBitfield(SchemaField field)
            => field.ByteGroup.Count == 0
               && Helpers.ParseBitRange(field.RawType) != null;

        /// <summary>
        /// Encode a field list, packing each run of bit ranges into the byte or bytes it
        /// shares (CR-2026-024).
        ///
        /// Encoding wrote each bit range as a whole byte holding its unshifted value,
        /// ignoring the range and <c>consume: 0</c> alike, so a LoRaWAN MHDR's three
        /// ranges came back as three bytes: <c>40</c> encoded as <c>020000</c>, the wrong
        /// length and the wrong bits, with no error. byte_group was given packing when its
        /// own encoding was fixed; a bare run - the same thing without the wrapper - never
        /// was. CR-2026-023 fixed the Python reference encoder; this is the same fix here.
        ///
        /// A run ends at the field whose <c>consume</c> closes the span, which is where
        /// decoding stops reading from the same offset.
        /// </summary>
        byte[] EncodeWithBitfieldRuns(List<SchemaField> fields,
                                      Dictionary<string, object?> data,
                                      Dictionary<string, int> patches, bool topLevel)
        {
            var output = new List<byte>();
            var run = new List<SchemaField>();

            void Flush()
            {
                if (run.Count == 0) return;
                var pending = new List<SchemaField>(run);
                run.Clear();
                try
                {
                    output.AddRange(EncodeBitfieldRun(pending, data));
                }
                catch (Exception e)
                {
                    if (!topLevel) throw;
                    var names = string.Join(", ", pending.Select(f => f.Name));
                    _result.Errors.Add($"Error encoding bit range(s) {names}: {e.Message}");
                }
            }

            foreach (var field in fields)
            {
                if (!IsBareBitfield(field))
                {
                    Flush();
                    try
                    {
                        output.AddRange(EncodeOne(field, data, patches, topLevel));
                    }
                    catch (Exception e)
                    {
                        if (!topLevel) throw;
                        _result.Errors.Add($"Error encoding {Describe(field)}: {e.Message}");
                    }
                    continue;
                }
                run.Add(field);
                if (field.Consume >= 1) Flush();
            }
            Flush();
            return output.ToArray();
        }

        /// <summary>Pack one run of bit ranges into the byte or bytes they share.</summary>
        byte[] EncodeBitfieldRun(List<SchemaField> run, Dictionary<string, object?> data)
        {
            ulong packed = 0;
            int size = 1;
            foreach (var member in run)
            {
                object? value = string.IsNullOrEmpty(member.Name) || member.Name.StartsWith("_")
                    ? 0.0
                    : (data.TryGetValue(member.Name, out var v) ? v : 0.0);
                value = ReverseModifiers(value, member);
                var (ok, numeric) = Helpers.ToFloat64(value);
                if (!ok)
                {
                    if (value is bool flag) numeric = flag ? 1 : 0;
                    else
                        // A label on a bit range: recover the number rather than writing
                        // zero, which would be the silent wrong answer this fix removes.
                        numeric = BitfieldLabelValue(value, member);
                }
                long raw = (long)Math.Round(numeric, MidpointRounding.ToEven);
                var bitRange = Helpers.ParseBitRange(member.RawType)!.Value;
                int bitLen = bitRange.end - bitRange.start + 1;
                size = Math.Max(size, Math.Max(1,
                    Math.Max(member.BitBaseBytes, member.Consume)));
                ulong mask = bitLen >= 64 ? ulong.MaxValue : (1UL << bitLen) - 1;
                packed |= ((ulong)raw & mask) << bitRange.start;
            }
            return Helpers.EncodeUint(packed, Math.Max(1, size), _endian);
        }

        /// <summary>The number a bit range's enum label stands for.</summary>
        static double BitfieldLabelValue(object? value, SchemaField member)
        {
            var label = value?.ToString() ?? "";
            if (member.Values != null)
            {
                foreach (var kv in member.Values)
                    if (kv.Value == label)
                        return kv.Key;
            }
            throw new InvalidOperationException($"bit range '{member.Name}': '{label}' "
                + "is not one of its declared values");
        }

        /// <summary>Encode a list of fields - a TLV case's value bytes, a match case's body.</summary>
        byte[] EncodeFieldList(List<SchemaField> fields, Dictionary<string, object?> data)
        {
            if (fields.Count == 0) return Array.Empty<byte>();
            return EncodeWithBitfieldRuns(fields, data, new Dictionary<string, int>(),
                                          topLevel: false);
        }

        List<SchemaField> ResolveDefinition(string refPath)
        {
            if (!refPath.StartsWith("#/definitions/"))
                throw new InvalidOperationException($"Unsupported $ref format: {refPath}");
            var name = refPath["#/definitions/".Length..];
            if (_schema.Definitions == null || !_schema.Definitions.TryGetValue(name, out var def))
                throw new InvalidOperationException($"Definition not found: {name}");
            return def.Fields;
        }

        // --- constructs --------------------------------------------------------------

        static bool GroupHasData(FlaggedGroup group, Dictionary<string, object?> data)
            => group.Fields.Any(f => !string.IsNullOrEmpty(f.Name) && data.ContainsKey(f.Name));

        /// <summary>Encode the groups whose fields the data carries; the absent ones cost no bytes.</summary>
        byte[] EncodeFlagged(FlaggedDef fd, Dictionary<string, object?> data)
        {
            var output = new List<byte>();
            var none = new Dictionary<string, int>();
            foreach (var group in fd.Groups)
            {
                if (!GroupHasData(group, data)) continue;
                // Routed through the run packing for consistency with every other field
                // list, not because anything needs it today: no `flagged` group in the
                // corpus holds a bit range. One that grows a run will pack correctly.
                var emit = group.Fields
                    .Where(gf => !string.IsNullOrEmpty(gf.Name) && !gf.Name.StartsWith("_")
                                 && gf.Type != FieldType.Number)
                    .ToList();
                output.AddRange(EncodeWithBitfieldRuns(emit, data, none, topLevel: false));
            }
            return output.ToArray();
        }

        /// <summary>
        /// Pack a byte_group's bit ranges back into their shared byte or bytes.
        /// </summary>
        /// <remarks>
        /// Without this the construct falls through to the plain field path, which finds no
        /// name and emits a single zero byte: the right length, the wrong bits, and no error
        /// to say so.
        /// </remarks>
        byte[] EncodeByteGroup(SchemaField group, Dictionary<string, object?> data)
        {
            ulong packed = 0;
            int size = group.Size > 0 ? group.Size : 1;

            foreach (var member in group.ByteGroup)
            {
                object? value = string.IsNullOrEmpty(member.Name) || member.Name.StartsWith("_")
                    ? 0.0
                    : (data.TryGetValue(member.Name, out var v) ? v : 0.0);
                value = ReverseModifiers(value, member);
                var (ok, numeric) = Helpers.ToFloat64(value);
                if (!ok)
                {
                    if (value is bool flag) numeric = flag ? 1 : 0;
                    else continue;
                }
                long raw = (long)Math.Round(numeric, MidpointRounding.ToEven);

                int bitStart = member.BitOffset;
                int bitLen = member.BitCount > 0 ? member.BitCount : 0;
                var bitRange = Helpers.ParseBitRange(member.RawType);
                if (bitRange != null)
                {
                    bitStart = bitRange.Value.start;
                    bitLen = bitRange.Value.end - bitRange.Value.start + 1;
                    // The base width is part of the member's type: u24[4:23] owns three
                    // bytes of the group, however small the group's own `size` says it is.
                    size = Math.Max(size, Math.Max(1, member.BitBaseBytes));
                }
                if (bitLen > 0)
                {
                    ulong mask = bitLen >= 64 ? ulong.MaxValue : (1UL << bitLen) - 1;
                    packed |= ((ulong)raw & mask) << bitStart;
                }
                else
                {
                    // A full-width member owns the group's bytes outright.
                    packed |= (ulong)raw;
                }
            }
            return Helpers.EncodeUint(packed, Math.Max(1, size), _endian);
        }

        /// <summary>
        /// Encode a repeat: its records back to back, and nothing else.
        /// </summary>
        /// <remarks>
        /// The framing costs no bytes here. `count: $n` and `byte_length: $len` name a field
        /// earlier in the list, which the main loop encodes from its own value, and
        /// `until: end` needs no header at all.
        /// </remarks>
        byte[] EncodeRepeat(SchemaField field, Dictionary<string, object?> data)
        {
            if (string.IsNullOrEmpty(field.Name) || !data.TryGetValue(field.Name, out var records)
                || records == null)
                return Array.Empty<byte>();

            List<object?> list = records switch
            {
                Dictionary<string, object?> single => new List<object?> { single },
                List<object?> many => many,
                _ => throw new InvalidOperationException(
                    $"repeat field '{field.Name}': expected a list of records, got {records.GetType().Name}")
            };

            var output = new List<byte>();
            foreach (var record in list)
            {
                if (record is not Dictionary<string, object?> asMap)
                    throw new InvalidOperationException($"repeat field '{field.Name}': "
                        + $"expected each record to be a mapping, got {record?.GetType().Name ?? "null"}");
                output.AddRange(EncodeFieldList(field.Fields, asMap));
            }
            return output.ToArray();
        }

        /// <summary>
        /// Rebuild a match construct's bytes from decoded output.
        /// </summary>
        /// <remarks>
        /// Two sources of the discriminator, encoded differently. An inline match with
        /// `length: N` read those bytes itself, so they are written back. A match on
        /// `field: $var` read nothing, so writing the discriminator here would duplicate the
        /// earlier field that produced it.
        /// </remarks>
        byte[] EncodeMatch(SchemaField match, Dictionary<string, object?> data)
        {
            object? discriminator = null;
            if (!string.IsNullOrEmpty(match.Name) && data.TryGetValue(match.Name, out var named))
            {
                discriminator = named;
            }
            else if (!string.IsNullOrEmpty(match.On))
            {
                var varName = match.On.TrimStart('$');
                if (data.TryGetValue(varName, out var byVar))
                {
                    discriminator = byVar;
                }
                else
                {
                    // The variable's name is often not the field's: rbs30x has
                    // `name: event_type` with `var: evt`, and matches on `$evt`. Decoded
                    // output is keyed by the field name, so encoding has to get from one to
                    // the other - and undo that field's lookup, since the output holds a
                    // label.
                    var source = FieldDeclaringVar(varName);
                    if (source != null && !string.IsNullOrEmpty(source.Name)
                        && data.TryGetValue(source.Name, out var byField))
                        discriminator = ReverseLookup(byField, source.Lookup);
                }
            }

            MatchCase? matched = null;
            if (discriminator != null)
            {
                foreach (var c in match.Cases)
                {
                    if (IsDefaultCase(c)) continue;
                    if (CaseMatches(discriminator, c.CaseValue))
                    {
                        matched = c;
                        break;
                    }
                }
            }
            // A known discriminator that matched no case takes the default, and takes it
            // ahead of the claimable-name heuristic: the schema said what an unmatched
            // value means, so guessing a case from the names present would contradict it.
            List<SchemaField>? fallbackFields = null;
            if (matched == null && discriminator != null)
            {
                matched = match.Cases.FirstOrDefault(IsDefaultCase);
                // The `default:` key beside `cases`, which nothing read here: a schema
                // declaring a fallback wrote the discriminator and nothing else, so
                // match-default-fields.yaml re-encoded `097f` as `09` (CR-2026-027).
                if (matched == null && match.MatchDefault is List<SchemaField> declared)
                    fallbackFields = declared;
            }
            // An inline match with no name reports nothing of itself, so the case has to be
            // recovered from which of its fields the data carries.
            if (fallbackFields == null)
            {
                matched ??= CasePresent(match.Cases, data);
                matched ??= match.Cases.FirstOrDefault(IsDefaultCase);
            }
            if (matched == null && fallbackFields == null)
                return Array.Empty<byte>();   // nothing in the data belongs to any case

            if (fallbackFields != null)
            {
                var fallbackOut = new List<byte>();
                if (match.Length > 0)
                {
                    var (ok, numeric) = Helpers.ToFloat64(discriminator);
                    var value = ok ? (long)Math.Round(numeric, MidpointRounding.ToEven) : 0L;
                    fallbackOut.AddRange(Helpers.EncodeUint((ulong)value, match.Length, _endian));
                }
                fallbackOut.AddRange(EncodeFieldList(fallbackFields, data));
                return fallbackOut.ToArray();
            }

            var output = new List<byte>();
            if (match.Length > 0)
            {
                var (ok, numeric) = Helpers.ToFloat64(discriminator);
                long value;
                if (ok)
                {
                    value = (long)Math.Round(numeric, MidpointRounding.ToEven);
                }
                else
                {
                    var fromKey = ParseIntAny(matched.CaseValue?.ToString() ?? "");
                    if (fromKey == null)
                        throw new InvalidOperationException(
                            $"match case {matched.CaseValue} names no single discriminator value");
                    value = fromKey.Value;
                }
                output.AddRange(WriteUint(value, match.Length, _endian));
            }
            output.AddRange(EncodeFieldList(matched.Fields, data));
            return output.ToArray();
        }

        static bool IsDefaultCase(MatchCase c)
            // The map form of `cases:` records a `default:` key as the literal string
            // rather than setting the flag, so both spellings are checked here.
            => c.IsDefault || (c.CaseValue as string) == "default";

        /// <summary>The case whose fields the data carries most of, or null.</summary>
        static MatchCase? CasePresent(List<MatchCase> cases, Dictionary<string, object?> data)
        {
            MatchCase? best = null;
            int bestHits = 0;
            foreach (var c in cases)
            {
                if (IsDefaultCase(c)) continue;
                int hits = c.Fields.Count(f => PayloadName(f) != null && data.ContainsKey(f.Name));
                if (hits > bestHits)
                {
                    best = c;
                    bestHits = hits;
                }
            }
            return best;
        }

        static bool CaseMatches(object? discriminator, object? caseValue)
        {
            if (caseValue == null) return false;
            var (ok, numeric) = Helpers.ToFloat64(discriminator);
            if (!ok)
                return string.Equals(discriminator?.ToString(), caseValue.ToString());
            long value = (long)Math.Round(numeric, MidpointRounding.ToEven);

            if (caseValue is List<object?> list)
                return list.Any(item =>
                {
                    var (itemOk, itemNum) = Helpers.ToFloat64(item);
                    return itemOk && (long)itemNum == value;
                });
            if (caseValue is Dictionary<string, object?> range)
            {
                var (minOk, min) = Helpers.ToFloat64(range.GetValueOrDefault("min"));
                var (maxOk, max) = Helpers.ToFloat64(range.GetValueOrDefault("max"));
                return (!minOk || value >= min) && (!maxOk || value <= max);
            }
            var (caseOk, caseNum) = Helpers.ToFloat64(caseValue);
            if (caseOk) return (long)caseNum == value;
            var parsed = ParseIntAny(caseValue.ToString() ?? "");
            return parsed != null && parsed.Value == value;
        }

        /// <summary>
        /// How well a candidate TLV case explains the data: how many of its fields are
        /// present, and whether it could have produced their values at all.
        /// </summary>
        /// <remarks>
        /// Two cases can define the same field name under different tags - am308 has `tvoc`
        /// under both [8, 125] (`div: 100`) and [8, 230] (raw). Only one of them wrote those
        /// bytes, and the arithmetic says which: 43.69 came from 4369 through `div: 100`
        /// exactly, while the raw case would need it rounded. A candidate that cannot
        /// reproduce the value it claims is ranked behind one that can.
        /// </remarks>
        /// <summary>
        /// Flatten a tlv case to the fields whose values are looked up in the case's own
        /// data map, descending into the constructs that carry no name of their own.
        /// </summary>
        /// <remarks>
        /// A byte_group or flagged field is nameless: its names sit in its group's fields,
        /// and EncodeByteGroup and EncodeFlagged both read them straight out of the same
        /// flat map. Collecting only the top level therefore found nothing to claim for
        /// such a case, so it never became a candidate and the channel encoded to no bytes
        /// <em>and no error</em>. hbi/mla20's case 0x20 is two of these, and
        /// _language-conformance/tlv-nameless-case.yaml pins it.
        /// <para>Deliberately not descended into: an object's or repeat's `fields`, whose
        /// values live in a nested map under the field's own name rather than in this map,
        /// so claiming their members would claim names this map does not have - the field's
        /// own name is claimed instead, which is what the nested objects in the milesight
        /// schemas rely on; and a nested match or tlv, where which branch supplies a name
        /// depends on the data, so claiming every branch's names would over-claim.</para>
        /// </remarks>
        static List<SchemaField> ClaimableFields(List<SchemaField> caseFields)
        {
            var claimable = new List<SchemaField>();
            CollectClaimable(caseFields, claimable);
            return claimable;
        }

        static void CollectClaimable(List<SchemaField>? fields, List<SchemaField> sink)
        {
            if (fields == null) return;
            foreach (var f in fields)
            {
                if (f.ByteGroup.Count > 0)
                {
                    CollectClaimable(f.ByteGroup, sink);
                    continue;
                }
                if (f.Flagged?.Groups != null)
                {
                    foreach (var group in f.Flagged.Groups)
                        CollectClaimable(group.Fields, sink);
                    continue;
                }
                if (PayloadName(f) == null) continue;
                sink.Add(f);
            }
        }

        (int matches, bool lossless) CaseFidelity(List<SchemaField> caseFields,
            Dictionary<string, object?> data)
        {
            int matches = 0;
            bool lossless = true;
            foreach (var f in ClaimableFields(caseFields))
            {
                var name = PayloadName(f);
                if (name == null || !data.TryGetValue(name, out var reported)) continue;
                matches++;
                var (ok, numeric) = Helpers.ToFloat64(ReverseLookup(reported, f.Lookup));
                if (!ok) continue;
                double value;
                try
                {
                    value = ReverseCanonicalModifiers(
                        ReverseTransformStages(numeric, f.Transform), f);
                }
                catch (InvalidOperationException)
                {
                    lossless = false;
                    continue;
                }
                if (Math.Abs(value - Math.Round(value, MidpointRounding.ToEven)) > 1e-9)
                    lossless = false;
                var bounds = IntegerRange(f.Type);
                if (bounds != null)
                {
                    double rounded = Math.Round(value, MidpointRounding.ToEven);
                    if (rounded < bounds.Value.low || rounded > bounds.Value.high)
                        // It does not fit the field, so this case cannot have written it.
                        lossless = false;
                }
            }
            return (matches, lossless);
        }

        /// <summary>
        /// Rebuild a TLV payload from decoded output.
        /// </summary>
        /// <remarks>
        /// A case whose fields are all absent is not emitted. A case that cannot be encoded -
        /// a wildcard tag (PS-270), or a field the data does not carry - throws, so the
        /// caller records it against the payload rather than writing a wrong tag.
        /// </remarks>
        byte[] EncodeTLV(SchemaField tlv, Dictionary<string, object?> data)
        {
            if (tlv.TLVCases == null || tlv.TLVCases.Count == 0) return Array.Empty<byte>();
            int lengthSize = Math.Max(0, tlv.LengthSize);
            var order = _order ?? data.Keys.ToList();

            var candidates = new List<(int position, int lossPenalty, int matches,
                string caseKey, List<SchemaField> caseFields, List<string> claimed)>();

            foreach (var (caseKey, caseFields) in tlv.TLVCases)
            {
                if (caseKey == "default") continue;
                var claimed = ClaimableFields(caseFields)
                    .Select(PayloadName)
                    .Where(n => n != null && data.ContainsKey(n))
                    .Select(n => n!)
                    .ToList();
                if (claimed.Count == 0) continue;
                int position = claimed.Min(n =>
                {
                    int index = order.ToList().IndexOf(n);
                    return index < 0 ? int.MaxValue : index;
                });
                var (matches, lossless) = CaseFidelity(caseFields, data);
                candidates.Add((position, lossless ? 0 : 1, matches, caseKey, caseFields, claimed));
            }

            // Payload order first, then the case that can reproduce the value, then the
            // fuller explanation of it.
            candidates.Sort((a, b) =>
            {
                if (a.position != b.position) return a.position.CompareTo(b.position);
                if (a.lossPenalty != b.lossPenalty) return a.lossPenalty.CompareTo(b.lossPenalty);
                return b.matches.CompareTo(a.matches);
            });

            // Every decoded field belongs to one channel. Without this a name defined under
            // two tags emits both of them, so am308 grows an extra channel.
            var spent = new HashSet<string>();
            var emitted = new List<(int position, string caseKey, List<SchemaField> caseFields)>();
            foreach (var candidate in candidates)
            {
                if (candidate.claimed.All(spent.Contains)) continue;
                foreach (var name in candidate.claimed) spent.Add(name);
                emitted.Add((candidate.position, candidate.caseKey, candidate.caseFields));
            }
            emitted.Sort((a, b) => a.position.CompareTo(b.position));

            var output = new List<byte>();
            foreach (var (_, caseKey, caseFields) in emitted)
            {
                var tag = EncodeTLVTag(caseKey, tlv);
                var value = EncodeFieldList(caseFields, data);
                output.AddRange(tag);
                if (lengthSize > 0)
                    output.AddRange(WriteUint(value.Length, lengthSize, _endian));
                output.AddRange(value);
            }
            return output.ToArray();
        }

        /// <summary>
        /// Rebuild a TLV tag from the case key that matched it while decoding.
        /// </summary>
        /// <remarks>
        /// The composite form carries the tag values in the key - "[3, 103]" against
        /// `tag_key: [channel_id, channel_type]` - so encoding reads them back out and
        /// writes each through its own `tag_fields` entry. A key using `!` or `*` (PS-270)
        /// names no single tag, so it cannot be encoded.
        /// </remarks>
        byte[] EncodeTLVTag(string caseKey, SchemaField tlv)
        {
            if (tlv.TagFields.Count > 0 && tlv.TagKey != null)
            {
                var text = caseKey.Trim();
                if (text.StartsWith("["))
                    text = text.TrimStart('[').TrimEnd(']');
                var parts = text.Split(',');
                var names = tlv.TagKey is List<object?> keyList
                    ? keyList.Select(k => k?.ToString() ?? "").ToList()
                    : new List<string> { tlv.TagKey.ToString() ?? "" };
                if (parts.Length != names.Count)
                    throw new InvalidOperationException(
                        $"TLV case '{caseKey}' does not match tag_key [{string.Join(", ", names)}]");

                var values = new Dictionary<string, long>();
                for (int i = 0; i < parts.Length; i++)
                {
                    var part = parts[i].Trim().Trim('"', '\'');
                    if (part == "*" || part.StartsWith("!"))
                        throw new InvalidOperationException($"TLV case '{caseKey}' matches a "
                            + "range of tags, so encoding cannot choose one");
                    var parsed = ParseIntAny(part);
                    if (parsed == null)
                        throw new InvalidOperationException($"TLV case '{caseKey}' has a tag "
                            + $"element that is not a number: {part}");
                    values[names[i]] = parsed.Value;
                }

                var output = new List<byte>();
                foreach (var tf in tlv.TagFields)
                {
                    if (!values.TryGetValue(tf.Name, out var value))
                        throw new InvalidOperationException(
                            $"TLV case '{caseKey}' gives no value for '{tf.Name}'");
                    output.AddRange(EncodeField(tf, value));
                }
                return output.ToArray();
            }

            int tagSize = tlv.TagSize > 0 ? tlv.TagSize : 1;
            var single = ParseIntAny(caseKey.Trim());
            if (single == null)
                throw new InvalidOperationException($"TLV case '{caseKey}' is not a tag value");
            return WriteUint(single.Value, tagSize, _endian);
        }

        /// <summary>The field that declared `var: name`, searched anywhere in the schema.</summary>
        SchemaField? FieldDeclaringVar(string varName)
        {
            var found = SearchForVar(_schema.Fields, varName);
            if (found != null) return found;
            if (_schema.Ports != null)
                foreach (var port in _schema.Ports.Values)
                {
                    found = SearchForVar(port.Fields, varName);
                    if (found != null) return found;
                }
            if (_schema.Definitions != null)
                foreach (var def in _schema.Definitions.Values)
                {
                    found = SearchForVar(def.Fields, varName);
                    if (found != null) return found;
                }
            return null;
        }

        static SchemaField? SearchForVar(List<SchemaField> fields, string varName)
        {
            foreach (var f in fields)
            {
                if (f.Var == varName && !string.IsNullOrEmpty(f.Name)) return f;
                foreach (var nested in NestedFieldLists(f))
                {
                    var found = SearchForVar(nested, varName);
                    if (found != null) return found;
                }
            }
            return null;
        }

        /// <summary>Every field list one field can contain, so a search reaches the whole schema.</summary>
        static IEnumerable<List<SchemaField>> NestedFieldLists(SchemaField f)
        {
            if (f.Fields.Count > 0) yield return f.Fields;
            if (f.ByteGroup.Count > 0) yield return f.ByteGroup;
            if (f.TagFields.Count > 0) yield return f.TagFields;
            foreach (var c in f.Cases)
                if (c.Fields.Count > 0) yield return c.Fields;
            if (f.TLVCases != null)
                foreach (var caseFields in f.TLVCases.Values) yield return caseFields;
            if (f.Flagged != null)
                foreach (var g in f.Flagged.Groups) yield return g.Fields;
            if (f.MatchInline != null) yield return new List<SchemaField> { f.MatchInline };
            if (f.TLVInline != null) yield return new List<SchemaField> { f.TLVInline };
        }

        // --- reversing the modifiers -------------------------------------------------

        /// <summary>
        /// Undo what decoding applied, in the opposite order: the lookup, then the transform
        /// chain, then the canonical modifiers.
        /// </summary>
        /// <remarks>
        /// The lookup comes first, before the numeric check. A lookup's whole purpose is to
        /// report a label, so the value arriving here is a string; reversing it after a
        /// numeric guard leaves the reversal dead code for every label.
        /// </remarks>
        object? ReverseModifiers(object? value, SchemaField field)
        {
            var reversed = ReverseLookup(value, field.Lookup);

            if (reversed is string label && field.Lookup is { Count: > 0 })
                // The label is not in the table, so it came from the mapping's `default`,
                // which stands for every value the table does not list (PS-269) - there is
                // no original to recover.
                throw new InvalidOperationException($"'{label}' is not a label in the lookup "
                    + $"for '{field.Name}'; a `default` label matches any unmapped value, so "
                    + "the value that produced it cannot be recovered");

            var (ok, numeric) = Helpers.ToFloat64(reversed);
            if (!ok)
            {
                if (reversed is bool flag) return flag;
                return reversed;
            }

            double result = ReverseCanonicalModifiers(
                ReverseTransformStages(numeric, field.Transform), field);

            if (field.Type is FieldType.F16 or FieldType.F32 or FieldType.F64)
                return result;
            // Half-to-even, matching the reference interpreter's rounding.
            return Math.Round(result, MidpointRounding.ToEven);
        }

        /// <summary>Map a label back to its integer.</summary>
        static object? ReverseLookup(object? value, Dictionary<int, string>? lookup)
        {
            if (lookup == null || lookup.Count == 0) return value;
            if (value is not string label) return value;
            foreach (var (key, entry) in lookup)
                if (entry == label)
                    return (double)key;
            return value;
        }

        /// <summary>
        /// Undo a transform chain, innermost stage last.
        /// </summary>
        /// <remarks>
        /// Decoding runs the stages in order, so encoding runs their inverses in reverse
        /// order. Rounding and clamping stages are identity in reverse: the precision they
        /// discarded cannot be recovered, and for a value that was in range they changed
        /// nothing. Genuinely irreversible arithmetic throws, so a caller reports the field
        /// rather than writing a wrong byte.
        /// </remarks>
        static double ReverseTransformStages(double value, List<TransformStage> stages)
        {
            for (int i = stages.Count - 1; i >= 0; i--)
            {
                var stage = stages[i];
                if (stage.Add != null) value -= stage.Add.Value;
                else if (stage.Sub != null) value += stage.Sub.Value;
                else if (stage.Mult != null)
                {
                    if (stage.Mult.Value == 0)
                        throw new InvalidOperationException("cannot undo 'mult: 0'");
                    value /= stage.Mult.Value;
                }
                else if (stage.Div != null) value *= stage.Div.Value;
                else if (stage.Sqrt)
                    throw new InvalidOperationException("cannot undo transform stage: sqrt");
                else if (stage.Abs)
                    throw new InvalidOperationException("cannot undo transform stage: abs");
                else if (stage.Log10)
                    throw new InvalidOperationException("cannot undo transform stage: log10");
                else if (stage.Log)
                    throw new InvalidOperationException("cannot undo transform stage: log");
                else if (stage.Pow != null)
                    throw new InvalidOperationException("cannot undo transform stage: pow");
                // A rounding or bounding stage is identity in reverse.
            }
            return value;
        }

        /// <summary>
        /// Invert the canonical modifiers. Decoding computes ((raw * mult) / div) + add, so
        /// encoding subtracts add, multiplies by div, then divides by mult (PS-101).
        /// </summary>
        static double ReverseCanonicalModifiers(double value, SchemaField field)
        {
            if (field.Add != null) value -= field.Add.Value;
            if (field.Div != null) value *= field.Div.Value;
            if (field.Mult is { } mult && mult != 0) value /= mult;
            return value;
        }

        // --- one field's bytes -------------------------------------------------------

        byte[] EncodeField(SchemaField field, object? value)
        {
            string endian = string.IsNullOrEmpty(field.Endian) ? _endian : field.Endian!;

            if (field.Type == FieldType.Bits || Helpers.ParseBitRange(field.RawType) != null)
            {
                // A bit range outside a byte_group: the reference interpreter writes the
                // value into one byte rather than reconstructing a byte it does not own.
                var (bitsOk, bitsValue) = Helpers.ToFloat64(value);
                return new[] { (byte)((bitsOk ? (long)bitsValue : 0L) & 0xFF) };
            }

            if (field.Type == FieldType.Bool)
            {
                bool set = value is bool flag
                    ? flag
                    : Helpers.ToFloat64(value) is (true, not 0);
                return new[] { (byte)(set ? 1 : 0) };
            }

            if (field.Type == FieldType.Enum)
                return EncodeEnum(field, value, endian);

            switch (field.Type)
            {
                case FieldType.U8 or FieldType.U16 or FieldType.U24 or FieldType.U32
                        or FieldType.U64:
                {
                    long raw = RequireNumber(field, value);
                    return WriteUint(raw, Helpers.InferLengthFromType(field.Type), endian);
                }
                case FieldType.S8 or FieldType.S16 or FieldType.S24 or FieldType.S32
                        or FieldType.S64:
                {
                    long raw = RequireNumber(field, value);
                    int size = Helpers.InferLengthFromType(field.Type);
                    long half = 1L << (size * 8 - 1);
                    if (size < 8 && (raw < -half || raw >= half))
                        throw new InvalidOperationException(
                            $"{raw} does not fit {size} signed bytes");
                    return Helpers.EncodeSint(raw, size, endian);
                }
                case FieldType.F16:
                {
                    var (ok, numeric) = Helpers.ToFloat64(value);
                    if (!ok) throw NotANumber(field, value);
                    return Helpers.EncodeFloat16(numeric, endian);
                }
                case FieldType.F32:
                {
                    var (ok, numeric) = Helpers.ToFloat64(value);
                    if (!ok) throw NotANumber(field, value);
                    return Helpers.EncodeFloat32((float)numeric, endian);
                }
                case FieldType.F64:
                {
                    var (ok, numeric) = Helpers.ToFloat64(value);
                    if (!ok) throw NotANumber(field, value);
                    return Helpers.EncodeFloat64(numeric, endian);
                }
                case FieldType.Skip:
                    return new byte[field.Length > 0 ? field.Length : 0];
                case FieldType.Bytes or FieldType.Hex:
                {
                    var raw = ToBytes(field, value);
                    return Pad(raw, EncodeLength(field, raw.Length));
                }
                case FieldType.Ascii or FieldType.String:
                {
                    var raw = System.Text.Encoding.UTF8.GetBytes(value?.ToString() ?? "");
                    return Pad(raw, EncodeLength(field, raw.Length));
                }
            }

            throw new InvalidOperationException($"Cannot encode type: {field.Type}");
        }

        byte[] EncodeEnum(SchemaField field, object? value, string endian)
        {
            int size = field.Base switch
            {
                "u16" or "s16" => 2,
                "u24" or "s24" => 3,
                "u32" or "s32" => 4,
                _ => 1
            };
            if (field.Values != null)
                foreach (var (key, label) in field.Values)
                    if (label == value as string)
                        return WriteUint(key, size, endian);
            var (ok, numeric) = Helpers.ToFloat64(value);
            if (!ok)
                // The label came from `default`, which stands for every unmapped value
                // (PS-068), so there is no original to recover.
                throw new InvalidOperationException($"enum field '{field.Name}': '{value}' is "
                    + "not one of its declared values");
            return WriteUint((long)Math.Round(numeric, MidpointRounding.ToEven), size, endian);
        }

        /// <summary>
        /// Parse a bitfield_string back into its packed integer.
        /// </summary>
        /// <remarks>
        /// Each declared part reads one delimited segment, in the base the part declares.
        /// The hex parts are lowercase on output (PS-074); parsing is case-insensitive.
        /// </remarks>
        byte[] EncodeBitfieldString(SchemaField field, string value)
        {
            var text = value;
            var prefix = field.Prefix ?? "";
            if (prefix.Length > 0 && text.StartsWith(prefix))
                text = text[prefix.Length..];
            var delimiter = field.Delimiter ?? ".";
            var segments = text.Split(delimiter);

            ulong packed = 0;
            for (int i = 0; i < field.Parts.Count; i++)
            {
                var part = field.Parts[i];
                if (part.Count < 2) continue;
                var (offOk, bitOff) = Helpers.ToInt(part[0]);
                var (lenOk, bitLen) = Helpers.ToInt(part[1]);
                if (!offOk || !lenOk) continue;
                string format = part.Count >= 3 && part[2] is string f ? f : "decimal";
                string segment = i < segments.Length ? segments[i].Trim() : "0";
                ulong raw;
                bool parsed = format == "hex"
                    ? ulong.TryParse(segment, System.Globalization.NumberStyles.HexNumber,
                        System.Globalization.CultureInfo.InvariantCulture, out raw)
                    : ulong.TryParse(segment, out raw);
                if (!parsed)
                    throw new InvalidOperationException($"bitfield_string field "
                        + $"'{field.Name}': segment '{segment}' is not {format}");
                ulong mask = bitLen >= 64 ? ulong.MaxValue : (1UL << bitLen) - 1;
                packed |= (raw & mask) << bitOff;
            }
            int length = field.Length > 0 ? field.Length : 2;
            return Helpers.EncodeUint(packed, length, _endian);
        }

        // --- primitives --------------------------------------------------------------

        static long RequireNumber(SchemaField field, object? value)
        {
            var (ok, numeric) = Helpers.ToFloat64(value);
            if (!ok)
            {
                if (value is bool flag) return flag ? 1 : 0;
                throw NotANumber(field, value);
            }
            return (long)Math.Round(numeric, MidpointRounding.ToEven);
        }

        static InvalidOperationException NotANumber(SchemaField field, object? value)
            => new($"field '{field.Name}': expected a number, got "
                + $"{value?.GetType().Name ?? "null"} {value}");

        /// <summary>Write an unsigned integer, refusing a value the width cannot hold.</summary>
        static byte[] WriteUint(long value, int size, string endian)
        {
            if (size <= 0) return Array.Empty<byte>();
            if (size < 8)
            {
                long max = (1L << (size * 8)) - 1;
                if (value < 0 || value > max)
                    throw new InvalidOperationException(
                        $"{value} does not fit {size} unsigned bytes");
            }
            return Helpers.EncodeUint((ulong)value, size, endian);
        }

        /// <summary>The byte count to write for a variable-length field.</summary>
        static int EncodeLength(SchemaField field, int natural)
            // `length: remaining` has no fixed count when encoding (PS-014) - the value
            // supplies it. It arrives as the negative sentinel the parser stores.
            => field.Length > 0 ? field.Length : Math.Max(0, natural);

        static byte[] Pad(byte[] raw, int length)
        {
            if (raw.Length == length) return raw;
            var output = new byte[length];
            Array.Copy(raw, output, Math.Min(raw.Length, length));
            return output;
        }

        static byte[] ToBytes(SchemaField field, object? value)
        {
            if (value is byte[] raw) return raw;
            if (value is List<object?> list)
                return list.Select(item =>
                {
                    var (ok, numeric) = Helpers.ToFloat64(item);
                    return (byte)(ok ? (long)numeric & 0xFF : 0);
                }).ToArray();
            // CR-2026-008/PS-281 makes the decoder report a byte sequence as a lowercase
            // hex string, so that is the form encoding has to accept for a round trip.
            var text = (value?.ToString() ?? "").Replace(" ", "").Replace(":", "");
            if (text.Length % 2 != 0)
                throw new InvalidOperationException($"field '{field.Name}': expected hex, got "
                    + $"'{value}' (odd number of digits)");
            var output = new byte[text.Length / 2];
            for (int i = 0; i < output.Length; i++)
            {
                if (!byte.TryParse(text.AsSpan(i * 2, 2),
                        System.Globalization.NumberStyles.HexNumber,
                        System.Globalization.CultureInfo.InvariantCulture, out output[i]))
                    throw new InvalidOperationException(
                        $"field '{field.Name}': expected hex, got '{value}'");
            }
            return output;
        }

        /// <summary>Inclusive bounds a field of this type can hold, or null if not an integer.</summary>
        static (double low, double high)? IntegerRange(FieldType type) => type switch
        {
            FieldType.U8 or FieldType.U16 or FieldType.U24 or FieldType.U32 or FieldType.U64
                => (0, Math.Pow(2, 8.0 * Helpers.InferLengthFromType(type)) - 1),
            FieldType.S8 or FieldType.S16 or FieldType.S24 or FieldType.S32 or FieldType.S64
                => (-Math.Pow(2, 8.0 * Helpers.InferLengthFromType(type) - 1),
                    Math.Pow(2, 8.0 * Helpers.InferLengthFromType(type) - 1) - 1),
            _ => null
        };

        /// <summary>A field's name as it appears in decoded output, or null if it never appears.</summary>
        static string? PayloadName(SchemaField f)
        {
            if (string.IsNullOrEmpty(f.Name) || f.Name.StartsWith("_")) return null;
            if (f.Type == FieldType.Number) return null;
            return f.Name;
        }

        /// <summary>Read an integer written as decimal, 0x hex or 0b binary.</summary>
        static long? ParseIntAny(string text)
        {
            var trimmed = text.Trim().Trim('"', '\'');
            if (trimmed.Length == 0) return null;
            bool negative = trimmed.StartsWith("-");
            if (negative) trimmed = trimmed[1..];
            long magnitude;
            if (trimmed.Length > 2 && trimmed.StartsWith("0x", StringComparison.OrdinalIgnoreCase))
            {
                if (!long.TryParse(trimmed[2..], System.Globalization.NumberStyles.HexNumber,
                        System.Globalization.CultureInfo.InvariantCulture, out magnitude))
                    return null;
            }
            else if (trimmed.Length > 2
                && trimmed.StartsWith("0b", StringComparison.OrdinalIgnoreCase))
            {
                try
                {
                    magnitude = Convert.ToInt64(trimmed[2..], 2);
                }
                catch (Exception)
                {
                    return null;
                }
            }
            else if (!long.TryParse(trimmed, out magnitude))
            {
                return null;
            }
            return negative ? -magnitude : magnitude;
        }
    }
}
