// Copyright (c) 2024-2026 Multitech Systems, Inc.
// SPDX-License-Identifier: MIT

package org.lora.schema;

import java.math.BigDecimal;
import java.math.BigInteger;
import java.math.RoundingMode;
import java.time.LocalDate;
import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Locale;
import java.util.Map;
import java.util.TreeSet;
import java.util.regex.Matcher;
import java.util.regex.Pattern;

/**
 * The interpreter output's {@code _meta} (Clause 7: PS-175, PS-177, PS-178, PS-180, PS-181,
 * PS-340 to PS-342, PS-371 to PS-376; CR-2026-088 PS-480/481; CR-2026-095 PS-489;
 * CR-2026-096 PS-490 to PS-497).
 *
 * <p>A port of the module functions in tools/schema_interpreter.py, one for one:
 * {@code meta_type}, {@code meta_declarations}, {@code meta_reference}, {@code field_meta},
 * {@code normalise_dev_eui}, {@code rx_time_seconds} and {@code meta_declaration_errors}.
 * Each works on the declaration as written (the raw YAML mapping, after {@code $ref}
 * splicing), because {@code _meta} reports what was declared: the typed {@link Field}
 * has already canonicalised the type and parsed a bit range into offsets.
 */
final class Meta {
    private Meta() { }

    private static final String VALUE_TOKEN = "${value}";

    /** Alias type names written as their canonical name in {@code _meta} (PS-493). */
    private static final Map<String, String> TYPE_ALIASES = new LinkedHashMap<>();
    static {
        for (int n : new int[] {8, 16, 24, 32, 64}) {
            TYPE_ALIASES.put("uint" + n, "u" + n);
            TYPE_ALIASES.put("int" + n, "s" + n);
            TYPE_ALIASES.put("i" + n, "s" + n);
        }
    }

    private static final Pattern DEV_EUI = Pattern.compile("[0-9a-f]{16}");
    private static final Pattern RECV_TIME = Pattern.compile(
            "(\\d{4})-(\\d{2})-(\\d{2})[T ](\\d{2}):(\\d{2}):(\\d{2})(?:\\.(\\d+))?"
            + "(Z|z|[+-]\\d{2}:?\\d{2})?");

    /**
     * A {@code _meta} entry's {@code type} (PS-493): the declared type, an alias by its
     * canonical name, a bit range as written. A named {@code tlv} with {@code merge: false}
     * is {@code tlv}. A field with no type is a schema error (PS-011, PS-441), so none is
     * invented: null leaves {@code type} out.
     */
    static String metaType(Map<?, ?> fieldDef) {
        Object declared = fieldDef.get("type");
        if (declared instanceof String s) {
            return TYPE_ALIASES.getOrDefault(s, s);
        }
        if (fieldDef.get("tlv") instanceof Map) {
            return "tlv";
        }
        return null;
    }

    /**
     * Reported name to declaration for one level of decoded output, first declaration
     * first (PS-371, PS-481 as amended). A construct that merges into its parent
     * contributes its fields at this level; internal and {@code name_from} fields have no
     * entry (PS-494, PS-492).
     */
    static Map<String, Map<?, ?>> declarations(Object fields) {
        Map<String, Map<?, ?>> found = new LinkedHashMap<>();
        visitDeclarations(fields, found);
        return found;
    }

