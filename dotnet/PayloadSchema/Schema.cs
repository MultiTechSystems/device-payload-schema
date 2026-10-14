// Copyright (c) 2024-2026 Multitech Systems, Inc.
// SPDX-License-Identifier: MIT

using YamlDotNet.RepresentationModel;

namespace PayloadSchema;

public enum FieldType
{
    Unknown,
    U8, U16, U24, U32, U64,
    // Two 16-bit big-endian units, least significant unit first (PS-271); not
    // expressible as U32 under either endianness.
    U32LE16, S32LE16,
    // The word-ordered float (PS-362) and the fourth ordering (PS-363).
    F32LE16, U32BE16LE, S32BE16LE, F32BE16LE,
    // The MCCI minifloats (PS-417 to PS-419).
    UFlt16, SFlt16, SFlt24,
    S8, S16, S24, S32, S64,
    F16, F32, F64,
    /// <summary>The upper half of a binary32 (PS-558, CR-2026-149).</summary>
    BF16,
    // Nibble-decimal (PS-329)
    UDec, SDec,
    Base64,
    // A computed field reporting an integer (PS-283). Parsed to Number with
    // SchemaField.IntegerResult set, so it never reaches the decoder as itself.
    Integer,
    Bool,
    Bits,
    Ascii,
    Hex,
    Bytes,
    Skip,
    String,
    // A boolean literal (PS-519): reads no bytes; not an alias of Bool (PS-065).
    Boolean,
    Number,
    Object,
    Match,
    TLV,
    Repeat,
    Enum,
    BitfieldString,
}

public class TransformStage
{
    public double? Add { get; set; }
    public double? Mult { get; set; }
    public double? Div { get; set; }
    /// <summary>Named operation form, e.g. {op: round, decimals: 2}.</summary>
    public string? Op { get; set; }
    public int Decimals { get; set; }
    /// <summary>
    /// Unary maths stages. Sqrt, Abs, Log10 and Log are flags; Pow carries its
    /// exponent. Nullable so an absent key stays distinguishable from pow: 0.
    /// </summary>
    public bool Sqrt { get; set; }
    public bool Abs { get; set; }
    public bool Log10 { get; set; }
    public bool Log { get; set; }
    public double? Pow { get; set; }
    /// <summary>`even` (the default), `away` from zero or `up` toward +infinity, for {op: round} (PS-390).</summary>
    public string? Ties { get; set; }
    /// <summary>Bound stages (PS-115). C# had none and passed the value through.</summary>
    public double? Floor { get; set; }
    public double? Ceiling { get; set; }
}

public class MatchCase
{
    public object? CaseValue { get; set; }
    public bool IsDefault { get; set; }
    public List<SchemaField> Fields { get; set; } = new();
    /// <summary>The key as written, for the case the decode record names (PS-572).</summary>
    internal YamlNode? KeyNode { get; set; }
}

public class ComputeDef
{
    public string Op { get; set; } = "";
    public string A { get; set; } = "";
    public string B { get; set; } = "";
}

public class GuardCondition
{
    public string Field { get; set; } = "";
    public double? Gt { get; set; }
    public double? Gte { get; set; }
    public double? Lt { get; set; }
    public double? Lte { get; set; }
    public double? Eq { get; set; }
    public double? Ne { get; set; }
    /// <summary>`in` / `not_in`: the value equals one of these, or none (PS-543).</summary>
    public double[]? In { get; set; }
    public double[]? NotIn { get; set; }
}

public class GuardDef
{
    public List<GuardCondition> When { get; set; } = new();
    /// <summary>
    /// The value when a condition fails. Without `else` the field is omitted (PS-400):
    /// NaN, which ApplyPostRead turns into an omission. It defaulted to 0, so a guard
    /// with no else reported a plausible 0 where it meant there is no reading.
    /// </summary>
    public double ElseValue { get; set; } = double.NaN;
}

public class FlaggedGroup
{
    public int Bit { get; set; }
    public List<SchemaField> Fields { get; set; } = new();
}

public class FlaggedDef
{
    /// <summary>The `flagged:` mapping, located by the decode record (PS-572).</summary>
    internal YamlNode? Anchor { get; set; }
    public string Field { get; set; } = "";
    public List<FlaggedGroup> Groups { get; set; } = new();
}

