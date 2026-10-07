// Copyright (c) 2024-2026 Multitech Systems, Inc.
// SPDX-License-Identifier: MIT

namespace PayloadSchema;

public class DecodeContext
{
    public byte[] Data { get; internal set; }
    public int Offset { get; set; }
    public string Endian { get; }
    public Dictionary<string, object?> Variables { get; } = new();
    public Dictionary<string, string> Quality { get; } = new();
    public List<string> Warnings { get; } = new();
    /// <summary>Names declared only inside some repeat's elements (PS-368).</summary>
    internal HashSet<string> RepeatOnlyNames { get; set; } = new();
    /// <summary>The fields of the element being decoded, or null outside one (PS-368).</summary>
    internal HashSet<string>? ElementNames { get; set; }

    /// <summary>
    /// PS-368: a reference that found no variable is an error where it names a field of
    /// the element being decoded (not yet decoded) or a name declared only inside a
    /// repeat's elements. Any other unbound reference keeps its caller's behaviour.
    /// Mirrors _ref in tools/schema_interpreter.py.
    /// </summary>
    internal void CheckUnbound(string name)
    {
        if (ElementNames != null && ElementNames.Contains(name))
            throw new InvalidOperationException(
                $"${name} refers to a field of this element that is not yet decoded (PS-368)");
        if (RepeatOnlyNames.Contains(name))
            throw new InvalidOperationException(
                $"${name} is declared only inside a repeat's elements, so it has no value here (PS-368)");
    }

    /// <summary>
    /// Runs <paramref name="body"/> reading a buffer that ends <paramref name="end"/> bytes
    /// into the payload; positions stay absolute (PS-350, PS-471).
    /// </summary>
    internal T WithRegion<T>(int end, Func<T> body)
    {
        var full = Data;
        if (end >= full.Length) return body();
        Data = full.AsSpan(0, end).ToArray();
        try { return body(); }
        finally { Data = full; }
    }

    public DecodeContext(byte[] data, string endian = "big")
    {
        Data = data;
        Endian = string.IsNullOrEmpty(endian) ? "big" : endian;
    }

    public int Remaining => Data.Length - Offset;

    public ReadOnlySpan<byte> Read(int n)
    {
        // `length: remaining` (PS-014). Guarded here because the bounds check below
        // passes for a negative n and AsSpan then throws ArgumentOutOfRange.
        if (n < 0)
            n = Remaining;
        if (Offset + n > Data.Length)
            throw new InvalidOperationException(
                $"Buffer underflow: need {n} bytes at offset {Offset}, but only {Remaining} remaining");
        var result = Data.AsSpan(Offset, n);
        Offset += n;
        return result;
    }

    public ReadOnlySpan<byte> Peek(int n, int relativeOffset = 0)
    {
        int pos = Offset + relativeOffset;
        if (pos + n > Data.Length)
            throw new InvalidOperationException($"Buffer underflow at peek offset {pos}");
        return Data.AsSpan(pos, n);
    }

    public string CheckValidRange(object? value, SchemaField field)
    {
        if (field.ValidRange == null || field.ValidRange.Length < 2)
            return "good";

        var (ok, numVal) = Helpers.ToFloat64(value);
        if (!ok) return "good";

        double min = field.ValidRange[0], max = field.ValidRange[1];
        if (numVal < min || numVal > max)
        {
            var warning = $"{field.Name}: value {numVal} outside valid range [{min}, {max}]";
            Warnings.Add(warning);
            Quality[field.Name] = "out_of_range";
            return "out_of_range";
        }

        Quality[field.Name] = "good";
        return "good";
    }
}
