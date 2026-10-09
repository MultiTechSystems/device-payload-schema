// Copyright (c) 2024-2026 Multitech Systems, Inc.
// SPDX-License-Identifier: MIT

package org.lora.schema;

import java.util.ArrayList;
import java.util.List;
import java.util.Map;
import java.util.regex.Pattern;

/**
 * 0.5.2 wave 6b: field-level rules (CR-2026-056, -059, -062, -065, -067). Each method
 * mirrors the function of the same purpose in tools/schema_interpreter.py:
 * {@code optional_errors}, {@code internal_name_errors}, {@code optional_field_size},
 * {@code resolve_length}, {@code _sentinel_hit}, {@code _range_omits} and
 * {@code _internal_encode_value}.
 *
 * <p>The schema rules are a walk over the raw document of their own, as {@link Wave6a}'s
 * are, because PS-433 applies to a field anywhere - a case body, a flagged group, a
 * trailer - and PS-404 to every field list, which the per-member checks in
 * {@code parseFields} see one at a time.
 */
final class Wave6b {
    private Wave6b() {}

    /** Names clause 7 reserves for interpreter metadata (PS-176, PS-433). */
    static final List<String> RESERVED_OUTPUT_NAMES = List.of("_meta", "_quality", "_warnings");

    private static final Pattern INTEGER = Pattern.compile("-?\\d+");

    /** Rejects the schema when a wave 6b schema rule fails, naming every failure. */
    static void checkSchema(Map<String, Object> raw) {
        List<String> errors = new ArrayList<>(optionalErrors(raw.get("fields"), "fields"));
        if (raw.get("ports") instanceof Map<?, ?> ports) {
            for (Map.Entry<?, ?> e : ports.entrySet()) {
                Object group = e.getValue() instanceof Map<?, ?> entry ? entry.get("fields") : e.getValue();
                errors.addAll(optionalErrors(group, "ports[" + e.getKey() + "].fields"));
            }
        }
        errors.addAll(internalNameErrors(raw));
        if (!errors.isEmpty()) {
            throw new SchemaException(String.join("; ", errors));
        }
    }

    /** PS-404: in any field list, every field after an optional field is optional too. */
    static List<String> optionalErrors(Object fields, String where) {
        List<String> errors = new ArrayList<>();
        if (!(fields instanceof List<?> list)) return errors;
        Object seen = null;
        for (int i = 0; i < list.size(); i++) {
            if (!(list.get(i) instanceof Map<?, ?> f)) continue;
            Object name = f.containsKey("name") ? f.get("name") : "?";
            if (Boolean.TRUE.equals(f.get("optional"))) {
                if (seen == null) seen = name;
            } else if (seen != null) {
                errors.add(where + "[" + i + "] (" + name + "): follows optional field '" + seen
                        + "', so it must be optional too (PS-404)");
            }
            for (Map.Entry<?, ?> e : f.entrySet()) {
                String key = String.valueOf(e.getKey());
                Object value = e.getValue();
                if (List.of("fields", "trailer", "default").contains(key) && value instanceof List) {
                    errors.addAll(optionalErrors(value, where + "[" + i + "]." + key));
                } else if ((key.equals("match") || key.equals("tlv")) && value instanceof Map<?, ?> construct) {
                    if (construct.get("cases") instanceof Map<?, ?> cases) {
                        for (Map.Entry<?, ?> c : cases.entrySet()) {
                            if (c.getValue() instanceof List) {
                                errors.addAll(optionalErrors(c.getValue(),
                                        where + "[" + i + "]." + key + "[" + c.getKey() + "]"));
                            }
                        }
                    }
                    if (construct.get("default") instanceof List) {
                        errors.addAll(optionalErrors(construct.get("default"),
                                where + "[" + i + "]." + key + ".default"));
                    }
                } else if (key.equals("flagged") && value instanceof Map<?, ?> flagged
                        && flagged.get("groups") instanceof List<?> groups) {
                    for (int g = 0; g < groups.size(); g++) {
                        if (groups.get(g) instanceof Map<?, ?> group) {
                            errors.addAll(optionalErrors(group.get("fields"),
                                    where + "[" + i + "].flagged[" + g + "]"));
                        }
                    }
                }
            }
        }
        return errors;
    }

    /** PS-433: no field may take a name reserved for interpreter metadata. */
    static List<String> internalNameErrors(Map<String, Object> raw) {
        List<String> errors = new ArrayList<>();
        walkNames(raw, "", errors);
        return errors;
    }

    private static void walkNames(Object node, String path, List<String> errors) {
        if (node instanceof Map<?, ?> map) {
            Object name = map.get("name");
            if (name != null && RESERVED_OUTPUT_NAMES.contains(name) && (map.containsKey("type") || map.containsKey("fields"))) {
                errors.add(path + ": '" + name + "' is reserved for interpreter metadata and "
                        + "cannot name a field (PS-433)");
            }
            for (Map.Entry<?, ?> e : map.entrySet()) {
                String key = String.valueOf(e.getKey());
                if (key.equals("test_vectors") || key.equals("definitions")) continue;
                walkNames(e.getValue(), path.isEmpty() ? key : path + "." + key, errors);
            }
        } else if (node instanceof List<?> list) {
            for (int i = 0; i < list.size(); i++) {
                walkNames(list.get(i), path + "[" + i + "]", errors);
            }
        }
    }