public class SchemaField
{
    public string Name { get; set; } = "";
    public FieldType Type { get; set; }
    /// <summary>An sN[start:end] range: sign-extended from the range's width (PS-352, PS-353).</summary>
    public bool SignedBits { get; set; }
    /// <summary>A byte_group's own byte order (PS-364), null for the context's.</summary>
    public string? GroupEndian { get; set; }
    /// <summary>A named code on an unsigned integer (PS-422).</summary>
    public string? Encoding { get; set; }
    /// <summary>`decimal` or `hex` on an ascii field reported as an integer (PS-560).</summary>
    public string? Numeral { get; set; }
    /// <summary>A computed field written `type: integer` (PS-283); its Type is Number.</summary>
    public bool IntegerResult { get; set; }
    public string RawType { get; set; } = "";
    public int Length { get; set; }
    /// <summary>
    /// Whether <c>length</c> was written as a number, so that <c>length: 0</c> - a field
    /// of no bytes (PS-392) - is told apart from no length at all, which takes the type's
    /// width.
    /// </summary>
    public bool LengthDeclared { get; set; }
    /// <summary>
    /// `length` naming a preceding field, with or without `$`, whose decoded value is the
    /// count (PS-464, PS-465). Stored without the `$`; Length is then 0.
    /// </summary>
    public string? LengthRef { get; set; }
    /// <summary>Decoded where its bytes remain, absent where none do (PS-402 to PS-405).</summary>
    public bool Optional { get; set; }
    /// <summary>Raw integers that mean "no reading": the field is absent (PS-427).</summary>
    public List<long>? Sentinel { get; set; }
    /// <summary>`out_of_range: omit`: a value outside valid_range is absent (PS-428).</summary>
    public bool OutOfRangeOmit { get; set; }
    /// <summary>A match on the bytes left in the region (PS-414): nothing is read.</summary>
    public bool MatchRemaining { get; set; }
    public int ByteOffset { get; set; }
    public int BitOffset { get; set; }
    public int BitCount { get; set; }
    /// <summary>
    /// Bytes the bit range is taken from. `u24[4:23]` means bits 4-23 of a 24-bit
    /// big-endian value, so all three bytes are read before masking. Defaults to 1,
    /// which is what every `u8[lo:hi]` range needs.
    /// </summary>
    public int BitBaseBytes { get; set; } = 1;
    public string? Endian { get; set; }

    // Modifiers
    public double? Add { get; set; }
    public double? Mult { get; set; }
    public double? Div { get; set; }
    public List<TransformStage> Transform { get; set; } = new();
    /// <summary>
    /// The lookup's labels, each a string, a number or a boolean (PS-106). Every label was
    /// read as text, so a boolean label was reported as the string "true" (CR-2026-104).
    /// </summary>
    public Dictionary<int, object>? Lookup { get; set; }
    /// <summary>True when `lookup` was written as a sequence, indexed from zero (PS-104).</summary>
    public bool LookupIsSequence { get; set; }
    /// <summary>Fallback for a mapping lookup with no entry for the value (PS-269), a scalar like any label.</summary>
    public object? LookupDefault { get; set; }
    /// <summary>Output key template resolved against earlier fields (PS-265).</summary>
    public string? NameFrom { get; set; }

    // Variable
    public string? Var { get; set; }
    public object? Value { get; set; }

    /// <summary>
    /// The <c>default</c> of an Option B match: the string <c>"error"</c> or
    /// <c>"skip"</c>, or a <c>List&lt;SchemaField&gt;</c> decoded when no case matches
    /// (CR-2026-020). Distinct from <see cref="MatchCase.IsDefault"/>, which marks a case
    /// keyed <c>default</c>.
    /// </summary>
    public object? MatchDefault { get; set; }

    // Nested / conditional
    public List<SchemaField> Fields { get; set; } = new();
    public string? On { get; set; }
    public List<MatchCase> Cases { get; set; } = new();

    // Repeat
    public object? Count { get; set; }
    public object? ByteLength { get; set; }
    public string? Until { get; set; }
    public int Max { get; set; }
    public int Min { get; set; }
    /// <summary>The element's zero-based iteration number is bound under this name (PS-366).</summary>
    public string? Index { get; set; }
    /// <summary>Bound to the number of elements reported, after the repeat (PS-367).</summary>
    public string? CountAs { get; set; }
    /// <summary>
    /// For a repeat declaring `element` (PS-528): the name its one field is decoded under,
    /// its own or "element". <see cref="Fields"/> then holds that one field, and the repeat
    /// reports each element's value rather than a record. Null for a `fields` repeat.
    /// </summary>
    public string? ElementName { get; set; }
    /// <summary>
    /// The sibling repeats a `$name[i]` in this repeat's identity, or its element's unit or
    /// ipso.instance, names (PS-546); each must report as many values as this repeat.
    /// </summary>
    public List<string> SiblingNames { get; set; } = new();
    /// <summary>The number <see cref="Index"/> counts from (PS-530), 0 by default.</summary>
    public int IndexBase { get; set; }
    /// <summary>
    /// A `records` entry (PS-541): field lists, each yielding one record that the enclosing
    /// repeat reports in place of its element. Null where the entry is not one.
    /// </summary>
    public List<List<SchemaField>>? Records { get; set; }
    /// <summary>An element is reported only where this holds (PS-386).</summary>
    public GuardCondition? PresentIf { get; set; }
    /// <summary>
    /// Bytes at the end of the payload an until-end repeat (PS-350) or a tlv (PS-471)
    /// must not read.
    /// </summary>
    public int Reserve { get; set; }
    /// <summary>Fields decoded from the reserved bytes before the first element (PS-383).</summary>
    public List<SchemaField>? Trailer { get; set; }
    /// <summary>A repeat's identity: its index or a $ reference to an element field (PS-372).</summary>
    public string? Identity { get; set; }
    /// <summary>
    /// A computed element field's value before the first element (PS-378): a double, or
    /// a "$name" naming a field decoded before the repeat.
    /// </summary>
    public object? Carry { get; set; }

