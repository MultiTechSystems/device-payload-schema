// Copyright (c) 2024-2026 Multitech Systems, Inc.
// SPDX-License-Identifier: MIT

using YamlDotNet.RepresentationModel;

namespace PayloadSchema;

/// <summary>
/// CR-2026-138 (PS-553): a field has a name; padding is a skip.
///
/// Mirrors <c>field_name_errors</c> in tools/schema_interpreter.py, which is the spec
/// prototype's <c>check_field_names</c>: every field that reads or reports a value has a
/// <c>name</c>, except a <c>skip</c>. A construct (<c>match</c>, <c>byte_group</c>,
/// <c>flagged</c>, <c>tlv</c>, <c>records</c>) is not a field, a byte_group member may
/// leave reserved bits unnamed, and a repeat's <c>element</c> is nameless (PS-528). Any
/// other nameless field rejects the schema at load, identified by its 1-based position.
/// Before, C# read such a field and dropped it, and the encoder wrote it as zero padding.
/// </summary>
public static partial class SchemaParser
{
    static readonly string[] NamelessConstructs = { "match", "byte_group", "flagged", "tlv" };

    static YamlNode? NameKey(YamlMappingNode map, string key)
    {
        foreach (var kv in map.Children)
            if (kv.Key is YamlScalarNode s && s.Value == key)
                return kv.Value;
        return null;
    }

    /// <summary>Throws the first PS-553 violation, over the document after its $refs are spliced.</summary>
    static void CheckFieldNames(YamlMappingNode root)
    {
        var label = NameKey(root, "name") is YamlScalarNode n ? $"Schema '{n.Value}'" : "Schema None";
        WalkNames(NameKey(root, "header"), label + ", header");
        WalkNames(NameKey(root, "fields"), label);
        if (NameKey(root, "ports") is YamlMappingNode ports)
        {
            foreach (var kv in ports.Children)
            {
                var group = kv.Value is YamlMappingNode entry ? NameKey(entry, "fields") : kv.Value;
                WalkNames(group, $"{label}, port {Scalar(kv.Key)}");
            }
        }
    }

    static void WalkNames(YamlNode? items, string where)
    {
        if (items is not YamlSequenceNode seq) return;
        int position = 0;
        foreach (var entry in seq.Children)
        {
            position++;
            if (entry is not YamlMappingNode item) continue;
            var type = NameKey(item, "type");
            if (NameKey(item, "records") is { } records)
            {
                if (records is YamlSequenceNode bodies)
                    foreach (var body in bodies.Children) WalkNames(body, where);
                continue;
            }
            if (type == null && NamelessConstructs.Any(k => NameKey(item, k) != null))
            {
                if (NameKey(item, "match") is YamlMappingNode match)
                {
                    if (NameKey(match, "cases") is YamlMappingNode cases)
                        foreach (var kv in cases.Children) WalkNames(kv.Value, where);
                    WalkNames(NameKey(match, "default"), where);
                }
                foreach (var key in new[] { "tlv", "flagged" })
                {
                    var spec = NameKey(item, key);
                    if (spec is YamlSequenceNode)
                    {
                        WalkNames(spec, where);
                    }
                    else if (spec is YamlMappingNode m)
                    {
                        WalkNames(NameKey(m, "fields"), where);
                        if (NameKey(m, "cases") is YamlMappingNode cases)
                            foreach (var kv in cases.Children) WalkNames(kv.Value, where);
                        if (NameKey(m, "groups") is YamlSequenceNode groups)
                            foreach (var g in groups.Children)
                                if (g is YamlMappingNode gm) WalkNames(NameKey(gm, "fields"), where);
                    }
                }
                continue;
            }
            var typeText = type == null ? null : Scalar(type);
            if (typeText == "skip") continue;
            if (type == null && NameKey(item, "object") != null) continue;   // PS-466 names it
            if (typeText == "match") continue;   // the withdrawn `type: match`, which Cr159 names
            var name = NameKey(item, "name") is YamlScalarNode ns ? ns.Value ?? "" : "";
            if (name.Length == 0)
                throw new InvalidOperationException(
                    $"{where}: field {position}, a {typeText ?? "field"}, has no name; a field that "
                    + "reads or reports a value is named, and padding is a `skip` (PS-553)");
            var inner = $"{where}, in '{name}'";
            WalkNames(NameKey(item, "fields"), inner);
            WalkNames(NameKey(item, "trailer"), inner);
        }
    }
}
