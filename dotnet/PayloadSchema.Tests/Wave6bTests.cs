using Xunit;

namespace PayloadSchema.Tests;

/// <summary>
/// 0.5.2 wave 6b: field-level rules. CR-2026-059 (optional), -062 (match on the bytes
/// remaining), -065 (sentinel, out_of_range: omit), -067 (internal fields) and -056 (a
/// length naming a field). The _language-conformance fixtures hold the decodes; these hold
/// what a vector cannot express - rejections, errors and encoding. Mirrors
/// tests/test_wave6b_field_rules.py.
/// </summary>
public class Wave6bTests
{
    /// <summary>A schema of the given fields, written as JSON.</summary>
    static string Fields(params string[] fields)
        => "{\"name\": \"probe\", \"fields\": [" + string.Join(", ", fields) + "]}";

    static Dictionary<string, object?> Decode(string schema, string hex)
        => SchemaDecoder.Decode(SchemaParser.Parse(schema), Convert.FromHexString(hex.Replace(" ", "")));

    static EncodeResult Encode(string schema, Dictionary<string, object?> data)
        => SchemaEncoder.Encode(SchemaParser.Parse(schema), data);

    public static IEnumerable<object[]> Rejected => new[]
    {
        new object[] { "PS-404", Fields("{\"name\": \"a\", \"type\": \"u8\", \"optional\": true}",
            "{\"name\": \"b\", \"type\": \"u8\"}") },
        new object[] { "PS-404", Fields("{\"name\": \"r\", \"type\": \"repeat\", \"count\": 1, \"fields\": ["
            + "{\"name\": \"a\", \"type\": \"u8\", \"optional\": true}, {\"name\": \"b\", \"type\": \"u8\"}]}") },
        new object[] { "PS-404", Fields("{\"name\": \"k\", \"type\": \"u8\"}",
            "{\"match\": {\"field\": \"$k\", \"cases\": {\"1\": [{\"name\": \"a\", \"type\": \"u8\", \"optional\": true}, "
            + "{\"name\": \"b\", \"type\": \"u8\"}]}}}") },
        new object[] { "PS-433", Fields("{\"name\": \"_meta\", \"type\": \"u8\"}") },
        new object[] { "PS-433", Fields("{\"name\": \"_quality\", \"type\": \"number\", \"value\": 1}") },
        new object[] { "PS-433", Fields("{\"name\": \"o\", \"type\": \"object\", \"fields\": ["
            + "{\"name\": \"_warnings\", \"type\": \"u8\"}]}") },
        new object[] { "PS-416", Fields("{\"name\": \"k\", \"type\": \"u8\", \"var\": \"k\"}",
            "{\"match\": {\"field\": \"$k\", \"remaining\": true, \"cases\": {\"1\": [{\"name\": \"a\", \"type\": \"u8\"}]}}}") },
        new object[] { "PS-416", Fields("{\"match\": {\"cases\": {\"1\": [{\"name\": \"a\", \"type\": \"u8\"}]}}}") },
        new object[] { "PS-414", Fields("{\"match\": {\"remaining\": false, \"cases\": {\"1\": [{\"name\": \"a\", \"type\": \"u8\"}]}}}") },
        new object[] { "PS-427", Fields("{\"name\": \"t\", \"type\": \"u8\", \"sentinel\": 5}") },
        new object[] { "PS-427", Fields("{\"name\": \"t\", \"type\": \"u8\", \"sentinel\": [\"x\"]}") },
        new object[] { "PS-428", Fields("{\"name\": \"t\", \"type\": \"u8\", \"valid_range\": [0, 1], \"out_of_range\": \"drop\"}") },
    };

    [Theory]
    [MemberData(nameof(Rejected))]
    public void ASchemaRuleIsEnforcedAtLoad(string tag, string schema)
    {
        var e = Assert.Throws<InvalidOperationException>(() => SchemaParser.Parse(schema));
        Assert.Contains(tag, e.Message);
    }

    // PS-402, PS-403, PS-405 --------------------------------------------------------------

    static readonly string Optional = Fields("{\"name\": \"a\", \"type\": \"u8\"}",
        "{\"name\": \"fw\", \"type\": \"u16\", \"optional\": true}",
        "{\"name\": \"ble\", \"type\": \"u16\", \"optional\": true}");

    [Fact]
    public void OptionalFieldsAreAbsentWhereNoBytesRemain()
    {
        Assert.Equal(new[] { "a" }, Decode(Optional, "01").Keys);
        var both = Decode(Optional, "01 0102 0304");
        Assert.Equal(258UL, both["fw"]);
        Assert.Equal(772UL, both["ble"]);
    }

