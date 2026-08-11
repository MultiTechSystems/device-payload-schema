// Copyright (c) 2024-2026 Multitech Systems, Inc.
// SPDX-License-Identifier: MIT

using System.Globalization;
using System.Text;

namespace PayloadSchema;

public static class SchemaDecoder
{
    public static Dictionary<string, object?> Decode(PayloadSchemaDefinition schema, byte[] data)
    {
        var ctx = new DecodeContext(data, schema.Endian);
        var result = new Dictionary<string, object?>();

        MergeTo(result, DecodeFields(schema.Fields, ctx, schema));

        if (ctx.Quality.Count > 0)
            result["_quality"] = new Dictionary<string, string>(ctx.Quality);

        return result;
    }

    public static Dictionary<string, object?> DecodeWithPort(PayloadSchemaDefinition schema, byte[] data, int fPort)
    {
        var fields = ResolveFields(schema, fPort);
        var ctx = new DecodeContext(data, schema.Endian);
        var result = new Dictionary<string, object?>();

        MergeTo(result, DecodeFields(fields, ctx, schema));

        if (ctx.Quality.Count > 0)
            result["_quality"] = new Dictionary<string, string>(ctx.Quality);

        return result;
    }

    static List<SchemaField> ResolveFields(PayloadSchemaDefinition schema, int fPort)
    {
        if (schema.Ports == null)
            return schema.Fields;

        var portKey = fPort.ToString();
        if (schema.Ports.TryGetValue(portKey, out var pd))
            return pd.Fields;
        if (schema.Ports.TryGetValue("default", out var dpd))
            return dpd.Fields;

        throw new InvalidOperationException($"No port definition for fPort {fPort} and no default in schema '{schema.Name}'");
    }

    static Dictionary<string, object?> DecodeFields(List<SchemaField> fields, DecodeContext ctx, PayloadSchemaDefinition? schema)
    {
        var result = new Dictionary<string, object?>();

        foreach (var field in fields)
        {
            // $ref
            if (field.Ref2 != null && schema?.Definitions != null)
            {
                var refResult = ResolveRef(field.Ref2, ctx, schema);
                MergeTo(result, refResult);
                foreach (var kv in refResult)
                    ctx.Variables[kv.Key] = kv.Value;
                continue;
            }

            // Byte group
            if (field.ByteGroup.Count > 0)
            {
                var bgResult = DecodeByteGroup(field, ctx);
                MergeTo(result, bgResult);
                foreach (var kv in bgResult)
                    ctx.Variables[kv.Key] = kv.Value;
                continue;
            }

            // TLV
            if (field.Type == FieldType.TLV)
            {
                MergeTo(result, DecodeTLV(field, ctx));
                continue;
            }

            // TLV inline
            if (field.TLVInline != null)
            {
                MergeTo(result, DecodeTLV(field.TLVInline, ctx));
                continue;
            }

            // Flagged
            if (field.Flagged != null)
            {
                var flaggedResult = DecodeFlagged(field.Flagged, ctx);
                MergeTo(result, flaggedResult);
                foreach (var kv in flaggedResult)
                    ctx.Variables[kv.Key] = kv.Value;
                continue;
            }

            // Match inline
            if (field.MatchInline != null)
            {
                var matchResult = DecodeMatch(field.MatchInline, ctx);
                if (matchResult is Dictionary<string, object?> matchMap)
                {
                    MergeTo(result, matchMap);
                    foreach (var kv in matchMap)
                        ctx.Variables[kv.Key] = kv.Value;
                }
                continue;
            }

            // Top-level match (type: match with on:) -- merge results
            if (field.Type == FieldType.Match && string.IsNullOrEmpty(field.Name))
            {
                var matchResult = DecodeMatch(field, ctx);
                if (matchResult is Dictionary<string, object?> matchMap)
                {
                    MergeTo(result, matchMap);
                    foreach (var kv in matchMap)
                        ctx.Variables[kv.Key] = kv.Value;
                }
                continue;
            }

            var value = DecodeField(field, ctx, schema);

            if (ReferenceEquals(value, Omitted))
            {
                // A mapping lookup with no entry and no default: the device
                // reported nothing this schema can name (PS-269).
                continue;
            }

            if (value != null && !string.IsNullOrEmpty(field.Name))
            {
                // A leading underscore marks an internal field: it becomes a
                // variable later fields can reference, but is not reported.
                // Without this an intermediate used to combine two words appeared
                // in the decoded output.
                if (!field.Name.StartsWith("_"))
                    result[ResolveFieldName(field, ctx)] = value;
                // Keyed by the schema-level name in Variables so $references keep
                // working when name_from is in play (PS-267).
                ctx.Variables[field.Name] = value;
                if (field.ValidRange is { Length: >= 2 })
                    ctx.CheckValidRange(value, field);
            }
        }

        return result;
    }

