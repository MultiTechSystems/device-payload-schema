// Copyright (c) 2024-2026 Multitech Systems, Inc.
// SPDX-License-Identifier: MIT

using Xunit;

namespace PayloadSchema.Tests;

/// <summary>
/// PS-347, PS-441: a <c>match</c> or <c>tlv</c> case body is a field list, <c>[]</c> for a
/// case that reads nothing. A bare string such as <c>5: skip</c> parsed as an empty case,
/// so a match decoded nothing for that value and reported success. Mirrors
/// tests/test_case_body_field_list.py.
/// </summary>
public class CaseBodyTests
{
    const string Want = "a case body is a field list; write [] for a case that reads nothing (PS-441)";

    [Theory]
    [InlineData("name: p\nfields:\n- match:\n    length: 1\n    cases:\n      5: skip\n      6: [{name: a, type: u8}]\n",
        "match.cases[5]")]
    [InlineData("name: p\nfields:\n- tlv:\n    tag_size: 1\n    cases:\n      1: skip\n", "tlv.cases[1]")]
    [InlineData("name: p\nfields:\n- name: g\n  type: object\n  fields:\n  - match:\n      length: 1\n      cases:\n        5: skip\n",
        "match.cases[5]")]
    public void ACaseBodyThatIsNotAFieldListIsRejectedAtLoad(string yaml, string prefix)
    {
        var thrown = Assert.Throws<InvalidOperationException>(() => SchemaParser.Parse(yaml));
        Assert.Contains(prefix + ": " + Want, thrown.Message);
    }

    [Fact]
    public void AnEmptyCaseBodyIsAccepted()
    {
        var schema = SchemaParser.Parse(
            "name: p\nfields:\n- match:\n    length: 1\n    cases:\n      5: []\n      6: [{name: a, type: u8}]\n");
        var output = SchemaDecoder.Decode(schema, new byte[] { 5 });
        Assert.Empty(output);
    }
}
