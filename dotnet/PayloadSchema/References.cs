// Copyright (c) 2024-2026 Multitech Systems, Inc.
// SPDX-License-Identifier: MIT

using System.Text.RegularExpressions;
using YamlDotNet.RepresentationModel;

namespace PayloadSchema;

/// <summary>
/// PS-539 (CR-2026-117, CR-2026-127): every <c>$</c> reference names something the schema
/// declares - a field, a <c>var</c>, a tag component, a repeat's <c>index</c> or
/// <c>count_as</c> - and the declaration comes before the reference in schema order (PS-122,
/// PS-147): a tlv's tag components before its cases, a repeat's <c>trailer</c> and elements
/// before its <c>present_if</c> and <c>identity</c>. The document is walked with every
/// <c>$ref</c> already spliced, so a definition's references are checked where it is used
/// and an unused definition is never checked. A nameless repeat element declares no name
/// (PS-528). <c>metadata</c> include and timestamp names count as declared first; a
/// <c>metadata.include</c> <c>source</c> names runtime input (PS-315) and is not checked.
/// Whether a name declared earlier is bound when it is read is a decode matter (PS-314).
/// The walk is the spec prototype's <c>key_vocabulary.check_references</c> and
/// <c>reference_errors</c> in tools/schema_interpreter.py; Go and Java walk the same way,
/// and all four give the messages identically.
/// </summary>
public static partial class SchemaParser
{
    /// <summary>Keys whose string value may be a <c>$</c> reference.</summary>
    static readonly HashSet<string> ReferenceKeys = new()
    {
        "ref", "a", "b", "field", "count", "byte_length", "carry", "instance", "unit", "identity", "interval",
        // CR-2026-198 (PS-621): a field decoded before the repeat. The spec prototype does
        // not list it; PS-621's text says the field is decoded before, as PS-539 checks.
        "bit_offset",
    };

    /// <summary>Keys holding data, not declarations or references. A match's <c>default</c> is a field list.</summary>
    static readonly HashSet<string> ReferenceDataKeys = new() { "lookup", "values", "else", "value" };

    /// <summary>Keys walked before the rest of their mapping: what the rest reads comes after them.</summary>
    static readonly string[] ReferenceFirstKeys = { "tag_fields", "trailer", "fields", "element" };

    static readonly Regex TemplateReference = new(@"\$\{(\w+)\}", RegexOptions.Compiled);

    /// <summary>PS-539's wording, the spec prototype's.</summary>
    internal static string ReferenceErrorMessage(string name, string key) =>
        $"`${name}` ({key}) names no field, var, tag component, index or count_as the schema declares (PS-539)";

    /// <summary>PS-539's ordering rule (CR-2026-127), the spec prototype's wording.</summary>
    internal static string ReferenceOrderMessage(string name, string key) =>
        $"`${name}` ({key}) is referenced before the schema declares it (PS-539, PS-122)";

    /// <summary>Every undeclared or forward reference's message, in schema order, each once.</summary>
    internal static List<string> ReferenceErrors(YamlMappingNode root)
    {
        var declared = new HashSet<string>();
        var ahead = new List<(string Name, string Key)>();

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
            var entries = map.Children.ToList();
            var ordered = ReferenceFirstKeys
                .SelectMany(f => entries.Where(kv => Scalar(kv.Key) == f))
                .Concat(entries.Where(kv => !ReferenceFirstKeys.Contains(Scalar(kv.Key))));
            foreach (var kv in ordered)
            {
                var k = Scalar(kv.Key);
                if (ReferenceDataKeys.Contains(k)) continue;
                if (kv.Value is YamlScalarNode scalar)
                {
                    var v = scalar.Value ?? "";
                    var names = new List<string>();
                    if (ReferenceKeys.Contains(k) && v.StartsWith("$"))
                    {
                        // PS-546: `$name[i]` names a sibling repeat and this one's index.
                        var indexed = Regex.Match(v.Substring(1), @"^(\w+)\[(\w+)\]$");
                        if (indexed.Success)
                        {
                            names.Add(indexed.Groups[1].Value);
                            names.Add(indexed.Groups[2].Value);
                        }
                        else
                            names.Add(v.Substring(1));
                    }
                    else if (k == "name_from" || (inSenml && k == "name"))
                        foreach (Match m in TemplateReference.Matches(v))
                            names.Add(m.Groups[1].Value);
                    else if (k == "template")                                  // PS-590
                        foreach (Match m in TemplateStrings.Reference.Matches(v))
                            names.Add(m.Groups[1].Value);
                    foreach (var n in names)
                        if (!declared.Contains(n)) ahead.Add((n, k));
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

        if (Get(root, "metadata") is YamlMappingNode metadata)
            foreach (var part in new[] { "include", "timestamps" })
                if (Get(metadata, part) is YamlSequenceNode entries)
                    foreach (var entry in entries.Children)
                        if (entry is YamlMappingNode e && Get(e, "name") is YamlScalarNode n && n.Value != null)
                            declared.Add(n.Value);
        Walk(Get(root, "header"), null, false);
        Walk(Get(root, "fields"), null, false);
        // Each port is a decode of its own (CR-2026-130): what one port declares is never
        // bound while another is decoded, so each starts from what the top level declares.
        var shared = new HashSet<string>(declared);
        var seen = new HashSet<string>(declared);
        if (Get(root, "ports") is YamlMappingNode ports)
            foreach (var kv in ports.Children)
            {
                declared.Clear();
                declared.UnionWith(shared);
                Walk(kv.Value, null, false);
                seen.UnionWith(declared);
            }
        declared.Clear();
        declared.UnionWith(seen);   // to tell "never declared" from "declared too late"

        var errors = new List<string>();
        foreach (var (name, key) in ahead)
        {
            var message = declared.Contains(name) ? ReferenceOrderMessage(name, key) : ReferenceErrorMessage(name, key);
            if (!errors.Contains(message)) errors.Add(message);
        }
        return errors;
    }

    /// <summary>Rejects the schema at the first undeclared or forward reference (PS-539).</summary>
    static void CheckReferences(YamlMappingNode root)
    {
        var errors = ReferenceErrors(root);
        if (errors.Count > 0)
            throw new InvalidOperationException(errors[0]);
    }
}
