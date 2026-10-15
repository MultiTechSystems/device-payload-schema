// Copyright (c) 2024-2026 Multitech Systems, Inc.
// SPDX-License-Identifier: MIT

namespace PayloadSchema;

/// <summary>
/// The outcome of encoding a data dictionary to payload bytes.
/// </summary>
/// <remarks>
/// Mirrors the reference interpreter's EncodeResult: a field that cannot be encoded is
/// recorded against the payload rather than aborting it, so one unrecoverable value does
/// not cost every other field its bytes. A required value the data does not carry is an
/// error naming the field (PS-551, CR-2026-134), as is a value that cannot have produced
/// any legal bytes. Where there is any error, <see cref="Payload"/> is empty: the bytes
/// written before it are a frame short of the rest.
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
    /// <summary>The FPort the encode used (PS-581): the port entry chosen for a schema with
    /// `ports`, else the caller's, else the document's `fPort`; 0 where there is none.</summary>
    public int FPort { get; internal set; }

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
    /// <summary>A number as JavaScript writes it, for the messages of the helpers beside this class.</summary>
    internal static string JsNumber(object? value) => Encoding.JsNumber(value);

    /// <param name="decodeRecord">The `_decode` that <see cref="SchemaDecoder.Interpret"/>
    /// produced with decodeRecord (PS-572), or null; it may instead arrive inside
    /// <paramref name="data"/> as `_decode`, and the argument wins. The encoder follows it
    /// (PS-573): each recorded case and discriminator, internal field and flags value is used
    /// where the input gives none, and input contradicting it is rejected. Where neither the
    /// input nor the record gives a match's case, this encoder derives it (PS-551's
    /// nested-match rule, PS-563, PS-564), as PS-574 permits.</param>
    public static EncodeResult Encode(PayloadSchemaDefinition schema,
        Dictionary<string, object?> data, IReadOnlyList<string>? order = null,
        Dictionary<string, object?>? decodeRecord = null)
        => EncodeWithPort(schema, data, 0, order, null, decodeRecord);

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
        string? direction = null, Dictionary<string, object?>? decodeRecord = null)
    {
        // PS-581: which port entry this encode uses, and the FPort it reports. A rejection
        // is reported through the result, as every encode failure is.
        int used;
        try
        {
            used = ChoosePort(schema, fPort, direction);
        }
        catch (InvalidOperationException ex) when (ex.Message.Contains("(PS-581)"))
        {
            var rejected = new EncodeResult();
            rejected.Errors.Add(ex.Message);
            return rejected;
        }
        SchemaDirection.Check(schema, used, direction);
        var result = new Encoding(schema, ResolveFieldsForEncode(schema, used), order).Run(data, decodeRecord);
        result.FPort = used;
        return result;
    }

    /// <summary>
    /// The FPort whose entry an encode uses, by PS-581 (CR-2026-169). A caller's FPort
    /// (non-zero; a port is 1 to 255) chooses its entry, or the `default` entry where none
    /// lists it, and an entry declared `uplink` is refused only where the direction is
    /// `downlink`. Without one: the single entry declaring `downlink`, else the single
    /// downlink-capable entry (`downlink`, `both` or no declaration, PS-287), else the input
    /// is rejected naming the candidates. `default` is never chosen without an FPort, having
    /// no number to report. A schema without `ports` gives the caller's FPort, else the
    /// document's, else 0.
    /// </summary>
    public static int ChoosePort(PayloadSchemaDefinition schema, int fPort, string? direction)
    {
        if (schema.Ports == null || schema.Ports.Count == 0)
            return fPort != 0 ? fPort : schema.DocFPort;
        static string Join(IEnumerable<int> ports)
        {
            var list = ports.ToList();
            return list.Count == 0 ? "nothing" : string.Join(", ", list);
        }
        List<int> Numbered(Func<PortDef, bool> ok) => schema.Ports
            .Where(kv => kv.Value != null && ok(kv.Value) && int.TryParse(kv.Key, out _))
            .Select(kv => int.Parse(kv.Key)).OrderBy(n => n).ToList();
        if (fPort != 0)
        {
            if (!schema.Ports.TryGetValue(fPort.ToString(), out var entry)
                && !schema.Ports.TryGetValue("default", out entry))
                throw new InvalidOperationException(
                    $"FPort {fPort} matches no port entry and schema '{schema.Name}' declares no default; "
                    + $"it defines {Join(Numbered(_ => true))} (PS-581)");
            if (entry.Direction == SchemaDirection.Uplink && direction == SchemaDirection.Downlink)
                throw new InvalidOperationException(
                    $"FPort {fPort} is declared direction:uplink and takes no downlink to encode (PS-581)");
            return fPort;
        }
        var tiers = new (string name, Func<PortDef, bool> ok)[]
        {
            ("declared downlink", e => e.Direction == SchemaDirection.Downlink),
            ("downlink-capable", e => e.Direction == null || e.Direction == SchemaDirection.Downlink
                                      || e.Direction == SchemaDirection.Both),
        };
        foreach (var (name, ok) in tiers)
        {
            var candidates = Numbered(ok);
            if (candidates.Count == 1) return candidates[0];
            if (candidates.Count > 1)
                throw new InvalidOperationException(
                    $"no FPort was given, and schema '{schema.Name}' has {candidates.Count} {name} port entries "
                    + $"({Join(candidates)}); give the FPort (PS-581)");
        }
        throw new InvalidOperationException(
            $"no FPort was given, and schema '{schema.Name}' has no port entry that takes a downlink (PS-581)");
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

    /// <summary>A literal: a string, number or boolean declaring <c>value</c> (PS-357).</summary>
    static bool IsLiteral(SchemaField f)
        => f.Type is FieldType.Number or FieldType.String or FieldType.Boolean && f.Value != null;

    /// <summary>A computed field: <c>type: number</c> (or integer) with no <c>value</c>.</summary>
    static bool IsComputed(SchemaField f) => f.Type == FieldType.Number && f.Value == null || IsTemplate(f);

    /// <summary>A template string (PS-590, CR-2026-176): computed, writing no bytes; an input
    /// value for it is ignored.</summary>
    static bool IsTemplate(SchemaField f) => f.Type == FieldType.String && f.Template != null;

    /// <summary>
    /// Every name a field list declares, anywhere in it, with whether some declaration
    /// reads payload bytes or computes from them, and the values its literals give.
    /// Mirrors declared_sources, for the empty-payload check (PS-442).
    /// </summary>
    public static Dictionary<string, (bool reads, List<object?> literals)> DeclaredSources(
        List<SchemaField> fields)
    {
        var found = new Dictionary<string, (bool reads, List<object?> literals)>();
        void Note(string name, bool literal, object? value)
        {
            if (!found.TryGetValue(name, out var entry)) entry = (false, new List<object?>());
            if (literal) entry.literals.Add(value); else entry.reads = true;
            found[name] = entry;
        }
        void WalkList(IEnumerable<SchemaField>? list)
        {
            if (list == null) return;
            foreach (var f in list) Walk(f);
        }
        void Walk(SchemaField f)
        {
            bool construct = f.MatchInline != null || f.ByteGroup.Count > 0 || f.Flagged != null
                || f.TLVInline != null || f.Ref2 != null || f.Records != null;
            if (!construct && !string.IsNullOrEmpty(f.Name))
            {
                if (IsLiteral(f)) Note(f.Name, true, f.Value); else Note(f.Name, false, null);
            }
            WalkList(f.Fields);
            WalkList(f.ByteGroup);
            WalkList(f.TagFields);
            WalkList(f.Trailer);
            foreach (var c in f.Cases) WalkList(c.Fields);
            if (f.MatchDefault is List<SchemaField> d) WalkList(d);
            if (f.Flagged != null) foreach (var g in f.Flagged.Groups) WalkList(g.Fields);
            if (f.TLVCases != null) foreach (var list in f.TLVCases.Values) WalkList(list);
            if (f.Records != null) foreach (var list in f.Records) WalkList(list);
            if (f.TLVInline != null) Walk(f.TLVInline);
            // An inline match's own name is its discriminator's, which reads bytes.
            if (f.MatchInline != null) Walk(f.MatchInline);
        }
        WalkList(fields);
        return found;
    }

    /// <summary>
    /// PS-442: an encoder that wrote no bytes, where the input gives a value that only a
    /// field reading payload bytes (or computing from them) reports, dropped that field's
    /// bytes without a word. The first such name, in the input's order. A value that can
    /// be written in no bytes is not one: an empty list, mapping or string, a value a
    /// literal of that name gives, and an internal or absent one. Mirrors
    /// empty_payload_error.
    /// </summary>
    public static string? EmptyPayloadError(List<SchemaField> fields, Dictionary<string, object?> data)
    {
        Dictionary<string, (bool reads, List<object?> literals)>? sources = null;
        foreach (var (key, value) in data)
        {
            if (key.StartsWith("_") || value == null) continue;
            if (value is string text && text.Length == 0) continue;
            if (value is System.Collections.ICollection coll && coll.Count == 0) continue;
            sources ??= DeclaredSources(fields);
            if (!sources.TryGetValue(key, out var entry) || !entry.reads) continue;
            if (entry.literals.Any(l => Helpers.SameLabel(l, value))) continue;
            return $"Encoding wrote no bytes, though the input gives '{key}', which the "
                + "schema reads from the payload (PS-442)";
        }
        return null;
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

        /// <summary>The decode record followed (PS-573); null when none was given.</summary>
        DecodeTrace? _trace;

        internal EncodeResult Run(Dictionary<string, object?> data,
            Dictionary<string, object?>? decodeRecord = null)
        {
            data ??= new Dictionary<string, object?>();
            // PS-573: the record, given beside the input or inside it as `_decode`; `_meta`
            // describes a decode and is not input either.
            if (data.ContainsKey("_decode") || data.ContainsKey("_meta"))
            {
                decodeRecord ??= data.GetValueOrDefault("_decode") as Dictionary<string, object?>;
                data = data.Where(kv => kv.Key is not ("_decode" or "_meta"))
                    .ToDictionary(kv => kv.Key, kv => kv.Value);
            }
            if (decodeRecord != null)
            {
                _trace = new DecodeTrace(_schema, decodeRecord);
                foreach (var key in data.Keys) _trace.InputNames.Add(key);
            }
            var output = new List<byte>();
            _t564.Clear();
            _d564 = 0;          // PS-564 is checked from here, outside any tlv or repeat

            // PS-551: a value the input omits but the rest of it fixes - a repeat's count or
            // byte_length field, a match's discriminator, a flagged mask - is derived before
            // anything is written, and a supplied one that disagrees is rejected. The flagged
            // mask was the one derivation already made, and it overwrote a supplied mask.
            try
            {
                data = DeriveInputs(_fields, data);
            }
            catch (Exception e)
            {
                _result.Errors.Add(e.Message);
                return _result;
            }

            // DeriveInputs also makes the masks the groups fix visible to the constructs
            // before the block, as the decoder's variables are: dnt binds its mask with an
            // inline match (CR-2026-136).

            // Bit ranges sharing a byte are packed together rather than a byte apiece
            // (CR-2026-024), so the whole list is walked at once: a run cannot be found
            // by looking at one field in isolation.
            var frame = EncodeWithBitfieldRuns(_fields, data, topLevel: true);
            var frameMeta = MetaOf(frame);
            output.AddRange(frame);
            if (_result.Errors.Count == 0)
            {
                try
                {
                    FrameMeta.Check(frame, MetaOf(frame));             // PS-551 (CR-2026-141)
                }
                catch (InvalidOperationException e)
                {
                    _result.Errors.Add(e.Message);
                }
            }

            if (_result.Errors.Count == 0)
            {
                // PS-564: the bytes from each `remaining` match to the end select its case.
                foreach (var check in _t564)
                {
                    var selected = RemainingSelection(check.Match, check.Tail);
                    if (!SameSelection(selected, check.Chosen))
                    {
                        _result.Errors.Add(RemainingMismatch(check.Match, check.Tail, selected,
                            check.Chosen));
                        break;
                    }
                }
            }

            if (_result.Errors.Count == 0)
            {
                // PS-587 (CR-2026-174): each checksum over the finished frame. One a construct
                // encoded apart from the frame - a tlv entry, a repeat element - did not reach
                // it, since the bytes before it were not written when it was.
                try
                {
                    var filled = (byte[])frame.Clone();
                    Checksum.Fill(filled, frameMeta?.Checksums ?? new List<ChecksumSlot>(), _checksumOpen);
                    if (_checksumOpen.Count > 0)
                        throw new FieldErrorException(Checksum.UnwrittenMessage(_checksumOpen[0].Name));
                    output.Clear();
                    output.AddRange(filled);
                }
                catch (InvalidOperationException e)
                {
                    _result.Errors.Add(e.Message);
                }
            }

            if (_result.Errors.Count == 0 && frame.Length == 0)
            {
                // PS-442: the last line against a construct that wrote nothing and said so
                // to no one - a match with no case chosen, a tlv entry no case claimed.
                var empty = EmptyPayloadError(_fields, data);
                if (empty != null) _result.Errors.Add(empty);
            }

            // A rejected input produces no payload: the bytes written before the error are
            // a frame short of the rest, as Go has always returned nothing.
            _result.Payload = _result.Errors.Count > 0 ? Array.Empty<byte>() : output.ToArray();
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
            List<SchemaField> siblings, FrameBuffer? frame = null)
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
                return EncodeMatch(field.MatchInline, data, frame?.Pending, field, siblings);
            if (field.Flagged != null)
                return EncodeFlagged(field.Flagged, data);

            switch (field.Type)
            {
                case FieldType.Repeat:
                    return EncodeRepeat(field, data, siblings);
                case FieldType.Object:
                {
                    // A nested object is reported under its own name (PS-139), so its
                    // members are read from that mapping. They were looked up in the
                    // enclosing data, where they are not, and every member encoded as zero.
                    // A `name_from` object was reported under its templated key, so that
                    // is the key to read it back from (CR-2026-030).
                    var objectKey = ResolveEncodeName(field, data) ?? field.Name;
                    if (!data.TryGetValue(objectKey, out var nested)
                        || nested is not Dictionary<string, object?> members)
                        // PS-551: a whole object the input omits. Its members were looked
                        // up in the enclosing data and written as zeros.
                        throw new InvalidOperationException(MissingFieldMessage(objectKey));
                    return EncodeObjectMembers(field.Fields, members);
                }
                case FieldType.Number:
                    // Derived from other fields: no bytes of its own.
                    return Array.Empty<byte>();
                case FieldType.Skip:
                {
                    // `remaining` gives no count to pad on encode (PS-014). CR-2026-141: padding
                    // carries no value, so its zeros cover no peek's bits and contradict none -
                    // a decoder skips them, whatever the peek before set there.
                    int n = field.Length > 0 ? field.Length : 0;
                    var pad = new byte[n];
                    _frameBytes = pad;
                    _framePending = 0;
                    _frameMeta = new FrameMeta(new byte[n], new List<Peek>());
                    return pad;
                }
            }

            // PS-359: a literal writes no bytes and its key is not required of the input.
            if (field.Type is FieldType.String or FieldType.Boolean && field.Value != null
                || IsTemplate(field))                                   // PS-590: computed
                return Array.Empty<byte>();
            // PS-587 (CR-2026-174): a checksum is computed over the bytes written, never
            // taken from the input; its value is filled in when the frame is finished.
            if (field.Checksum != null)
                return ChecksumPlaceholder(field, frame);
            // PS-589 (CR-2026-175): a non-consuming byte string writes nothing and is not
            // required; a value given for it is recorded on the frame and checked where the
            // frame closes.
            if (field.IsEcho)
            {
                frame?.AddEcho(field, data, _endian);
                return Array.Empty<byte>();
            }
            // PS-360: `value` on a field that reads bytes is the constant to write,
            // whatever the input supplies.
            if (field.Value != null)
                return EncodeField(field, field.Value);

            // A nameless field other than a skip is refused at load (PS-553, CR-2026-138);
            // it was written here as zero padding.
            object? value;
            if ((field.Name ?? "").StartsWith("_"))
                // PS-434: an internal field that reads bytes writes its value (handled
                // above), else the input's, else it is an error naming the field. This
                // wrote 0, which reads back as a value the device never sent.
                value = InternalEncodeValue(field, data);
            else
            {
                // `name_from` reports the value under a templated key, so that is the key
                // to read it back from (CR-2026-030).
                var lookupName = ResolveEncodeName(field, data);
                if (lookupName is null)
                    throw new InvalidOperationException($"name_from '{field.NameFrom}' "
                        + "references a field the data does not carry, so its output key "
                        + "cannot be rebuilt");
                if (data.TryGetValue(lookupName, out var supplied) && supplied != null)
                    value = supplied;
                else if (HasFieldDefault(field))
                    value = FieldDefault(field);   // PS-554: a declared default, as input
                else if (field.Sentinel is { Count: > 0 })
                    // A reading the decode omitted as "no reading" (PS-427) goes back as
                    // the sentinel it was, not as zero.
                    return EncodeSentinel(field);
                else if (field.Type == FieldType.Bool && frame != null && field.Consume == 0)
                {
                    // PS-551 (CR-2026-141): an absent bool that does not consume is fixed
                    // by the fields that write its bit, so it is derived and writes none.
                    frame.PlaceBits(1, false, field.Bit, 0UL, 0, 1, field.Name, derived: true);
                    return Array.Empty<byte>();
                }
                else
                    // PS-551: rejected, naming the field. This warned "Missing field" at
                    // the top level, said nothing anywhere else, and wrote a zero, which
                    // reads back as a real zero.
                    throw new InvalidOperationException(MissingFieldMessage(lookupName));
            }

            if (field.Type == FieldType.BitfieldString)
                return EncodeBitfieldString(field, value?.ToString() ?? "");
            var reversedValue = ReverseModifiers(value, field);
            if (field.Type == FieldType.Bool && frame != null)
            {
                // A bool is its `bit` of the byte it peeks, advancing only where it
                // declares `consume` (PS-389, PS-066, CR-2026-136). It was written as a
                // whole byte of 0 or 1.
                bool set = reversedValue is bool b ? b
                    : Helpers.ToFloat64(reversedValue) is (true, not 0);
                frame.PlaceBits(1, false, field.Bit, set ? 1UL : 0UL, field.Consume, 1, field.Name);
                return Array.Empty<byte>();
            }
            return EncodeField(field, reversedValue);
        }



        /// <summary>
        /// The output key a <c>name_from</c> field was reported under, resolved from the
        /// data rather than from decode variables.
        ///
        /// Encoding is handed the decoded output, keyed by field name, and has no variables
        /// to resolve a template against. So a <c>${ref}</c> is looked for in the data
        /// first, and failing that through a top-level field declaring <c>var: ref</c> -
        /// whose name is often not the variable's (PS-267).
        ///
        /// Without this the encoder looked the value up under the schema-declared name,
        /// found nothing, warned about a key the schema never reports, and wrote a zero:
        /// name-from.yaml re-encoded <c>032a</c> as <c>0300</c> (CR-2026-030).
        /// </summary>
        /// <returns>the resolved key, or null where the template cannot be rebuilt</returns>
        string? ResolveEncodeName(SchemaField field, Dictionary<string, object?> data)
        {
            if (string.IsNullOrEmpty(field.NameFrom))
                return field.Name;

            var unresolved = false;
            var resolved = System.Text.RegularExpressions.Regex.Replace(
                field.NameFrom!, @"\$\{(\w+)}", match =>
                {
                    var reference = match.Groups[1].Value;
                    if (!data.TryGetValue(reference, out var value) || value is null)
                    {
                        value = null;
                        foreach (var sibling in _fields)
                        {
                            if (sibling.Var == reference && !string.IsNullOrEmpty(sibling.Name))
                            {
                                data.TryGetValue(sibling.Name!, out value);
                                break;
                            }
                        }
                    }
                    if (value is null)
                    {
                        unresolved = true;
                        return "";
                    }
                    // The data carries what the field reports, its label included (PS-532).
                    return Wave5.RenderTemplateValue(value)
                        ?? throw new InvalidOperationException(
                            Wave5.TemplateShapeError(field.NameFrom!, reference));
                });
            return unresolved ? null : resolved;
        }

        /// <summary>
        /// What an encoder writes for an internal field that reads bytes (PS-434): its
        /// `value`, else the input's value under its name, else an error naming it. Mirrors
        /// _internal_encode_value in tools/schema_interpreter.py.
        /// </summary>
        /// <summary>
        /// PS-554 (CR-2026-139): whether an omitted input takes the field's declared
        /// <c>default</c>, which is in the field's reported form and is encoded exactly as
        /// that value given as input, so one that cannot be encoded is rejected as such input
        /// is (PS-168). A declared default is a defined absence (PS-551 as amended). An enum's
        /// <c>default</c> is its unmapped label (PS-068), so it is not written. Read from the declaration as written.
        /// </summary>
        static bool HasFieldDefault(SchemaField field)
        {
            if (field.Node == null) return false;
            YamlDotNet.RepresentationModel.YamlNode? type = null;
            bool found = false;
            foreach (var kv in field.Node.Children)
            {
                if (kv.Key is not YamlDotNet.RepresentationModel.YamlScalarNode k) continue;
                if (k.Value == "default") found = true;
                if (k.Value == "type") type = kv.Value;
            }
            var typeText = (type as YamlDotNet.RepresentationModel.YamlScalarNode)?.Value;
            return found && typeText != "enum";
        }

        static object? FieldDefault(SchemaField field)
        {
            foreach (var kv in field.Node!.Children)
                if (kv.Key is YamlDotNet.RepresentationModel.YamlScalarNode { Value: "default" })
                    return Meta.FromYaml(kv.Value);
            return null;
        }

        static object? InternalEncodeValue(SchemaField field, Dictionary<string, object?> data)
        {
            if (field.Value != null) return field.Value;
            if (data.TryGetValue(field.Name, out var supplied)) return supplied;
            if (HasFieldDefault(field)) return FieldDefault(field);              // PS-554
            throw new InvalidOperationException($"internal field '{field.Name}' reads payload bytes "
                + "and declares no value, and the input does not supply it (PS-434)");
        }

        /// <summary>
        /// A field's first sentinel, written as the raw integer the decode compared (PS-427):
        /// no modifier reversed and no `encoding` applied, since both came after the compare.
        /// </summary>
        byte[] EncodeSentinel(SchemaField field)
        {
            long raw = field.Sentinel![0];
            if (field.Encoding != null)
                return WriteUint(raw, Helpers.InferLengthFromType(field.Type),
                    string.IsNullOrEmpty(field.Endian) ? _endian : field.Endian!);
            return EncodeField(field, (double)raw);
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
                                      Dictionary<string, object?> data, bool topLevel)
        {
            var entry = new EnclosingList(fields);                              // CR-2026-165
            _enclosing.Add(entry);
            try { return EncodeWithBitfieldRunsIn(fields, data, topLevel, entry); }
            finally { _enclosing.RemoveAt(_enclosing.Count - 1); }
        }

        /// <summary>
        /// The field lists enclosing the entry being encoded, within the current reported
        /// object, each with its current entry (CR-2026-165: the names declared outside a
        /// recorded match). Reset for an object and for each repeat element.
        /// </summary>
        List<EnclosingList> _enclosing = new();

        sealed class EnclosingList
        {
            public readonly List<SchemaField> Fields;
            public SchemaField? Current;
            public EnclosingList(List<SchemaField> fields) { Fields = fields; }
        }

        /// <summary>An object's members or a repeat element's fields: a new reported
        /// object, so the lists enclosing it do not report beside it.</summary>
        byte[] EncodeObjectMembers(List<SchemaField> fields, Dictionary<string, object?> data)
        {
            var saved = _enclosing;
            _enclosing = new List<EnclosingList>();
            try { return EncodeFieldList(fields, data); }
            finally { _enclosing = saved; }
        }

        byte[] EncodeWithBitfieldRunsIn(List<SchemaField> fields,
                                        Dictionary<string, object?> data, bool topLevel,
                                        EnclosingList entry)
        {
            // CR-2026-136: laid out as the decoder reads it. A bit range or bool that does
            // not consume leaves its bytes pending, and the next field's bytes are ORed
            // into them rather than written after them.
            var output = new FrameBuffer();
            var run = new List<SchemaField>();

            void Flush()
            {
                if (run.Count == 0) return;
                var pending = new List<SchemaField>(run);
                run.Clear();
                try
                {
                    EncodeBitfieldRun(pending, data, output);
                }
                catch (Exception e)
                {
                    if (!topLevel) throw;
                    var names = string.Join(", ", pending.Select(f => f.Name));
                    _result.Errors.Add($"Error encoding bit range(s) {names}: {e.Message}");
                }
            }

            // PS-405: an optional field is written only where the input supplies it, and one
            // supplied after an omitted earlier one cannot be placed.
            string? omittedOptional = null;

            // PS-564 (the reference's TailLevel): once an item of this list is written, the
            // bytes it added go to the tail of every check an earlier item registered.
            var levelChecks = new List<Check564>();
            int? levelSize = null;
            int levelMark = 0;
            void CloseItem()
            {
                if (levelSize == null) return;
                int grown = output.Size - levelSize.Value;
                foreach (var c in levelChecks) c.Tail += grown;
                levelChecks.AddRange(_t564.Skip(levelMark));
                levelSize = null;
            }

            foreach (var field in fields)
            {
                entry.Current = field;
                CloseItem();
                levelSize = output.Size;
                levelMark = _t564.Count;
                if (field.Optional && !string.IsNullOrEmpty(field.Name) && !field.Name.StartsWith("_"))
                {
                    var key = ResolveEncodeName(field, data) ?? field.Name;
                    if (!data.TryGetValue(key, out var supplied) || supplied == null)
                    {
                        Flush();
                        omittedOptional ??= field.Name;
                        continue;
                    }
                    if (omittedOptional != null)
                    {
                        Flush();
                        var message = $"optional field '{field.Name}' is supplied while the earlier "
                            + $"optional field '{omittedOptional}' is not (PS-405)";
                        if (!topLevel) throw new InvalidOperationException(message);
                        _result.Errors.Add($"Error encoding {field.Name}: {message}");
                        continue;
                    }
                }
                if (!IsBareBitfield(field))
                {
                    Flush();
                    try
                    {
                        var bytes = EncodeOne(field, data, fields, output);
                        output.Extend(bytes, CarriedBy(bytes), MetaOf(bytes));
                    }
                    catch (Exception e)
                    {
                        if (!topLevel) throw;
                        // A FieldErrorException already names its field in full (PS-537).
                        _result.Errors.Add(e is FieldErrorException ? e.Message
                            : $"Error encoding {Describe(field)}: {e.Message}");
                    }
                    continue;
                }
                run.Add(field);
                if (field.Consume >= 1) Flush();
            }
            Flush();
            CloseItem();
            var frameBytes = output.Frame();
            _frameBytes = frameBytes;
            _framePending = output.PendingCount;
            _frameMeta = output.Meta();
            return frameBytes;
        }

        /// <summary>PS-587: the checksums placed and not yet computed (the reference's
        /// _checksum_open). One left at the end never reached the top-level frame.</summary>
        readonly List<SchemaField> _checksumOpen = new();

        /// <summary>
        /// PS-587 (CR-2026-174): the checksum's bytes, zero until the frame is finished
        /// (<see cref="Checksum.Fill"/>). Bits a peek left pending would be ORed into it, so
        /// its range is not all written: rejected. The reference's _checksum_placeholder.
        /// </summary>
        byte[] ChecksumPlaceholder(SchemaField field, FrameBuffer? frame)
        {
            if (frame != null && frame.PendingCount > 0)
                throw new FieldErrorException(Checksum.UnwrittenMessage(field.Name));
            int size = Checksum.Width(field.RawType)!.Value / 8;
            var endian = string.IsNullOrEmpty(field.Endian) ? _endian : field.Endian!;
            _checksumOpen.Add(field);
            var bytes = new byte[size];
            var covered = new byte[size];
            Array.Fill(covered, (byte)0xFF);
            _frameBytes = bytes;
            _framePending = 0;
            _frameMeta = new FrameMeta(covered, new List<Peek>(),
                new List<ChecksumSlot> { new(0, field, endian == "little") });
            return bytes;
        }

        // The last field list's bytes and how many at its end are still pending: a
        // construct whose bytes are exactly those carries the tail to the enclosing list
        // (CR-2026-136, the reference's Frame). Matched by reference, so bytes built any
        // other way carry nothing.
        byte[]? _frameBytes;
        int _framePending;

        int CarriedBy(byte[] bytes) => ReferenceEquals(bytes, _frameBytes) ? _framePending : 0;

        // CR-2026-141 (PS-551): the last field list's covered bits and peeks, carried the
        // same way; bytes built any other way were written whole by their fields.
        FrameMeta? _frameMeta;

        FrameMeta? MetaOf(byte[] bytes) => ReferenceEquals(bytes, _frameBytes) ? _frameMeta : null;

        static bool HasBitMarker(SchemaField f)
            => f.RawType.IndexOfAny(new[] { '[', ':', '<' }) >= 0;

        /// <summary>A named bit range or bool that does not consume (PS-060, PS-066). The
        /// reference's is_peek_field.</summary>
        static bool IsPeekField(SchemaField f)
            => !string.IsNullOrEmpty(f.Name) && f.Consume == 0
               && (f.Type == FieldType.Bool
                   || (f.ByteGroup.Count == 0 && f.Type != FieldType.BitfieldString && HasBitMarker(f)));

        /// <summary>
        /// The peeks in a field list a whole field after them writes over - read from the
        /// schema, so a tlv case can be chosen without them (PS-551, CR-2026-141). Counted only
        /// where the next field that reads bytes is a plain whole field: a range that
        /// consumes, a skip or a construct ends the span first, and its bits may be left
        /// unwritten (wt303's status nibble beside a mode nibble). The reference's
        /// covered_peeks.
        /// </summary>
        static HashSet<SchemaField> CoveredPeeks(List<SchemaField> fields)
        {
            var output = new HashSet<SchemaField>(ReferenceEqualityComparer.Instance);
            var pending = new List<SchemaField>();
            foreach (var f in fields)
            {
                if (IsPeekField(f)) { pending.Add(f); continue; }
                if (f.Type == FieldType.Number || IsTemplate(f)
                    || (f.Type is FieldType.String or FieldType.Boolean && f.Value != null))
                    continue;
                bool construct = f.MatchInline != null || f.TLVInline != null || f.ByteGroup.Count > 0
                    || f.Flagged != null || f.Ref2 != null
                    || f.Type is FieldType.TLV or FieldType.Match;
                if (!construct && !HasBitMarker(f)
                    && f.Type is not (FieldType.Skip or FieldType.Object or FieldType.Repeat or FieldType.Bool))
                    output.UnionWith(pending);
                pending.Clear();
            }
            return output;
        }

        /// <summary>
        /// Place one run of bit ranges into the byte or bytes they share. Each range is ORed
        /// into the frame's pending base, in the run's byte order, and advances only by its
        /// own <c>consume</c> (CR-2026-136, PS-016), so the bytes a range peeks are shared
        /// with whatever field comes next, as a decoder reads them. The run used to be
        /// written whole, and the next field's bytes after it.
        /// </summary>
        void EncodeBitfieldRun(List<SchemaField> run, Dictionary<string, object?> data,
                               FrameBuffer output)
        {
            // The run's shared base is in its fields' effective byte order (PS-059): a
            // field's own `endian` where one declares it, else the schema's.
            var runEndian = run.Select(m => m.Endian).FirstOrDefault(e => !string.IsNullOrEmpty(e)) ?? _endian;
            bool little = runEndian == "little";
            // Every range is resolved and checked before any is placed, so a rejected value
            // leaves nothing of its run pending in the frame.
            var placements = new List<(int baseSize, int start, ulong bits, int consume, int width,
                string? name, bool derived)>();
            foreach (var member in run)
            {
                if (!string.IsNullOrEmpty(member.Name) && member.Value == null
                    && !member.Name.StartsWith("_") && member.Consume == 0
                    && !(data.TryGetValue(member.Name, out var given0) && given0 != null)
                    && !HasFieldDefault(member))                            // PS-554 first
                {
                    // PS-551 (CR-2026-141): a range that does not consume is fixed by the
                    // fields that write its bits, so it is derived and writes none; the
                    // frame's check rejects it as missing where a bit is left unwritten.
                    var derivedRange = Helpers.ParseBitRange(member.RawType)!.Value;
                    placements.Add((Math.Max(1, member.BitBaseBytes), derivedRange.start, 0UL, 0,
                        derivedRange.end - derivedRange.start + 1, member.Name, true));
                    continue;
                }
                // A nameless range is refused at load (PS-553); it was written as zero bits.
                object? value = member.Value != null || (member.Name ?? "").StartsWith("_")
                        ? InternalEncodeValue(member, data)                    // PS-360, PS-434
                    : data.TryGetValue(member.Name, out var v) && v != null ? v
                    : HasFieldDefault(member) ? FieldDefault(member)            // PS-554
                    // PS-551. A range was written as zero bits, which read back as a value.
                    : throw new InvalidOperationException(MissingFieldMessage(member.Name));
                var given = value;
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
                var bitRange = Helpers.ParseBitRange(member.RawType)!.Value;
                int bitLen = bitRange.end - bitRange.start + 1;
                // PS-552, PS-064, PS-168: rounded ties to even and checked against the
                // range's width - never masked into a neighbour's bits. The mask below only
                // keeps a signed value's two's complement within the width (PS-062).
                long raw = CheckBitRange(member, RoundToInteger(numeric), bitLen, given);
                ulong mask = bitLen >= 64 ? ulong.MaxValue : (1UL << bitLen) - 1;
                placements.Add((Math.Max(1, member.BitBaseBytes), bitRange.start,
                    (ulong)raw & mask, member.Consume, bitLen, member.Name, false));
            }
            foreach (var (baseSize, start, bits, consume, width, name, derived) in placements)
                output.PlaceBits(baseSize, little, start, bits, consume, width, name, derived);
        }

        /// <summary>The number a bit range's enum label stands for.</summary>
        static double BitfieldLabelValue(object? value, SchemaField member)
        {
            var label = value?.ToString() ?? "";
            var codes = EnumCodes(member, label);
            if (codes.Count > 1) throw SharedLabel(member.Name, value, codes);   // PS-169
            if (codes.Count == 1) return codes[0];
            throw new InvalidOperationException($"bit range '{member.Name}': '{label}' "
                + "is not one of its declared values");
        }

        /// <summary>Encode a list of fields - a TLV case's value bytes, a match case's body.</summary>
        byte[] EncodeFieldList(List<SchemaField> fields, Dictionary<string, object?> data)
        {
            if (fields.Count == 0) return Array.Empty<byte>();
            data = DeriveInputs(fields, data);                                  // PS-551
            return EncodeWithBitfieldRuns(fields, data, topLevel: false);
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

        // --- PS-551: missing input and what the rest of the input fixes ----------------

        /// <summary>
        /// What every encoder says for a required value the input omits (PS-551). The spec
        /// prototype's text, so a cross-language diff compares equal strings.
        /// </summary>
        internal static string MissingFieldMessage(string name) => $"Missing required field: {name}";

        /// <summary>PS-574: a match whose case neither the input nor a decode record gives,
        /// and which this encoder could not derive either, named by its discriminator.</summary>
        internal static string UndeterminedCaseMessage(string name) => MissingFieldMessage(name) + " (PS-574)";

        static string MatchLabel(SchemaField match)
            => !string.IsNullOrEmpty(match.Name) ? $"'{match.Name}'"
               : !string.IsNullOrEmpty(match.Var) ? $"'{match.Var}'" : "an unnamed match";

        static MatchCase? DefaultCaseOf(SchemaField match) => match.Cases.FirstOrDefault(IsDefaultCase);

        /// <summary>The case a decode record names (PS-573): null for "default", else the
        /// case whose key reads the same. A key the match does not have is rejected.</summary>
        static MatchCase? RecordedCase(SchemaField match, object? key)
        {
            if (key is string { } text && text == "default") return null;
            var wanted = (key is string str ? str : PayloadSchema.Meta.PyRepr(key)).Trim();
            foreach (var c in match.Cases)
            {
                if (IsDefaultCase(c) || c.KeyNode == null) continue;
                if (PayloadSchema.Meta.KeyText(c.KeyNode).Trim() == wanted) return c;
            }
            throw new InvalidOperationException($"Field {MatchLabel(match)}: the recorded case "
                + $"{MessageValue(key)} is not a case of the match (PS-573)");
        }

        static IEnumerable<YamlDotNet.RepresentationModel.YamlNode> Nodes(List<SchemaField>? fields)
            => (fields ?? new List<SchemaField>()).Select(f => (YamlDotNet.RepresentationModel.YamlNode?)f.Node)
                .Where(n => n != null)!;

        /// <summary>
        /// PS-573: under a case taken from the decode record, the input may not give a field
        /// only another case of the match declares (<paramref name="chosen"/> null for the
        /// default). A name something outside the match declares within the same reported
        /// object - the other entries of the lists enclosing it - is not one (CR-2026-165);
        /// the match's default list is one of its cases (_check_recorded_case).
        /// </summary>
        void CheckRecordedCase(SchemaField match, MatchCase? chosen,
            Dictionary<string, object?> data)
        {
            var defaultBody = DefaultBody(match);
            var mine = new HashSet<string>(DecodeTrace.DeclaredFields(
                Nodes(chosen != null ? chosen.Fields : defaultBody)));
            var others = new HashSet<string>();
            foreach (var c in match.Cases)
            {
                if (IsDefaultCase(c) || ReferenceEquals(c, chosen)) continue;
                others.UnionWith(DecodeTrace.DeclaredFields(Nodes(c.Fields)));
            }
            if (chosen != null && defaultBody != null)
                others.UnionWith(DecodeTrace.DeclaredFields(Nodes(defaultBody)));
            var outside = new HashSet<string>();
            foreach (var e in _enclosing)
                outside.UnionWith(DecodeTrace.DeclaredFields(
                    Nodes(e.Fields.Where(f => !ReferenceEquals(f, e.Current)).ToList())));
            var clash = others.Where(n => !mine.Contains(n) && !outside.Contains(n) && data.ContainsKey(n))
                .OrderBy(n => n, StringComparer.Ordinal).ToList();
            if (clash.Count > 0)
                throw new InvalidOperationException($"Field {MatchLabel(match)}: the input gives "
                    + $"'{clash[0]}', which only another case declares, against the recorded case (PS-573)");
        }

        /// <summary>
        /// The field of this list a <c>$ref</c> names, by its name or its <c>var:</c>.
        /// Searched into a byte_group's members, since a count is often a few bits of a
        /// shared byte; not into any other construct. Mirrors _size_source.
        /// </summary>
        static SchemaField? SizeSource(List<SchemaField> fields, string reference)
        {
            foreach (var f in fields)
            {
                if (f.ByteGroup.Count > 0)
                {
                    var found = SizeSource(f.ByteGroup, reference);
                    if (found != null) return found;
                    continue;
                }
                if (!string.IsNullOrEmpty(f.Name) && (f.Name == reference || f.Var == reference))
                    return f;
            }
            return null;
        }

        /// <summary>
        /// Whether a field's value can be derived and written as it stands: it reads bytes
        /// of its own and reports them unchanged. Mirrors _derivable.
        /// </summary>
        static bool Derivable(SchemaField? source)
        {
            if (source == null || string.IsNullOrEmpty(source.Name)) return false;
            if (source.Type == FieldType.Number || IsTemplate(source)) return false;
            if (source.Type is FieldType.String or FieldType.Boolean && source.Value != null) return false;
            return source.Value == null && source.NameFrom == null && source.Lookup == null
                && source.Mult == null && source.Div == null && source.Add == null
                && source.Transform.Count == 0 && source.Polynomial == null
                && source.Encoding == null && source.Compute == null && source.Ref == null
                && source.Guard == null;
        }

        /// <summary>
        /// Whether <paramref name="source"/> is a bit range in a run nothing closes with
        /// <c>consume</c>. Such a run reads bytes the fields after it read again (r718x, vobo,
        /// dnt, the match-u32-peek fixture), and the encoder writes those bytes twice, so a
        /// value derived into it would add bytes rather than fix them. Mirrors _is_peek.
        /// </summary>
        static bool IsPeek(List<SchemaField> fields, SchemaField source)
        {
            var run = new List<SchemaField>();
            foreach (var f in fields)
            {
                if (!IsBareBitfield(f))
                {
                    if (run.Contains(source)) return true;
                    run.Clear();
                    continue;
                }
                run.Add(f);
                if (f.Consume >= 1)
                {
                    if (run.Contains(source)) return false;
                    run.Clear();
                }
            }
            return run.Contains(source);
        }

        /// <summary>The field a <c>$ref</c> names, where a value can be derived into it.</summary>
        static SchemaField? DerivedSource(List<SchemaField> fields, object? reference)
        {
            if (reference is not string text || !text.StartsWith("$")) return null;
            var source = SizeSource(fields, text[1..]);
            if (!Derivable(source) || IsPeek(fields, source!)) return null;
            return source;
        }

        /// <summary>The one discriminator a match case key stands for, or null.</summary>
        static long? ExactCaseValue(object? caseValue)
        {
            switch (caseValue)
            {
                case null or bool: return null;
                case int i: return i;
                case long l: return l;
                case double d when d == Math.Floor(d): return (long)d;
                case string s: return ParseIntAny(s);
                default: return null;
            }
        }

        /// <summary>
        /// The discriminator of the one match case whose fields the input gives; null where
        /// none is given, two tie, the default explains the input as well, or the case
        /// stands for more than one value. Mirrors _sole_case_given.
        /// </summary>
        long? SoleCaseGiven(SchemaField match, Dictionary<string, object?> data)
        {
            var (best, tied) = BestCase(match, data);
            return best == null || tied ? null : ExactCaseValue(best.CaseValue);
        }

        /// <summary>
        /// A match's default field list - <c>default:</c> beside its cases, or a default
        /// case - or null where its default is <c>error</c> or <c>skip</c>. Mirrors
        /// _default_body.
        /// </summary>
        static List<SchemaField>? DefaultBody(SchemaField match)
        {
            if (match.MatchDefault is List<SchemaField> declared) return declared;
            return match.Cases.FirstOrDefault(IsDefaultCase)?.Fields;
        }

        /// <summary>
        /// The case whose fields the input gives most of, scored by BodyScore; the first of
        /// equal cases wins, and <c>tied</c> says another equalled it. Where the match's
        /// default list explains more of the input than any case, no case is returned.
        /// Mirrors _best_case.
        /// </summary>
        (MatchCase? best, bool tied) BestCase(SchemaField match, Dictionary<string, object?> data)
        {
            MatchCase? best = null;
            int bestHits = 0;
            bool tied = false;
            foreach (var c in match.Cases)
            {
                if (IsDefaultCase(c)) continue;
                int hits = BodyScore(c.Fields, data) ?? 0;
                if (hits > bestHits)
                {
                    best = c;
                    bestHits = hits;
                    tied = false;
                }
                else if (hits > 0 && hits == bestHits)
                    tied = true;
            }
            var fallback = DefaultBody(match);
            if (best != null && fallback != null && (BodyScore(fallback, data) ?? 0) > bestHits)
                return (null, false);
            return (best, tied);
        }

        /// <summary>
        /// How many of a match branch's reported names the input gives, or null where the
        /// input rules the branch out (no-silent-empty). Counted through a byte_group's or
        /// flagged group's members, a nested match's best branch and a tlv's cases. A literal
        /// the input gives with another value rules the branch out (PS-359, PS-513), as does
        /// a nested match every branch of which is ruled out and whose default is not
        /// <c>skip</c>. A computed field is not counted. Mirrors _body_score: only the top
        /// level of a branch used to be counted, so a branch whose fields sit in another
        /// construct scored nothing, no case was chosen, and the match wrote no bytes and
        /// reported nothing.
        /// </summary>
        int? BodyScore(List<SchemaField>? body, Dictionary<string, object?> data)
        {
            if (body == null) return null;
            int score = 0;
            foreach (var f in body)
            {
                if (f.ByteGroup.Count > 0)
                {
                    var hits = BodyScore(f.ByteGroup, data);
                    if (hits == null) return null;
                    score += hits.Value;
                    continue;
                }
                if (f.Flagged?.Groups != null)
                {
                    foreach (var group in f.Flagged.Groups)
                        score += BodyScore(group.Fields, data) ?? 0;
                    continue;
                }
                if (f.MatchInline is { } inner)
                {
                    if (!string.IsNullOrEmpty(inner.Name) && data.ContainsKey(inner.Name)) score++;
                    var bodies = inner.Cases.Where(c => !IsDefaultCase(c)).Select(c => c.Fields).ToList();
                    var fallback = DefaultBody(inner);
                    if (fallback != null) bodies.Add(fallback);
                    int? best = null;
                    foreach (var b in bodies)
                    {
                        var h = BodyScore(b, data);
                        if (h != null && (best == null || h > best)) best = h;
                    }
                    if (best != null) score += best.Value;
                    else if ((inner.MatchDefault as string ?? "error") != "skip") return null;
                    continue;
                }
                var tlvCases = f.TLVInline?.TLVCases ?? (f.Type == FieldType.TLV ? f.TLVCases : null);
                if (tlvCases != null)
                {
                    var names = new HashSet<string>();
                    foreach (var caseFields in tlvCases.Values)
                        foreach (var (c, _) in CaseClaims(caseFields, false))
                            if (!string.IsNullOrEmpty(c.Name)) names.Add(c.Name);
                    score += names.Count(data.ContainsKey);
                    continue;
                }
                if (string.IsNullOrEmpty(f.Name) || f.Name.StartsWith("_") || IsComputed(f)) continue;
                string? key;
                try
                {
                    key = ResolveEncodeName(f, data);
                }
                catch (InvalidOperationException)
                {
                    key = null;
                }
                if (key == null || !data.TryGetValue(key, out var given)) continue;
                if (IsLiteral(f) && !Helpers.SameLabel(f.Value, given)) return null;
                score++;
            }
            return score;
        }

        /// <summary>
        /// PS-551: the values this field list's input omits but the rest of it fixes - a
        /// repeat's <c>count: $n</c> or <c>byte_length: $n</c> field from its list, a match's
        /// <c>field: $d</c> discriminator from the one case whose fields are given, a flagged
        /// mask from the groups given. A supplied value is kept and checked where it is used;
        /// a supplied flagged mask disagreeing with the groups is rejected here. The input
        /// itself is not modified. Mirrors _derive_inputs.
        /// </summary>
        Dictionary<string, object?> DeriveInputs(List<SchemaField> fields, Dictionary<string, object?> data)
        {
            var derived = new Dictionary<string, object?>(data);
            if (_trace != null) RecordedInputs(fields, data, derived);             // PS-573
            foreach (var f in fields)
            {
                if (Cr181.IsByteString(f.Type) && f.LengthRef != null
                    && data.TryGetValue(f.Name, out var byteValue) && byteValue != null)
                {
                    // PS-602, PS-551 (CR-2026-181): the value's byte count fixes the field its
                    // length names; a given value that differs is rejected.
                    int count = Cr181.Input(f, byteValue).Length;
                    var lengthSource = SizeSource(fields, f.LengthRef);
                    var lengthName = lengthSource?.Name ?? f.LengthRef;
                    if (!derived.TryGetValue(lengthName, out var givenLength) || givenLength == null)
                    {
                        if (Derivable(lengthSource) && !IsPeek(fields, lengthSource!))
                            derived[lengthName] = (long)count;
                        continue;
                    }
                    var bound = ReferenceBinding(givenLength, lengthSource);
                    if (bound is not bool && Helpers.ToFloat64(bound) is (true, var n) && n != count)
                        throw new FieldErrorException($"Field '{f.Name}' is given {count} bytes, but "
                            + $"'{lengthName}', its length, is {MessageValue(bound)} (PS-602)");
                    continue;
                }
                if (f.Type == FieldType.Repeat)
                {
                    var records = RepeatRecords(f, data);
                    if (records == null) continue;
                    foreach (var (reference, isCount) in new[] { (f.Count, true), (f.ByteLength, false) })
                    {
                        var source = DerivedSource(fields, reference);
                        if (source == null || (derived.TryGetValue(source.Name, out var have) && have != null))
                            continue;
                        derived[source.Name] = isCount
                            ? (long)records.Count
                            : (long)EncodeRepeatElements(f, data, records, null).Length;
                    }
                }
                else if (f.MatchInline is { } match)
                {
                    var source = DerivedSource(fields, match.On);
                    if (source == null || (derived.TryGetValue(source.Name, out var have) && have != null))
                        continue;
                    var key = SoleCaseGiven(match, data);
                    if (key != null) derived[source.Name] = key.Value;
                }
                else if (f.Flagged is { } flagged)
                {
                    var mask = flagged.Field.TrimStart('$');
                    var maskSource = SizeSource(fields, mask);
                    var name = maskSource?.Name ?? mask;
                    long bits = 0, declared = 0;
                    foreach (var group in flagged.Groups)
                    {
                        declared |= 1L << group.Bit;
                        if (GroupHasData(group, data)) bits |= 1L << group.Bit;
                    }
                    data.TryGetValue(name, out var given);
                    if (given == null && RecordedFlags(flagged) is { } recordedFlags)
                    {
                        // PS-573: the flags the decode record holds; the groups written are
                        // the ones they set (EncodeFlagged), so there is nothing to check.
                        if (!derived.TryGetValue(name, out var already) || already == null)
                            derived[name] = recordedFlags;
                        continue;
                    }
                    if (given == null && derived.TryGetValue(name, out var supplied) && supplied != null)
                    {
                        // An internal mask the decode record supplied (PS-573), checked as a
                        // supplied one is.
                        given = supplied;
                        data = new Dictionary<string, object?>(data) { [name] = given };
                    }
                    if (given == null)
                    {
                        derived[name] = bits;
                        continue;
                    }
                    // The groups test what a reference to the mask binds (PS-523,
                    // CR-2026-132): an enum's code, the number under a text label.
                    // Compared as reported, an enum name or a text label passed unchecked.
                    given = ReferenceBinding(given, maskSource);
                    if (given is bool || given is string) continue;
                    var (ok, numeric) = Helpers.ToFloat64(given);
                    if (!ok || numeric != Math.Floor(numeric)) continue;
                    if (((long)numeric & declared) != bits)
                        throw new InvalidOperationException($"Flagged field '{name}' is given "
                            + $"{(long)numeric}, and the groups given set {bits} (PS-551)");
                }
            }
            return derived;
        }

        // --- constructs --------------------------------------------------------------

        /// <summary>
        /// Whether the input gives any of a flagged group's values: a member's own name, or
        /// a name inside a construct the group holds (a nested flagged block, a byte_group).
        /// </summary>
        /// <summary>
        /// PS-573: what the decode record supplies for this field list - each internal
        /// field's recorded value where the input does not give it (an input giving another
        /// value is rejected, naming the field), and each flagged construct's recorded flags
        /// under its flags field where the input gives it no value. A byte_group's internal
        /// members are looked into, as the record locates them. Mirrors _recorded_inputs.
        /// </summary>
        void RecordedInputs(List<SchemaField> fields, Dictionary<string, object?> data,
            Dictionary<string, object?> derived)
        {
            var trace = _trace!;
            void Internal(SchemaField f)
            {
                var name = f.Name;
                if (string.IsNullOrEmpty(name) || !name.StartsWith('_') || IsComputed(f) || IsLiteral(f)
                    || f.Value != null || f.Node == null)
                    return;
                var (found, recorded) = trace.Lookup(trace.Internal, f.Node);
                if (!found) return;
                if (data.TryGetValue(name, out var given) && !trace.Supplied.Contains(name))
                {
                    if (!SameValue(given, recorded))
                        throw new InvalidOperationException($"Field '{name}' is given {MessageValue(given)}, "
                            + $"but the decode record holds {MessageValue(recorded)} (PS-573)");
                    return;
                }
                derived[name] = recorded;
                trace.Supplied.Add(name);
            }
            foreach (var f in fields)
            {
                if (f.ByteGroup.Count > 0)
                {
                    foreach (var member in f.ByteGroup) Internal(member);
                    continue;
                }
                if (f.Flagged is { } flagged)
                {
                    var mask = flagged.Field.TrimStart('$');
                    var (found, recorded) = trace.Lookup(trace.Flagged, flagged.Anchor);
                    if (mask.Length > 0 && found
                        && (!data.TryGetValue(mask, out var given) || given == null)
                        && (!derived.TryGetValue(mask, out var have) || have == null))
                        derived[mask] = recorded;
                    continue;
                }
                Internal(f);
            }
        }

        /// <summary>Python's == on two decoded values: numbers by value, True as 1.</summary>
        static bool SameValue(object? a, object? b)
        {
            object? AsNumber(object? v) => v is bool flag ? (flag ? 1.0 : 0.0) : v;
            var (aok, an) = Helpers.ToFloat64(AsNumber(a));
            var (bok, bn) = Helpers.ToFloat64(AsNumber(b));
            if (aok && bok && a is not string && b is not string) return an == bn;
            return Equals(a, b);
        }

        /// <summary>The flags the decode record holds for a flagged construct (PS-573), where
        /// the input gives its flags field no value; else null.</summary>
        long? RecordedFlags(FlaggedDef fd)
        {
            if (_trace == null) return null;
            if (_trace.InputNames.Contains(fd.Field.TrimStart('$'))) return null;
            var (found, recorded) = _trace.Lookup(_trace.Flagged, fd.Anchor);
            if (!found || recorded is bool or string or null) return null;
            var (ok, numeric) = Helpers.ToFloat64(recorded);
            return ok ? (long)Math.Floor(numeric) : null;
        }

        static bool GroupHasData(FlaggedGroup group, Dictionary<string, object?> data)
            => group.Fields.Any(f => !string.IsNullOrEmpty(f.Name) && data.ContainsKey(f.Name))
               || ClaimableFields(group.Fields).Any(f => data.ContainsKey(f.Name));

        /// <summary>Encode the groups whose fields the data carries; the absent ones cost no bytes.</summary>
        byte[] EncodeFlagged(FlaggedDef fd, Dictionary<string, object?> data)
        {
            // CR-2026-136: the given groups' members are one field list, laid out as a
            // decoder reads them, so a bool or bit range in a group is its bits of a shared
            // byte (vobo's digital groups), and a peek at the end carries to the field after
            // the block. Each group used to be its own list, so a byte two groups shared was
            // written twice. An internal member goes through PS-434 in EncodeOne; an omitted
            // member declaring `sentinel` writes its first one (PS-427). A member with no
            // name is taken only where it is a construct in its group, as the reference
            // takes it.
            var members = new List<SchemaField>();
            // PS-573: with a decode record and no flags in the input, the groups its recorded
            // flags set.
            var recordedFlags = RecordedFlags(fd);
            foreach (var group in fd.Groups)
            {
                if (recordedFlags is { } set ? ((set >> group.Bit) & 1) == 0 : !GroupHasData(group, data))
                    continue;
                if (group.Fields.Any(gf => string.IsNullOrEmpty(gf.Name)))
                {
                    // A construct in the group - a nested flagged block, a byte_group - is
                    // encoded as any field list is, so the whole group joins the list. It
                    // was passed over as nameless, so a nested group's values were dropped.
                    members.AddRange(group.Fields);
                    continue;
                }
                members.AddRange(group.Fields.Where(gf => !string.IsNullOrEmpty(gf.Name)));
            }
            // A given group's members are each required (PS-551), as in any field list: a
            // nested block's mask is derived and a run of bit ranges is packed.
            if (members.Count == 0) return Array.Empty<byte>();
            return EncodeWithBitfieldRuns(members, DeriveInputs(members, data), topLevel: false);
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
            int size = Cr131.ByteGroupWidth(group);    // PS-070, CR-2026-131: as decoded

            foreach (var member in group.ByteGroup)
            {
                // A computed or literal member has no bits of its own (PS-359).
                if (member.Type == FieldType.Number || IsTemplate(member)
                    || (member.Type is FieldType.String or FieldType.Boolean && member.Value != null))
                    continue;
                object? value = string.IsNullOrEmpty(member.Name) ? 0.0
                    : member.Value != null || member.Name.StartsWith("_")
                        ? InternalEncodeValue(member, data)                    // PS-360, PS-434
                    : data.TryGetValue(member.Name, out var v) && v != null ? v
                    : HasFieldDefault(member) ? FieldDefault(member)            // PS-554
                    // PS-551. A member was written as zero bits, which read back as a value.
                    : throw new InvalidOperationException(MissingFieldMessage(member.Name));
                var given = value;
                if (member.Type == FieldType.Bool)
                {
                    // A bool member is one bit of the group's value (PS-364).
                    if (value is true) packed |= 1UL << member.Bit;
                    continue;
                }
                value = ReverseModifiers(value, member);
                var (ok, numeric) = Helpers.ToFloat64(value);
                if (!ok)
                {
                    if (value is bool flag) numeric = flag ? 1 : 0;
                    else continue;
                }
                double rounded = RoundToInteger(numeric);                          // PS-552
                long raw = (long)rounded;

                int bitStart = member.BitOffset;
                int bitLen = member.BitCount > 0 ? member.BitCount : 0;
                var bitRange = Helpers.ParseBitRange(member.RawType);
                if (bitRange != null)
                {
                    bitStart = bitRange.Value.start;
                    bitLen = bitRange.Value.end - bitRange.Value.start + 1;
                    // PS-168: checked against the range's width, where it was masked silently.
                    raw = CheckBitRange(member, rounded, bitLen, given);
                }
                if (bitLen > 0)
                {
                    ulong mask = bitLen >= 64 ? ulong.MaxValue : (1UL << bitLen) - 1;
                    packed |= ((ulong)raw & mask) << bitStart;
                }
                else
                {
                    // A whole integer is its full range (PS-070), masked like any other member.
                    var whole = Cr131.MemberRange(member);
                    int bits = whole.high + 1;
                    var (low, high) = IntegerBounds(bits, whole.signed);
                    raw = (long)CheckFits(rounded, low, high, member.Name, given);     // PS-168
                    packed |= bits >= 64 ? (ulong)raw : (ulong)raw & ((1UL << bits) - 1);
                }
            }
            return Helpers.EncodeUint(packed, Math.Max(1, size), group.GroupEndian ?? _endian);
        }

        /// <summary>
        /// Encode a repeat: its records back to back, and nothing else.
        /// </summary>
        /// <remarks>
        /// The framing costs no bytes here. `count: $n` and `byte_length: $len` name a field
        /// earlier in the list, which the main loop encodes from its own value, and
        /// `until: end` needs no header at all.
        /// </remarks>
        /// <summary>
        /// Whether an element's fields reach a `records` entry (PS-541): directly, or through
        /// a match case or default (records_reachable in the reference).
        /// </summary>
        static bool RecordsReachable(IEnumerable<SchemaField> fields) =>
            fields.Any(f => f.Records != null
                || (f.MatchInline is { } m && (m.Cases.Any(c => RecordsReachable(c.Fields))
                    || (m.MatchDefault is List<SchemaField> d && RecordsReachable(d)))));

        /// <summary>
        /// PS-551: an absent list is a missing value, and a list must agree with its count
        /// and byte_length, literal or referenced. An absent list wrote nothing, leaving the
        /// frame short of the elements a count or byte_length field still declared.
        /// </summary>
        // PS-564 is not checked inside a tlv or repeat (the reference's tail_unchecked).
        byte[] EncodeRepeat(SchemaField field, Dictionary<string, object?> data,
            List<SchemaField> siblings)
        {
            _d564++;
            try { return EncodeRepeatUnchecked(field, data, siblings); }
            finally { _d564--; }
        }

        byte[] EncodeRepeatUnchecked(SchemaField field, Dictionary<string, object?> data,
            List<SchemaField> siblings)
        {
            data.TryGetValue(field.Name ?? "", out var raw);
            if (raw != null && raw is not (Dictionary<string, object?> or System.Collections.IList))
                throw new InvalidOperationException(
                    $"repeat field '{field.Name}': expected a list of records, got {raw.GetType().Name}");
            var list = RepeatRecords(field, data)
                ?? throw new InvalidOperationException(MissingFieldMessage(field.Name ?? ""));
            var count = RepeatSize(field.Count, data, siblings);
            var output = new List<byte>(EncodeRepeatElements(field, data, list, count));
            var byteLength = RepeatSize(field.ByteLength, data, siblings);
            if (byteLength != null && output.Count != byteLength)
                throw new InvalidOperationException($"Repeat '{field.Name}' encodes to {output.Count} "
                    + $"bytes for a byte_length of {byteLength} (PS-551)");
            // PS-385: the elements, then the trailer, whose values sit beside the repeat.
            if (field.Trailer is { Count: > 0 })
            {
                var trailer = EncodeFieldList(field.Trailer, data);
                FrameMeta.Check(trailer, MetaOf(trailer));                      // PS-551
                output.AddRange(trailer);
            }
            return output.ToArray();
        }

        /// <summary>A repeat's list as the input gives it, or null where it gives none.</summary>
        static List<object?>? RepeatRecords(SchemaField field, Dictionary<string, object?> data)
        {
            if (string.IsNullOrEmpty(field.Name) || !data.TryGetValue(field.Name, out var records))
                return null;
            return records switch
            {
                Dictionary<string, object?> single => new List<object?> { single },
                List<object?> many => many,
                System.Collections.IList other => other.Cast<object?>().ToList(),
                _ => null,
            };
        }

        /// <summary>A repeat's `count` or `byte_length` as the schema or the input states it.</summary>
        static long? RepeatSize(object? size, Dictionary<string, object?> data, List<SchemaField> siblings)
        {
            object? value = size;
            if (size is string text && text.StartsWith("$"))
            {
                var source = SizeSource(siblings, text[1..]);
                data.TryGetValue(source?.Name ?? text[1..], out value);
            }
            if (value is bool || value is string) return null;
            var (ok, numeric) = Helpers.ToFloat64(value);
            if (!ok || numeric != Math.Floor(numeric)) return null;
            return (long)numeric;
        }

        /// <summary>
        /// A repeat's elements back to back: no framing, no trailer. `count`, where the schema
        /// or the input states one, is checked after the refusals that concern the schema
        /// rather than the input (PS-550, PS-542, PS-387).
        /// </summary>
        // PS-564 is not checked inside a tlv or repeat (the reference's tail_unchecked).
        byte[] EncodeRepeatElements(SchemaField field, Dictionary<string, object?> data,
            List<object?> list, long? count)
        {
            _d564++;
            try { return EncodeRepeatElementsUnchecked(field, data, list, count); }
            finally { _d564--; }
        }

        byte[] EncodeRepeatElementsUnchecked(SchemaField field, Dictionary<string, object?> data,
            List<object?> list, long? count)
        {
            // PS-550: a sibling identifying these values position by position is given as
            // many values as this repeat, as a decoder requires (PS-546).
            foreach (var sibling in field.SiblingNames)
            {
                data.TryGetValue(sibling, out var given);
                if (given is not System.Collections.IList siblingValues || siblingValues.Count != list.Count)
                    throw new InvalidOperationException($"Repeat '{field.Name}' is given {list.Count} values "
                        + $"and '{sibling}', which identifies them, "
                        + $"{(given is System.Collections.IList l ? l.Count.ToString() : "none")} (PS-550)");
            }
            if (RecordsReachable(field.Fields))
                // PS-542: the records do not say which element they came from.
                throw new InvalidOperationException($"Repeat '{field.Name}' has elements that yield "
                    + "several records, which cannot be encoded (PS-542)");
            if (field.ElementName != null)
            {
                // PS-529: each value is encoded as the element, wrapped as it was decoded.
                // PS-544: a null is written as the element's first sentinel, which the field
                // list writes as raw bits for a null value; with no sentinel it is an error.
                if (field.Fields.Count == 0 || field.Fields[0].Sentinel is not { Count: > 0 })
                    for (int p = 0; p < list.Count; p++)
                        if (list[p] == null)
                            throw new InvalidOperationException(
                                $"Repeat '{field.Name}' has null at position {p}, and its element "
                                + "declares no `sentinel` to write for it (PS-544)");
                list = list.Select(v => (object?)new Dictionary<string, object?> { [field.ElementName] = v })
                    .ToList();
            }

            // PS-387: an element present_if dropped is not in the data, and its bytes were
            // in the payload, so they cannot be written back. Only elements that read
            // nothing (computed fields and literals) can be encoded.
            if (field.PresentIf != null && field.Fields.Any(f =>
                    f.Type != FieldType.Number && !IsTemplate(f)
                    && !(f.Type is FieldType.String or FieldType.Boolean && f.Value != null)))
                throw new InvalidOperationException($"repeat field '{field.Name}' declares present_if "
                    + "and its elements read payload bytes, so the elements it dropped cannot be "
                    + "encoded (PS-387)");
            if (count != null && list.Count != count)
                // PS-551: the count and the list must agree; nothing is padded or cut.
                throw new InvalidOperationException($"Repeat '{field.Name}' is given {list.Count} "
                    + $"values for a count of {count} (PS-551)");

            var output = new List<byte>();
            int position = 0;
            foreach (var record in list)
            {
                if (record is not Dictionary<string, object?> asMap)
                    throw new InvalidOperationException($"repeat field '{field.Name}': "
                        + $"expected each record to be a mapping, got {record?.GetType().Name ?? "null"}");
                if (!string.IsNullOrEmpty(field.Index))
                {
                    // PS-370: the index is bound while each element is encoded, as it was
                    // while each was decoded, so a name_from template resolves the same way.
                    asMap = new Dictionary<string, object?>(asMap) { [field.Index!] = position + field.IndexBase };
                }
                // PS-572: the element's position, as its decode recorded it.
                var pushed = _trace?.Push(field.Node, "repeat", position) ?? false;
                byte[] element;
                try { element = EncodeObjectMembers(field.Fields, asMap); }
                finally { _trace?.Pop(pushed); }
                FrameMeta.Check(element, MetaOf(element));   // PS-551: an element closes with it
                output.AddRange(element);
                position++;
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
        byte[] EncodeMatch(SchemaField match, Dictionary<string, object?> data,
            IReadOnlyList<byte>? peeked = null, SchemaField? owner = null,
            List<SchemaField>? siblings = null)
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
                    // The output holds what the field reported. Where that is a text or
                    // boolean label, the reference bound the number under it (PS-523), and
                    // the cases are keyed by that number (PS-524).
                    discriminator = ReferenceBinding(byVar, FieldNamed(varName));
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
                        discriminator = ReferenceBinding(byField, source);
                }
            }

            if (discriminator == null && !string.IsNullOrEmpty(match.Var)
                && data.TryGetValue(match.Var, out var byMatchVar) && byMatchVar != null)
                // An inline discriminator bound to a variable the rest of the input fixes -
                // dnt's presence mask, which the flagged block after it reads.
                discriminator = byMatchVar;
            if (discriminator == null && match.Length > 0 && peeked != null
                && peeked.Count >= match.Length)
            {
                // CR-2026-136: a bit range before the match peeked these bytes and has
                // filled them, so they are the discriminator the decoder will read
                // (r718x's command and device bytes, dnt's first command).
                ulong peekValue = 0;
                for (int i = 0; i < match.Length; i++)
                {
                    int at = _endian == "little" ? match.Length - 1 - i : i;
                    peekValue = (peekValue << 8) | peeked[at];
                }
                discriminator = (double)peekValue;
            }

            // PS-573: where the input gives no discriminator, the decode record's is written
            // and its case taken - before any derivation, which PS-574 makes the fallback.
            bool given = discriminator != null;
            if (!string.IsNullOrEmpty(match.On) && _trace != null
                && _trace.Supplied.Contains(match.On.TrimStart('$')))
                given = false;          // an internal value the record supplied, not input
            Dictionary<string, object?>? record = null;
            if (_trace != null)
            {
                var (found, entry) = _trace.Lookup(_trace.Matches, match.Anchor);
                if (found) record = entry as Dictionary<string, object?>;
            }
            bool fromRecord = false;
            MatchCase? recordedCase = null;
            List<SchemaField>? recordedFallback = null;
            if (!given && record != null)
            {
                if (match.MatchRemaining)
                {
                    fromRecord = true;
                    recordedCase = RecordedCase(match, record.GetValueOrDefault("case"));
                    if (recordedCase == null)
                        recordedFallback = DefaultCaseOf(match) == null
                            ? (DefaultBody(match) ?? new List<SchemaField>()) : null;
                    recordedCase ??= DefaultCaseOf(match);
                }
                else if (discriminator == null)
                    discriminator = record.GetValueOrDefault("value");
            }

            if (match.Length > 0)
                CheckWrittenDiscriminator(discriminator, match.Name);              // PS-557
            if (match.Length > 0 && discriminator != null
                && Helpers.ToFloat64(discriminator) is (true, var early))
            {
                // PS-168, before a case is looked for, as the spec prototype checks it.
                var (elow, ehigh) = IntegerBounds(match.Length * 8, false);
                CheckFits(Math.Round(early, MidpointRounding.ToEven), elow, ehigh,
                    !string.IsNullOrEmpty(match.Name) ? match.Name!
                    : !string.IsNullOrEmpty(match.Var) ? match.Var! : "match", discriminator);
            }
            MatchCase? matched = recordedCase;
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
            if (record != null && !given)
                CheckRecordedCase(match, matched != null && !IsDefaultCase(matched) ? matched : null,
                    data);                                                       // PS-573
            // A known discriminator that matched no case takes the default, and takes it
            // ahead of the claimable-name heuristic: the schema said what an unmatched
            // value means, so guessing a case from the names present would contradict it.
            List<SchemaField>? fallbackFields = recordedFallback;
            if (fromRecord && matched == null && fallbackFields == null)
                fallbackFields = DefaultBody(match) ?? new List<SchemaField>();
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
                // Recovered from which of its fields the input gives, scored through the
                // constructs a branch holds (BestCase); a `remaining` match given no case's
                // fields takes the case matching 0 bytes, which writing nothing selects, unless
                // its default list explains some of the input.
                // PS-563 by score only where no discriminator is given: a given one that
                // matches no case takes the default or is rejected (PS-565).
                if (discriminator == null) matched ??= BestCase(match, data).best;
                if (matched == null && match.MatchRemaining
                    && (BodyScore(DefaultBody(match), data) ?? 0) == 0)
                    matched = match.Cases.FirstOrDefault(c => !IsDefaultCase(c)
                        && CaseMatches(0L, c.CaseValue));
            }
            var label = !string.IsNullOrEmpty(match.Name) ? match.Name!
                : !string.IsNullOrEmpty(match.Var) ? match.Var! : "match";
            if (matched == null && fallbackFields == null && discriminator == null && match.Length > 0)
            {
                // CR-2026-151 (PS-551): where neither the input nor one of this match's own
                // cases fixes the discriminator, a nested match reading it, with exactly one
                // case the input fills, does.
                var nested = NestedDiscriminator(match, data, label);
                if (nested != null)
                {
                    discriminator = (double)nested.Value.value;
                    matched = nested.Value.matched;
                    fallbackFields = nested.Value.fallback;
                    if (!string.IsNullOrEmpty(match.Var))
                        data = new Dictionary<string, object?>(data) { [match.Var!] = (long)nested.Value.value };
                }
            }
            if (fallbackFields == null)
                matched ??= match.Cases.FirstOrDefault(IsDefaultCase);
            if (matched == null && fallbackFields == null
                && match.MatchDefault is List<SchemaField> declaredFallback)
                // The `default:` beside `cases`, taken as the reference takes it where
                // nothing in the input belongs to a case; its discriminator is then missing.
                fallbackFields = declaredFallback;
            if (matched == null && fallbackFields == null)
            {
                var mode = match.MatchDefault as string ?? "error";
                if (match.Length > 0 && discriminator == null)
                    // PS-551, PS-442: an inline discriminator the input neither gives nor
                    // fixes through a case's fields, nor a bit range before it peeked. This
                    // returned no bytes at all where the match was nameless or its default
                    // `skip`. Named by the discriminator, its `var` where it has no `name`.
                    throw new InvalidOperationException(UndeterminedCaseMessage(label));
                if (mode == "skip")
                    // The discriminator, if the match reads one, and nothing for the case.
                    fallbackFields = new List<SchemaField>();
                else if (discriminator != null)
                    // PS-442: no case for the discriminator given, and no default.
                    throw new InvalidOperationException(
                        $"No matching case for value {DiscriminatorText(discriminator)}");
                else
                    // PS-442, PS-551: no discriminator given or derivable, and no case
                    // whose fields the input gives. This wrote nothing and reported nothing.
                    throw new InvalidOperationException(UndeterminedCaseMessage(
                        label != "match" ? label
                        : !string.IsNullOrEmpty(match.On) ? match.On.TrimStart('$') : "match"));
            }

            if (fallbackFields != null)
            {
                var fallbackOut = new List<byte>();
                if (match.Length > 0)
                {
                    var (ok, numeric) = Helpers.ToFloat64(discriminator);
                    // A `default` stands for every unlisted value, so it names none to
                    // write; a zero was written here (PS-551).
                    if (!ok) throw new InvalidOperationException(UndeterminedCaseMessage(label));
                    var rounded = Math.Round(numeric, MidpointRounding.ToEven);
                    var (dlow, dhigh) = IntegerBounds(match.Length * 8, false);
                    CheckFits(rounded, dlow, dhigh, label, discriminator);                 // PS-168
                    fallbackOut.AddRange(Helpers.EncodeUint((ulong)(long)rounded, match.Length, _endian));
                }
                var fallbackBody = EncodeFieldList(fallbackFields, data);
                int fallbackPending = CarriedBy(fallbackBody);
                var fallbackMeta = FrameMeta.Prefixed(fallbackOut.Count, MetaOf(fallbackBody),
                    fallbackBody.Length);
                fallbackOut.AddRange(fallbackBody);
                var fallbackBytes = fallbackOut.ToArray();
                Register564(match, fallbackBody.Length, null);
                _frameBytes = fallbackBytes;
                _framePending = fallbackPending;
                _frameMeta = fallbackMeta;
                return fallbackBytes;
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
                    if (IsDefaultCase(matched!))
                        // PS-551: the default case names no value to write for the
                        // discriminator the input left out.
                        throw new InvalidOperationException(UndeterminedCaseMessage(label));
                    var fromKey = ExactCaseValue(matched!.CaseValue);
                    if (fromKey == null)
                        throw new InvalidOperationException(
                            $"match case {matched!.CaseValue} names no single discriminator value");
                    value = fromKey.Value;
                }
                var (wlow, whigh) = IntegerBounds(match.Length * 8, false);
                CheckFits(value, wlow, whigh, label, discriminator ?? value);              // PS-168
                output.AddRange(WriteUint(value, match.Length, _endian));
            }
            var body = EncodeFieldList(matched!.Fields, data);
            int bodyPending = CarriedBy(body);
            var bodyMeta = FrameMeta.Prefixed(output.Count, MetaOf(body), body.Length);
            output.AddRange(body);
            var matchBytes = output.ToArray();
            Register564(match, body.Length, IsDefaultCase(matched!) ? null : matched);
            _frameBytes = matchBytes;
            _framePending = bodyPending;
            _frameMeta = bodyMeta;
            return matchBytes;
        }

        // --- PS-564: the bytes a `remaining` match writes select its case --------------

        /// <summary>A `remaining` match's chosen case (null for its default) and the bytes
        /// from it to the end of the payload so far.</summary>
        sealed class Check564
        {
            public SchemaField Match = null!;
            public long Tail;
            public MatchCase? Chosen;
        }

        readonly List<Check564> _t564 = new();
        int _d564 = 1;      // inside a tlv or repeat, or outside Run: not checked

        void Register564(SchemaField match, int bodyLength, MatchCase? chosen)
        {
            // Inside a tlv or repeat the bytes after the match are not all in one field
            // list, so it is not checked there (the reference's tail_unchecked).
            if (match.MatchRemaining && _d564 == 0)
                _t564.Add(new Check564 { Match = match, Tail = bodyLength, Chosen = chosen });
        }

        static readonly MatchCase DefaultSelected = new() { IsDefault = true };

        /// <summary>The case a decoder selects for <paramref name="count"/> bytes left
        /// (PS-414, PS-415): the first in declaration order whose key holds the count, else
        /// <see cref="DefaultSelected"/> where the match declares a default, else null.</summary>
        static MatchCase? RemainingSelection(SchemaField match, long count)
        {
            foreach (var c in match.Cases)
                if (!IsDefaultCase(c) && CaseMatches((double)count, c.CaseValue)) return c;
            if (match.Cases.Any(IsDefaultCase) || match.MatchDefault is List<SchemaField>
                || (match.MatchDefault as string) == "skip")
                return DefaultSelected;
            return null;
        }

        static bool SameSelection(MatchCase? selected, MatchCase? chosen)
            => chosen == null ? object.ReferenceEquals(selected, DefaultSelected)
                : object.ReferenceEquals(selected, chosen);

        static string RemainingMismatch(SchemaField match, long count, MatchCase? selected,
            MatchCase? chosen)
        {
            var label = !string.IsNullOrEmpty(match.Name) ? $"'{match.Name}'" : "an unnamed match";
            static string Describe(MatchCase? c) => c == null ? "no case"
                : object.ReferenceEquals(c, DefaultSelected) || IsDefaultCase(c) ? "its default"
                : "case " + (c.CaseValue is string text ? text : DiscriminatorText(c.CaseValue!));
            return $"Field {label}: the {count} byte(s) from it to the end of the payload select "
                + $"{Describe(selected)}, not {Describe(chosen ?? DefaultSelected)}, the case its "
                + "input gives (PS-564)";
        }

        static bool IsDefaultCase(MatchCase c)
            // The map form of `cases:` records a `default:` key as the literal string
            // rather than setting the flag, so both spellings are checked here.
            => c.IsDefault || (c.CaseValue as string) == "default";

        /// <summary>
        /// CR-2026-151 (PS-551), applied on spec main for 0.5.5: the value a nested
        /// match fixes for this match's discriminator, with the outer case it selects (or the
        /// `default` fields beside the cases), or null where no nested match reading it has
        /// a case the input fills. The input fixes it where it fills exactly one such case,
        /// whose key is a single value selecting, in this match and in every match between
        /// that reads it, the branch holding the nested match; otherwise it is rejected,
        /// naming the discriminator. Mirrors the reference's _nested_discriminator.
        /// </summary>
        static (long value, MatchCase? matched, List<SchemaField>? fallback)? NestedDiscriminator(
            SchemaField match, Dictionary<string, object?> data, string label)
        {
            var names = new HashSet<string>();
            if (!string.IsNullOrEmpty(match.Var)) names.Add(match.Var!);
            if (!string.IsNullOrEmpty(match.Name)) names.Add(match.Name!);
            if (names.Count == 0) return null;
            List<string> NamesOf(List<SchemaField> fields) =>
                fields.Where(f => PayloadName(f) != null).Select(f => f.Name!).ToList();
            // A case is filled where the input gives every one of its fields and nothing any
            // other case of its match declares beyond them (Netvox r718n3's case 3 holds
            // the fields of cases 1 and 2 together).
            bool Filled(List<string> own, HashSet<string> given) =>
                own.Count > 0 && own.All(data.ContainsKey) && given.All(own.Contains);
            // A step is the match and the branch taken: a case, or null for the default beside.
            var filled = new List<(List<(SchemaField m, MatchCase? branch)> path, MatchCase c)>();
            void Walk(List<SchemaField>? fields, List<(SchemaField m, MatchCase? branch)> path)
            {
                if (fields == null) return;
                foreach (var f in fields)
                {
                    var inner = f.MatchInline;
                    if (inner == null) continue;
                    var on = (inner.On ?? "").TrimStart('$');
                    bool reads = on.Length > 0 && names.Contains(on);
                    var given = reads
                        ? inner.Cases.Where(c => !IsDefaultCase(c)).SelectMany(c => NamesOf(c.Fields))
                            .Where(data.ContainsKey).ToHashSet()
                        : new HashSet<string>();
                    foreach (var c in inner.Cases)
                    {
                        var next = path;
                        if (reads)
                        {
                            if (!IsDefaultCase(c) && Filled(NamesOf(c.Fields), given)) filled.Add((path, c));
                            next = new List<(SchemaField, MatchCase?)>(path) { (inner, c) };
                        }
                        Walk(c.Fields, next);
                    }
                    if (inner.MatchDefault is List<SchemaField> fb)
                        Walk(fb, reads ? new List<(SchemaField, MatchCase?)>(path) { (inner, null) } : path);
                }
            }
            foreach (var c in match.Cases)
                Walk(c.Fields, new List<(SchemaField, MatchCase?)> { (match, c) });
            if (match.MatchDefault is List<SchemaField> outerDefault)
                Walk(outerDefault, new List<(SchemaField, MatchCase?)> { (match, null) });
            if (filled.Count == 0) return null;
            if (filled.Count != 1) throw new InvalidOperationException(UndeterminedCaseMessage(label));
            var (steps, filledCase) = filled[0];
            var value = ExactCaseValue(filledCase.CaseValue);
            if (value == null) throw new InvalidOperationException(UndeterminedCaseMessage(label));
            foreach (var (m, branch) in steps)
            {
                MatchCase? selected = m.Cases.FirstOrDefault(c => !IsDefaultCase(c)
                    && CaseMatches((double)value.Value, c.CaseValue));
                if (selected == null && branch != null && IsDefaultCase(branch)) selected = branch;
                if (!ReferenceEquals(selected, branch))
                    throw new InvalidOperationException(UndeterminedCaseMessage(label));
            }
            var outer = steps[0].branch;
            return outer != null
                ? (value.Value, outer, null)
                : (value.Value, null, (List<SchemaField>)match.MatchDefault!);
        }

        /// <summary>A discriminator as Python's str() writes it: an integral number as an integer.</summary>
        static string DiscriminatorText(object discriminator)
        {
            if (discriminator is bool b) return b ? "True" : "False";
            var (ok, numeric) = Helpers.ToFloat64(discriminator);
            if (!ok) return discriminator.ToString() ?? "";
            if (numeric == Math.Floor(numeric) && Math.Abs(numeric) < 1e15) return ((long)numeric).ToString();
            return numeric.ToString("R", System.Globalization.CultureInfo.InvariantCulture);
        }

        static bool CaseMatches(object? discriminator, object? caseValue)
        {
            if (caseValue == null) return false;
            var (ok, numeric) = Helpers.ToFloat64(discriminator);
            if (!ok)
                return string.Equals(discriminator?.ToString(), caseValue.ToString());
            // A fraction selects no case it would round to: 2.5 chose case 2 here, where
            // every decoder reads it as matching no single key (SchemaDecoder.DecodeMatch).
            if (numeric != Math.Truncate(numeric))
                return SchemaDecoder.FractionInRange(numeric, caseValue);
            long value = (long)numeric;

            if (caseValue is string text && Helpers.ParseListCaseKey(text) is { } listed)
                return listed.Contains(value);
            if (caseValue is string spelled && spelled.Contains(".."))
                // An inclusive range (PS-398), as the decoder reads it; it fell through to
                // ParseIntAny and matched nothing.
                return SchemaDecoder.FractionInRange(value, caseValue);
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
        /// <summary>
        /// The reference's _case_claims: claimable fields, a flagged group's (whose mask says
        /// which are present) and an `optional: true` field marked optional (PS-527).
        /// </summary>
        static List<(SchemaField field, bool optional)> CaseClaims(List<SchemaField>? fields, bool optional)
        {
            var sink = new List<(SchemaField, bool)>();
            if (fields == null) return sink;
            var covered = CoveredPeeks(fields);
            foreach (var f in fields)
            {
                if (f.ByteGroup.Count > 0) { sink.AddRange(CaseClaims(f.ByteGroup, optional)); continue; }
                if (f.Flagged?.Groups != null)
                {
                    foreach (var group in f.Flagged.Groups) sink.AddRange(CaseClaims(group.Fields, true));
                    continue;
                }
                if (f.MatchInline != null) { sink.AddRange(MatchClaims(f.MatchInline)); continue; }
                if (!string.IsNullOrEmpty(f.Name) && !f.Name.StartsWith("_")
                    && (IsLiteral(f) || IsComputed(f) || f.IsEcho))
                {
                    // CR-2026-175: a non-consuming byte string writes nothing either (PS-589).
                    // A literal or computed field writes no bytes, but only this case reports
                    // it, so it claims its name, never required: a literal where the input
                    // gives its value, a computed field so the case is chosen and what it is
                    // computed from is asked for (PS-434). Neither claimed anything, so an
                    // entry reporting only these was never written (PS-442).
                    sink.Add((f, true));
                    continue;
                }
                if (!string.IsNullOrEmpty(f.Name) && !f.Name.StartsWith("_") && f.Checksum != null)
                {
                    sink.Add((f, true));        // PS-587: computed, never taken from the input
                    continue;
                }
                if (PayloadName(f) == null) continue;
                // PS-551 (CR-2026-141): a peek the case's next field writes over is derived
                // where the input omits it, so the case does not need it given.
                sink.Add((f, optional || f.Optional || covered.Contains(f)));
            }
            return sink;
        }

        /// <summary>
        /// The reference's _match_claims: a match claims its name and every branch's
        /// fields, each optional, since which branch supplies a name depends on the data;
        /// each name once. Not descending into it, a tlv case whose only content is a
        /// nameless match claimed nothing and was never written - the encode returned an
        /// empty payload with no error - and an object holding one refused every value.
        /// </summary>
        static List<(SchemaField field, bool optional)> MatchClaims(SchemaField match)
        {
            var sink = new List<(SchemaField, bool)>();
            var seen = new HashSet<string>();
            var name = PayloadName(match);
            if (name != null)
            {
                var type = match.Length switch
                {
                    2 => FieldType.U16, 3 => FieldType.U24, 4 => FieldType.U32,
                    8 => FieldType.U64, _ => FieldType.U8,
                };
                sink.Add((new SchemaField { Name = name, Type = type, RawType = type.ToString().ToLowerInvariant() }, true));
                seen.Add(name);
            }
            var bodies = match.Cases.Select(c => c.Fields).ToList();
            if (match.MatchDefault is List<SchemaField> fallback) bodies.Add(fallback);
            foreach (var body in bodies)
                foreach (var (f, _) in CaseClaims(body, true))
                {
                    // A literal is seen by its value too: branches report one name as
                    // different literals (aquascope's `pc`), and the input's value says which.
                    var seenKey = IsLiteral(f) ? f.Name + "\u0000" + LiteralKey(f.Value) : f.Name;
                    if (f.Name != null && seen.Add(seenKey)) sink.Add((f, true));
                }
            return sink;
        }

        static string LiteralKey(object? value) => value switch
        {
            bool b => "b:" + b,
            string t => "s:" + t,
            _ => Helpers.ToFloat64(value) is (true, var d)
                ? "n:" + d.ToString("R", System.Globalization.CultureInfo.InvariantCulture)
                : "o:" + value,
        };

        /// <summary>Whether a type's encoded value carries a fraction (FRACTIONAL_TYPES).</summary>
        static bool IsFractionalType(FieldType t) => t is FieldType.F16 or FieldType.BF16 or FieldType.F32
            or FieldType.F64 or FieldType.UDec or FieldType.SDec or FieldType.F32LE16
            or FieldType.F32BE16LE or FieldType.UFlt16 or FieldType.SFlt16 or FieldType.SFlt24;

        /// <summary>
        /// The reference's reverses_to_integer, a proto rule the specification does not
        /// state: round half to even, as PS-552 rounds what is written, and accept the value
        /// within 1e-9 x max(1, |n|) of that integer n. An absolute 1e-9 refused 0xfffffe
        /// through div: 100, which reverses to 16777214.000000002.
        /// </summary>
        internal static bool ReversesToInteger(double raw)
        {
            double n = Math.Round(raw, MidpointRounding.ToEven);
            return Math.Abs(raw - n) <= 1e-9 * Math.Max(1.0, Math.Abs(n));
        }

        /// <summary>
        /// Whether a case's field takes a value (PS-527): an object element supplies exactly
        /// the members it declares, and a field takes no value it cannot write.
        /// </summary>
        bool Fits(SchemaField f, object? value, Dictionary<string, string> modes, bool exactOnly)
        {
            if (IsLiteral(f)) return Helpers.SameLabel(f.Value, value);        // PS-359, PS-513
            if (IsComputed(f)) return true;        // asks for what it is computed from (PS-434)
            var name = f.Name;
            if (f.Type == FieldType.Object && value is Dictionary<string, object?> whole
                && !(modes.TryGetValue(name, out var md) && md == "merge"))
            {
                var claims = CaseClaims(f.Fields, false);
                var every = claims.Select(c => c.field.Name).ToHashSet();
                return claims.All(c => c.optional || whole.ContainsKey(c.field.Name))
                    && whole.Keys.All(every.Contains);
            }
            // A mapping is no value for a scalar field, nor a non-mapping for an object: one
            // name may be an object under one tag and a number under another.
            bool isMap = value is Dictionary<string, object?>;
            if (isMap && f.Type != FieldType.Object && f.Type != FieldType.Repeat) return false;
            if (f.Type == FieldType.Object && !isMap
                && !(modes.TryGetValue(name, out var mode) && mode == "merge")) return false;
            var (fits, exact) = CaseTakes(f, value);
            return fits && (exact || !exactOnly);
        }

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

        /// <summary>A repeat takes a list whose elements its members take (CR-2026-181), so a
        /// list of 3-byte ids goes to the case whose element declares `length: 3`, not a 6-byte
        /// one declared first (the registry's Tektelic orca tracker).</summary>
        static (bool fits, bool exact) RepeatTakes(SchemaField f, List<object?> items)
        {
            bool exact = true;
            foreach (var item in items)
            {
                if (item == null) continue;
                var record = f.ElementName != null
                    ? new Dictionary<string, object?> { [f.ElementName] = item }
                    : item as Dictionary<string, object?>;
                if (record == null) continue;
                foreach (var member in f.Fields)
                {
                    if (string.IsNullOrEmpty(member.Name)
                        || !record.TryGetValue(member.Name, out var v) || v == null) continue;
                    var (fits, memberExact) = CaseTakes(member, v);
                    if (!fits) return (false, false);
                    exact = exact && memberExact;
                }
            }
            return (true, exact);
        }

        /// <summary>
        /// The reference's _case_takes (CR-2026-148, PS-527 amended): whether a tlv case's
        /// field can take a value, and whether it reproduces it exactly. Fits: the value
        /// with the field's arithmetic undone (and, for an integer type or bit range,
        /// rounded per PS-552) lies within the type's range (PS-168); a float takes any
        /// finite value no larger than its largest. Exact: the arithmetic re-applied to the
        /// raw value written, through the type's storage, gives the value under binary64
        /// equality. No tolerance anywhere; the test this replaces asked for an integer
        /// within an absolute 1e-9, which refused every float field and 0xfffffe through
        /// div: 100.
        /// </summary>
        static (bool fits, bool exact) CaseTakes(SchemaField f, object? value)
        {
            if (Cr181.IsByteString(f.Type))                        // CR-2026-181 (PS-527)
            {
                var took = Cr181.Takes(f, value);
                return (took, took);
            }
            if (f.Type == FieldType.Repeat && value is List<object?> items)
                return RepeatTakes(f, items);
            var reversed = ReverseLookup(value, f.Lookup);
            if (reversed is bool) return (true, true);
            var (ok, given) = Helpers.ToFloat64(reversed);
            if (!ok) return (true, true);
            double raw;
            try
            {
                raw = ReverseCanonicalModifiers(ReverseTransformStages(given, f.Transform), f);
            }
            catch (InvalidOperationException)
            {
                return (false, false);
            }
            if (double.IsNaN(raw) || double.IsInfinity(raw)) return (false, false);
            double stored;
            var range = Helpers.ParseBitRange(f.RawType);
            if (range != null)
            {
                stored = Math.Round(raw, MidpointRounding.ToEven);
                if (!FitsBits(stored, range.Value.end - range.Value.start + 1,
                        f.RawType.StartsWith("s"))) return (false, false);
            }
            else if (IntegerBits(f.Type) is { } ib)
            {
                stored = Math.Round(raw, MidpointRounding.ToEven);
                if (!FitsBits(stored, ib.bits, ib.signed)) return (false, false);
            }
            else if (f.Type == FieldType.BF16)
            {
                try { stored = Bf16.Decode(Bf16.Encode(raw, f.Name)); }       // PS-559's rounding
                catch (InvalidOperationException) { return (false, false); }
            }
            else if (FloatMax(f.Type) is { } max)
            {
                if (Math.Abs(raw) > max) return (false, false);
                stored = f.Type switch
                {
                    FieldType.F64 => raw,
                    FieldType.F16 => F16Round(raw),
                    _ => (double)(float)raw,
                };
            }
            else if (f.Type is FieldType.UDec or FieldType.SDec)
            {
                double n = Math.Round(raw * 10, MidpointRounding.ToEven);           // PS-556
                bool sdec = f.Type == FieldType.SDec;
                if (n < (sdec ? -80 : 0) || n > (sdec ? 79 : 159)) return (false, false);
                double whole = Math.Floor(n / 10);
                stored = whole + (n - whole * 10) * 0.1;
            }
            else if (f.Type is FieldType.UFlt16 or FieldType.SFlt16 or FieldType.SFlt24)
            {
                double? v;
                try { v = Wave4.DecodeMinifloat(f.Type, Wave4.EncodeMinifloat(f.Type, raw)); }
                catch (Exception) { return (false, false); }
                if (v == null) return (false, false);
                stored = v.Value;
            }
            else
            {
                return (true, true);
            }
            return (true, SchemaDecoder.ApplyModifiers(stored, f) == given);
        }

        /// <summary>The largest finite magnitude of a binary float type (CR-2026-148).</summary>
        static double? FloatMax(FieldType t) => t switch
        {
            FieldType.F16 => 65504.0,
            FieldType.F32 or FieldType.F32LE16 or FieldType.F32BE16LE => float.MaxValue,
            FieldType.F64 => double.PositiveInfinity,
            _ => null,
        };

        /// <summary>An integer type's width and sign, word-ordered and 64-bit included.</summary>
        static (int bits, bool signed)? IntegerBits(FieldType t) => t switch
        {
            FieldType.U8 => (8, false), FieldType.U16 => (16, false), FieldType.U24 => (24, false),
            FieldType.U32 or FieldType.U32LE16 or FieldType.U32BE16LE => (32, false),
            FieldType.U64 => (64, false),
            FieldType.S8 => (8, true), FieldType.S16 => (16, true), FieldType.S24 => (24, true),
            FieldType.S32 or FieldType.S32LE16 or FieldType.S32BE16LE => (32, true),
            FieldType.S64 => (64, true),
            _ => null,
        };

        static bool FitsBits(double v, int bits, bool signed) => signed
            ? v >= -Math.ScaleB(1.0, bits - 1) && v < Math.ScaleB(1.0, bits - 1)
            : v >= 0 && v < Math.ScaleB(1.0, bits);

        /// <summary>A value rounded to the nearest IEEE half, a tie to even, from binary64.</summary>
        internal static double F16Round(double x)
        {
            if (x == 0 || double.IsInfinity(x) || double.IsNaN(x)) return x;
            double a = Math.Abs(x);
            int e = Math.Max(Math.ILogB(a), -14);
            double q = Math.ScaleB(1.0, e - 10);
            double r = Math.Round(a / q, MidpointRounding.ToEven) * q;
            return x < 0 ? -r : r;
        }

        /// <summary>
        /// Rebuild a TLV payload from decoded output.
        /// </summary>
        /// <remarks>
        /// A case whose fields are all absent is not emitted. A case that cannot be encoded -
        /// a wildcard tag (PS-270), or a field the data does not carry - throws, so the
        /// caller records it against the payload rather than writing a wrong tag.
        /// </remarks>
        // PS-564 is not checked inside a tlv or repeat (the reference's tail_unchecked).
        byte[] EncodeTLV(SchemaField tlv, Dictionary<string, object?> data)
        {
            _d564++;
            try { return EncodeTLVUnchecked(tlv, data); }
            finally { _d564--; }
        }

        byte[] EncodeTLVUnchecked(SchemaField tlv, Dictionary<string, object?> data)
        {
            // `merge: false` reports its entries as a list under `channels`, which the
            // claiming below cannot see, so this wrote nothing and reported success. Encoding
            // the list is not defined yet; refuse it by name, as the reference does.
            if (tlv.Merge == false)
                throw new InvalidOperationException("a tlv with merge: false reports its entries under 'channels', and encoding them is not supported");
            if (tlv.TLVCases == null || tlv.TLVCases.Count == 0) return Array.Empty<byte>();
            int lengthSize = Math.Max(0, tlv.LengthSize);
            var order = _order ?? data.Keys.ToList();

            // PS-525 to PS-527 (CR-2026-110, -125, -128): each value - a scalar, each element
            // of an array PS-157 collected, a `last` value, a merged object's members - goes
            // under the first case whose declared fields it supplies in full; nothing is
            // zero-filled or dropped, and what no case takes is an error. A case requiring
            // more fields is tried first, then declaration order, so em400-mud's [131, 103]
            // (temperature, temperature_alarm) is not stranded by [3, 103] (temperature).
            var modes = tlv.TLVRepeatModes;
            string ModeOf(string n) => modes.TryGetValue(n, out var md) ? md : "collect";
            var entries = new List<(string key, List<SchemaField> fields, List<SchemaField> required,
                List<SchemaField> optional, int rank)>();
            // CR-2026-173: a merged object's leaf paths, at every depth, and the paths it
            // combines as objects.
            var declaredMembers = new Dictionary<string, HashSet<string>>();
            var mergedObjects = new Dictionary<string, HashSet<string>>();
            foreach (var (caseKey, caseFields) in tlv.TLVCases)
            {
                if (caseKey == "default") continue;
                var required = new List<SchemaField>();
                var optional = new List<SchemaField>();
                foreach (var (f, isOptional) in CaseClaims(caseFields, false))
                {
                    (isOptional ? optional : required).Add(f);
                    var n = f.Name;
                    if (ModeOf(n) != "merge") continue;
                    if (!declaredMembers.TryGetValue(n, out var set)) declaredMembers[n] = set = new();
                    if (!mergedObjects.TryGetValue(n, out var objs)) mergedObjects[n] = objs = new();
                    SchemaParser.TypedMembers(f.Fields, new List<string>(), set, objs);
                }
                entries.Add((caseKey, caseFields, required, optional, entries.Count));
            }
            // An object counts its members (CR-2026-129), so a merged {adr, dr} case outranks {dr}.
            // A merged object counts its leaves, at every depth (CR-2026-173).
            int Width(List<SchemaField> fields) => fields.Sum(f =>
                f.Type == FieldType.Object && ModeOf(f.Name) == "merge"
                    ? Math.Max(1, SchemaParser.MergedLeaves(f).Count)
                    : f.Type == FieldType.Object ? Math.Max(1, CaseClaims(f.Fields, false).Count) : 1);
            entries = entries.OrderByDescending(e => Width(e.required)).ThenBy(e => e.rank).ToList();

            // A supplied member of a merged object that no case declares has nowhere to go.
            var givenLeaves = new Dictionary<string, HashSet<string>>();
            foreach (var (n, members) in declaredMembers)
            {
                if (!data.TryGetValue(n, out var value) || value is not Dictionary<string, object?> whole) continue;
                var given = new HashSet<string>();
                SchemaParser.LeafPaths(whole, mergedObjects[n], new List<string>(), given);
                givenLeaves[n] = given;
                var stray = given.Where(k => !members.Contains(k)).Select(k => $"'{SchemaParser.Dotted(k)}'")
                    .OrderBy(k => k, StringComparer.Ordinal).ToList();
                if (stray.Count > 0)
                    throw new InvalidOperationException($"tlv: the merged object '{n}' is given "
                        + $"[{string.Join(", ", stray)}], which no case declares (PS-527)");
            }

            var queues = new Dictionary<string, Queue<object?>>();
            foreach (var e in entries)
                foreach (var f in e.required.Concat(e.optional))
                {
                    var n = f.Name;
                    if (!data.TryGetValue(n, out var v) || queues.ContainsKey(n) || ModeOf(n) == "merge") continue;
                    bool collected = v is System.Collections.IList && !(f.Type == FieldType.Repeat
                        || (f.Type == FieldType.Bytes && f.Format == "array")) && ModeOf(n) == "collect";
                    queues[n] = collected
                        ? new Queue<object?>(((System.Collections.IList)v!).Cast<object?>())
                        : new Queue<object?>(new[] { v });
                }
            var pending = new Dictionary<string, HashSet<string>>();
            foreach (var (n, given) in givenLeaves)
                pending[n] = new HashSet<string>(given);

            var written = new List<(string key, List<SchemaField> fields, Dictionary<string, object?> data,
                int position, int sub, int seq)>();
            // CR-2026-148 (PS-527 amended): a first sweep places only values a case reproduces
            // exactly, a second the rest; the ranking applies within each.
            foreach (var exactOnly in new[] { true, false })
            {
                bool progress = true;
                while (progress)
                {
                    progress = false;
                    foreach (var e in entries)
                    {
                        while (true)
                        {
                            var values = new Dictionary<string, object?>();
                            int taken = 0;
                            bool complete = true;
                            foreach (var f in e.required)
                            {
                                var n = f.Name;
                                if (ModeOf(n) == "merge")
                                {
                                    if (!pending.ContainsKey(n) || data[n] is not Dictionary<string, object?> whole)
                                    {
                                        complete = false;
                                        break;
                                    }
                                    var leaves = SchemaParser.MergedLeaves(f);
                                    bool fresh = false;
                                    foreach (var leaf in leaves)
                                    {
                                        if (!givenLeaves[n].Contains(leaf)) { complete = false; break; }
                                        fresh |= pending[n].Contains(leaf);
                                    }
                                    if (!complete) break;
                                    values[n] = SchemaParser.ValueAt(whole, leaves);
                                    if (fresh) taken++;
                                    continue;
                                }
                                if (!queues.TryGetValue(n, out var q) || q.Count == 0 || !Fits(f, q.Peek(), modes, exactOnly))
                                {
                                    complete = false;
                                    break;
                                }
                                values[n] = q.Peek();
                                taken++;
                            }
                            if (!complete) break;
                            foreach (var f in e.optional)
                            {
                                var n = f.Name;
                                if (queues.TryGetValue(n, out var q) && q.Count > 0 && Fits(f, q.Peek(), modes, exactOnly))
                                {
                                    values[n] = q.Peek();
                                    taken++;
                                }
                            }
                            if (taken == 0) break;
                            var entryData = new Dictionary<string, object?>(data);
                            int position = int.MaxValue, sub = 0;
                            foreach (var (n, v) in values)
                            {
                                entryData[n] = v;
                                int index = order.ToList().IndexOf(n);
                                int at = index < 0 ? int.MaxValue : index, rank = 0;
                                if (ModeOf(n) == "merge")
                                {
                                    var done = new HashSet<string>();
                                    SchemaParser.LeafPaths((Dictionary<string, object?>)v!, mergedObjects[n],
                                        new List<string>(), done);
                                    pending[n].ExceptWith(done);
                                    // Parts of one merged object go out in case declaration
                                    // order (CR-2026-173), as the reference's `position`.
                                    rank = e.rank;
                                }
                                else
                                    queues[n].Dequeue();
                                if (at < position || (at == position && rank < sub))
                                {
                                    position = at;
                                    sub = rank;
                                }
                            }
                            written.Add((e.key, e.fields, entryData, position, sub, written.Count));
                            progress = true;
                        }
                    }
                }
            }
            foreach (var (n, q) in queues)
                if (q.Count > 0)
                    throw new InvalidOperationException($"tlv: no case takes the value of '{n}' "
                        + $"{q.Peek()} in full (PS-502, PS-527)");
            foreach (var (n, left) in pending)
                if (left.Count > 0)
                    throw new InvalidOperationException("tlv: no case takes the members ["
                        + string.Join(", ", left.Select(m => $"'{SchemaParser.Dotted(m)}'").OrderBy(m => m, StringComparer.Ordinal))
                        + $"] of the merged object '{n}' in full (PS-527)");
            // A `name_from` key no claim can name. Where the template reads a tag component
            // the tag is not in the data, so no case can take the value - and it was dropped
            // with nothing reported. A key some field declares by name is that field's.
            Dictionary<string, (bool reads, List<object?> literals)>? declaredNames = null;
            foreach (var caseFields in tlv.TLVCases.Values)
                foreach (var (f, _) in CaseClaims(caseFields, false))
                {
                    if (string.IsNullOrEmpty(f.NameFrom)) continue;
                    var pattern = new System.Text.RegularExpressions.Regex("^" + string.Join(".+",
                        System.Text.RegularExpressions.Regex.Split(f.NameFrom!, @"\$\{\w+\}")
                            .Select(System.Text.RegularExpressions.Regex.Escape)) + "$");
                    foreach (var key in order)
                    {
                        if (!data.ContainsKey(key) || queues.ContainsKey(key) || pending.ContainsKey(key)
                            || !pattern.IsMatch(key)) continue;
                        declaredNames ??= DeclaredSources(_fields);
                        if (declaredNames.ContainsKey(key)) continue;
                        throw new InvalidOperationException($"tlv: no case takes the value of '{key}' "
                            + $"{data[key]} in full (PS-502, PS-527)");
                    }
                }

            // Payload order, so a payload round-trips; entries of one name keep the order taken.
            var output = new List<byte>();
            int entryIndex = 0;
            foreach (var w in written.OrderBy(w => w.position).ThenBy(w => w.sub).ThenBy(w => w.seq))
            {
                var tag = EncodeTLVTag(w.key, tlv);
                // PS-572: the entry's index in payload order, as its decode recorded it.
                var pushed = _trace?.Push(tlv.Anchor, "tlv", entryIndex++) ?? false;
                byte[] value;
                try { value = EncodeFieldList(w.fields, w.data); }
                finally { _trace?.Pop(pushed); }
                FrameMeta.Check(value, MetaOf(value));   // PS-551: a tlv value is measured whole
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

        /// <summary>
        /// The value a `$` reference bound for a field that reported <paramref name="reported"/>
        /// (PS-523): a numeric label as itself, else the number its lookup maps from - the key
        /// of a label, or the value a `${value}` default wrote. A label only a plain `default`
        /// carries has no number to recover and is returned as it is, matching no case.
        /// </summary>
        static object? ReferenceBinding(object? reported, SchemaField? source)
        {
            // CR-2026-132: an enum bound its code; the data carries its name.
            if (source?.Type == FieldType.Enum && reported is string name)
                return ReverseEnum(name, source);
            if (source?.Lookup is not { Count: > 0 } || DecodeContext.IsNumericLabel(reported))
                return reported;
            var reversed = ReverseLookup(reported, source.Lookup, source.Name);   // PS-169
            if (reversed is string text && Wave5.Template(source) is { } template
                && Wave5.MatchTemplate(template, text) is { } recovered)
                return recovered;
            return reversed;
        }

        /// <summary>The code an enum reported <paramref name="name"/> for, else the name.</summary>
        static object? ReverseEnum(string name, SchemaField field)
        {
            var codes = EnumCodes(field, name);
            if (codes.Count > 1) throw SharedLabel(field.Name, name, codes);   // PS-169
            if (codes.Count == 1) return (double)codes[0];
            if (field.EnumDefault == name) return name;
            if (name.StartsWith("unknown(") && name.EndsWith(")")
                && int.TryParse(name[8..^1], out var n))
                return (double)n;
            return name;
        }

        /// <summary>The field declaring <paramref name="name"/>, searched anywhere in the schema.</summary>
        SchemaField? FieldNamed(string name) =>
            SearchSchema(f => f.Name == name);

        /// <summary>The field that declared `var: name`, searched anywhere in the schema.</summary>
        SchemaField? FieldDeclaringVar(string varName) =>
            SearchSchema(f => f.Var == varName && !string.IsNullOrEmpty(f.Name));

        SchemaField? SearchSchema(Func<SchemaField, bool> wanted)
        {
            // The fields being encoded first: a ports schema may declare one name per port.
            var found = Search(_fields, wanted) ?? Search(_schema.Fields, wanted);
            if (found != null) return found;
            if (_schema.Ports != null)
                foreach (var port in _schema.Ports.Values)
                {
                    found = Search(port.Fields, wanted);
                    if (found != null) return found;
                }
            if (_schema.Definitions != null)
                foreach (var def in _schema.Definitions.Values)
                {
                    found = Search(def.Fields, wanted);
                    if (found != null) return found;
                }
            return null;
        }

        static SchemaField? Search(List<SchemaField> fields, Func<SchemaField, bool> wanted)
        {
            foreach (var f in fields)
            {
                if (wanted(f)) return f;
                foreach (var nested in NestedFieldLists(f))
                {
                    var found = Search(nested, wanted);
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
            // The value as given, for a PS-168 rejection to name (PS-552).
            _givenField = field;
            _givenValue = value;
            var reversed = ReverseLookup(value, field.Lookup, field.Name ?? "?");   // PS-169

            if (reversed is bool unmatched && field.Lookup is { Count: > 0 })
                // PS-513: a boolean matches only a boolean label, and none has this one
                // under a key of its own. Where only the `default` carries it, it stands
                // for every unmapped value (PS-269, PS-409): there is nothing to write.
                throw new InvalidOperationException(
                    $"{(unmatched ? "true" : "false")} is not a label with a key of its own in "
                    + $"the lookup for '{field.Name}'; a boolean matches only a boolean label, "
                    + "and a `default` label has no value to recover (PS-409, PS-513)");

            // PS-409: a string matching the ${value} default carries the value it stands for.
            if (reversed is string text && Wave5.Template(field) is { } template)
                reversed = Wave5.MatchTemplate(template, text) ?? throw new InvalidOperationException(
                    $"'{text}' is neither a label in the lookup for '{field.Name}' nor a match "
                    + $"for its default '{template}' (PS-409)");

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

            if (field.Type is FieldType.F16 or FieldType.BF16 or FieldType.F32 or FieldType.F64
                or FieldType.UDec or FieldType.SDec or FieldType.F32LE16 or FieldType.F32BE16LE
                or FieldType.UFlt16 or FieldType.SFlt16 or FieldType.SFlt24)
                return result;
            // Half-to-even, matching the reference interpreter's rounding.
            return Math.Round(result, MidpointRounding.ToEven);
        }

        /// <summary>Map a label back to its integer.</summary>
        /// <remarks>
        /// Labels are compared by <see cref="Helpers.SameLabel"/> (PS-513). Only a string
        /// was looked up, so a boolean passed through and was written as its own 0 or 1 -
        /// `false` through `[true, false]` wrote index 0 (CR-2026-104).
        /// </remarks>
        static object? ReverseLookup(object? value, Dictionary<int, object>? lookup,
            string? name = null)
        {
            if (lookup == null || lookup.Count == 0) return value;
            // PS-169 (CR-2026-146): a label two or more codes share names no one code, so it
            // is rejected, naming the field. It wrote the first found. With no name - a
            // caller only ranking candidate cases - the smallest code is taken.
            var codes = lookup.Where(kv => Helpers.SameLabel(kv.Value, value))
                .Select(kv => kv.Key).OrderBy(k => k).ToList();
            if (codes.Count == 0) return value;
            if (codes.Count > 1 && name != null) throw SharedLabel(name, value, codes);
            return (double)codes[0];
        }

        /// <summary>The PS-169 rejection, in the spec prototype's words; codes ascending.</summary>
        static InvalidOperationException SharedLabel(string? name, object? label,
            IEnumerable<int> codes) =>
            new($"Field '{name}': the label {LabelText(label)} is shared by codes "
                + $"[{string.Join(", ", codes.OrderBy(c => c))}], so it cannot be encoded (PS-169)");

        /// <summary>A value quoted in a PS-573 message (CR-2026-164): a number as JavaScript's
        /// String(number), anything else as Python's repr.</summary>
        static string MessageValue(object? value) => value switch
        {
            sbyte or byte or short or ushort or int or uint or long or ulong or float or double
                or decimal => JsNumber(value),
            _ => PayloadSchema.Meta.PyRepr(value),
        };

        /// <summary>
        /// A label as the PS-169 and PS-067 messages write it: text in single quotes, a
        /// boolean as JSON writes it, a number as JavaScript's String(number) does.
        /// </summary>
        static string LabelText(object? label) => label switch
        {
            string text => $"'{text}'",
            bool flag => flag ? "true" : "false",
            _ => JsNumber(label),
        };

        /// <summary>
        /// PS-168 for udec/sdec (PS-330): one byte holds a whole part in a nibble and tenths
        /// 0 to 9, so 0 to 15.9, or -8 to 7.9 signed. <paramref name="tenths"/> is the value
        /// as encoded, in tenths. Both said "does not fit udec", in words of their own.
        /// </summary>
        /// <summary>
        /// PS-557 (CR-2026-147): a fractional value given for a discriminator the encoder
        /// writes is rejected, naming the match as PS-535 does, and never rounded or
        /// truncated.
        /// </summary>
        static void CheckWrittenDiscriminator(object? value, string? name)
        {
            if (value is not (double or float)) return;
            double d = Convert.ToDouble(value);
            if (double.IsNaN(d) || double.IsInfinity(d) || d == Math.Truncate(d)) return;
            var label = string.IsNullOrEmpty(name) ? "an unnamed match" : $"'{name}'";
            throw new InvalidOperationException(
                $"Field {label}: the discriminator {JsNumber(d)} is not an integer, so it "
                + "cannot be written (PS-557)");
        }

        static void CheckNibbleDecimal(long tenths, bool signed, string? name, object? given)
        {
            long low = signed ? -80 : 0, high = signed ? 79 : 159;
            if (tenths >= low && tenths <= high) return;
            long abs = Math.Abs(tenths);
            var encoded = (tenths < 0 ? "-" : "") + (abs / 10) + (abs % 10 != 0 ? "." + (abs % 10) : "");
            throw new InvalidOperationException(
                $"Field '{name}': {JsNumber(given)} encodes as {encoded}, outside "
                + $"{(signed ? "-8" : "0")} to {(signed ? "7.9" : "15.9")} (PS-168)");
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

        // --- PS-552 rounding and the PS-168 fit check ----------------------------------

        SchemaField? _givenField;
        object? _givenValue;

        /// <summary>The value the input gave for this field, before its arithmetic was
        /// inverted; <paramref name="fallback"/> where ReverseModifiers did not see it.</summary>
        object? Given(SchemaField field, object? fallback)
            => ReferenceEquals(_givenField, field) ? _givenValue : fallback;

        /// <summary>
        /// PS-552: the nearest integer to the binary64 value, a tie to even. Kept as a double
        /// so a value past the range of <c>long</c> still reaches the fit check intact.
        /// </summary>
        static double RoundToInteger(double value) => Math.Round(value, MidpointRounding.ToEven);

        /// <summary>The range a whole integer of <paramref name="bits"/> bits holds.</summary>
        static (double low, double high) IntegerBounds(int bits, bool signed)
            => signed
                ? (-Math.Pow(2, bits - 1), Math.Pow(2, bits - 1) - 1)
                : (0, Math.Pow(2, bits) - 1);

        /// <summary>
        /// PS-168, PS-552: the rounded integer fits the field's type, or the input is rejected
        /// naming the field and the value given, in the spec prototype's text. It was masked
        /// to the width, or reported as "N does not fit 1 unsigned bytes" with no field.
        /// </summary>
        static double CheckFits(double raw, double low, double high, string? name, object? given,
            string? bitRange = null)
        {
            // A bit range (PS-064, CR-2026-136) names its type after the range, as the spec
            // prototype does: "... outside 0 to 15 for u8[0:3] (PS-168)".
            if (raw < low || raw > high)
                throw new InvalidOperationException(
                    $"Field '{name}': {JsNumber(given)} encodes as {JsNumber(raw)}, outside "
                    + $"{JsNumber(low)} to {JsNumber(high)}"
                    + (bitRange != null ? $" for {bitRange}" : "") + " (PS-168)");
            return raw;
        }

        /// <summary>
        /// A bit range's type is its width (PS-168, PS-552), signed where the type starts with
        /// <c>s</c>. One place, so the reading of PS-168 for a bit range is easy to adjust.
        /// </summary>
        static long CheckBitRange(SchemaField member, double raw, int bitLen, object? given)
        {
            bool signed = (member.RawType ?? "").StartsWith("s");
            var (low, high) = IntegerBounds(bitLen, signed);
            return (long)CheckFits(raw, low, high, member.Name, given, member.RawType);
        }

        /// <summary>A number as JavaScript's String(number) writes it: no `.0` on an
        /// integral value, else the shortest round-tripping digits.</summary>
        internal static string JsNumber(object? value)
        {
            var inv = System.Globalization.CultureInfo.InvariantCulture;
            switch (value)
            {
                case null: return "null";
                case bool b: return b ? "true" : "false";
                case string text: return text;
            }
            var (ok, d) = Helpers.ToFloat64(value);
            if (!ok) return value.ToString() ?? "";
            if (d == Math.Floor(d) && Math.Abs(d) < 1e21)
                return Math.Abs(d) < 9e15
                    ? ((long)d).ToString(inv)
                    : ((decimal)d).ToString(inv);
            var digits = d.ToString("R", inv);
            int e = digits.IndexOf('E');
            if (e < 0) return digits;
            // JavaScript writes 1e-6 and above in fixed notation and the rest as 3.4e+38,
            // 1e-7; .NET writes 3.4E+38 and 1E-05.
            if (Math.Abs(d) >= 1e-6 && Math.Abs(d) < 1e21)
                return decimal.Parse(digits, System.Globalization.NumberStyles.Float, inv).ToString(inv);
            int exponent = int.Parse(digits.Substring(e + 1), inv);
            return digits.Substring(0, e) + "e" + (exponent < 0 ? "-" : "+") + Math.Abs(exponent);
        }

        // --- one field's bytes -------------------------------------------------------

        byte[] EncodeField(SchemaField field, object? value)
        {
            string endian = string.IsNullOrEmpty(field.Endian) ? _endian : field.Endian!;

            if (field.Type == FieldType.Bits || Helpers.ParseBitRange(field.RawType) != null)
            {
                // A lone bit range: the value in place, in its base's bytes, as the
                // reference writes it. Rounded and checked against the range's width
                // (PS-552, PS-168); it was truncated and masked to one byte.
                var (bitsOk, bitsValue) = Helpers.ToFloat64(value);
                if (!bitsOk)
                {
                    if (value is bool bit) bitsValue = bit ? 1 : 0;
                    else throw NotANumber(field, value);
                }
                var lone = Helpers.ParseBitRange(field.RawType);
                if (lone == null) return new[] { (byte)((long)RoundToInteger(bitsValue) & 0xFF) };
                int loneLen = lone.Value.end - lone.Value.start + 1;
                long loneRaw = CheckBitRange(field, RoundToInteger(bitsValue), loneLen,
                    Given(field, value));
                ulong loneMask = loneLen >= 64 ? ulong.MaxValue : (1UL << loneLen) - 1;
                return Helpers.EncodeUint(((ulong)loneRaw & loneMask) << lone.Value.start,
                    Math.Max(1, field.BitBaseBytes), endian);
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
                // The word-ordered spellings: least significant 16-bit unit first, each
                // unit big-endian, and `endian` plays no part (PS-271, PS-272). There was
                // no case for either, so both fell to the throw at the end of this method
                // and a schema carrying one could be decoded and not re-encoded
                // (CR-2026-028).
                //
                // Deliberately not passing `endian`, for the reason the decoder gives:
                // honouring it would make u32le16 with endian little a second spelling of
                // little-endian u32.
                case FieldType.F32LE16 or FieldType.U32BE16LE or FieldType.S32BE16LE or FieldType.F32BE16LE:
                {
                    var (ok, numeric) = Helpers.ToFloat64(value);
                    if (!ok) throw NotANumber(field, value);
                    if (field.Type is FieldType.U32BE16LE or FieldType.S32BE16LE)
                    {
                        // PS-552, PS-168: a 32-bit integer, rounded and checked.
                        var (low, high) = IntegerBounds(32, field.Type == FieldType.S32BE16LE);
                        numeric = CheckFits(RoundToInteger(numeric), low, high, field.Name,
                            Given(field, value));
                    }
                    return Wave4.WriteWordOrdered(field.Type, numeric);
                }
                case FieldType.UFlt16 or FieldType.SFlt16 or FieldType.SFlt24:
                {
                    var (ok, numeric) = Helpers.ToFloat64(value);
                    if (!ok) throw NotANumber(field, value);
                    return Helpers.EncodeUint(Wave4.EncodeMinifloat(field.Type, numeric),
                        Wave4.MinifloatSize(field.Type), endian);
                }
                // Word-ordered (PS-271, PS-272): least significant unit first, each unit
                // big-endian; rounded and checked as 32 bits (PS-552, PS-168).
                case FieldType.U32LE16 or FieldType.S32LE16:
                {
                    var (low, high) = IntegerBounds(32, field.Type == FieldType.S32LE16);
                    long raw = (long)CheckFits(RequireRounded(field, value), low, high,
                        field.Name, Given(field, value));                              // PS-168
                    long combined = raw < 0 ? raw + 0x100000000L : raw;
                    combined &= 0xFFFFFFFFL;
                    var wordOrdered = new List<byte>();
                    wordOrdered.AddRange(Helpers.EncodeUint((ulong)(combined & 0xFFFF), 2, "big"));
                    wordOrdered.AddRange(Helpers.EncodeUint((ulong)(combined >> 16), 2, "big"));
                    return wordOrdered.ToArray();
                }
                case FieldType.U8 or FieldType.U16 or FieldType.U24 or FieldType.U32
                        or FieldType.U64:
                {
                    // PS-552: rounded ties to even before any encoding, then checked against
                    // the type (PS-168).
                    double rounded = RequireRounded(field, value);
                    int width = Helpers.InferLengthFromType(field.Type);
                    var (low, high) = IntegerBounds(width * 8, false);
                    if (field.Encoding != null)
                    {
                        // The inverse of the named code, after the modifiers are reversed (PS-463).
                        ulong coded = Wave4.EncodeEncoding((long)rounded, field.Encoding, width, field.Name);
                        CheckFits(coded, low, high, field.Name, Given(field, value));
                        return Helpers.EncodeUint(coded, width, endian);
                    }
                    CheckFits(rounded, low, high, field.Name, Given(field, value));
                    return Helpers.EncodeUint(rounded >= 9223372036854775808.0
                        ? (ulong)rounded : (ulong)(long)rounded, width, endian);
                }
                case FieldType.S8 or FieldType.S16 or FieldType.S24 or FieldType.S32
                        or FieldType.S64:
                {
                    int size = Helpers.InferLengthFromType(field.Type);
                    var (low, high) = IntegerBounds(size * 8, true);
                    long raw = (long)CheckFits(RequireRounded(field, value), low, high,
                        field.Name, Given(field, value));                              // PS-168
                    return Helpers.EncodeSint(raw, size, endian);
                }
                case FieldType.BF16:
                {
                    var (ok, numeric) = Helpers.ToFloat64(value);
                    if (!ok) throw NotANumber(field, value);
                    return Helpers.EncodeUint(Bf16.Encode(numeric, field.Name), 2, endian);   // PS-559
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
                case FieldType.UDec or FieldType.SDec:
                {
                    // The inverse of PS-330, as tools/schema_interpreter.py writes it:
                    // floor the whole part so the tenths stay non-negative.
                    var (ok, numeric) = Helpers.ToFloat64(value);
                    if (!ok) throw NotANumber(field, value);
                    // PS-556 (CR-2026-147): n is the value times 10, rounded half to even
                    // on the binary64 product (PS-552); the upper nibble is n div 10 and
                    // the lower n mod 10, both floored. Rounding the fraction after
                    // splitting off the whole part wrote 3.45 as 0x35.
                    double product = Math.Round(numeric * 10, MidpointRounding.ToEven);
                    long n = (long)Math.Clamp(product, -9e18, 9e18);
                    CheckNibbleDecimal(n, field.Type == FieldType.SDec,
                        field.Name, Given(field, value));                                 // PS-168
                    long whole = (long)Math.Floor(n / 10.0);
                    return new[] { (byte)(((whole & 0x0F) << 4) | (n - whole * 10)) };
                }
                case FieldType.Skip:
                    return new byte[field.Length > 0 ? field.Length : 0];
                case FieldType.Bytes or FieldType.Hex or FieldType.Base64:
                    return Cr181.Encode(field, value);      // CR-2026-181: PS-081, PS-601, PS-602
                case FieldType.String or FieldType.Boolean when field.Value != null || IsTemplate(field):
                    // A literal or a template came from no bytes, so it writes none.
                    return Array.Empty<byte>();
                case FieldType.Ascii when field.Numeral != null && Helpers.ToFloat64(value).Item1:
                {
                    // PS-561: digits in the declared base, rounded as PS-552 requires.
                    int? fixedLen = field.Terminator == null && field.Length > 0 && field.LengthRef == null
                        ? field.Length : null;                                  // PS-593: no width
                    var text = Numeral.Encode(field.Name, Helpers.ToFloat64(value).Item2,
                        fixedLen, field.Numeral);
                    return Helpers.EncodeAscii(field.Name, text, fixedLen,
                        _result.Warnings, field.Nul, field.Terminator, !field.KeepTerminator);
                }
                case FieldType.Ascii or FieldType.String:
                    // PS-537. A fixed `length` pads and bounds the value; `remaining` or a
                    // reference has no fixed count, so the value supplies it.
                    return Helpers.EncodeAscii(field.Name, value?.ToString() ?? "",
                        field.Length > 0 && field.LengthRef == null ? field.Length : null,
                        _result.Warnings, field.Nul,
                        field.Type == FieldType.Ascii ? field.Terminator : null,
                        !field.KeepTerminator);
            }

            throw new InvalidOperationException($"Cannot encode type: {field.Type}");
        }

        byte[] EncodeEnum(SchemaField field, object? value, string endian)
        {
            var bases = field.Base ?? "u8";
            int size = bases switch
            {
                "u16" or "s16" => 2,
                "u24" or "s24" => 3,
                "u32" or "s32" => 4,
                _ => 1
            };
            bool signed = bases.StartsWith("s");
            double raw;
            if (value is string name)
            {
                // A name back to its code (PS-067). One several codes share names none of
                // them (PS-169); one with no code, the `default` label among them, cannot be
                // recovered (PS-068, PS-554).
                var codes = EnumCodes(field, name);
                if (codes.Count == 0)
                    throw new InvalidOperationException(
                        $"Field '{field.Name}': {LabelText(name)} is not a name of the enum (PS-067)");
                if (codes.Count > 1) throw SharedLabel(field.Name, name, codes);
                raw = codes[0];
            }
            else if (value is not bool && Helpers.ToFloat64(value) is (true, var numeric))
                raw = Math.Round(numeric, MidpointRounding.ToEven);                    // PS-552
            else
                throw new InvalidOperationException(
                    $"Field '{field.Name}': {LabelText(value)} is not a name or code of the enum (PS-067)");
            // PS-168, naming the enum: a code past its base was "300 does not fit 1
            // unsigned bytes", with no field.
            var (low, high) = IntegerBounds(size * 8, signed);
            CheckFits(raw, low, high, field.Name, Given(field, value));
            long code = (long)raw;
            return WriteUint(code < 0 ? code + (1L << (size * 8)) : code, size, endian);
        }

        /// <summary>Every code an enum gives <paramref name="name"/> as its name, ascending.</summary>
        static List<int> EnumCodes(SchemaField field, string name) =>
            field.Values == null ? new List<int>()
                : field.Values.Where(kv => kv.Value == name).Select(kv => kv.Key)
                    .OrderBy(k => k).ToList();

        /// <summary>
        /// Parse a bitfield_string back into its packed integer.
        /// </summary>
        /// <remarks>
        /// Each declared part reads one delimited segment, in the base the part declares.
        /// The hex parts are lowercase on output (PS-074); parsing is case-insensitive.
        /// </remarks>
        byte[] EncodeBitfieldString(SchemaField field, string value)
        {
            var parts = field.Parts
                .Where(p => p.Count >= 2 && Helpers.ToInt(p[0]).Item1 && Helpers.ToInt(p[1]).Item1)
                .ToList();
            // PS-605, PS-606 (CR-2026-183): the prefix required, the segment count checked, an
            // empty delimiter split by width.
            var segments = BitfieldParts.Segments(field.Name, field.Prefix, field.Delimiter, parts, value);

            ulong packed = 0;
            for (int i = 0; i < parts.Count; i++)
            {
                var part = parts[i];
                var (_, bitOff) = Helpers.ToInt(part[0]);
                var (_, bitLen) = Helpers.ToInt(part[1]);
                object? format = part.Count >= 3 ? part[2] : "decimal";
                string segment = segments[i];
                // PS-096, PS-431, PS-583: parsed by the part's format; a value the width
                // cannot hold is rejected, never masked.
                ulong raw = BitfieldParts.ParseSegment(segment, bitLen, format, field.Name);
                ulong mask = bitLen >= 64 ? ulong.MaxValue : (1UL << bitLen) - 1;
                packed |= (raw & mask) << bitOff;
            }
            int length = field.Length > 0 ? field.Length : 2;
            // PS-050a: the field's own byte order, as it is decoded; this wrote the schema's.
            return Helpers.EncodeUint(packed, length,
                string.IsNullOrEmpty(field.Endian) ? _endian : field.Endian!);
        }

        // --- primitives --------------------------------------------------------------

        /// <summary>The value as a number rounded ties to even (PS-552), kept a double so the
        /// fit check sees it whole.</summary>
        static double RequireRounded(SchemaField field, object? value)
        {
            var (ok, numeric) = Helpers.ToFloat64(value);
            if (!ok)
            {
                if (value is bool flag) return flag ? 1 : 0;
                throw NotANumber(field, value);
            }
            return RoundToInteger(numeric);
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
            if (f.Type == FieldType.Number || IsTemplate(f)) return null;
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

/// <summary>
/// The bytes a field list writes, laid out as a decoder reads them (CR-2026-136). The
/// reference's FrameBuffer in tools/schema_interpreter.py, ported.
/// </summary>
/// <remarks>
/// A bit range or a bool that does not <c>consume</c> leaves the bytes it filled pending:
/// a decoder peeks them (PS-059, PS-066), so the next field reads the same bytes, and its
/// own bits or bytes are ORed into them. Bytes still pending at the end are written too,
/// since a decoder reads them.
/// </remarks>
internal sealed class FrameBuffer
{
    readonly List<byte> _buf = new();
    readonly List<byte> _pending = new();
    // CR-2026-141 (PS-551): per position in this list's frame, the bits some field wrote,
    // and each bit range or bool placed, for the check where the frame closes.
    readonly List<byte> _covered = new();
    readonly List<Peek> _peeks = new();
    readonly List<ChecksumSlot> _checksums = new();          // PS-587, as FrameMeta
    // CR-2026-175 (PS-589): each non-consuming byte string given a value, checked where
    // the frame closes (FrameMeta.Check).
    readonly List<Echo> _echoes = new();

    /// <summary>PS-589: a non-consuming byte string writes nothing and is not required; a
    /// value given for it is recorded at the position a decoder is at - the start of any
    /// bytes still pending - with its length read from the input (null for
    /// <c>remaining</c>).</summary>
    public void AddEcho(SchemaField field, Dictionary<string, object?> data, string endian = "big")
    {
        if (string.IsNullOrEmpty(field.Name)
            || !data.TryGetValue(field.Name, out var given) || given == null) return;
        int? length;
        if (field.IsPeekedNumber) length = SchemaField.WholeNumberSize(field.Type);   // PS-604
        else if (field.LengthRef != null)
        {
            if (!data.TryGetValue(field.LengthRef, out var n)
                || Helpers.ToFloat64(n) is not (true, var d) || d < 0 || d != Math.Floor(d))
                throw new InvalidOperationException($"Missing required field: {field.LengthRef}");
            length = (int)d;
        }
        else if (field.Length < 0) length = null;
        else length = field.Length != 0 || field.LengthDeclared
            ? field.Length : Helpers.InferLengthFromType(field.Type);
        _echoes.Add(new Echo(_buf.Count, field, given, length, endian));
    }

    void Cover(int position, byte[] mask)
    {
        while (_covered.Count < position + mask.Length) _covered.Add(0);
        for (int i = 0; i < mask.Length; i++) _covered[position + i] |= mask[i];
    }

    /// <summary>The metadata of the frame <see cref="Frame"/> returns.</summary>
    public FrameMeta Meta()
    {
        int size = _buf.Count + _pending.Count;
        var covered = new byte[size];
        for (int i = 0; i < size && i < _covered.Count; i++) covered[i] = _covered[i];
        return new FrameMeta(covered, new List<Peek>(_peeks), new List<ChecksumSlot>(_checksums),
            new List<Echo>(_echoes));
    }

    public IReadOnlyList<byte> Pending => _pending;

    public int PendingCount => _pending.Count;

    /// <summary>Bytes written and pending, for PS-564's tails.</summary>
    public int Size => _buf.Count + _pending.Count;

    /// <summary>Write a field's bytes, ORed from the start with whatever is pending; the
    /// pending bytes they cover are then dropped. Where the bytes are a nested field list's
    /// and its last <paramref name="carried"/> are still pending there - peeked and consumed
    /// by nothing in it - they stay pending here, ORed with any older pending beyond them,
    /// so the field after the construct shares them, as a decoder reads it. The reference's
    /// FrameBuffer.extend and its Frame.pending.</summary>
    public void Extend(byte[] data, int carried = 0, FrameMeta? meta = null)
    {
        carried = Math.Min(Math.Max(carried, 0), data.Length);
        int offset = _buf.Count;
        int shared = Math.Min(_pending.Count, data.Length);
        for (int i = 0; i < shared; i++)
        {
            // PS-551 (CR-2026-141): a field would OR over a peek's different bits.
            if ((_pending[i] & ~data[i] & FrameMeta.CoveredAt(meta, i)) != 0)
            {
                var frame = new byte[_buf.Count + data.Length];
                _buf.CopyTo(frame, 0);
                data.CopyTo(frame, _buf.Count);
                throw new InvalidOperationException(FrameMeta.Disagreement(_peeks, frame));
            }
        }
        var mask = new byte[data.Length];
        for (int i = 0; i < data.Length; i++) mask[i] = FrameMeta.CoveredAt(meta, i);
        Cover(offset, mask);
        if (meta != null)
        {
            foreach (var peek in meta.Peeks) _peeks.Add(peek with { Position = peek.Position + offset });
            foreach (var slot in meta.Checksums) _checksums.Add(slot with { Position = slot.Position + offset });
            foreach (var echo in meta.Echoes) _echoes.Add(echo with { Position = echo.Position + offset });
        }
        var merged = new byte[data.Length];
        for (int i = 0; i < data.Length; i++)
            merged[i] = i < shared ? (byte)(data[i] | _pending[i]) : data[i];
        _pending.RemoveRange(0, shared);
        if (carried > 0)
        {
            int written = merged.Length - carried;
            for (int i = 0; i < written; i++) _buf.Add(merged[i]);
            var tail = new List<byte>();
            for (int i = written; i < merged.Length; i++) tail.Add(merged[i]);
            for (int i = 0; i < _pending.Count; i++)
            {
                if (i < tail.Count) tail[i] |= _pending[i];
                else tail.Add(_pending[i]);
            }
            _pending.Clear();
            _pending.AddRange(tail);
            return;
        }
        _buf.AddRange(merged);
    }

    /// <summary>OR <paramref name="bits"/> at <paramref name="start"/> into the pending base,
    /// then move <paramref name="consume"/> bytes to the output (PS-016).</summary>
    public void PlaceBits(int baseSize, bool little, int start, ulong bits, int consume,
        int width = 0, string? name = null, bool derived = false)
    {
        if (width > 0 && !string.IsNullOrEmpty(name))
        {
            // CR-2026-141 (PS-551): recorded for the check where the frame closes. A derived
            // range - absent, and not consuming - writes no bits: the fields that write them
            // fix its value. A nameless range is padding, and is not checked.
            _peeks.Add(new Peek(_buf.Count, baseSize, little, start, width, derived ? null : bits, name!));
            if (!derived) Cover(_buf.Count, FrameMeta.MaskBytes(baseSize, little, start, width));
        }
        while (_pending.Count < baseSize) _pending.Add(0);
        ulong shifted = start >= 64 ? 0 : bits << start;
        for (int i = 0; i < baseSize && i < 8; i++)
        {
            // Byte i of the base, in its byte order: big-endian puts the most significant
            // byte first.
            int significance = little ? i : baseSize - 1 - i;
            if (significance < 8)
                _pending[i] |= (byte)(shifted >> (8 * significance));
        }
        if (consume > 0)
        {
            while (_pending.Count < consume) _pending.Add(0);
            _buf.AddRange(_pending.GetRange(0, consume));
            _pending.RemoveRange(0, consume);
        }
    }

    public byte[] Frame()
    {
        var all = new byte[_buf.Count + _pending.Count];
        _buf.CopyTo(all, 0);
        _pending.CopyTo(all, _buf.Count);
        return all;
    }
}

/// <summary>A bit range or bool placed in a frame (CR-2026-141): its base's position and
/// size, byte order, low bit and width, the unsigned bits given (null where derived), and
/// its name.</summary>
internal sealed record Peek(int Position, int Size, bool Little, int Start, int Width,
    ulong? Raw, string Name);

/// <summary>A non-consuming byte string given a value (CR-2026-175, PS-589): where a decoder
/// reads it, its field, the value given, and its length (null for <c>remaining</c>, the end
/// of the frame it is checked in).</summary>
internal sealed record Echo(int Position, SchemaField Field, object? Given, int? Length,
    string Endian = "big");

/// <summary>
/// A frame's PS-551 metadata (CR-2026-141): per byte, the bits some field wrote, and each
/// bit range or bool placed. The reference's Frame.covered and Frame.peeks; bytes a whole
/// field wrote carry none, and are written in full.
/// </summary>
internal sealed class FrameMeta
{
    public byte[] Covered { get; }
    public List<Peek> Peeks { get; }
    /// <summary>PS-587 (CR-2026-174): each checksum written as a placeholder, computed
    /// where the whole frame is known (<see cref="Checksum.Fill"/>).</summary>
    public List<ChecksumSlot> Checksums { get; }
    public List<Echo> Echoes { get; }

    public FrameMeta(byte[] covered, List<Peek> peeks, List<ChecksumSlot>? checksums = null,
        List<Echo>? echoes = null)
    {
        Covered = covered;
        Peeks = peeks;
        Checksums = checksums ?? new List<ChecksumSlot>();
        Echoes = echoes ?? new List<Echo>();
    }

    public static byte CoveredAt(FrameMeta? meta, int i)
        => meta == null ? (byte)0xFF : i < meta.Covered.Length ? meta.Covered[i] : (byte)0;

    /// <summary>`prefix` written whole, then a body with this metadata.</summary>
    public static FrameMeta? Prefixed(int prefix, FrameMeta? body, int bodyLength)
    {
        if (body == null) return null;
        var covered = new byte[prefix + bodyLength];
        for (int i = 0; i < prefix; i++) covered[i] = 0xFF;
        for (int i = 0; i < bodyLength; i++) covered[prefix + i] = CoveredAt(body, i);
        return new FrameMeta(covered,
            body.Peeks.Select(p => p with { Position = p.Position + prefix }).ToList(),
            body.Checksums.Select(c => c with { Position = c.Position + prefix }).ToList(),
            body.Echoes.Select(e => e with { Position = e.Position + prefix }).ToList());
    }

    static System.Numerics.BigInteger Read(byte[] frame, int position, int size, bool little)
    {
        System.Numerics.BigInteger v = 0;
        for (int i = 0; i < size; i++)
        {
            int at = little ? position + size - 1 - i : position + i;
            v = (v << 8) | (at < frame.Length ? frame[at] : 0);
        }
        return v;
    }

    static System.Numerics.BigInteger Mask(int start, int width)
        => ((System.Numerics.BigInteger.One << width) - 1) << start;

    public static byte[] MaskBytes(int size, bool little, int start, int width)
    {
        var m = Mask(start, width);
        var bytes = new byte[size];
        for (int i = 0; i < size; i++)
        {
            int significance = little ? i : size - 1 - i;
            bytes[i] = (byte)((m >> (8 * significance)) & 0xFF);
        }
        return bytes;
    }

    /// <summary>The reference's peek_disagreement.</summary>
    public static string Disagreement(IEnumerable<Peek> peeks, byte[] frame)
    {
        foreach (var p in peeks)
        {
            if (p.Raw == null || p.Position + p.Size > frame.Length) continue;
            var found = Read(frame, p.Position, p.Size, p.Little) & Mask(p.Start, p.Width);
            if (found != (new System.Numerics.BigInteger(p.Raw.Value) << p.Start))
                return $"Field '{p.Name}' is given {p.Raw.Value}, but the fields that share its "
                    + $"bits write {found >> p.Start} (PS-551)";
        }
        return "A field writes bits a peek before it set otherwise (PS-551)";
    }

    /// <summary>
    /// The reference's check_peeks, where a frame closes: every bit range and bool reads
    /// back what it was given, and a derived one has every bit written by another field,
    /// or it is missing.
    /// </summary>
    public static void Check(byte[] frame, FrameMeta? meta)
    {
        if (meta == null) return;
        CheckPeeks(frame, meta);
        CheckEchoes(frame, meta);
    }

    /// <summary>
    /// PS-589 (CR-2026-175), the reference's check_echoes: a value given for a
    /// non-consuming byte string equals what the field decodes from the bytes the other
    /// fields wrote over its span - its format and separator, or PS-534's trailing 0x00
    /// removal, applied first - or the input is rejected, naming the field.
    /// </summary>
    static void CheckEchoes(byte[] frame, FrameMeta meta)
    {
        foreach (var e in meta.Echoes)
        {
            int length = e.Length ?? Math.Max(0, frame.Length - e.Position);
            string written;
            if (e.Position + length > frame.Length)
                written = "too few bytes";
            else
            {
                object? found;
                try
                {
                    found = e.Field.IsPeekedNumber
                        ? SchemaDecoder.PeekedNumberValue(e.Field, frame.AsSpan(e.Position, length).ToArray(), e.Endian)
                        : EchoValue(e.Field, frame.AsSpan(e.Position, length));
                }
                catch (Exception) { found = null; }
                if (found != null && EchoEquals(found, e.Given)) continue;
                written = found != null ? EchoText(found) : "too few bytes";
            }
            throw new FieldErrorException($"Field '{e.Field.Name}' is given {EchoText(e.Given)}, "
                + $"but the other fields write {written} over its span "
                + (e.Field.IsPeekedNumber ? "(PS-604)" : "(PS-589)"));
        }
    }

    /// <summary>What a non-consuming byte string decodes from <paramref name="span"/>.</summary>
    static object EchoValue(SchemaField field, ReadOnlySpan<byte> span)
    {
        switch (field.Type)
        {
            case FieldType.Bytes:
                return Helpers.FormatBytes(span.ToArray(), field.Format, field.Separator);
            case FieldType.Hex:
                return Convert.ToHexString(span).ToLowerInvariant();
            default:
            {
                object text = Helpers.DecodeAscii(field.Name, span, new List<string>());
                if (field.Numeral != null)
                    text = Numeral.Decode(field.Name, (string)text, field.Numeral);
                return text;
            }
        }
    }

    static bool EchoEquals(object found, object? given)
    {
        if (found is string fs) return given is string gs && string.Equals(fs, gs, StringComparison.Ordinal);
        if (found is System.Collections.IList fl)
        {
            if (given is not System.Collections.IList gl || gl.Count != fl.Count) return false;
            for (int i = 0; i < fl.Count; i++)
                if (fl[i] == null || !EchoEquals(fl[i]!, gl[i])) return false;
            return true;
        }
        if (found is bool) return Equals(found, given);
        var (fok, fd) = Helpers.ToFloat64(found);
        var (gok, gd) = Helpers.ToFloat64(given);
        return fok && gok && given is not bool && given is not string && fd == gd;
    }

    /// <summary>A value as a PS-589 message writes it: text in single quotes, a number as
    /// JavaScript's String(number), a list of octets as <c>'[1, 2]'</c>.</summary>
    static string EchoText(object? value)
    {
        switch (value)
        {
            case System.Collections.IList list:
                var parts = new List<string>();
                foreach (var item in list) parts.Add(EchoText(item).Trim('\''));
                return "'[" + string.Join(", ", parts) + "]'";
            case bool b:
                return b ? "true" : "false";
            case string s:
                return "'" + s + "'";
            case null:
                return "'None'";
        }
        if (Helpers.ToFloat64(value).Item1) return SchemaEncoder.JsNumber(value);
        return "'" + value + "'";
    }

    static void CheckPeeks(byte[] frame, FrameMeta meta)
    {
        foreach (var p in meta.Peeks)
        {
            var mask = Mask(p.Start, p.Width);
            if (p.Raw == null)
            {
                System.Numerics.BigInteger have = 0;
                for (int i = 0; i < p.Size; i++)
                {
                    int at = p.Little ? p.Position + p.Size - 1 - i : p.Position + i;
                    have = (have << 8) | (at < meta.Covered.Length ? meta.Covered[at] : 0);
                }
                if ((have & mask) != mask)
                    throw new InvalidOperationException($"Missing required field: {p.Name}");
                continue;
            }
            var found = Read(frame, p.Position, p.Size, p.Little) & mask;
            if (found != (new System.Numerics.BigInteger(p.Raw.Value) << p.Start))
            {
                var padded = frame;
                if (p.Position + p.Size > frame.Length)
                {
                    padded = new byte[p.Position + p.Size];
                    frame.CopyTo(padded, 0);
                }
                throw new InvalidOperationException(Disagreement(new[] { p }, padded));
            }
        }
    }
}