    private static void visitDeclarations(Object items, Map<String, Map<?, ?>> found) {
        if (!(items instanceof List<?> list)) return;
        for (Object item : list) {
            if (!(item instanceof Map<?, ?> f)) continue;
            Object group = f.get("byte_group");
            if (group != null) {
                visitDeclarations(group instanceof Map<?, ?> g ? g.get("fields") : group, found);
            }
            if (f.get("flagged") instanceof Map<?, ?> flagged) {
                for (Object g : listOrEmpty(flagged.get("groups"))) {
                    if (g instanceof Map<?, ?> gm) visitDeclarations(gm.get("fields"), found);
                }
            }
            if (f.get("match") instanceof Map<?, ?> match) {
                if (match.get("cases") instanceof Map<?, ?> cases) {
                    for (Object body : cases.values()) {
                        if (body instanceof List) visitDeclarations(body, found);
                    }
                }
                if (match.get("default") instanceof List) visitDeclarations(match.get("default"), found);
                if (match.get("name") instanceof String label && !label.startsWith("_")) {
                    if (!found.containsKey(label)) {
                        Map<String, Object> pseudo = new LinkedHashMap<>();
                        pseudo.put("name", label);
                        pseudo.put("type", "u" + (8 * pyInt(match.containsKey("length")
                                ? match.get("length") : 1)));
                        found.put(label, pseudo);
                    }
                }
            }
            if ("match".equals(f.get("type")) && f.get("cases") instanceof Map<?, ?> cases) {
                for (Object body : cases.values()) {
                    if (body instanceof List) visitDeclarations(body, found);
                }
            }
            if (f.get("tlv") instanceof Map<?, ?> tlv && !Boolean.FALSE.equals(mergeOf(tlv))) {
                if (tlv.get("cases") instanceof Map<?, ?> cases) {
                    for (Object body : cases.values()) {
                        if (body instanceof List) visitDeclarations(body, found);
                    }
                }
                continue;
            }
            if (!(f.get("name") instanceof String name) || name.startsWith("_")
                    || truthy(f.get("name_from"))) {
                continue;
            }
            if (f.containsKey("match") && !f.containsKey("type")) continue;
            found.putIfAbsent(name, f);
        }
    }

    /** {@code tlv.get('merge', True)}: absent is true, a present value is as written. */
    private static Object mergeOf(Map<?, ?> tlv) {
        return tlv.containsKey("merge") ? tlv.get("merge") : Boolean.TRUE;
    }

    /**
     * A per-element {@code unit}, {@code ipso.instance} or {@code identity} as PS-375 lists
     * it: the repeat's index as {index, count}, or an element field as {field, values}
     * with a lookup's {@code default} last, marked {@code open} where that default carries
     * {@code ${value}} (PS-489). Anything else is itself.
     */
    static Object reference(Object value, Map<?, ?> repeat, boolean bare) {
        if (repeat == null || !(value instanceof String text)) return value;
        if (!(bare || text.startsWith("$"))) return value;
        String name = text.startsWith("$") ? text.substring(1) : text;
        Map<String, Object> listing = new LinkedHashMap<>();
        if (name.equals(repeat.get("index"))) {
            Object count = repeat.get("count");
            if (!isInteger(count)) count = repeat.get("max");
            listing.put("index", name);
            listing.put("count", count);
            return listing;
        }
        Map<?, ?> target = Map.of();
        for (Object member : listOrEmpty(repeat.get("fields"))) {
            if (member instanceof Map<?, ?> m && name.equals(m.get("name"))) {
                target = m;
                break;
            }
        }
        Object lookup = target.get("lookup");
        List<Object> values = new ArrayList<>();
        if (lookup instanceof Map<?, ?> mapping) {
            for (Map.Entry<?, ?> entry : mapping.entrySet()) {
                if (!"default".equals(entry.getKey())) values.add(entry.getValue());
            }
            if (mapping.containsKey("default")) values.add(mapping.get("default"));
        } else if (lookup instanceof List<?> sequence) {
            values.addAll(sequence);
        } else if (target.containsKey("value")) {
            values.add(target.get("value"));
        }
        listing.put("field", name);
        listing.put("values", values);
        if (lookup instanceof Map<?, ?> mapping && mapping.get("default") instanceof String d
                && d.contains(VALUE_TOKEN)) {
            listing.put("open", Boolean.TRUE);                      // PS-489
        }
        return listing;
    }

