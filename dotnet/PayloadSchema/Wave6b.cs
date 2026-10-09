// Copyright (c) 2024-2026 Multitech Systems, Inc.
// SPDX-License-Identifier: MIT

using YamlDotNet.RepresentationModel;

namespace PayloadSchema;

/// <summary>
/// 0.5.2 wave 6b: field-level rules (CR-2026-056, -059, -062, -065, -067). The schema
/// rules, checked when the schema is loaded. Each mirrors the function of the same purpose
/// in tools/schema_interpreter.py: <c>optional_errors</c> and <c>internal_name_errors</c>,
/// and the sentinel and out_of_range checks of tools/validate_schema.py.
/// </summary>
public static partial class SchemaParser
{
    /// <summary>Names clause 7 reserves for interpreter metadata (PS-176, PS-433).</summary>
    static readonly string[] ReservedOutputNames = { "_meta", "_quality", "_warnings" };

    /// <summary>PS-427: sentinel is a non-empty list of integers. PS-428: omit or flag.</summary>
    static void CheckFieldRules(YamlMappingNode fm)
    {
        var name = Get(fm, "name") is { } n ? Scalar(n) : "?";
        if (Get(fm, "sentinel") is { } sentinel
            && !(sentinel is YamlSequenceNode seq && seq.Children.Count > 0
                 && seq.Children.All(item => YamlInt(item) != null)))
            throw new InvalidOperationException(
                $"Field '{name}': sentinel must be a non-empty list of integers (PS-427)");
        if (Get(fm, "out_of_range") is { } oor && YamlString(oor) is not ("omit" or "flag"))
            throw new InvalidOperationException(
                $"Field '{name}': out_of_range must be omit or flag (PS-428)");
    }

    /// <summary>Rejects a schema breaking PS-404 or PS-433, over the whole document.</summary>
    static void CheckWave6b(YamlMappingNode root)
    {
        var errors = new List<string>();
        if (Get(root, "fields") is YamlSequenceNode top)
            OptionalErrors(top, "fields", errors);
        if (Get(root, "ports") is YamlMappingNode ports)
            foreach (var kv in ports.Children)
            {
                var group = kv.Value is YamlMappingNode entry ? Get(entry, "fields") : kv.Value;
                if (group is YamlSequenceNode seq)
                    OptionalErrors(seq, $"ports[{Scalar(kv.Key)}].fields", errors);
            }
        InternalNameErrors(root, "", errors);
        if (errors.Count > 0)
            throw new InvalidOperationException(string.Join("; ", errors));
    }

    /// <summary>PS-404: in any field list, every field after an optional field is optional too.</summary>
    static void OptionalErrors(YamlSequenceNode? fields, string where, List<string> errors)
    {
        if (fields == null) return;
        string? seen = null;
        int i = -1;
        foreach (var item in fields.Children)
        {
            i++;
            if (item is not YamlMappingNode f) continue;
            var name = Get(f, "name") is { } n ? Scalar(n) : "?";
            if (Get(f, "optional") is YamlScalarNode { Style: YamlDotNet.Core.ScalarStyle.Plain } o
                && o.Value is "true" or "True")
                seen ??= name;
            else if (seen != null)
                errors.Add($"{where}[{i}] ({name}): follows optional field '{seen}', so it must be "
                    + "optional too (PS-404)");
            foreach (var kv in f.Children)
            {
                var key = Scalar(kv.Key);
                if (key is "fields" or "trailer" or "default" && kv.Value is YamlSequenceNode list)
                    OptionalErrors(list, $"{where}[{i}].{key}", errors);
                else if (key is "match" or "tlv" && kv.Value is YamlMappingNode body)
                {
                    if (Get(body, "cases") is YamlMappingNode cases)
                        foreach (var c in cases.Children)
                            if (c.Value is YamlSequenceNode caseBody)
                                OptionalErrors(caseBody, $"{where}[{i}].{key}[{Scalar(c.Key)}]", errors);
                    if (Get(body, "default") is YamlSequenceNode fallback)
                        OptionalErrors(fallback, $"{where}[{i}].{key}.default", errors);
                }
                else if (key == "flagged" && kv.Value is YamlMappingNode flagged
                         && Get(flagged, "groups") is YamlSequenceNode groups)
                {
                    int g = -1;
                    foreach (var group in groups.Children)
                    {
                        g++;
                        if (group is YamlMappingNode gm)
                            OptionalErrors(Get(gm, "fields") as YamlSequenceNode,
                                $"{where}[{i}].flagged[{g}]", errors);
                    }
                }
            }
        }
    }

    /// <summary>PS-433: no field may take a name reserved for interpreter metadata.</summary>
    static void InternalNameErrors(YamlNode node, string path, List<string> errors)
    {
        if (node is YamlMappingNode map)
        {
            if (Get(map, "name") is YamlScalarNode { Value: { } name }
                && ReservedOutputNames.Contains(name) && (Has(map, "type") || Has(map, "fields")))
                errors.Add($"{path}: '{name}' is reserved for interpreter metadata and cannot name "
                    + "a field (PS-433)");
            foreach (var kv in map.Children)
            {
                var key = Scalar(kv.Key);
                if (key is "test_vectors" or "definitions") continue;
                InternalNameErrors(kv.Value, path.Length > 0 ? $"{path}.{key}" : key, errors);
            }
        }
        else if (node is YamlSequenceNode seq)
        {
            for (int i = 0; i < seq.Children.Count; i++)
                InternalNameErrors(seq.Children[i], $"{path}[{i}]", errors);
        }
    }
}