    [Fact]
    public void SomeBytesButTooFewIsAnError()
    {
        var e = Assert.Throws<InvalidOperationException>(() => Decode(Optional, "0102"));
        Assert.Contains("PS-403", e.Message);
    }

    [Fact]
    public void AnOptionalObjectIsPresentOrAbsentWhole()
    {
        var schema = Fields("{\"name\": \"a\", \"type\": \"u8\"}",
            "{\"name\": \"gps\", \"type\": \"object\", \"optional\": true, \"fields\": ["
            + "{\"name\": \"lat\", \"type\": \"s16\"}, {\"name\": \"lon\", \"type\": \"s16\"}]}");
        Assert.Equal(new[] { "a" }, Decode(schema, "01").Keys);
        var gps = Assert.IsType<Dictionary<string, object?>>(Decode(schema, "0100010002")["gps"]);
        Assert.Equal(1L, gps["lat"]);
        Assert.Equal(2L, gps["lon"]);
        var e = Assert.Throws<InvalidOperationException>(() => Decode(schema, "010001"));
        Assert.Contains("PS-403", e.Message);
    }

    [Fact]
    public void AnOptionalFieldSuppliedAfterAnOmittedOneIsRejected()
    {
        var result = Encode(Optional, new() { ["a"] = 1, ["ble"] = 2 });
        Assert.False(result.Success);
        Assert.Contains(result.Errors, e => e.Contains("PS-405"));
    }

    [Fact]
    public void OmittedOptionalFieldsWriteNothing()
    {
        var result = Encode(Optional, new() { ["a"] = 1 });
        Assert.True(result.Success);
        Assert.Empty(result.Warnings);
        Assert.Equal(new byte[] { 1 }, result.Payload);
        Assert.Equal(new byte[] { 1, 0, 2 }, Encode(Optional, new() { ["a"] = 1, ["fw"] = 2 }).Payload);
    }

    // PS-414 ------------------------------------------------------------------------------

    [Fact]
    public void RemainingExcludesAnEnclosingReserve()
    {
        var schema = Fields("{\"name\": \"r\", \"type\": \"repeat\", \"until\": \"end\", \"reserve\": 1, \"max\": 1, "
            + "\"fields\": [{\"match\": {\"remaining\": true, \"cases\": {\"2\": [{\"name\": \"pair\", \"type\": \"u16\"}]}}}]}",
            "{\"name\": \"tail\", \"type\": \"u8\"}");
        var output = Decode(schema, "0102FF");
        var element = Assert.IsType<Dictionary<string, object?>>(
            Assert.Single(Assert.IsType<List<object?>>(output["r"])));
        Assert.Equal(258UL, element["pair"]);
        Assert.Equal(255UL, output["tail"]);
    }

    [Fact]
    public void AMatchOnRemainingWritesNoDiscriminator()
    {
        var schema = Fields("{\"name\": \"s\", \"type\": \"u8\"}",
            "{\"match\": {\"remaining\": true, \"cases\": {\"2\": [{\"name\": \"short\", \"type\": \"u16\"}], "
            + "\"4\": [{\"name\": \"long\", \"type\": \"u32\"}]}}}");
        Assert.Equal(new byte[] { 1, 0, 0, 0, 5 }, Encode(schema, new() { ["s"] = 1, ["long"] = 5 }).Payload);
    }

    // PS-427, PS-428 ----------------------------------------------------------------------

    [Fact]
    public void SentinelComparesTheBitsBeforeAnEncoding()
    {
        var schema = Fields("{\"name\": \"v\", \"type\": \"u8\", \"encoding\": \"bcd\", \"sentinel\": [255]}");
        // 0xFF is no BCD codeword: compared first, it is absent rather than an error.
        Assert.Empty(Decode(schema, "FF"));
        Assert.Equal(42.0, Convert.ToDouble(Decode(schema, "42")["v"]));
        // Under gray, 0xFF decodes to 170 and 0x80 to 255: the bits are what is compared.
        var gray = Fields("{\"name\": \"v\", \"type\": \"u8\", \"encoding\": \"gray\", \"sentinel\": [255]}");
        Assert.Empty(Decode(gray, "FF"));
        Assert.Equal(255.0, Convert.ToDouble(Decode(gray, "80")["v"]));
        // Encoding an omitted reading writes the sentinel's bits, not its codeword.
        Assert.Equal(new byte[] { 0xFF }, Encode(schema, new()).Payload);
    }