    /**
     * One {@code _meta.fields} entry, from one declaration (PS-178, PS-493, PS-480, PS-371,
     * PS-481). {@code repeat} is the repeat whose elements the field belongs to, or null.
     */
    static Map<String, Object> fieldMeta(Map<?, ?> fieldDef, Map<?, ?> repeat) {
        Map<String, Object> entry = new LinkedHashMap<>();
        String declaredType = metaType(fieldDef);
        if (declaredType != null) entry.put("type", declaredType);
        Map<?, ?> senml = fieldDef.get("senml") instanceof Map<?, ?> s ? s : Map.of();
        Object unit = truthy(senml.get("unit")) ? senml.get("unit") : fieldDef.get("unit");
        if (unit != null) entry.put("unit", reference(unit, repeat, false));   // PS-178
        if (fieldDef.get("ipso") instanceof Map<?, ?> ipso && ipso.containsKey("object")) {
            Map<String, Object> out = new LinkedHashMap<>();
            out.put("object", ipso.get("object"));
            out.put("instance", reference(ipso.containsKey("instance") ? ipso.get("instance") : 0,
                    repeat, false));
            out.put("resource", ipso.containsKey("resource") ? ipso.get("resource") : 5700);
            entry.put("ipso", out);
        }
        if (senml.get("name") instanceof String senmlName) {
            Map<String, Object> out = new LinkedHashMap<>();
            out.put("name", senmlName);                             // PS-480; a template, PS-373
            entry.put("senml", out);
        }
        if (truthy(fieldDef.get("description"))) entry.put("description", fieldDef.get("description"));
        if ("repeat".equals(declaredType)) {
            Map<String, Object> elements = new LinkedHashMap<>();
            for (Map.Entry<String, Map<?, ?>> member : declarations(fieldDef.get("fields")).entrySet()) {
                elements.put(member.getKey(), fieldMeta(member.getValue(), fieldDef));   // PS-371
            }
            entry.put("elements", elements);
            if (fieldDef.containsKey("identity")) {
                entry.put("identity", reference(fieldDef.get("identity"), fieldDef, true));
            }
        } else if ("object".equals(declaredType)) {
            Map<String, Object> members = new LinkedHashMap<>();
            for (Map.Entry<String, Map<?, ?>> member : declarations(fieldDef.get("fields")).entrySet()) {
                members.put(member.getKey(), fieldMeta(member.getValue(), null));        // PS-481
            }
            entry.put("fields", members);
        }
        return entry;
    }

    /** PS-496: 16 lower-case hex digits, {@code -}, {@code :} and spaces removed; else null. */
    static String normaliseDevEui(Object text) {
        if (!(text instanceof String s)) return null;
        String eui = s.replaceAll("[-: ]", "").toLowerCase(Locale.ROOT);
        return DEV_EUI.matcher(eui).matches() ? eui : null;
    }

    /**
     * PS-177, PS-495: {@code recvTime} as numeric Unix seconds in UTC, keeping its
     * milliseconds; null where it is neither an ISO 8601 time nor a number. A whole number
     * of seconds is a Long; otherwise the fraction, rounded half to even at three digits in
     * decimal, is read with the seconds as one decimal, so every implementation lands on
     * the same double.
     */
    static Object rxTimeSeconds(Object recvTime) {
        if (recvTime instanceof Boolean) return null;
        if (recvTime instanceof Number) return recvTime;
        if (!(recvTime instanceof String text)) return null;
        Matcher m = RECV_TIME.matcher(text.strip());
        if (!m.matches()) return null;
        int year = Integer.parseInt(m.group(1));
        int month = Integer.parseInt(m.group(2));
        long day = Long.parseLong(m.group(3));
        long hour = Long.parseLong(m.group(4));
        long minute = Long.parseLong(m.group(5));
        long second = Long.parseLong(m.group(6));
        // calendar.timegm validates the year and month (through datetime.date) and adds
        // the day, hour, minute and second arithmetically - as this does.
        if (year < 1 || month < 1 || month > 12) return null;
        long days = LocalDate.of(year, month, 1).toEpochDay() + day - 1;
        long seconds = days * 86400 + hour * 3600 + minute * 60 + second;
        String zone = m.group(8);
        if (zone != null && !zone.equals("Z") && !zone.equals("z")) {
            int sign = zone.charAt(0) == '-' ? -1 : 1;
            String digits = zone.substring(1).replace(":", "");
            seconds -= sign * (Long.parseLong(digits.substring(0, 2)) * 3600
                    + Long.parseLong(digits.substring(2)) * 60);
        }
        String fraction = m.group(7) == null ? "0" : m.group(7);
        BigDecimal exact = BigDecimal.valueOf(seconds)
                .add(new BigDecimal("0." + fraction).setScale(3, RoundingMode.HALF_EVEN));
        if (exact.signum() == 0 || exact.stripTrailingZeros().scale() <= 0) {
            return exact.longValueExact();
        }
        return Double.parseDouble(exact.toPlainString());
    }

