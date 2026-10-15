package org.lora.schema;

import java.math.BigInteger;
import java.util.ArrayList;
import java.util.Collections;
import java.util.Comparator;
import java.util.HashMap;
import java.util.HashSet;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.Set;
import java.util.regex.Matcher;
import java.util.regex.Pattern;

/**
 * CR-2026-187 (PS-616 to PS-618): {@code bit_labels} on an unsigned integer or unsigned bit
 * range reports the labels of its set bits, as a list or, with {@code join}, as one text.
 * Mirrors bit_labels_error, decode_bit_labels and encode_bit_labels in
 * tools/schema_interpreter.py, in the spec prototype's text.
 */
final class BitLabels {
    private BitLabels() {}

    private static final List<String> KEYS = List.of("bit_labels", "join", "none", "order");
    /** The keys PS-616 rejects beside bit_labels, in the prototype's order. */
    private static final List<String> EXCLUDED = List.of("lookup", "add", "mult", "div",
            "transform", "polynomial", "time", "compute", "value", "encoding");
    private static final Map<String, Integer> WHOLE = new HashMap<>();
    static {
        for (int w : new int[] {8, 16, 24, 32, 64}) {
            WHOLE.put("u" + w, w);
            WHOLE.put("uint" + w, w);
        }
    }
    private static final Pattern RANGE = Pattern.compile("^u(\\d+)\\[(\\d+):(\\d+)\\]$");

    /** One entry: the key as written, its bit number (null where it is none), its label. */
    record Entry(Object key, Integer bit, Object label) {}

    static Integer width(Object type) {
        if (!(type instanceof String t)) return null;
        t = t.strip();
        if (WHOLE.containsKey(t)) return WHOLE.get(t);
        Matcher m = RANGE.matcher(t);
        if (m.matches()) {
            int lo = Integer.parseInt(m.group(2)), hi = Integer.parseInt(m.group(3));
            if (hi >= lo) return hi - lo + 1;
        }
        return null;
    }

    static List<Entry> entries(Object raw) {
        if (!(raw instanceof Map<?, ?> map)) return null;
        List<Entry> out = new ArrayList<>();
        for (Map.Entry<?, ?> e : map.entrySet()) {
            Object k = e.getKey();
            Integer bit = null;
            if (k instanceof Integer || k instanceof Long) {
                long n = ((Number) k).longValue();
                if (n >= Integer.MIN_VALUE && n <= Integer.MAX_VALUE) bit = (int) n;
            } else if (k instanceof String s && !s.isEmpty() && s.chars().allMatch(Character::isDigit)) {
                try {
                    bit = Integer.parseInt(s);
                    k = bit;
                } catch (NumberFormatException ignored) {
                    // a digit string too long for a bit number: reported as itself
                }
            }
            out.add(new Entry(k, bit, e.getValue()));
        }
        return out;
    }

    static boolean declared(Field field) {
        Map<String, Object> raw = field == null ? null : field.getRaw();
        return raw != null && raw.get("bit_labels") != null;
    }

    /** PS-616, PS-617 at load over one field mapping: the first error, in the prototype's order. */
    static void check(Map<String, Object> fm) {
        if (!fm.containsKey("type")) return;
        boolean any = false;
        for (String k : KEYS) any |= fm.containsKey(k);
        if (!any) return;
        String where = "Field '" + (fm.get("name") == null ? "None" : fm.get("name")) + "'";
        Object labels = fm.get("bit_labels");
        if (labels == null) {
            throw new SchemaException(where + ": join, none and order belong to bit_labels (PS-616)");
        }
        Integer width = width(fm.get("type"));
        if (width == null) {
            throw new SchemaException(where + ": bit_labels is declared on an unsigned integer or an "
                    + "unsigned bit range, not " + Meta.pyRepr(fm.get("type")) + " (PS-616)");
        }
        List<String> clash = new ArrayList<>();
        for (String k : EXCLUDED) if (fm.containsKey(k)) clash.add(k);
        if (!clash.isEmpty()) {
            throw new SchemaException(where + ": bit_labels reports labels, so "
                    + String.join(", ", clash) + " does not apply (PS-616)");
        }
        List<Entry> entries = entries(labels);
        if (entries == null || entries.isEmpty()) {
            throw new SchemaException(where + ": bit_labels maps bit numbers to labels (PS-616)");
        }
        Set<String> seen = new HashSet<>();
        for (Entry e : entries) {
            if (e.bit() == null || e.bit() < 0 || e.bit() >= width) {
                Object shown = e.bit() != null ? (Object) String.valueOf(e.bit()) : Meta.pyRepr(e.key());
                throw new SchemaException(where + ": bit " + shown + " is outside its " + width
                        + " bits (PS-617)");
            }
            if (!(e.label() instanceof String label)) {
                throw new SchemaException(where + ": the label of bit " + e.bit() + " is a string (PS-616)");
            }
            if (!seen.add(label)) {
                throw new SchemaException(where + ": two bits have the label " + Meta.pyRepr(label)
                        + " (PS-617)");
            }
        }
        Object join = fm.get("join");
        if (join != null) {
            if (!(join instanceof String j)) {
                throw new SchemaException(where + ": join is a string (PS-616)");
            }
            if (!j.isEmpty()) {
                for (String label : seen) {
                    if (label.contains(j)) {
                        throw new SchemaException(where + ": a label contains the join text "
                                + Meta.pyRepr(j) + " (PS-617)");
                    }
                }
            }
        }
        Object none = fm.get("none");
        if (none != null && (!(none instanceof String n) || seen.contains(n))) {
            throw new SchemaException(where + ": none is a string that no bit has as its label (PS-616)");
        }
        Object order = fm.getOrDefault("order", "ascending");
        if (!"ascending".equals(order) && !"descending".equals(order)) {
            throw new SchemaException(where + ": order is ascending or descending (PS-616)");
        }
    }