    static Dictionary<string, object?> ResolveRef(string refPath, DecodeContext ctx, PayloadSchemaDefinition schema)
    {
        if (!refPath.StartsWith("#/definitions/"))
            throw new InvalidOperationException($"Unsupported $ref format: {refPath}");

        var defName = refPath["#/definitions/".Length..];
        if (schema.Definitions == null || !schema.Definitions.TryGetValue(defName, out var def))
            throw new InvalidOperationException($"Definition not found: {defName}");

        return DecodeFields(def.Fields, ctx, schema);
    }

    static Dictionary<string, object?> DecodeByteGroup(SchemaField field, DecodeContext ctx)
    {
        int size = field.Size > 0 ? field.Size : 1;
        var data = ctx.Read(size);
        var result = new Dictionary<string, object?>();

        // The group's bytes are assembled in the schema's byte order - big-endian
        // unless the document says otherwise. Assembling little-endian
        // unconditionally was invisible while every multi-byte group happened to
        // carry no bit range: rakwireless/qingping packs a 12-bit temperature as
        // u24[12:23], and bytes 2D F1 C4 became 0xC4F12D rather than 0x2DF1C4.
        ulong rawVal = 0;
        if (ctx.Endian == "little")
        {
            for (int i = data.Length - 1; i >= 0; i--)
                rawVal = (rawVal << 8) | data[i];
        }
        else
        {
            foreach (var b in data)
                rawVal = (rawVal << 8) | b;
        }

        foreach (var subfield in field.ByteGroup)
        {
            int bitStart = subfield.BitOffset;
            int bitLen = subfield.BitCount > 0 ? subfield.BitCount : 8;

            // Parse from type if not set
            var bitRange = Helpers.ParseBitRange(subfield.RawType);
            if (bitRange != null)
            {
                bitStart = bitRange.Value.start;
                bitLen = bitRange.Value.end - bitRange.Value.start + 1;
            }

            ulong mask = ((1UL << bitLen) - 1);
            double value = (double)((rawVal >> bitStart) & mask);

            if (!string.IsNullOrEmpty(subfield.Name))
                result[subfield.Name] = value;
        }

        return result;
    }

    static Dictionary<string, object?> DecodeFlagged(FlaggedDef fd, DecodeContext ctx)
    {
        if (!ctx.Variables.TryGetValue(fd.Field, out var flagsVal))
            throw new InvalidOperationException($"Flagged field reference not found: {fd.Field}");

        var (_, flags) = Helpers.ToInt(flagsVal);
        var result = new Dictionary<string, object?>();

        foreach (var group in fd.Groups)
        {
            int isPresent = (flags >> group.Bit) & 1;
            if (isPresent != 0)
                MergeTo(result, DecodeFields(group.Fields, ctx, null));
        }

        return result;
    }

    static object? DecodeField(SchemaField field, DecodeContext ctx, PayloadSchemaDefinition? schema)
    {
        int length = field.Length > 0 ? field.Length : Helpers.InferLengthFromType(field.Type);
        string endian = field.Endian ?? ctx.Endian;
        object? value = null;