    [Fact]
    public void AbsentReadingsJoinQualityOnlyWhereItIsProduced()
    {
        var withRange = Fields("{\"name\": \"t\", \"type\": \"s16\", \"sentinel\": [-32768]}",
            "{\"name\": \"x\", \"type\": \"u8\", \"valid_range\": [0, 10]}");
        var quality = Assert.IsType<Dictionary<string, string>>(Decode(withRange, "800005")["_quality"]);
        Assert.Equal(new Dictionary<string, string> { ["x"] = "good", ["t"] = "absent" }, quality);

        var alone = Fields("{\"name\": \"t\", \"type\": \"s16\", \"sentinel\": [-32768]}");
        Assert.False(Decode(alone, "8000").ContainsKey("_quality"));

        // A field declaring valid_range produces `_quality` itself.
        var omit = Fields("{\"name\": \"h\", \"type\": \"u8\", \"valid_range\": [0, 100], \"out_of_range\": \"omit\"}");
        var output = Decode(omit, "C8");
        Assert.False(output.ContainsKey("h"));
        Assert.Equal("out_of_range", Assert.IsType<Dictionary<string, string>>(output["_quality"])["h"]);
    }

    [Fact]
    public void AnOmittedValueIsNotBound()
    {
        var schema = Fields("{\"name\": \"t\", \"type\": \"u8\", \"sentinel\": [255], \"var\": \"tv\"}",
            "{\"name\": \"u\", \"type\": \"number\", \"ref\": \"$t\", \"mult\": 2}");
        Assert.Equal(10.0, Convert.ToDouble(Decode(schema, "05")["u"]));
        Assert.Throws<InvalidOperationException>(() => Decode(schema, "FF"));
    }

    [Fact]
    public void AnOmittedSentinelFieldEncodesAsItsFirstSentinel()
    {
        var schema = Fields("{\"name\": \"t\", \"type\": \"s16\", \"div\": 10, \"sentinel\": [-32768, 32767]}",
            "{\"name\": \"b\", \"type\": \"u8\"}");
        var result = Encode(schema, new() { ["b"] = 90 });
        Assert.True(result.Success);
        Assert.Equal(new byte[] { 0x80, 0x00, 0x5A }, result.Payload);
    }

    // PS-434 ------------------------------------------------------------------------------

    [Fact]
    public void AnInternalFieldWithNoValueAndNoInputIsAnEncodeError()
    {
        var schema = Fields("{\"name\": \"_version\", \"type\": \"u8\"}", "{\"name\": \"a\", \"type\": \"u8\"}");
        var result = Encode(schema, new() { ["a"] = 1 });
        Assert.False(result.Success);
        Assert.Contains(result.Errors, e => e.Contains("PS-434") && e.Contains("_version"));
    }

    [Fact]
    public void AnInternalFieldTakesItsValueFromTheInputWhereSupplied()
    {
        var schema = Fields("{\"name\": \"_version\", \"type\": \"u8\"}", "{\"name\": \"a\", \"type\": \"u8\"}");
        Assert.Equal(new byte[] { 3, 1 }, Encode(schema, new() { ["_version"] = 3, ["a"] = 1 }).Payload);
    }

    [Fact]
    public void AnInternalValueIsWrittenWhateverTheInputSays()
    {
        var schema = Fields("{\"name\": \"_version\", \"type\": \"u8\", \"value\": 2}", "{\"name\": \"a\", \"type\": \"u8\"}");
        Assert.Equal(new byte[] { 2, 1 }, Encode(schema, new() { ["_version"] = 9, ["a"] = 1 }).Payload);
    }

    [Fact]
    public void AnInternalBitRangeFollowsTheSameRule()
    {
        var schema = Fields("{\"name\": \"_r\", \"type\": \"u8[0:3]\"}",
            "{\"name\": \"v\", \"type\": \"u8[4:7]\", \"consume\": 1}");
        Assert.Contains(Encode(schema, new() { ["v"] = 1 }).Errors, e => e.Contains("PS-434"));
        var declared = Fields("{\"name\": \"_r\", \"type\": \"u8[0:3]\", \"value\": 5}",
            "{\"name\": \"v\", \"type\": \"u8[4:7]\", \"consume\": 1}");
        Assert.Equal(new byte[] { 0x15 }, Encode(declared, new() { ["v"] = 1 }).Payload);
    }

