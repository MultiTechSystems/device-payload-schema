using Xunit;

namespace PayloadSchema.Tests;

/// <summary>
/// PS-539 (CR-2026-127) checks references in one decode order: within a mapping,
/// <c>tag_fields</c>, <c>trailer</c>, <c>fields</c> and <c>element</c> first, then the
/// remaining keys in source order. Each schema below differs from its pair only in the order
/// two keys are written, and that order alone decides whether the reference follows its
/// declaration. Mirrors tests/test_ps539_walk_order.py and go/schema/ps539_walk_order_test.go.
/// </summary>
public class ReferenceWalkOrderTests
{
    const string Order = "is referenced before the schema declares it (PS-539, PS-122)";

    [Theory]
    // A match's cases written before its discriminator declare the name it reads.
    [InlineData("fields:\n  - {name: k, type: u8}\n"
                + "  - match: {cases: {1: [{name: x, type: u8}]}, default: skip, field: $x}\n", null)]
    [InlineData("fields:\n  - {name: k, type: u8}\n"
                + "  - match: {field: $x, default: skip, cases: {1: [{name: x, type: u8}]}}\n",
                "`$x` (field) " + Order)]
    // A match's default field list against its cases.
    [InlineData("fields:\n  - {name: k, type: u8}\n  - match: {field: $k, default: [{name: x, type: u8}], "
                + "cases: {1: [{name: y, type: number, ref: $x}]}}\n", null)]
    [InlineData("fields:\n  - {name: k, type: u8}\n  - match: {field: $k, default: [{name: y, type: number, ref: $x}], "
                + "cases: {1: [{name: x, type: u8}]}}\n", "`$x` (ref) " + Order)]
    // Ports in source order, not sorted as strings ("10" < "9") or as numbers.
    [InlineData("ports:\n  9: {fields: [{name: x, type: u8}]}\n"
                + "  10: {fields: [{name: y, type: number, ref: $x}]}\n", null)]
    [InlineData("ports:\n  2: {fields: [{name: y, type: number, ref: $x}]}\n"
                + "  1: {fields: [{name: x, type: u8}]}\n", "`$x` (ref) " + Order)]
    // The same, authored as JSON.
    [InlineData("{\"name\": \"t\", \"fields\": [{\"name\": \"k\", \"type\": \"u8\"}, {\"match\": "
                + "{\"cases\": {\"1\": [{\"name\": \"x\", \"type\": \"u8\"}]}, \"default\": \"skip\", \"field\": \"$x\"}}]}", null)]
    [InlineData("{\"name\": \"t\", \"fields\": [{\"name\": \"k\", \"type\": \"u8\"}, {\"match\": "
                + "{\"field\": \"$x\", \"default\": \"skip\", \"cases\": {\"1\": [{\"name\": \"x\", \"type\": \"u8\"}]}}}]}",
                "`$x` (field) " + Order)]
    public void ReferenceOrderFollowsTheSource(string doc, string? refused)
    {
        var text = doc.StartsWith("{") ? doc : "name: t\n" + doc;
        if (refused == null)
        {
            SchemaParser.Parse(text);
        }
        else
        {
            var e = Assert.ThrowsAny<Exception>(() => SchemaParser.Parse(text));
            Assert.Equal(refused, e.Message);
        }
    }
}