        switch (field.Type)
        {
            case FieldType.U8:
            case FieldType.U16:
            case FieldType.U24:
            case FieldType.U32:
            case FieldType.U64:
            {
                var data = ctx.Read(length);
                value = (double)Helpers.DecodeUint(data, endian);
                break;
            }

            case FieldType.S8:
            case FieldType.S16:
            case FieldType.S24:
            case FieldType.S32:
            case FieldType.S64:
            {
                var data = ctx.Read(length);
                value = (double)Helpers.DecodeSint(data, endian);
                break;
            }

            case FieldType.F16:
            case FieldType.F32:
            case FieldType.F64:
            {
                int size = field.Type switch
                {
                    FieldType.F16 => 2,
                    FieldType.F32 => 4,
                    FieldType.F64 => 8,
                    _ => 4
                };
                var data = ctx.Read(size);
                value = Helpers.DecodeFloat(data, size, endian);
                break;
            }

            case FieldType.Bool:
            {
                var data = ctx.Peek(1);
                value = Helpers.DecodeBits(data[0], field.Bit, 1) != 0;
                if (field.Consume > 0)
                    ctx.Read(field.Consume);
                break;
            }

            case FieldType.Bits:
            {
                // Read the whole base width, not just the first byte: a range wider
                // than a byte (u24[0:11] for a packed 12-bit humidity) decoded from
                // byte zero alone and reported a value with no error.
                int baseBytes = field.BitBaseBytes > 0 ? field.BitBaseBytes : 1;
                var data = ctx.Peek(baseBytes, field.ByteOffset);
                long baseValue = 0;
                foreach (var bt in data)
                    baseValue = (baseValue << 8) | bt;
                int bits = field.BitCount > 0 ? field.BitCount : 1;
                long mask = bits >= 63 ? long.MaxValue : (1L << bits) - 1;
                value = (double)((baseValue >> field.BitOffset) & mask);
                // An explicit range does not advance the cursor by itself: several
                // fields share one byte and the last of them declares `consume`.
                if (field.Consume > 0)
                    ctx.Read(field.Consume);
                break;
            }

            case FieldType.String:
            {
                if (length > 0)
                {
                    var data = ctx.Read(length);
                    value = Encoding.ASCII.GetString(data).TrimEnd('\0');
                }
                else
                {
                    value = field.Value;
                }
                break;
            }

            case FieldType.Ascii:
            {
                var data = ctx.Read(length);
                value = Encoding.ASCII.GetString(data).TrimEnd('\0');
                break;
            }

            case FieldType.Enum:
            {
                int baseLen = field.Base switch
                {
                    "u16" or "s16" => 2,
                    "u32" or "s32" => 4,
                    _ => 1
                };
                var data = ctx.Read(baseLen);
                int intVal = (int)Helpers.DecodeUint(data, endian);
                if (field.Values != null && field.Values.TryGetValue(intVal, out var enumStr))
                    value = enumStr;
                // An unmapped value takes the declared default (PS-068). Returning
                // the raw integer ignored the default the schema asked for.
                else if (field.EnumDefault != null)
                    value = field.EnumDefault;
                else
                    value = (double)intVal;
                break;
            }

            case FieldType.Hex:
            {
                var data = ctx.Read(length);
                value = Convert.ToHexString(data).ToLowerInvariant();
                break;
            }

            case FieldType.Skip:
            {
                ctx.Read(length);
                return null;
            }

            case FieldType.Bytes:
            {
                var data = ctx.Read(length);
                value = Helpers.FormatBytes(data.ToArray(), field.Format, field.Separator);
                break;
            }

            case FieldType.Repeat:
            {
                value = DecodeRepeat(field, ctx, schema);
                break;
            }

            case FieldType.BitfieldString:
            {
                var data = ctx.Read(length);
                ulong intVal = Helpers.DecodeUint(data, endian);
                string delimiter = field.Delimiter ?? ".";
                string prefix = field.Prefix ?? "";
                var partStrs = new List<string>();
                foreach (var part in field.Parts)
                {
                    if (part.Count < 2) continue;
                    var (_, bitOff) = Helpers.ToInt(part[0]);
                    var (_, bitLen) = Helpers.ToInt(part[1]);
                    string format = part.Count >= 3 && part[2] is string f ? f : "decimal";
                    ulong mask = (1UL << bitLen) - 1;
                    ulong raw = (intVal >> bitOff) & mask;
                    partStrs.Add(format == "hex"
                        ? raw.ToString("X")
                        : raw.ToString());
                }
                value = prefix + string.Join(delimiter, partStrs);
                break;
            }

            case FieldType.Number:
            {
                value = DecodeNumber(field, ctx);
                break;
            }

            case FieldType.Object:
            {
                value = DecodeFields(field.Fields, ctx, schema);
                break;
            }

            case FieldType.Match:
            {
                value = DecodeMatch(field, ctx);
                break;
            }

            case FieldType.TLV:
            {
                return DecodeTLV(field, ctx);
            }

            default:
                throw new InvalidOperationException($"Unknown field type: {field.Type} ({field.RawType})");
        }

        // Apply modifiers, skipping a Number whose value came from a ref or a
        // compute - DecodeNumber already applied its stages, so doing it again here
        // doubles them. The ref case was already skipped; compute was not, so a
        // compute field carrying a transform had it run twice, and
        // decentlab/dl-blg's voltage_ratio came out as -0.4999999996 where the
        // vendor decoder says 0.0064094: its `div: 16777216` and `add: -0.5` were
        // each applied a second time. Nothing caught it - that schema had no test
        // vectors at all.
        if (field.Type == FieldType.Number && (field.Ref != null || field.Compute != null))
        {
            // already handled in DecodeNumber
        }
        else
        {
            var (ok, numVal) = Helpers.ToFloat64(value);
            if (ok)
            {
                numVal = ApplyModifiers(numVal, field);
                value = numVal;
            }
        }

        // Apply lookup. A mapping is matched on its keys, which need not start at
        // zero or be contiguous (PS-268). An unmatched value omits the field rather
        // than reporting the raw integer under a name that promises a label, unless
        // a default is declared (PS-269).
        if (field.Lookup != null)
        {
            var (ok, intVal) = Helpers.ToInt(value);
            if (ok)
            {
                if (field.Lookup.TryGetValue(intVal, out var lookupStr))
                    value = lookupStr;
                else if (field.LookupDefault != null)
                    value = field.LookupDefault;
                else if (!field.LookupIsSequence)
                    return Omitted;
            }
        }

        // Store variable
        if (field.Var != null)
            ctx.Variables[field.Var] = value;

        return value;
    }

    /// <summary>
    /// Applies a transform array in list order. A stage normally carries one
    /// arithmetic op; where it carries several they run in the canonical order
    /// mult, div, add, so a stage cannot mean different things in different
    /// languages. A stage may instead name an operation, as {op: round, decimals: N}.
    /// </summary>

    /// <summary>
    /// Signals that a computed field is absent because its divisor was zero (PS-278).
    /// A distinct NaN payload, so it travels the existing double-valued compute path.
    /// </summary>
    internal static readonly double ComputeOmitted = BitConverter.Int64BitsToDouble(0x7ff8000000000abcL);