    // Bytes format
    public string? Format { get; set; }
    public string? Separator { get; set; }

    // Bool
    public int Bit { get; set; }
    public int Consume { get; set; }
    /// <summary>Whether <c>consume</c> was written, so <c>consume: 0</c> differs from no
    /// <c>consume</c> at all (CR-2026-175).</summary>
    public bool ConsumeDeclared { get; set; }

    /// <summary>A <c>bytes</c>, <c>hex</c> or <c>ascii</c> field declaring <c>consume: 0</c>
    /// (PS-588): it reports its span and leaves the read position, and an encoder writes
    /// nothing for it (PS-589).</summary>
    public bool IsEcho => ConsumeDeclared && Consume == 0
        && (Type == FieldType.Bytes || Type == FieldType.Hex || Type == FieldType.Ascii);

    // Enum
    public string? Base { get; set; }
    public Dictionary<int, string>? Values { get; set; }
    /// <summary>Value an unmapped enum reports (PS-068). Distinct from
    /// LookupDefault, which serves the `lookup` construct.</summary>
    public string? EnumDefault { get; set; }

    // Byte group
    public List<SchemaField> ByteGroup { get; set; } = new();
    public int Size { get; set; }

    // $ref
    public string? Ref2 { get; set; }

    // TLV
    public int TagSize { get; set; }
    public int LengthSize { get; set; }
    public List<SchemaField> TagFields { get; set; } = new();
    public object? TagKey { get; set; }
    public bool? Merge { get; set; }
    public string? UnknownMode { get; set; }
    public Dictionary<string, List<SchemaField>>? TLVCases { get; set; }
    /// <summary>How each name the tlv reports combines when more than one entry reports
    /// it (PS-157, PS-525): collect, merge or last. A name not listed collects.</summary>
    public Dictionary<string, string> TLVRepeatModes { get; set; } = new();
    /// <summary>The members of each merged object that declare `on_repeat: last`, the
    /// only members two entries may both report (PS-525).</summary>
    public Dictionary<string, HashSet<string>> TLVLastMembers { get; set; } = new();

    // Bitfield string
    public List<List<object>> Parts { get; set; } = new();
    public string? Delimiter { get; set; }
    public string? Prefix { get; set; }


    // Semantic
    public double[]? ValidRange { get; set; }
    public double? Resolution { get; set; }
    public string? UNECE { get; set; }
    public string? Unit { get; set; }
    public IpsoDef? Ipso { get; set; }
    public SenmlDef? Senml { get; set; }
    public string? SemanticId { get; set; }

    // Computed fields
    public string? Ref { get; set; }
    public double[]? Polynomial { get; set; }
    public ComputeDef? Compute { get; set; }
    public GuardDef? Guard { get; set; }

    // Inline constructs
    public FlaggedDef? Flagged { get; set; }
    public SchemaField? TLVInline { get; set; }
    public SchemaField? MatchInline { get; set; }

    /// <summary>
    /// The mapping this field was parsed from, after `$ref` expansion. `_meta` describes a
    /// field as declared (PS-493, PS-178), which the typed properties above no longer say.
    /// </summary>
    internal YamlMappingNode? Node { get; set; }
    /// <summary>
    /// For the synthetic field a `match:` or `tlv:` block parses to, that block's mapping;
    /// for a field holding `records`, the records list. The node the decode record
    /// locates (PS-572, CR-2026-162).
    /// </summary>
    internal YamlNode? Anchor { get; set; }
}

public class PortDef
{
    public string? Direction { get; set; }
    public string? Description { get; set; }
    public List<SchemaField> Fields { get; set; } = new();
}

public class DefinitionDef
{
    public List<SchemaField> Fields { get; set; } = new();
}

public class IpsoDef
{
    public int Object { get; set; }
    public int Instance { get; set; }
    public int Resource { get; set; } = 5700;
}

public class SenmlDef
{
    public string? Name { get; set; }
    public string? Unit { get; set; }
}

public class PayloadSchemaDefinition
{
    public string Name { get; set; } = "";
    public int Version { get; set; }
    public string? Description { get; set; }
    public string Endian { get; set; } = "big";
    /// <summary>Applies to the whole schema where it has no Ports (PS-291).</summary>
    public string? Direction { get; set; }
    public List<SchemaField> Fields { get; set; } = new();
    public Dictionary<string, PortDef>? Ports { get; set; }
    public Dictionary<string, DefinitionDef>? Definitions { get; set; }
    /// <summary>Names declared only inside some repeat's elements (PS-368).</summary>
    internal HashSet<string> RepeatOnlyNames { get; set; } = new();
    /// <summary>The document after `$ref` expansion, for `_meta` (PS-175).</summary>
    internal YamlMappingNode? Root { get; set; }
}
