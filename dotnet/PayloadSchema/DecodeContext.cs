// Copyright (c) 2024-2026 Multitech Systems, Inc.
// SPDX-License-Identifier: MIT

namespace PayloadSchema;

/// <summary>
/// A read or skip past the end of the payload, less any `reserve` in force (PS-535,
/// CR-2026-115). Thrown unnamed by <see cref="DecodeContext.Read"/> and
/// <see cref="DecodeContext.Peek"/>; the innermost field that made the read restates it as
/// <c>Field '&lt;name&gt;': ... (PS-535)</c> (<see cref="Name"/>), and every enclosing field
/// passes it through unchanged. The text is the spec prototype's and the reference's
/// (BufferUnderflow in tools/schema_interpreter.py), so a cross-language diff compares
/// equal strings.
/// </summary>
public sealed class BufferUnderflowException : InvalidOperationException
{
    /// <summary>True once the message identifies the field.</summary>
    public bool Named { get; }
    /// <summary>The unnamed text, before a field was named.</summary>
    public string Detail { get; }

    BufferUnderflowException(string message, string detail, bool named) : base(message)
    {
        Detail = detail;
        Named = named;
    }

    internal static BufferUnderflowException Need(int n, int offset, int end)
    {
        var text = $"Buffer underflow: need {n} bytes at offset {offset}, "
            + $"but only {Math.Max(0, end - offset)} remaining";
        return new BufferUnderflowException(text, text, false);
    }

    /// <summary>A short read whose text is given whole (PS-593's missing terminator).</summary>
    internal static BufferUnderflowException Text(string text) =>
        new BufferUnderflowException(text, text, false);

    /// <summary>A bit range or a bool reads its byte(s) without consuming them.</summary>
    internal static BufferUnderflowException PeekAt(int pos)
    {
        var text = $"Buffer underflow at peek offset {pos}";
        return new BufferUnderflowException(text, text, false);
    }

    /// <summary>How a PS-535 error identifies a field: its name, else what it is.</summary>
    internal static string Label(string? name, string? kind) =>
        !string.IsNullOrEmpty(name) ? $"'{name}'"
            : $"an unnamed {(string.IsNullOrEmpty(kind) ? "field" : kind)}";

    /// <summary>PS-535: the error of the field that made the read; a named one is kept.</summary>
    internal BufferUnderflowException Name(string? name, string? kind) => NameAs(Label(name, kind));

    /// <summary>This error identified by a ready-made PS-535 label.</summary>
    internal BufferUnderflowException NameAs(string label) =>
        Named ? this
            : new BufferUnderflowException($"Field {label}: {Detail} (PS-535)", Detail, true);

    /// <summary>
    /// How PS-535 identifies a byte_group: by its members, as the spec prototype does -
    /// <c>byte_group of 'f', 'w'</c>, every named member in declaration order, internal
    /// <c>_</c> members included - else <c>an unnamed byte_group</c> (CR-2026-131).
    /// </summary>
    internal static string ByteGroupLabel(IEnumerable<string?> memberNames)
    {
        var quoted = memberNames.Where(n => !string.IsNullOrEmpty(n)).Select(n => $"'{n}'").ToList();
        return quoted.Count == 0 ? "an unnamed byte_group" : "byte_group of " + string.Join(", ", quoted);
    }
}

public class DecodeContext
{
    public byte[] Data { get; internal set; }
    public int Offset { get; set; }
    public string Endian { get; }
    public Dictionary<string, object?> Variables { get; } = new();
    /// <summary>The decode record being kept (PS-572); null when not asked for.</summary>
    internal DecodeTrace? Trace { get; set; }
    /// <summary>
    /// PS-532: the value a looked-up field reported, by the name it binds, for `name_from`
    /// templates - which read the label while a `$` reference reads PS-523's binding.
    /// <see cref="SchemaDecoder.Omitted"/> where the lookup had no entry. A name bound by
    /// any other field has no entry here, and a template reads its variable.
    /// </summary>
    internal Dictionary<string, object?> Reported { get; } = new();
    /// <summary>
    /// PS-523: what a looked-up field binds, by field definition, set where its lookup ran:
    /// the label where it is a number, else the number before the lookup.
    /// </summary>
    internal Dictionary<SchemaField, object?> LookupBinding { get; } = new(ReferenceEqualityComparer.Instance);
    /// <summary>
    /// CR-2026-132: the code an enum read, by field definition, which its reference binds.
    /// </summary>
    internal Dictionary<SchemaField, object?> EnumCodes { get; } = new(ReferenceEqualityComparer.Instance);

    /// <summary>
    /// Binds <paramref name="name"/> for <paramref name="field"/> as PS-523 says, keeping
    /// the reported value for templates (PS-532). A field with no lookup binds what it
    /// reported. Mirrors Scope.bind in the spec prototype.
    /// </summary>
    internal void Bind(SchemaField field, string name, object? reported)
    {
        if ((field.Lookup != null || field.Type == FieldType.Enum || BitLabels.Declared(field))
            && LookupBinding.TryGetValue(field, out var binding))
        {
            Variables[name] = binding;
            Reported[name] = reported;
        }
        else
        {
            Variables[name] = reported;
            Reported.Remove(name);
        }
    }