    internal static bool IsComputeOmitted(double v) =>
        BitConverter.DoubleToInt64Bits(v) == 0x7ff8000000000abcL;

    /// <summary>
    /// Integer division rounded toward negative infinity (PS-276).
    ///
    /// Corrected on the integers deliberately: (long)Math.Floor((double)a / b) is wrong
    /// above 2^53, where a long is not exactly representable as a double - for
    /// a = 2^53+1, b = 1 it yields 9007199254740992 rather than 9007199254740993.
    /// </summary>
    static long FloorDiv(long a, long b)
    {
        long q = a / b;
        if ((a % b != 0) && ((a < 0) != (b < 0))) q--;
        return q;
    }

    /// <summary>
    /// The remainder matching FloorDiv, so a == FloorDiv(a,b)*b + FloorMod(a,b) holds
    /// for every combination of signs (PS-277). Exact for the full long range.
    /// </summary>
    static long FloorMod(long a, long b) => ((a % b) + b) % b;

    /// <summary>
    /// Rounds to <paramref name="decimals"/> places, half-to-even, on the stored value
    /// rather than on value*10^decimals - see the note at the call site for why.
    /// </summary>
    static double RoundHalfEvenDecimal(double value, int decimals)
    {
        if (double.IsNaN(value) || double.IsInfinity(value)) return value;
        if (decimals < 0) decimals = 0;
        var text = value.ToString("F" + decimals, System.Globalization.CultureInfo.InvariantCulture);
        return double.TryParse(text, System.Globalization.NumberStyles.Float,
            System.Globalization.CultureInfo.InvariantCulture, out var rounded)
            ? rounded
            : value;
    }

    static double ApplyTransformStages(double numVal, List<TransformStage> stages)
    {
        foreach (var stage in stages)
        {
            if (stage.Op == "round")
            {
                // Half-to-even AND decimal-correct, matching Python, Java and Go.
                //
                // Math.Round(double, int, ToEven) is documented to lose precision this
                // way, and it does: it gives 2.36 for 2.355 and 2.68 for 2.675, where
                // the stored doubles are 2.35499999999999998 and 2.67499999999999982 and
                // the other implementations give 2.35 and 2.67. Rounding through
                // `decimal` is no better - it treats the literal as exact, so it agrees
                // on those two and then gives 2.34 for 2.345.
                //
                // Formatting to a fixed number of decimals is correctly rounded on the
                // stored value and breaks ties to even, which is both jobs at once.
                numVal = RoundHalfEvenDecimal(numVal, stage.Decimals);
                continue;
            }
            // Unary maths stages, each exclusive of the others and of the arithmetic
            // ops, in the order the Python interpreter checks them. The domain clamps
            // match it exactly: sqrt of a negative and log of a non-positive would
            // otherwise yield NaN and poison every later stage, where the interpreter
            // yields 0 and log(1e-10).
            if (stage.Sqrt) { numVal = Math.Sqrt(Math.Max(0.0, numVal)); continue; }
            if (stage.Abs) { numVal = Math.Abs(numVal); continue; }
            if (stage.Pow.HasValue) { numVal = Math.Pow(numVal, stage.Pow.Value); continue; }
            if (stage.Log10) { numVal = Math.Log10(Math.Max(1e-10, numVal)); continue; }
            if (stage.Log) { numVal = Math.Log(Math.Max(1e-10, numVal)); continue; }
            if (stage.Mult.HasValue) numVal *= stage.Mult.Value;
            if (stage.Div.HasValue && stage.Div.Value != 0) numVal /= stage.Div.Value;
            if (stage.Sub.HasValue) numVal -= stage.Sub.Value;
            if (stage.Add.HasValue) numVal += stage.Add.Value;
        }
        return numVal;
    }

    /// <summary>
    /// Applies a field's bare modifiers and then its transform stages. Both run
    /// when both are present - a field may scale with `mult` and then round with a
    /// stage - which an either/or chain here used to get wrong, dropping the
    /// modifier. The modifiers run in the canonical order mult, div, add, whatever
    /// order the keys were written in (PS-101).
    /// </summary>
    static double ApplyModifiers(double numVal, SchemaField field)
    {
        if (field.Mult.HasValue) numVal *= field.Mult.Value;
        if (field.Div.HasValue && field.Div.Value != 0) numVal /= field.Div.Value;
        if (field.Add.HasValue) numVal += field.Add.Value;
        return ApplyTransformStages(numVal, field.Transform);
    }

    static object? DecodeNumber(SchemaField field, DecodeContext ctx)
    {
        double numVal;