    [Fact]
    public void AnInternalByteGroupMemberFollowsTheSameRule()
    {
        var schema = Fields("{\"byte_group\": {\"size\": 1, \"fields\": [{\"name\": \"_r\", \"type\": \"u8[0:3]\"}, "
            + "{\"name\": \"v\", \"type\": \"u8[4:7]\"}]}}");
        Assert.Contains(Encode(schema, new() { ["v"] = 1 }).Errors, e => e.Contains("PS-434"));
        Assert.Equal(new byte[] { 0x17 }, Encode(schema, new() { ["v"] = 1, ["_r"] = 7 }).Payload);
    }

    [Fact]
    public void AnInternalFlaggedMemberFollowsTheSameRule()
    {
        var schema = Fields("{\"name\": \"mask\", \"type\": \"u8\"}",
            "{\"flagged\": {\"field\": \"mask\", \"groups\": [{\"bit\": 0, \"fields\": ["
            + "{\"name\": \"_r\", \"type\": \"u8\"}, {\"name\": \"v\", \"type\": \"u8\"}]}]}}");
        var missing = Encode(schema, new() { ["mask"] = 1, ["v"] = 2 });
        Assert.Contains(missing.Errors, e => e.Contains("PS-434") && e.Contains("_r"));
        var supplied = Encode(schema, new() { ["mask"] = 1, ["_r"] = 9, ["v"] = 2 });
        Assert.True(supplied.Success);
        Assert.Equal(new byte[] { 1, 9, 2 }, supplied.Payload);
    }

    [Fact]
    public void AnOmittedFlaggedMemberEncodesAsItsFirstSentinel()
    {
        var schema = Fields("{\"name\": \"mask\", \"type\": \"u8\"}",
            "{\"flagged\": {\"field\": \"mask\", \"groups\": [{\"bit\": 0, \"fields\": ["
            + "{\"name\": \"t\", \"type\": \"s16\", \"div\": 10, \"sentinel\": [-32768]}, "
            + "{\"name\": \"v\", \"type\": \"u8\"}]}]}}");
        var result = Encode(schema, new() { ["mask"] = 1, ["v"] = 2 });
        Assert.True(result.Success);
        Assert.Equal(new byte[] { 1, 0x80, 0x00, 2 }, result.Payload);
    }

    [Fact]
    public void AnInternalComputedFieldWritesNothing()
    {
        var schema = Fields("{\"name\": \"a\", \"type\": \"u8\"}",
            "{\"name\": \"_double\", \"type\": \"number\", \"ref\": \"$a\", \"mult\": 2}");
        var result = Encode(schema, new() { ["a"] = 4 });
        Assert.True(result.Success);
        Assert.Equal(new byte[] { 4 }, result.Payload);
    }

    [Fact]
    public void AnInternalDiscriminatorIsNotInferredFromTheCase()
    {
        // The encoder no longer guesses `_kind` from the case the data fits: it is an
        // error where neither the schema nor the input gives it (PS-434).
        var schema = Fields("{\"name\": \"_kind\", \"type\": \"u8\"}",
            "{\"match\": {\"field\": \"$_kind\", \"cases\": {\"1\": [{\"name\": \"t\", \"type\": \"u8\"}]}}}");
        Assert.Contains(Encode(schema, new() { ["t"] = 7 }).Errors, e => e.Contains("PS-434"));
    }

    // PS-464, PS-465 ----------------------------------------------------------------------

    [Fact]
    public void ALengthNamingAFieldTakesItsValue()
    {
        var schema = Fields("{\"name\": \"n\", \"type\": \"u8\"}",
            "{\"name\": \"frame\", \"type\": \"bytes\", \"length\": \"$n\"}",
            "{\"name\": \"m\", \"type\": \"u8\"}",
            "{\"name\": \"label\", \"type\": \"ascii\", \"length\": \"m\"}");
        var output = Decode(schema, "02AABB0141");
        Assert.Equal("aabb", output["frame"]);
        Assert.Equal("A", output["label"]);
        Assert.Equal(new byte[] { 2, 0xAA, 0xBB, 1, 0x41 }, SchemaEncoder.Encode(SchemaParser.Parse(schema), output).Payload);
    }

    [Fact]
    public void AnUnresolvedLengthNamesTheField()
    {
        var e = Assert.Throws<InvalidOperationException>(() =>
            Decode(Fields("{\"name\": \"b\", \"type\": \"bytes\", \"length\": \"$n\"}"), "01"));
        Assert.Contains("PS-465", e.Message);
        Assert.Contains("'n'", e.Message);
    }
}
