// Copyright (c) 2024-2026 Multitech Systems, Inc.
// SPDX-License-Identifier: MIT

using YamlDotNet.Core;
using YamlDotNet.RepresentationModel;

namespace PayloadSchema;

public static partial class SchemaParser
{
    /// <summary>
    /// Plain scalars that YAML 1.1 reads as booleans and YAML 1.2 as strings (PS-575,
    /// CR-2026-164). `y` and `n` are not here: they are field names.
    /// </summary>
    static readonly HashSet<string> Yaml11Booleans = new()
    {
        "yes", "Yes", "YES", "no", "No", "NO", "on", "On", "ON", "off", "Off", "OFF",
    };

    /// <summary>
    /// Reject a plain (unquoted) scalar whose text is a YAML 1.1 boolean, found in the
    /// representation model's scalar styles before any value is read, so a YAML 1.1 and a
    /// YAML 1.2 loader agree. Mirrors check_yaml_booleans in tools/schema_interpreter.py.
    /// </summary>
    static void CheckYamlBooleans(YamlNode root)
    {
        var seen = new HashSet<YamlNode>(ReferenceEqualityComparer.Instance);
        var found = new List<YamlScalarNode>();
        void Walk(YamlNode node)
        {
            if (!seen.Add(node)) return;
            switch (node)
            {
                case YamlScalarNode s:
                    if (s.Style == ScalarStyle.Plain && s.Value != null && Yaml11Booleans.Contains(s.Value))
                        found.Add(s);
                    break;
                case YamlMappingNode m:
                    foreach (var kv in m.Children) { Walk(kv.Key); Walk(kv.Value); }
                    break;
                case YamlSequenceNode q:
                    foreach (var child in q.Children) Walk(child);
                    break;
            }
        }
        Walk(root);
        if (found.Count == 0) return;
        // The first in the document, as the event stream the reference reads would give it.
        var first = found.OrderBy(n => n.Start.Line).ThenBy(n => n.Start.Column).First();
        throw new InvalidOperationException(
            $"Line {first.Start.Line}: the unquoted value '{first.Value}' is a boolean in YAML 1.1 "
            + $"and a string in YAML 1.2; quote it (\"{first.Value}\") or write true or false (PS-575)");
    }
}