        if (field.Ref != null)
        {
            var refName = field.Ref.TrimStart('$');
            if (!ctx.Variables.TryGetValue(refName, out var refVal))
                throw new InvalidOperationException($"Ref field not found: {refName}");

            var (ok, rv) = Helpers.ToFloat64(refVal);
            numVal = ok ? rv : 0;

            if (field.Polynomial is { Length: > 0 })
                numVal = Helpers.EvaluatePolynomial(field.Polynomial, numVal);

            // Modifiers first, then the transform stages - the order the interpreter
            // uses. Running the stages first made a field that scales with `mult` and
            // then rounds with a stage round before it had scaled.
            numVal = ApplyModifiers(numVal, field);
        }
        else if (field.Compute != null)
        {
            // Guard must be checked before compute to prevent e.g. div-by-zero
            if (field.Guard != null && !EvaluateGuardConditions(field.Guard, ctx))
                return field.Guard.ElseValue;
            // A compute takes its transform stages and no bare modifiers, as the
            // interpreter does. These were not applied at all, so a computed field
            // asking to be rounded was reported unrounded.
            var computed = EvaluateCompute(field.Compute, ctx);
            // A zero divisor omits the field (PS-278), short-circuiting before the
            // transform stages so they never see the sentinel.
            if (IsComputeOmitted(computed)) return null;
            numVal = ApplyTransformStages(computed, field.Transform);
        }
        else
        {
            var (ok, v) = Helpers.ToFloat64(field.Value);
            numVal = ok ? v : 0;
        }

        // Guard for non-compute fields (ref-based)
        if (field.Guard != null && field.Compute == null)
            numVal = EvaluateGuard(field.Guard, numVal, ctx);

        return numVal;
    }

    static double EvaluateCompute(ComputeDef cd, DecodeContext ctx)
    {
        double a = ResolveOperand(cd.A, ctx);
        double b = ResolveOperand(cd.B, ctx);

        return cd.Op switch
        {
            // PS-278: a zero divisor omits the field. Throwing, as this used to,
            // abandoned the decode entirely where other implementations carried on.
            "div" => b == 0 ? ComputeOmitted : a / b,
            "mul" => a * b,
            "add" => a + b,
            "sub" => a - b,
            // PS-277 floored: the remainder takes the divisor's sign, so mod(a, 8)
            // stays in 0..7. C#'s native % truncates, giving -1 where the floored
            // answer is 2. PS-284: operands truncate toward zero first.
            "mod" => b == 0 ? ComputeOmitted : (double)FloorMod((long)a, (long)b),
            // PS-276 floored: rounds toward negative infinity, not toward zero.
            "idiv" => b == 0 ? ComputeOmitted : (double)FloorDiv((long)a, (long)b),
            _ => throw new InvalidOperationException($"Unknown compute op: {cd.Op}")
        };
    }

    static double ResolveOperand(string op, DecodeContext ctx)
    {
        if (op.StartsWith('$'))
        {
            var name = op[1..];
            if (ctx.Variables.TryGetValue(name, out var val))
            {
                var (ok, f) = Helpers.ToFloat64(val);
                if (ok) return f;
            }
            throw new InvalidOperationException($"Operand field not found: {op}");
        }
        return double.Parse(op, CultureInfo.InvariantCulture);
    }

    static bool EvaluateGuardConditions(GuardDef gd, DecodeContext ctx)
    {
        foreach (var cond in gd.When)
        {
            var fieldName = cond.Field.TrimStart('$');
            if (!ctx.Variables.TryGetValue(fieldName, out var fieldVal))
                return false;
            var (ok, fv) = Helpers.ToFloat64(fieldVal);
            if (!ok) return false;
            if (cond.Gt.HasValue && !(fv > cond.Gt.Value)) return false;
            if (cond.Gte.HasValue && !(fv >= cond.Gte.Value)) return false;
            if (cond.Lt.HasValue && !(fv < cond.Lt.Value)) return false;
            if (cond.Lte.HasValue && !(fv <= cond.Lte.Value)) return false;
            if (cond.Eq.HasValue && fv != cond.Eq.Value) return false;
            if (cond.Ne.HasValue && fv == cond.Ne.Value) return false;
        }
        return true;
    }

    static double EvaluateGuard(GuardDef gd, double value, DecodeContext ctx)
    {
        foreach (var cond in gd.When)
        {
            var fieldName = cond.Field.TrimStart('$');
            if (!ctx.Variables.TryGetValue(fieldName, out var fieldVal))
                return gd.ElseValue;

            var (ok, fv) = Helpers.ToFloat64(fieldVal);
            if (!ok) return gd.ElseValue;

            if (cond.Gt.HasValue && !(fv > cond.Gt.Value)) return gd.ElseValue;
            if (cond.Gte.HasValue && !(fv >= cond.Gte.Value)) return gd.ElseValue;
            if (cond.Lt.HasValue && !(fv < cond.Lt.Value)) return gd.ElseValue;
            if (cond.Lte.HasValue && !(fv <= cond.Lte.Value)) return gd.ElseValue;
            if (cond.Eq.HasValue && fv != cond.Eq.Value) return gd.ElseValue;
            if (cond.Ne.HasValue && fv == cond.Ne.Value) return gd.ElseValue;
        }
        return value;
    }

    static object? DecodeMatch(SchemaField field, DecodeContext ctx)
    {
        int matchValue;