    /// <summary>
    /// Re-binds a name from a construct's merged output. A name its own field already bound
    /// under PS-523 is left alone: the output holds the label, not the binding.
    /// </summary>
    internal void BindOutput(string name, object? value)
    {
        if (Reported.TryGetValue(name, out var reported) && Equals(reported, value))
            return;
        Variables[name] = value;
        Reported.Remove(name);
    }

    /// <summary>Whether a lookup label is a number, which PS-523 binds as itself.</summary>
    internal static bool IsNumericLabel(object? label) =>
        label is not bool && label is not string && Helpers.ToFloat64(label).ok;
    /// <summary>
    /// The records the current repeat element yields through `records` (PS-541); null
    /// outside an element.
    /// </summary>
    internal List<Dictionary<string, object?>>? PendingRecords { get; set; }
    public Dictionary<string, string> Quality { get; } = new();
    public List<string> Warnings { get; } = new();
    /// <summary>
    /// Set where a PS-302 warning has already reported the bytes left, so PS-472 reports
    /// no second one for them.
    /// </summary>
    internal bool LeftoverReported { get; set; }
    /// <summary>
    /// PS-475: a looked-up field's value before its lookup, which `valid_range` compares,
    /// by field definition. Mirrors _pre_lookup in tools/schema_interpreter.py.
    /// </summary>
    internal Dictionary<SchemaField, double> PreLookup { get; } = new(ReferenceEqualityComparer.Instance);
    /// <summary>PS-443: fields whose failed guard reported its `else`, which ends the sequence.</summary>
    internal HashSet<SchemaField> GuardElse { get; } = new(ReferenceEqualityComparer.Instance);
    /// <summary>
    /// Readings omitted under PS-427/PS-428 that wait to learn whether `_quality` is
    /// produced at all; joined to it at the end of the decode only where it is.
    /// </summary>
    internal Dictionary<string, string> PendingAbsent { get; } = new();
    /// <summary>Above zero while a flagged group's members decode; their marks always wait.</summary>
    internal int FlaggedDepth { get; set; }

    /// <summary>
    /// PS-490: the declaration that wrote each top-level reported value, by output key.
    /// The last one decoded is the one whose value is reported, so the last write wins.
    /// Mirrors _producers in tools/schema_interpreter.py.
    /// </summary>
    internal Dictionary<string, SchemaField> Producers { get; } = new(StringComparer.Ordinal);
    /// <summary>Above zero inside an object's members or a repeat's elements, which record nothing.</summary>
    internal int MetaDepth { get; set; }

    /// <summary>Records the declaration about to report <paramref name="key"/> (its name by default).</summary>
    internal void Produced(SchemaField field, string? key = null)
    {
        if (MetaDepth > 0) return;
        key ??= field.Name;
        if (!string.IsNullOrEmpty(key) && !key.StartsWith('_'))
            Producers[key] = field;
    }

    /// <summary>
    /// Records an omitted reading (PS-427, PS-428). A field declaring valid_range produces
    /// `_quality` itself, so its mark goes in at once; any other waits (PS-182). Mirrors
    /// _mark_absent in tools/schema_interpreter.py.
    /// </summary>
    internal void MarkAbsent(SchemaField field, string why)
    {
        if (field.ValidRange is { Length: >= 2 } && FlaggedDepth == 0)
            Quality[field.Name] = why;
        else
            PendingAbsent[field.Name] = why;
    }

    /// <summary>Joins the waiting marks to `_quality` where it is produced.</summary>
    internal void FinishQuality()
    {
        if (Quality.Count == 0) return;
        foreach (var (name, why) in PendingAbsent)
            Quality.TryAdd(name, why);
    }
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
    /// <summary>The FPort the decode was given, null for none (PS-635, PS-636).</summary>
    internal int? FrameFPort { get; set; }
    /// <summary>The whole payload's length, whatever data is in view (PS-635).</summary>
    internal long PayloadLength { get; set; }

    public ReadOnlySpan<byte> Read(int n)
    {
        // `length: remaining` (PS-014). Guarded here because the bounds check below
        // passes for a negative n and AsSpan then throws ArgumentOutOfRange.
        if (n < 0)
            n = Remaining;
        if (Offset + n > Data.Length)
            throw BufferUnderflowException.Need(n, Offset, Data.Length);
        var result = Data.AsSpan(Offset, n);
        Offset += n;
        return result;
    }

    public ReadOnlySpan<byte> Peek(int n, int relativeOffset = 0)
    {
        int pos = Offset + relativeOffset;
        if (pos + n > Data.Length)
            throw BufferUnderflowException.PeekAt(pos);
        return Data.AsSpan(pos, n);
    }

    public string CheckValidRange(object? value, SchemaField field)
    {
        if (field.ValidRange == null || field.ValidRange.Length < 2)
            return "good";

        // PS-443 as amended: a failed guard's `else` ends the sequence and is not compared.
        if (GuardElse.Contains(field))
        {
            Quality[field.Name] = "good";
            return "good";
        }
        // PS-475: a looked-up field is compared on the number before its lookup; compared
        // after it, a label is no number and every value read "good".
        if (field.Lookup != null)
        {
            if (!PreLookup.TryGetValue(field, out var before))
            {
                Quality[field.Name] = "good";
                return "good";
            }
            value = before;
        }

        var (ok, numVal) = Helpers.ToFloat64(value);
        if (!ok)
        {
            // The reference records "good" for a value it cannot compare.
            Quality[field.Name] = "good";
            return "good";
        }

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
