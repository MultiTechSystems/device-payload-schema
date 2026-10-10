// Copyright (c) 2024-2026 Multitech Systems, Inc.
// SPDX-License-Identifier: MIT

using Xunit;

namespace PayloadSchema.Tests;

/// <summary>CR-2026-107: a boolean literal (PS-357, PS-358 amended; PS-519, PS-520).</summary>
public class CR2026_107Tests
{
    [Theory]
    [InlineData("PS-519", "{name: k, type: boolean}")]
    [InlineData("PS-520", "{name: k, type: boolean, value: null}")]
    [InlineData("PS-358", "{name: k, type: boolean, value: true, mult: 2}")]
    [InlineData("PS-520", "{name: k, type: boolean, value: \"true\"}")]
    [InlineData("PS-520", "{name: k, type: boolean, value: 1}")]
    [InlineData("PS-358", "{name: k, type: boolean, value: true, lookup: {1: x}}")]
    public void AMalformedBooleanLiteralIsRejected(string tag, string field)
    {
        var thrown = Assert.Throws<InvalidOperationException>(
            () => SchemaParser.Parse("name: p\nfields:\n  - " + field + "\n"));
        Assert.Contains(tag, thrown.Message);
    }

    [Theory]
    [InlineData("top", "  - {name: a, type: u8}\n  - {name: hw, type: boolean, value: true}\n  - {name: b, type: u8}\n", "0105")]
    [InlineData("match", "  - {name: kind, type: u8}\n  - match:\n      field: $kind\n      cases:\n        1:\n"
        + "          - {name: hw, type: boolean, value: true}\n          - {name: b, type: u8}\n", "0105")]
    [InlineData("tlv", "  - tlv:\n      tag_size: 1\n      cases:\n        1:\n"
        + "          - {name: hw, type: boolean, value: true}\n          - {name: b, type: u8}\n", "0105")]
    [InlineData("object", "  - name: o\n    type: object\n    fields:\n      - {name: hw, type: boolean, value: true}\n"
        + "      - {name: b, type: u8}\n", "05")]
    [InlineData("repeat", "  - name: r\n    type: repeat\n    count: 2\n    fields:\n"
        + "      - {name: hw, type: boolean, value: true}\n      - {name: b, type: u8}\n", "0506")]
    public void ABooleanLiteralWorksInEveryFieldList(string context, string body, string hex)
    {
        var schema = SchemaParser.Parse("name: p\nfields:\n" + body);
        var payload = Convert.FromHexString(hex);
        var output = SchemaDecoder.Decode(schema, payload);
        object? hw = context switch
        {
            "object" => ((Dictionary<string, object?>)output["o"]!)["hw"],
            "repeat" => ((Dictionary<string, object?>)((System.Collections.IList)output["r"]!)[1]!)["hw"],
            _ => output["hw"],
        };
        Assert.Equal(true, hw);
        Assert.Equal(payload, SchemaEncoder.Encode(schema, output).Payload);
    }
}