        if (!string.IsNullOrEmpty(field.On))
        {
            var varName = field.On.TrimStart('$');
            if (!ctx.Variables.TryGetValue(varName, out var val))
                throw new InvalidOperationException($"Variable not found: ${varName}");
            var (_, iv) = Helpers.ToInt(val);
            matchValue = iv;
        }
        else
        {
            int length = field.Length > 0 ? field.Length : 1;
            var data = ctx.Read(length);
            matchValue = (int)Helpers.DecodeUint(data, ctx.Endian);
        }

        foreach (var c in field.Cases)
        {
            if (c.IsDefault)
                return DecodeFields(c.Fields, ctx, null);

            var caseVal = c.CaseValue;
            if (caseVal == null) continue;

            bool matched = caseVal switch
            {
                int iv => matchValue == iv,
                double dv => matchValue == (int)dv,
                List<object?> list => list.Any(item =>
                {
                    var (ok2, itemInt) = Helpers.ToInt(item);
                    return ok2 && matchValue == itemInt;
                }),
                _ => false
            };

            if (matched)
                return DecodeFields(c.Fields, ctx, null);
        }

        return null;
    }

    static Dictionary<string, object?> DecodeTLV(SchemaField field, DecodeContext ctx)
    {
        int tagSize = field.TagSize > 0 ? field.TagSize : 1;
        int lengthSize = field.LengthSize;
        bool merge = field.Merge ?? true;
        string unknownMode = field.UnknownMode ?? "skip";

        var result = new Dictionary<string, object?>();
        var channels = new List<Dictionary<string, object?>>();

        while (ctx.Remaining > 0)
        {
            var tag = new List<int>();

            if (field.TagFields.Count > 0)
            {
                var tagValues = new Dictionary<string, int>();
                foreach (var tf in field.TagFields)
                {
                    int len = tf.Length > 0 ? tf.Length : 1;
                    var data = ctx.Read(len);
                    int val = (int)Helpers.DecodeUint(data, ctx.Endian);
                    if (!string.IsNullOrEmpty(tf.Name))
                        tagValues[tf.Name] = val;
                }

                if (field.TagKey is List<object?> tkList)
                {
                    foreach (var k in tkList)
                        if (k is string ks && tagValues.TryGetValue(ks, out var tv))
                            tag.Add(tv);
                }
                else if (field.TagKey is string tks)
                {
                    if (tagValues.TryGetValue(tks, out var tv))
                        tag.Add(tv);
                }
                else if (field.TagFields.Count > 0 && !string.IsNullOrEmpty(field.TagFields[0].Name))
                {
                    if (tagValues.TryGetValue(field.TagFields[0].Name, out var tv))
                        tag.Add(tv);
                }
            }
            else
            {
                var data = ctx.Read(tagSize);
                tag.Add((int)Helpers.DecodeUint(data, ctx.Endian));
            }

            int dataLength = -1;
            if (lengthSize > 0)
            {
                var lenData = ctx.Read(lengthSize);
                dataLength = (int)Helpers.DecodeUint(lenData, ctx.Endian);
            }

            string? caseKey = FindTLVCaseKey(field.TLVCases, tag);

            if (caseKey != null && field.TLVCases != null)
            {
                var caseFields = field.TLVCases[caseKey];
                var caseResult = DecodeFields(caseFields, ctx, null);

                if (merge)
                {
                    foreach (var kv in caseResult)
                    {
                        if (result.ContainsKey(kv.Key))
                        {
                            if (result[kv.Key] is List<object?> arr)
                                arr.Add(kv.Value);
                            else
                                result[kv.Key] = new List<object?> { result[kv.Key], kv.Value };
                        }
                        else
                        {
                            result[kv.Key] = kv.Value;
                        }
                    }
                }
                else
                {
                    var entry = new Dictionary<string, object?> { ["tag"] = tag };
                    foreach (var kv in caseResult)
                        entry[kv.Key] = kv.Value;
                    channels.Add(entry);
                }
            }
            else
            {
                if (unknownMode == "error")
                    throw new InvalidOperationException($"Unknown TLV tag: [{string.Join(", ", tag)}]");
                if (dataLength >= 0)
                    ctx.Read(dataLength);
                else
                    break;
            }
        }

        if (!merge)
            result["channels"] = channels;

        return result;
    }

    static string? FindTLVCaseKey(Dictionary<string, List<SchemaField>>? cases, List<int> tag)
    {
        if (cases == null) return null;

        if (tag.Count == 1)
        {
            var key = tag[0].ToString();
            if (cases.ContainsKey(key)) return key;
        }

        var tagJson = $"[{string.Join(",", tag)}]";
        if (cases.ContainsKey(tagJson)) return tagJson;

        // Compare composite keys numerically. The rendering above has no space
        // after the comma while schemas are written "[1, 117]", so the exact
        // comparison missed every composite key. This also handles keys that
        // exclude a value with `!` or ignore a tag field with `*`, taking exact
        // keys first, then negated, then wildcard (PS-270).
        for (int wanted = 0; wanted <= 2; wanted++)
        {
            foreach (var candidate in cases.Keys)
            {
                var (matched, specificity) = MatchCompositeCaseKey(candidate, tag);
                if (matched && specificity == wanted) return candidate;
            }
        }

        return null;
    }

