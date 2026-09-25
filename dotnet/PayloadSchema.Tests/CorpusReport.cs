// Copyright (c) 2024-2026 Multitech Systems, Inc.
// SPDX-License-Identifier: MIT

using System.Text.Json;
using YamlDotNet.RepresentationModel;

namespace PayloadSchema.Tests;

/// <summary>
/// Per-vector reporting for the corpus conformance runner, for tools/verdicts-gate.py.
///
/// A pass count against a floor cannot say which vectors an implementation fails, so a
/// vector that decodes correctly in Python and differently here is invisible whenever the
/// total still clears the floor. Two optional environment variables change that; with
/// neither set the runner behaves exactly as before:
///
///   CORPUS_REPORT=/path/file.json  also write one {schema, index, vector, status, detail}
///                                  entry per vector (pass, fail, error or skip)
///   CORPUS_ONLY=a/x.yaml,b/y.yaml  run only these schemas (relative to schemas/devices; a
///                                  leading "schemas/devices/" is accepted). The floor is
///                                  not applied to a restricted run.
///   CORPUS_ROOT=/some/dir          walk this directory instead of schemas/devices, so a
///                                  scratch schema can be run outside the repository.
/// </summary>
sealed class CorpusReport
{
    readonly string? _path = Environment.GetEnvironmentVariable("CORPUS_REPORT");
    readonly HashSet<string>? _only;
    readonly List<Dictionary<string, object>> _entries = new();

    public CorpusReport()
    {
        var raw = Environment.GetEnvironmentVariable("CORPUS_ONLY");
        if (string.IsNullOrWhiteSpace(raw)) return;
        _only = new HashSet<string>(StringComparer.Ordinal);
        foreach (var item in raw.Split(','))
        {
            var rel = Relative(item.Trim());
            if (rel.Length > 0) _only.Add(rel);
        }
    }

    public static string Relative(string path)
    {
        var rel = path.Replace('\\', '/');
        const string prefix = "schemas/devices/";
        return rel.StartsWith(prefix, StringComparison.Ordinal) ? rel[prefix.Length..] : rel;
    }

    /// <summary>Whether CORPUS_ONLY or CORPUS_ROOT moved the run off the whole corpus.</summary>
    public bool Restricted =>
        _only != null || !string.IsNullOrEmpty(Environment.GetEnvironmentVariable("CORPUS_ROOT"));

    public int RestrictedCount => _only?.Count ?? 0;

    public bool Includes(string rel) => _only == null || _only.Contains(rel);

    public void Add(string schema, int index, string vector, string status, string? detail)
    {
        if (string.IsNullOrEmpty(_path)) return;
        var text = detail ?? "";
        if (text.Length > 200) text = text[..200];
        _entries.Add(new Dictionary<string, object>
        {
            ["schema"] = schema,
            ["index"] = index,
            ["vector"] = vector,
            ["status"] = status,
            ["detail"] = text,
        });
    }

    /// <summary>
    /// Records every vector of a schema this interpreter could not parse. The schema's own
    /// YAML is read again for the vector list; if even that fails nothing is recorded, and
    /// the gate reports the vectors as missing from this implementation instead.
    /// </summary>
    public void SchemaFailed(string schema, string text, string message)
    {
        if (string.IsNullOrEmpty(_path)) return;
        try
        {
            var yaml = new YamlStream();
            yaml.Load(new StringReader(text));
            var root = (YamlMappingNode)yaml.Documents[0].RootNode;
            if (!root.Children.TryGetValue(new YamlScalarNode("test_vectors"), out var node)
                || node is not YamlSequenceNode vectors)
                return;
            for (int i = 0; i < vectors.Children.Count; i++)
            {
                if (vectors.Children[i] is not YamlMappingNode vector) continue;
                var name = Scalar(vector, "name");
                if (Scalar(vector, "payload").Length == 0)
                    Add(schema, i, name, "skip", "no payload (encode vector)");
                else
                    Add(schema, i, name, "error", "parse: " + message);
            }
        }
        catch (Exception)
        {
            // Unreadable YAML: leave the vectors unrecorded (see above).
        }
    }

    public void Write(Action<string> log)
    {
        if (string.IsNullOrEmpty(_path)) return;
        var json = JsonSerializer.Serialize(_entries, new JsonSerializerOptions { WriteIndented = true });
        File.WriteAllText(_path, json + "\n");
        log($"corpus report: {_entries.Count} vectors written to {_path}");
    }

    public static string Scalar(YamlMappingNode node, string key) =>
        node.Children.TryGetValue(new YamlScalarNode(key), out var value)
            && value is YamlScalarNode scalar
                ? scalar.Value ?? ""
                : "";
}
