// Copyright (c) 2024-2026 Multitech Systems, Inc.
// SPDX-License-Identifier: MIT

using System.Text.RegularExpressions;
using YamlDotNet.RepresentationModel;

namespace PayloadSchema;

/// <summary>
/// PS-539 (CR-2026-117): every <c>$</c> reference names something the schema declares - a
/// field, a <c>var</c>, a tag component, a repeat's <c>index</c> or <c>count_as</c> - in the
/// header, the fields, a port or a definition, at any depth. A <c>metadata</c> include or
/// timestamp name counts as declared; a <c>metadata.include</c> <c>source</c> names runtime
/// input (PS-315) and is not checked. Whether the name is bound when it is read is a decode
/// matter (PS-314), so a name declared later, or in a branch not taken, is valid here.
/// The walk is the spec prototype's <c>key_vocabulary.check_references</c> and
/// <c>reference_errors</c> in tools/schema_interpreter.py; Go and Java walk the same way,
/// and all four give the message identically.
/// </summary>
public static partial class SchemaParser
{
    /// <summary>Keys whose string value may be a <c>$</c> reference.</summary>
    static readonly HashSet<string> ReferenceKeys = new()
    {
        "ref", "a", "b", "field", "count", "byte_length", "carry", "instance", "unit", "identity",
    };

    /// <summary>Keys holding data, not declarations or references. A match's <c>default</c> is a field list.</summary>
    static readonly HashSet<string> ReferenceDataKeys = new() { "lookup", "values", "else", "value" };

    static readonly Regex TemplateReference = new(@"\$\{(\w+)\}", RegexOptions.Compiled);

    /// <summary>PS-539's wording, the spec prototype's.</summary>
    internal static string ReferenceErrorMessage(string name, string key) =>
        $"`${name}` ({key}) names no field, var, tag component, index or count_as the schema declares (PS-539)";

    /// <summary>Every undeclared reference's message, in source order, each once.</summary>
    internal static List<string> ReferenceErrors(YamlMappingNode root)
    {
        var declared = new HashSet<string>();
        var references = new List<(string Name, string Key)>();

        void Walk(YamlNode? node, string? key, bool inSenml)
        {
            if (node is YamlSequenceNode seq)
            {
                foreach (var item in seq.Children) Walk(item, null, false);
                return;
            }
            if (node is not YamlMappingNode map) return;
            if (!inSenml)
                foreach (var nameKey in new[] { "name", "var", "index", "count_as" })
                    if (Get(map, nameKey) is YamlScalarNode s && s.Value != null)
                        declared.Add(s.Value);
            // An `element` that declares no name is decoded as `element` (PS-528), so its
            // `present_if` and `name_from` can name it.
            if (!inSenml && Get(map, "element") is YamlMappingNode element && Get(element, "name") is not YamlScalarNode)
                declared.Add("element");
            foreach (var kv in map.Children)
            {
                var k = Scalar(kv.Key);
                if (ReferenceDataKeys.Contains(k)) continue;
                if (kv.Value is YamlScalarNode scalar)
                {
                    var v = scalar.Value ?? "";
                    if (ReferenceKeys.Contains(k) && v.StartsWith("$"))
                        references.Add((v.Substring(1), k));
                    else if (k == "name_from" || (inSenml && k == "name"))
                        foreach (Match m in TemplateReference.Matches(v))
                            references.Add((m.Groups[1].Value, k));
                }
                else if (k == "default")
                {
                    if (key == "match")
                        Walk(kv.Value, null, false);    // a match's default is a field list
                }
                else
                {
                    Walk(kv.Value, k, k == "senml");
                }
            }
        }

        Walk(Get(root, "header"), null, false);
        Walk(Get(root, "fields"), null, false);
        foreach (var part in new[] { "ports", "definitions" })
            if (Get(root, part) is YamlMappingNode groups)
                foreach (var kv in groups.Children)
                    Walk(kv.Value, null, false);
        if (Get(root, "metadata") is YamlMappingNode metadata)
            foreach (var part in new[] { "include", "timestamps" })
                if (Get(metadata, part) is YamlSequenceNode entries)
                    foreach (var entry in entries.Children)
                        if (entry is YamlMappingNode e && Get(e, "name") is YamlScalarNode n && n.Value != null)
                            declared.Add(n.Value);

        var errors = new List<string>();
        foreach (var (name, key) in references)
        {
            if (declared.Contains(name)) continue;
            var message = ReferenceErrorMessage(name, key);
            if (!errors.Contains(message)) errors.Add(message);
        }
        return errors;
    }

    /// <summary>Rejects the schema at the first undeclared reference (PS-539).</summary>
    static void CheckReferences(YamlMappingNode root)
    {
        var errors = ReferenceErrors(root);
        if (errors.Count > 0)
            throw new InvalidOperationException(errors[0]);
    }
}