    /// <summary>Sentinel for a field that produced no value and is left out.</summary>
    internal static readonly object Omitted = new();

    static readonly System.Text.RegularExpressions.Regex NameFromPattern =
        new(@"\$\{(\w+)\}");

    /// <summary>Resolves a field's output key, honouring name_from (PS-265, PS-266).</summary>
    static string ResolveFieldName(SchemaField field, DecodeContext ctx)
    {
        if (string.IsNullOrEmpty(field.NameFrom)) return field.Name;

        var missing = new List<string>();
        var resolved = NameFromPattern.Replace(field.NameFrom, match =>
        {
            var reference = match.Groups[1].Value;
            if (!ctx.Variables.TryGetValue(reference, out var value))
            {
                missing.Add(reference);
                return string.Empty;
            }
            var (ok, intVal) = Helpers.ToInt(value);
            return ok ? intVal.ToString() : value?.ToString() ?? string.Empty;
        });
        if (missing.Count > 0)
            throw new InvalidOperationException(
                $"name_from for '{field.Name}' references {string.Join(", ", missing)}, " +
                "which has not been decoded");
        return resolved;
    }

    /// <summary>
    /// Matches a composite TLV case key against a tag, allowing `!value` to exclude
    /// and `*` to ignore a tag field. Specificity is 0 exact, 1 negated, 2 wildcard.
    /// </summary>
    static (bool, int) MatchCompositeCaseKey(string key, List<int> tag)
    {
        var trimmed = key.Trim();
        if (!trimmed.StartsWith("[")) return (false, 0);
        trimmed = trimmed.TrimStart('[').TrimEnd(']');
        var parts = trimmed.Split(',');
        if (parts.Length != tag.Count) return (false, 0);

        int specificity = 0;
        for (int i = 0; i < parts.Length; i++)
        {
            var part = parts[i].Trim().Trim('"', '\'');
            if (part == "*")
            {
                specificity = Math.Max(specificity, 2);
                continue;
            }
            bool negated = part.StartsWith("!");
            var text = negated ? part.Substring(1).Trim() : part;
            int expected;
            if (text.StartsWith("0x") || text.StartsWith("0X"))
            {
                if (!int.TryParse(text.Substring(2),
                        System.Globalization.NumberStyles.HexNumber, null, out expected))
                    return (false, 0);
            }
            else if (!int.TryParse(text, out expected))
            {
                return (false, 0);
            }
            if (negated)
            {
                specificity = Math.Max(specificity, 1);
                if (tag[i] == expected) return (false, 0);
            }
            else if (tag[i] != expected)
            {
                return (false, 0);
            }
        }
        return (true, specificity);
    }

    static List<object?> DecodeRepeat(SchemaField field, DecodeContext ctx, PayloadSchemaDefinition? schema)
    {
        int maxIterations = field.Max > 0 ? field.Max : 1000;
        int minIterations = field.Min;
        var result = new List<object?>();

        if (field.Count != null)
        {
            int count;
            if (field.Count is int ci) count = ci;
            else if (field.Count is string cs)
            {
                var varName = cs.TrimStart('$');
                if (ctx.Variables.TryGetValue(varName, out var val))
                {
                    var (_, iv) = Helpers.ToInt(val);
                    count = iv;
                }
                else throw new InvalidOperationException($"Repeat count variable not found: {cs}");
            }
            else count = 0;

            if (count > maxIterations) count = maxIterations;
            for (int i = 0; i < count; i++)
                result.Add(DecodeFields(field.Fields, ctx, schema));
        }
        else if (field.ByteLength != null)
        {
            int byteLength;
            if (field.ByteLength is int bli) byteLength = bli;
            else if (field.ByteLength is string bls)
            {
                var varName = bls.TrimStart('$');
                if (ctx.Variables.TryGetValue(varName, out var val))
                {
                    var (_, iv) = Helpers.ToInt(val);
                    byteLength = iv;
                }
                else throw new InvalidOperationException($"Repeat byte_length variable not found: {bls}");
            }
            else byteLength = 0;

            int endOffset = ctx.Offset + byteLength;
            int iterations = 0;
            while (ctx.Offset < endOffset && iterations < maxIterations)
            {
                result.Add(DecodeFields(field.Fields, ctx, schema));
                iterations++;
            }
        }
        else if (field.Until == "end")
        {
            int iterations = 0;
            while (ctx.Remaining > 0 && iterations < maxIterations)
            {
                result.Add(DecodeFields(field.Fields, ctx, schema));
                iterations++;
            }
        }
        else
        {
            throw new InvalidOperationException("Repeat field must specify count, byte_length, or until");
        }

        if (result.Count < minIterations)
            throw new InvalidOperationException($"Repeat produced {result.Count} elements, but minimum is {minIterations}");

        return result;
    }

    static void MergeTo(Dictionary<string, object?> target, Dictionary<string, object?> source)
    {
        foreach (var kv in source)
            target[kv.Key] = kv.Value;
    }
}