    /**
     * The per-field keys, checked on one list member: {@code sentinel} a non-empty list of
     * integers (PS-427), {@code out_of_range} omit or flag (PS-428), and a match's
     * discriminator source (PS-399, PS-414, PS-416).
     */
    static void checkField(Map<String, Object> fm) {
        String at = "Field '" + fm.get("name") + "': ";
        if (fm.containsKey("sentinel")) {
            boolean ok = fm.get("sentinel") instanceof List<?> list && !list.isEmpty();
            if (ok) {
                for (Object s : (List<?>) fm.get("sentinel")) {
                    if (!(s instanceof Integer || s instanceof Long || s instanceof java.math.BigInteger)) ok = false;
                }
            }
            if (!ok) {
                throw new SchemaException(at + "sentinel must be a non-empty list of integers (PS-427)");
            }
        }
        if (fm.get("out_of_range") != null && !List.of("flag", "omit").contains(fm.get("out_of_range"))) {
            throw new SchemaException(at + "out_of_range must be omit or flag (PS-428)");
        }
        if (fm.get("match") instanceof Map<?, ?> match) {
            int sources = 0;
            for (String key : List.of("field", "length", "remaining")) {
                if (match.containsKey(key)) sources++;
            }
            // PS-399: with both `field` and `length`, `field` won and the `length` byte
            // was left unread, misaligning every later field. PS-416 adds `remaining`.
            if (sources != 1) {
                throw new SchemaException(at + "a match must declare exactly one of 'field', "
                        + "'length' and 'remaining' (PS-399, PS-416)");
            }
            if (match.containsKey("remaining") && !Boolean.TRUE.equals(match.get("remaining"))) {
                throw new SchemaException(at + "a match's remaining must be true (PS-414)");
            }
        }
    }

    /**
     * A {@code length} naming a field (PS-464): the name, without any {@code $}, or null
     * for an integer, {@code remaining}, or no length at all.
     */
    static String lengthRef(Object spec) {
        if (!(spec instanceof String s)) return null;
        String text = s.trim();
        if (text.equalsIgnoreCase("remaining") || INTEGER.matcher(text).matches()) return null;
        return text.startsWith("$") ? text.substring(1) : text;
    }

    /** The byte count a named length resolves to (PS-464, PS-465). */
    static int resolveLength(Field field, DecodeContext ctx) {
        String name = field.getLengthRef();
        if (!ctx.getVariables().containsKey(name)) {
            throw new SchemaException.DecodeException("field '" + field.getName() + "': length names '"
                    + name + "', which is not a field decoded before this one (PS-465)");
        }
        Object value = ctx.getVariables().get(name);
        double d = value instanceof Number n && !(value instanceof Boolean) ? n.doubleValue() : Double.NaN;
        if (Double.isNaN(d) || d < 0 || d != Math.rint(d) || Double.isInfinite(d)) {
            throw new SchemaException.DecodeException("field '" + field.getName() + "': length names '"
                    + name + "', whose value " + value + " is not a byte count (PS-464)");
        }
        return (int) d;
    }

    /**
     * The bytes an optional field takes, or 0 where its size depends on the payload, in
     * which case it is present where at least one byte remains. An {@code object} is sized
     * as its fields sum (CR-2026-059).
     */
    static int optionalFieldSize(Field field, java.util.function.ToIntFunction<List<Field>> fixedSize) {
        if (field.getType() == FieldType.OBJECT) {
            return field.getFields() == null ? 0 : fixedSize.applyAsInt(field.getFields());
        }
        return fixedSize.applyAsInt(List.of(field));
    }

    /** PS-427: the integer read, before any modifier or encoding, is a sentinel. */
    static boolean sentinelHit(Field field, Object raw) {
        List<Long> sentinels = field.getSentinel();
        if (sentinels == null || sentinels.isEmpty()) return false;
        if (!(raw instanceof Long || raw instanceof Integer || raw instanceof Short || raw instanceof Byte)) {
            return false;
        }
        return sentinels.contains(((Number) raw).longValue());
    }

    /** PS-428: {@code out_of_range: omit} and a value outside {@code valid_range}. */
    static boolean rangeOmits(Field field, Object value) {
        double[] bounds = field.getOmitOutside();
        if (bounds == null || !(value instanceof Number n) || value instanceof Boolean) return false;
        double v = n.doubleValue();
        return !(bounds[0] <= v && v <= bounds[1]);
    }

    /**
     * What an encoder writes for an internal field that reads bytes (PS-434): its
     * {@code value} (PS-360); else the input's value under its name; else an error naming
     * it. This wrote 0, which reads back as a value the device never sent wherever the
     * field was not padding.
     */
    static Object internalEncodeValue(Field field, Map<String, Object> data) {
        if (field.getValue() != null) return field.getValue();
        String name = field.getName();
        if (data.containsKey(name)) return data.get(name);
        throw new SchemaException.EncodeException("internal field '" + name + "' reads payload bytes "
                + "and declares no value, and the input does not supply it (PS-434)");
    }
}