    /** What PS-491 requires one reported name's declarations to agree on, as Python's repr. */
    private static Map<String, String> agreementKey(Map<?, ?> fieldDef) {
        Map<String, String> key = new LinkedHashMap<>();
        key.put("unit", pyRepr(fieldDef.get("unit")));
        Object senml = fieldDef.get("senml");
        if (senml instanceof Map<?, ?> s) {
            TreeSet<String> pairs = new TreeSet<>();
            for (Map.Entry<?, ?> e : s.entrySet()) {
                pairs.add(pyRepr(pyStr(e.getKey())) + ", " + pyRepr(pyStr(e.getValue())));
            }
            key.put("senml", "tuple" + pairs);
        } else {
            key.put("senml", pyRepr(senml));
        }
        Object ipso = fieldDef.get("ipso");
        if (ipso instanceof Map<?, ?> i) {
            key.put("ipso", "(" + pyRepr(i.get("object")) + ", "
                    + pyRepr(i.containsKey("instance") ? i.get("instance") : 0) + ", "
                    + pyRepr(i.containsKey("resource") ? i.get("resource") : 5700) + ")");
        } else {
            key.put("ipso", pyRepr(ipso));
        }
        return key;
    }

    /**
     * PS-491: the declarations of one reported name in one field list must agree on
     * {@code unit}, {@code senml} and {@code ipso}. A field list is the top level, each
     * port's, and each object's or repeat's members; match and tlv cases, flagged groups
     * and byte_groups merge into theirs. A merge:false tlv's cases together are one list.
     */
    static List<String> declarationErrors(Map<?, ?> schema) {
        List<String> errors = new ArrayList<>();
        if (schema == null) return errors;
        if (schema.get("fields") instanceof List<?> fields) check(fields, "fields", errors);
        if (schema.get("ports") instanceof Map<?, ?> ports) {
            for (Map.Entry<?, ?> entry : ports.entrySet()) {
                Object group = entry.getValue() instanceof Map<?, ?> e ? e.get("fields") : entry.getValue();
                if (group instanceof List<?> list) check(list, "port " + entry.getKey(), errors);
            }
        }
        return errors;
    }

    private static void check(List<?> fields, String where, List<String> errors) {
        Map<String, List<Map<?, ?>>> seen = new LinkedHashMap<>();
        visitAgreement(fields, where, seen, errors);
        for (Map.Entry<String, List<Map<?, ?>>> entry : seen.entrySet()) {
            List<Map<String, String>> keys = new ArrayList<>();
            for (Map<?, ?> d : entry.getValue()) keys.add(agreementKey(d));
            List<String> differing = new ArrayList<>();
            for (String k : List.of("unit", "senml", "ipso")) {
                TreeSet<String> distinct = new TreeSet<>();
                for (Map<String, String> key : keys) distinct.add(key.get(k));
                if (distinct.size() > 1) differing.add(k);
            }
            if (!differing.isEmpty()) {
                errors.add(String.format("Field '%s' is declared %d times in %s and its declarations "
                        + "differ in %s; the declarations of one reported name must agree on unit, "
                        + "senml and ipso (PS-491)", entry.getKey(), entry.getValue().size(), where,
                        String.join(", ", differing)));
            }
        }
    }

    private static void visitAgreement(Object items, String where, Map<String, List<Map<?, ?>>> seen,
                                       List<String> errors) {
        if (!(items instanceof List<?> list)) return;
        for (Object item : list) {
            if (!(item instanceof Map<?, ?> f)) continue;
            Object group = f.get("byte_group");
            if (group != null) {
                visitAgreement(group instanceof Map<?, ?> g ? g.get("fields") : group, where, seen, errors);
            }
            if (f.get("flagged") instanceof Map<?, ?> flagged) {
                for (Object g : listOrEmpty(flagged.get("groups"))) {
                    if (g instanceof Map<?, ?> gm) visitAgreement(gm.get("fields"), where, seen, errors);
                }
            }
            if (f.get("match") instanceof Map<?, ?> match) {
                if (match.get("cases") instanceof Map<?, ?> cases) {
                    for (Object body : cases.values()) {
                        if (body instanceof List) visitAgreement(body, where, seen, errors);
                    }
                }
                if (match.get("default") instanceof List) visitAgreement(match.get("default"), where, seen, errors);
            }
            if ("match".equals(f.get("type")) && f.get("cases") instanceof Map<?, ?> cases) {
                for (Object body : cases.values()) {
                    if (body instanceof List) visitAgreement(body, where, seen, errors);
                }
            }
            if (f.get("tlv") instanceof Map<?, ?> tlv) {
                List<Object> bodies = new ArrayList<>();
                if (tlv.get("cases") instanceof Map<?, ?> cases) {
                    for (Object body : cases.values()) {
                        if (body instanceof List) bodies.add(body);
                    }
                }
                if (Boolean.FALSE.equals(mergeOf(tlv))) {
                    List<Object> members = new ArrayList<>();
                    for (Object body : bodies) members.addAll((List<?>) body);
                    check(members, where + "/" + nameOr(f, "tlv"), errors);
                } else {
                    for (Object body : bodies) visitAgreement(body, where, seen, errors);
                }
                continue;
            }
            Object type = f.get("type");
            if (("object".equals(type) || "repeat".equals(type)) && f.get("fields") instanceof List<?> members) {
                check(members, where + "/" + nameOr(f, "?"), errors);
            }
            if (f.get("name") instanceof String name && !name.startsWith("_")
                    && !truthy(f.get("name_from"))) {
                seen.computeIfAbsent(name, k -> new ArrayList<>()).add(f);
            }
        }
    }

