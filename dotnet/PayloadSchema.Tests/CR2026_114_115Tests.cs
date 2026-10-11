// Copyright (c) 2024-2026 Multitech Systems, Inc.
// SPDX-License-Identifier: MIT

using Xunit;

namespace PayloadSchema.Tests;

/// <summary>
/// CR-2026-115 (PS-535: a read or skip past the end is an error naming the field) and
/// CR-2026-114 (PS-534, PS-537, PS-538: an ascii value's padding and its bytes above 0x7F),
/// on la-payload-schema main at c932961.
///
/// The fixture _language-conformance/ascii-latin1.yaml holds every implementation to the
/// decodes and round trips. What a fixture cannot state - a rejection, its exact text - is
/// here, mirroring tests/test_cr_2026_114_115.py. The messages are the spec prototype's,
/// compared as exact strings so a cross-language diff compares equal.
/// </summary>
public class CR2026_114_115Tests
{
    static PayloadSchemaDefinition Load(string fields)
        => SchemaParser.Parse("name: probe\nendian: big\nfields:\n" + fields);

    static string ShortRead(string fields, string hex)
        => Assert.ThrowsAny<InvalidOperationException>(
            () => SchemaDecoder.Decode(Load(fields), Convert.FromHexString(hex))).Message;

    static string Need(string label, int n, int offset, int remaining)
        => $"Field {label}: Buffer underflow: need {n} bytes at offset {offset}, "
           + $"but only {remaining} remaining (PS-535)";

    static string Peek(string label, int offset)
        => $"Field {label}: Buffer underflow at peek offset {offset} (PS-535)";

    public static IEnumerable<object[]> ShortReads => new[]
    {
        new object[] { "- {name: n, type: u16}\n", "01", Need("'n'", 2, 0, 1) },
        new object[] { "- {type: skip, length: 3}\n", "", Need("an unnamed skip", 3, 0, 0) },
        new object[] { "- {name: pad, type: skip, length: 3}\n", "00", Need("'pad'", 3, 0, 1) },
        new object[] { "- {name: s, type: ascii, length: 4}\n", "4142", Need("'s'", 4, 0, 2) },
        new object[] { "- {name: h, type: hex, length: 4}\n", "41", Need("'h'", 4, 0, 1) },
        new object[] { "- {name: b, type: bytes, length: 4}\n", "41", Need("'b'", 4, 0, 1) },
        new object[] { "- {name: f, type: f32}\n", "0102", Need("'f'", 4, 0, 2) },
        new object[] { "- {name: w, type: u32le16}\n", "0102", Need("'w'", 4, 0, 2) },
        new object[] { "- {name: ud, type: udec}\n", "", Need("'ud'", 1, 0, 0) },
        new object[] { "- {name: _k, type: u16}\n", "01", Need("'_k'", 2, 0, 1) },
        new object[] { "- {name: e, type: enum, base: u16, values: {1: one}}\n", "00",
            Need("'e'", 2, 0, 1) },
        new object[] { "- {name: n, type: u8}\n- {name: s, type: ascii, length: $n}\n", "0541",
            Need("'s'", 5, 1, 1) },
        // The innermost field is named; the enclosing construct adds nothing.
        new object[] { "- {name: o, type: object, fields: [{name: x, type: u16}]}\n", "01",
            Need("'x'", 2, 0, 1) },
        new object[] { "- {name: r, type: repeat, count: 2, fields: [{name: v, type: u16}]}\n",
            "000102", Need("'v'", 2, 2, 1) },
        new object[] { "- {name: r, type: repeat, count: 2, element: {name: v, type: u16}}\n",
            "000102", Need("'v'", 2, 2, 1) },
        new object[] { "- {name: r, type: repeat, count: 2, element: {type: u16}}\n",
            "000102", Need("'element'", 2, 2, 1) },
        new object[] { "- tlv: {tag_size: 1, cases: {1: [{name: t, type: u16}]}}\n", "0101",
            Need("'t'", 2, 1, 1) },
        // A reserve is the end of the payload for the reads inside it (PS-350, PS-471).
        new object[] { "- tlv: {tag_size: 1, reserve: 1, cases: {1: [{name: t, type: u16}]}}\n",
            "010102", Need("'t'", 2, 1, 1) },
        new object[] { "- {name: k, type: u8, var: k}\n"
            + "- match: {field: $k, cases: {1: [{name: m, type: u16}]}}\n", "0101",
            Need("'m'", 2, 1, 1) },
        new object[] { "- {name: fl, type: u8}\n"
            + "- flagged: {field: fl, groups: [{bit: 0, fields: [{name: g, type: u16}]}]}\n",
            "0101", Need("'g'", 2, 1, 1) },
        new object[] { "- match: {length: 2, name: kind, cases: {1: [{name: m, type: u8}]}}\n",
            "00", Need("'kind'", 2, 0, 1) },
        new object[] { "- match: {length: 2, cases: {1: [{name: m, type: u8}]}}\n", "00",
            Need("an unnamed match", 2, 0, 1) },
        // A bit range and a bool read without consuming: the prototype's peek form.
        new object[] { "- {name: b, type: 'u16[0:3]', consume: 2}\n", "01", Peek("'b'", 0) },
        new object[] { "- {name: bo, type: bool, bit: 1}\n", "", Peek("'bo'", 0) },
        // The base fits; the `consume` past it is a read like any other.
        new object[] { "- {name: b, type: 'u8[0:3]', consume: 2}\n", "01", Need("'b'", 2, 0, 1) },
        new object[] { "- {name: bo, type: bool, consume: 2}\n", "01", Need("'bo'", 2, 0, 1) },
        new object[] { "- byte_group: {size: 2, fields: [{name: a, type: 'u16[0:3]'}, "
            + "{name: c, type: 'u16[4:7]'}]}\n", "01", Peek("byte_group of 'a', 'c'", 0) },
        // CR-2026-131: the group is one read, named by every member in order, internal
        // ones included, whichever member's base would have fitted.
        new object[] { "- byte_group: {size: 2, fields: [{name: a, type: 'u8[0:3]'}, "
            + "{name: _r, type: 'u8[4:7]'}]}\n", "01", Peek("byte_group of 'a', '_r'", 0) },
        new object[] { "- byte_group: {size: 2, fields: [{type: 'u8[0:3]'}]}\n", "01",
            Peek("an unnamed byte_group", 0) },
    };