    /** (bit, label) in the field's declared order (PS-616). */
    static List<Entry> table(Field field) {
        List<Entry> entries = new ArrayList<>(entries(field.getRaw().get("bit_labels")));
        entries.sort(Comparator.comparingInt(e -> e.bit() == null ? Integer.MAX_VALUE : e.bit()));
        if ("descending".equals(field.getRaw().get("order"))) Collections.reverse(entries);
        return entries;
    }

    static BigInteger number(Object value) {
        if (value instanceof BigInteger b) return b.signum() >= 0 ? b : null;
        if (value instanceof Long || value instanceof Integer || value instanceof Short
                || value instanceof Byte) {
            long n = ((Number) value).longValue();
            return n >= 0 ? BigInteger.valueOf(n) : null;
        }
        if (value instanceof Number n) {
            double d = n.doubleValue();
            if (d >= 0 && d == Math.rint(d) && !Double.isInfinite(d)) {
                return new java.math.BigDecimal(d).toBigInteger();
            }
        }
        return null;
    }

    /** PS-616, PS-617: the labels of the set bits the mapping lists; an unlisted set bit warns. */
    static Object decode(Field field, BigInteger n, DecodeContext ctx) {
        List<Entry> table = table(field);
        Set<Integer> listed = new HashSet<>();
        for (Entry e : table) listed.add(e.bit());
        for (int bit = 0; bit < n.bitLength(); bit++) {
            if (n.testBit(bit) && !listed.contains(bit)) {
                ctx.addWarning("Field '" + field.getName() + "': bit " + bit
                        + " is set and has no label; it is not reported (PS-617)");
            }
        }
        List<Object> labels = new ArrayList<>();
        for (Entry e : table) {
            if (e.bit() != null && e.bit() >= 0 && n.testBit(e.bit())) labels.add(e.label());
        }
        Map<String, Object> raw = field.getRaw();
        if (labels.isEmpty() && raw.get("none") != null) labels.add(raw.get("none"));
        if (raw.containsKey("join")) {
            List<String> parts = new ArrayList<>();
            for (Object l : labels) parts.add(String.valueOf(l));
            return String.join(String.valueOf(raw.get("join")), parts);
        }
        return labels;
    }

    /**
     * PS-618: the number with the bits of the given labels set, every other bit 0. A list, or
     * the joined text where {@code join} is declared; an empty one or the {@code none} label is 0.
     */
    static long encode(Field field, Object value) {
        String name = field.getName();
        Map<String, Object> raw = field.getRaw();
        Map<String, Integer> byLabel = new LinkedHashMap<>();
        List<String> labels = new ArrayList<>();
        for (Entry e : table(field)) {
            if (e.label() instanceof String s) {
                byLabel.put(s, e.bit());
                labels.add(s);
            }
        }
        boolean hasJoin = raw.containsKey("join");
        Object none = raw.get("none");
        List<Object> items = new ArrayList<>();
        if (value instanceof String text && hasJoin) {
            String join = String.valueOf(raw.get("join"));
            if (text.isEmpty() || text.equals(none)) {
                // nothing set
            } else if (!join.isEmpty()) {
                items.addAll(List.of((Object[]) text.split(Pattern.quote(join), -1)));
            } else {
                labels.sort((a, b) -> b.length() - a.length());
                for (String a : labels) {
                    for (String b : labels) {
                        if (!a.equals(b) && b.startsWith(a)) {
                            throw new SchemaException.EncodeException("Field '" + name + "': with an "
                                    + "empty join, one label is a prefix of another, so its text "
                                    + "cannot be split; the schema decodes but is not encoded (PS-617)");
                        }
                    }
                }
                String rest = text;
                while (!rest.isEmpty()) {
                    String found = null;
                    for (String l : labels) {
                        if (rest.startsWith(l)) {
                            found = l;
                            break;
                        }
                    }
                    if (found == null) {
                        throw new SchemaException.EncodeException("Field '" + name + "': "
                                + Meta.pyRepr(rest) + " does not begin with a label (PS-618)");
                    }
                    items.add(found);
                    rest = rest.substring(found.length());
                }
            }
        } else if (value instanceof List<?> list) {
            for (Object item : list) {
                if (none != null && none.equals(item)) continue;
                items.add(item);
            }
        } else {
            throw new SchemaException.EncodeException("Field '" + name + "': "
                    + Encoder.messageValue(value) + " is not a list of labels"
                    + (hasJoin ? " or joined text" : "") + " (PS-618)");
        }
        long n = 0;
        for (Object item : items) {
            if (!(item instanceof String s) || !byLabel.containsKey(s)) {
                throw new SchemaException.EncodeException("Field '" + name + "': "
                        + Encoder.messageValue(item) + " is not one of its labels (PS-618)");
            }
            n |= 1L << byLabel.get(s);
        }
        return n;
    }
}