// Semantic output formatters

public static class SemanticFormatter
{
    /// <summary>
    /// Convert decoded data to SenML format (RFC 8428).
    /// </summary>
    public static List<Dictionary<string, object?>> ToSenML(
        PayloadSchemaDefinition schema, 
        Dictionary<string, object?> decoded)
    {
        var records = new List<Dictionary<string, object?>>();
        var fields = GetAllFields(schema);

        foreach (var kv in decoded)
        {
            if (kv.Key.StartsWith("_")) continue; // Skip internal fields
            
            var field = FindField(fields, kv.Key);
            var record = new Dictionary<string, object?>();
            
            // Use SenML name if defined, otherwise field name
            record["n"] = field?.Senml?.Name ?? kv.Key;
            
            // Set value based on type
            if (kv.Value is bool b)
                record["vb"] = b;
            else if (kv.Value is string s)
                record["vs"] = s;
            else if (kv.Value is byte[] bytes)
                record["vd"] = Convert.ToBase64String(bytes);
            else
                record["v"] = kv.Value;
            
            // Set unit (prefer SenML unit, fall back to field unit)
            var unit = field?.Senml?.Unit ?? field?.Unit;
            if (!string.IsNullOrEmpty(unit))
                record["u"] = unit;
            
            records.Add(record);
        }
        
        return records;
    }

    /// <summary>
    /// Convert decoded data to IPSO Smart Object format.
    /// </summary>
    public static Dictionary<string, object?> ToIPSO(
        PayloadSchemaDefinition schema, 
        Dictionary<string, object?> decoded)
    {
        var result = new Dictionary<string, object?>();
        var fields = GetAllFields(schema);

        foreach (var kv in decoded)
        {
            if (kv.Key.StartsWith("_")) continue; // Skip internal fields
            
            var field = FindField(fields, kv.Key);
            
            if (field?.Ipso != null)
            {
                // Use IPSO object ID as key
                var objKey = $"/{field.Ipso.Object}";
                if (field.Ipso.Instance > 0)
                    objKey += $"/{field.Ipso.Instance}";
                
                var obj = new Dictionary<string, object?> { ["value"] = kv.Value };
                
                var unit = field.Unit;
                if (!string.IsNullOrEmpty(unit))
                    obj["unit"] = unit;
                
                result[objKey] = obj;
            }
            else
            {
                // No IPSO mapping, use field name
                result[kv.Key] = kv.Value;
            }
        }
        
        return result;
    }

    /// <summary>
    /// Get field metadata including units for all decoded fields.
    /// </summary>
    public static Dictionary<string, FieldMetadata> GetMetadata(
        PayloadSchemaDefinition schema,
        Dictionary<string, object?> decoded)
    {
        var result = new Dictionary<string, FieldMetadata>();
        var fields = GetAllFields(schema);

        foreach (var kv in decoded)
        {
            if (kv.Key.StartsWith("_")) continue;
            
            var field = FindField(fields, kv.Key);
            if (field != null)
            {
                result[kv.Key] = new FieldMetadata
                {
                    Unit = field.Unit,
                    UNECE = field.UNECE,
                    ValidRange = field.ValidRange,
                    Resolution = field.Resolution,
                    SemanticId = field.SemanticId,
                    IpsoObject = field.Ipso?.Object,
                    SenmlName = field.Senml?.Name,
                    SenmlUnit = field.Senml?.Unit
                };
            }
        }
        
        return result;
    }

    static List<SchemaField> GetAllFields(PayloadSchemaDefinition schema)
    {
        var all = new List<SchemaField>();
        all.AddRange(schema.Fields);
        
        // Include fields from definitions
        if (schema.Definitions != null)
        {
            foreach (var def in schema.Definitions.Values)
                all.AddRange(def.Fields);
        }
        
        // Flatten nested fields
        return FlattenFields(all);
    }

    static List<SchemaField> FlattenFields(List<SchemaField> fields)
    {
        var result = new List<SchemaField>();
        foreach (var f in fields)
        {
            result.Add(f);
            if (f.Fields.Count > 0)
                result.AddRange(FlattenFields(f.Fields));
            if (f.ByteGroup.Count > 0)
                result.AddRange(FlattenFields(f.ByteGroup));
            if (f.Flagged != null)
            {
                foreach (var g in f.Flagged.Groups)
                    result.AddRange(FlattenFields(g.Fields));
            }
            foreach (var c in f.Cases)
                result.AddRange(FlattenFields(c.Fields));
        }
        return result;
    }

    static SchemaField? FindField(List<SchemaField> fields, string name)
    {
        return fields.FirstOrDefault(f => f.Name == name);
    }
}

public class FieldMetadata
{
    public string? Unit { get; set; }
    public string? UNECE { get; set; }
    public double[]? ValidRange { get; set; }
    public double? Resolution { get; set; }
    public string? SemanticId { get; set; }
    public int? IpsoObject { get; set; }
    public string? SenmlName { get; set; }
    public string? SenmlUnit { get; set; }
}
