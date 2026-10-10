// Copyright (c) 2024-2026 Multitech Systems, Inc.
// SPDX-License-Identifier: MIT

using Xunit;

namespace PayloadSchema.Tests;

/// <summary>
/// CR-2026-102: a field's ipso is a mapping carrying object, and its senml a mapping
/// (PS-510), wherever the field sits. The same cases are in
/// tests/test_cr_2026_102_ipso_senml_mappings.py, cr102_test.go and CR2026102Test.
/// </summary>
public class CR2026_102Tests
{
    const string Ipso = "The ipso of 't' is a mapping that carries `object`, such as {object: 3303}; "
        + "a bare object number is not a shorthand (PS-510)";
    const string Senml = "The senml of 't' is a mapping, such as {unit: Cel} (PS-510)";

    static readonly Dictionary<string, string> Contexts = new()
    {
        ["top"] = "fields:\n  - FIELD\n",
        ["port"] = "ports:\n  1:\n    fields:\n      - FIELD\n",
        ["match"] = "fields:\n  - {name: kind, type: u8}\n  - match:\n      field: $kind\n      cases:\n        1:\n          - FIELD\n",
        ["tlv"] = "fields:\n  - tlv:\n      tag_size: 1\n      cases:\n        1:\n          - FIELD\n",
        ["object"] = "fields:\n  - name: o\n    type: object\n    fields:\n      - FIELD\n",
        ["repeat"] = "fields:\n  - name: r\n    type: repeat\n    count: 2\n    fields:\n      - FIELD\n",
        ["byte_group"] = "fields:\n  - byte_group:\n      size: 1\n      fields:\n        - BITS\n",
        ["flagged"] = "fields:\n  - {name: mask, type: u8}\n  - flagged:\n      field: mask\n      groups:\n"
            + "        - bit: 0\n          fields:\n            - FIELD\n",
        ["definition"] = "definitions:\n  d:\n    fields:\n      - FIELD\nfields:\n  - $ref: '#/definitions/d'\n",
    };

    static string Schema(string context, string annotation) =>
        "name: p\n" + Contexts[context]
            .Replace("FIELD", "{name: t, type: u8, " + annotation + "}")
            .Replace("BITS", "{name: t, type: 'u8[0:7]', " + annotation + "}");

    public static IEnumerable<object[]> Refused()
    {
        var cases = new (string, string)[]
        {
            ("ipso: 3303", Ipso), ("ipso: '3303'", Ipso), ("ipso: null", Ipso),
            ("ipso: [3303]", Ipso), ("ipso: {resource: 5700}", Ipso),
            ("senml: 'Cel'", Senml), ("senml: null", Senml), ("senml: ['Cel']", Senml),
        };
        foreach (var context in Contexts.Keys)
            foreach (var (annotation, message) in cases)
                yield return new object[] { context, annotation, message };
    }

    public static IEnumerable<object[]> Accepted()
    {
        var cases = new[]
        {
            "ipso: {object: 3303}", "ipso: {object: 3303, instance: 1, resource: 5700}",
            "senml: {unit: Cel}", "senml: {name: x, unit: Cel}",
        };
        foreach (var context in Contexts.Keys)
            foreach (var annotation in cases)
                yield return new object[] { context, annotation };
    }

    [Theory]
    [MemberData(nameof(Refused))]
    public void ANonMappingAnnotationIsRefused(string context, string annotation, string message)
    {
        var thrown = Assert.Throws<InvalidOperationException>(() => SchemaParser.Parse(Schema(context, annotation)));
        Assert.Equal(message, thrown.Message);
    }

    [Theory]
    [MemberData(nameof(Accepted))]
    public void AMappingAnnotationIsAccepted(string context, string annotation)
    {
        SchemaParser.Parse(Schema(context, annotation));
    }

    [Fact]
    public void AnUnreferencedDefinitionIsHeldToItToo()
    {
        var thrown = Assert.Throws<InvalidOperationException>(() => SchemaParser.Parse(
            "name: p\ndefinitions:\n  d:\n    fields:\n      - {name: t, type: u8, ipso: 3303}\n"
            + "fields:\n  - {name: a, type: u8}\n"));
        Assert.Equal(Ipso, thrown.Message);
    }
}