    [Theory]
    [MemberData(nameof(ShortReads))]
    public void AShortReadNamesTheField(string fields, string hex, string expected)
        => Assert.Equal(expected, ShortRead(fields, hex));

    [Fact]
    public void RemainingAtTheEndIsNotAnError()
    {
        var result = SchemaDecoder.Decode(Load(
            "- {name: a, type: u8}\n- {name: rest, type: hex, length: remaining}\n"),
            new byte[] { 1 });
        Assert.Equal("", result["rest"]);
    }

    const string Label = "- {name: label, type: ascii, length: 6}\n";

    static List<string> Warnings(Dictionary<string, object?> result)
        => result.TryGetValue("_warnings", out var w) && w is List<string> list ? list : new();

    [Fact]
    public void AHighByteIsItsLatin1CharacterWithAWarning()
    {
        var result = SchemaDecoder.Decode(Load(Label), Convert.FromHexString("41E900000000"));
        Assert.Equal("Aé", result["label"]);
        Assert.Contains("Field 'label': a byte above 0x7F in an ascii value (PS-073, PS-538)",
            Warnings(result));
    }

    [Fact]
    public void OnlyTrailingNulsAreDropped()
    {
        var result = SchemaDecoder.Decode(Load(Label), Convert.FromHexString("414200430000"));
        Assert.Equal("AB\0C", result["label"]);
        Assert.Empty(Warnings(result));
        Assert.Equal("", SchemaDecoder.Decode(Load(Label), new byte[6])["label"]);
        Assert.Equal(" A ", SchemaDecoder.Decode(Load(Label),
            Convert.FromHexString("204120000000"))["label"]);
    }

    static EncodeResult Encode(string value, int length = 4)
        => SchemaEncoder.Encode(Load($"- {{name: s, type: ascii, length: {length}}}\n"),
            new Dictionary<string, object?> { ["s"] = value });

    [Fact]
    public void AShortValueIsPadded()
    {
        var result = Encode("AB");
        Assert.True(result.Success);
        Assert.Equal("41420000", Convert.ToHexString(result.Payload).ToLowerInvariant());
        Assert.Empty(result.Warnings);
    }

    [Fact]
    public void ALongValueIsRejected()
        => Assert.Equal(
            new[] { "Field 's': an ascii value of 6 bytes is longer than its length of 4 (PS-537)" },
            Encode("ABCDEF").Errors);

    [Fact]
    public void ACharacterWithNoByteIsRejected()
        => Assert.Equal(
            new[] { "Field 's': an ascii value holds a character above U+00FF, which has no byte (PS-537)" },
            Encode("€").Errors);

    [Fact]
    public void ALatin1CharacterIsItsByteWithAWarning()
    {
        var result = Encode("ABCé");
        Assert.True(result.Success);
        Assert.Equal("414243e9", Convert.ToHexString(result.Payload).ToLowerInvariant());
        Assert.Equal(new[] { "Field 's': a character above U+007F in an ascii value (PS-537)" },
            result.Warnings);
    }

    [Fact]
    public void TheRoundTripIsLossless()
    {
        var schema = Load(Label);
        foreach (var hex in new[] { "41E900000000", "80A0FF410000", "414200430000" })
        {
            var decoded = SchemaDecoder.Decode(schema, Convert.FromHexString(hex));
            decoded.Remove("_warnings");
            var encoded = SchemaEncoder.Encode(schema, decoded);
            Assert.True(encoded.Success);
            Assert.Equal(hex, Convert.ToHexString(encoded.Payload));
        }
    }
}