    /** {@code f.get('name', fallback)} as Python's {@code %s} writes it. */
    private static String nameOr(Map<?, ?> f, String fallback) {
        return f.containsKey("name") ? pyStr(f.get("name")) : fallback;
    }

    private static List<?> listOrEmpty(Object value) {
        return value instanceof List<?> list ? list : List.of();
    }

    private static boolean isInteger(Object value) {
        return value instanceof Integer || value instanceof Long || value instanceof Short
                || value instanceof Byte || value instanceof BigInteger;
    }

    /** Python's {@code int(x)} for a match's {@code length}. */
    private static long pyInt(Object value) {
        if (value instanceof Number n) return n.longValue();
        if (value instanceof String s) return Long.parseLong(s.strip());
        throw new SchemaException("match length " + value + " is not an integer");
    }

    /** Python truthiness for the values a YAML document holds. */
    static boolean truthy(Object value) {
        if (value == null) return false;
        if (value instanceof Boolean b) return b;
        if (value instanceof String s) return !s.isEmpty();
        if (value instanceof Number n) return n.doubleValue() != 0;
        if (value instanceof Map<?, ?> m) return !m.isEmpty();
        if (value instanceof List<?> l) return !l.isEmpty();
        return true;
    }

    /** Python's {@code str()} of a YAML value. */
    static String pyStr(Object value) {
        if (value instanceof String s) return s;
        return pyRepr(value);
    }

    /** Python's {@code repr()} of a YAML value, which is how the messages quote one. */
    static String pyRepr(Object value) {
        if (value == null) return "None";
        if (value instanceof Boolean b) return b ? "True" : "False";
        if (value instanceof Double || value instanceof Float) {
            double d = ((Number) value).doubleValue();
            if (Double.isNaN(d)) return "nan";
            if (Double.isInfinite(d)) return d > 0 ? "inf" : "-inf";
            return String.valueOf(d);
        }
        if (value instanceof Number n) return n.toString();
        if (value instanceof String s) {
            char quote = s.indexOf('\'') >= 0 && s.indexOf('"') < 0 ? '"' : '\'';
            StringBuilder out = new StringBuilder().append(quote);
            for (int i = 0; i < s.length(); i++) {
                char c = s.charAt(i);
                if (c == '\\') out.append("\\\\");
                else if (c == quote) out.append('\\').append(c);
                else if (c == '\n') out.append("\\n");
                else if (c == '\r') out.append("\\r");
                else if (c == '\t') out.append("\\t");
                else if (c < 0x20 || c == 0x7f) out.append(String.format("\\x%02x", (int) c));
                else out.append(c);
            }
            return out.append(quote).toString();
        }
        if (value instanceof List<?> list) {
            List<String> parts = new ArrayList<>();
            for (Object item : list) parts.add(pyRepr(item));
            return "[" + String.join(", ", parts) + "]";
        }
        if (value instanceof Map<?, ?> map) {
            List<String> parts = new ArrayList<>();
            for (Map.Entry<?, ?> e : map.entrySet()) parts.add(pyRepr(e.getKey()) + ": " + pyRepr(e.getValue()));
            return "{" + String.join(", ", parts) + "}";
        }
        return String.valueOf(value);
    }
}
