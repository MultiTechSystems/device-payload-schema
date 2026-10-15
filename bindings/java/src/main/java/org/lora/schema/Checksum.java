package org.lora.schema;

import java.math.BigInteger;
import java.util.List;
import java.util.Map;

/**
 * CR-2026-174 (PS-585 to PS-587): a {@code checksum} on an unsigned integer field holds a
 * check value over the bytes before it. A decoder computes it and compares (PS-586); an
 * encoder computes it over the finished frame and writes it (PS-587). Mirrors
 * compute_checksum, checksum_field_errors, checksum_errors, checksum_mismatch and
 * checksum_unwritten_message in tools/schema_interpreter.py, in the spec prototype's text.
 */
final class Checksum {
    private Checksum() {}

    /** The algorithms, sorted as PS-585's message lists them, and the bits each needs. */
    private static final Map<String, Integer> ALGOS = new java.util.TreeMap<>(Map.of(
            "sum8", 8, "xor8", 8, "crc8/smbus", 8, "crc16/kermit", 16));
    private static final List<String> KEYS = List.of("algo", "seed", "from", "on_mismatch");
    /** The unsigned integer types a checksum may be declared on, by width in bits. */
    private static final Map<String, Integer> WIDTHS = Map.ofEntries(
            Map.entry("u8", 8), Map.entry("u16", 16), Map.entry("u24", 24),
            Map.entry("u32", 32), Map.entry("u64", 64),
            Map.entry("uint8", 8), Map.entry("uint16", 16), Map.entry("uint24", 24),
            Map.entry("uint32", 32), Map.entry("uint64", 64));

    /**
     * The four algorithms. The CRCs are the catalogue entries: over "123456789",
     * crc8/smbus is 0xF4 and crc16/kermit 0x2189.
     */
    static int compute(String algo, byte[] data, int from, int to, int seed) {
        switch (algo) {
            case "sum8": {
                int sum = seed;
                for (int i = from; i < to; i++) sum += data[i] & 0xFF;
                return sum & 0xFF;
            }
            case "xor8": {
                int value = seed;
                for (int i = from; i < to; i++) value ^= data[i] & 0xFF;
                return value;
            }
            case "crc8/smbus": {            // poly 0x07, init 0, not reflected, no final XOR
                int crc = 0;
                for (int i = from; i < to; i++) {
                    crc ^= data[i] & 0xFF;
                    for (int b = 0; b < 8; b++) {
                        crc = (crc & 0x80) != 0 ? ((crc << 1) ^ 0x07) & 0xFF : (crc << 1) & 0xFF;
                    }
                }
                return crc;
            }
            case "crc16/kermit": {          // poly 0x1021 reflected (0x8408), init 0, no final XOR
                int crc = 0;
                for (int i = from; i < to; i++) {
                    crc ^= data[i] & 0xFF;
                    for (int b = 0; b < 8; b++) {
                        crc = (crc & 1) != 0 ? (crc >>> 1) ^ 0x8408 : crc >>> 1;
                    }
                }
                return crc;
            }
            default:
                throw new IllegalArgumentException("Unknown checksum algorithm " + Meta.pyRepr(algo)
                        + " (PS-585)");
        }
    }

    static int compute(String algo, byte[] data) {
        return compute(algo, data, 0, data.length, 0);
    }

    // --- the declaration, read from a field that passed check() --------------------------

    static String algo(Map<String, Object> spec) {
        return String.valueOf(spec.get("algo"));
    }

    static int seed(Map<String, Object> spec) {
        return spec.get("seed") instanceof Number n ? n.intValue() : 0;
    }

    static int from(Map<String, Object> spec) {
        return spec.get("from") instanceof Number n ? n.intValue() : 0;
    }

    static boolean warns(Map<String, Object> spec) {
        return "warn".equals(spec.get("on_mismatch"));
    }

    /** The bytes a checksum field of {@code type} writes. */
    static int size(String type) {
        Integer bits = WIDTHS.get(type);
        return bits == null ? 1 : bits / 8;
    }

    // --- PS-585: load ---------------------------------------------------------------------

    /** PS-585 over the whole document, test vectors skipped; the first failure only. */
    static void check(Map<String, Object> raw) {
        String error = errors(raw, true);
        if (error != null) throw new SchemaException(error);
    }

    private static String errors(Object node, boolean top) {
        if (node instanceof List<?> list) {
            for (Object item : list) {
                String e = errors(item, false);
                if (e != null) return e;
            }
        } else if (node instanceof Map<?, ?> map) {
            if (map.containsKey("checksum") && (map.containsKey("type") || map.containsKey("name"))) {
                String e = fieldError(map);
                if (e != null) return e;
            }
            for (Map.Entry<?, ?> entry : map.entrySet()) {
                if (top && "test_vectors".equals(entry.getKey())) continue;
                if (entry.getValue() instanceof List || entry.getValue() instanceof Map) {
                    String e = errors(entry.getValue(), false);
                    if (e != null) return e;
                }
            }
        }
        return null;
    }

    private static boolean isInteger(Object v) {
        return v instanceof Integer || v instanceof Long || v instanceof Short
                || v instanceof Byte || v instanceof BigInteger;
    }

    /** checksum_field_errors: one field declaring {@code checksum}, or null where it holds. */
    static String fieldError(Map<?, ?> field) {
        Object rawName = field.get("name");
        String name = rawName == null ? "<unnamed>" : String.valueOf(rawName);
        if (!(field.get("checksum") instanceof Map<?, ?> spec)) {
            return "The checksum of '" + name + "' is a mapping, such as {algo: xor8} (PS-585)";
        }
        for (Object key : spec.keySet()) {
            String k = String.valueOf(key);
            if (!KEYS.contains(k) && !"description".equals(k) && !k.startsWith("x-")) {
                return "`" + k + "` is not a key of the checksum of '" + name + "' (PS-448)";
            }
        }
        Object type = field.get("type");
        Integer width = type instanceof String t ? WIDTHS.get(t) : null;
        if (width == null) {
            return "Field '" + name + "': a checksum is declared on an unsigned integer type "
                    + "(u8 to u64), not " + Meta.pyRepr(type) + " (PS-585)";
        }
        Object algo = spec.get("algo");
        if (!(algo instanceof String a) || !ALGOS.containsKey(a)) {
            return "The checksum of '" + name + "': `algo` is one of "
                    + String.join(", ", ALGOS.keySet()) + ", not " + Meta.pyRepr(algo) + " (PS-585)";
        }
        if (width < ALGOS.get(a)) {
            return "Field '" + name + "': " + type + " cannot hold a " + a + " result (PS-585)";
        }
        if (spec.containsKey("seed") && !"sum8".equals(a) && !"xor8".equals(a)) {
            return "The checksum of '" + name + "': `seed` applies to sum8 and xor8, not " + a
                    + " (PS-585)";
        }
        Object seed = spec.containsKey("seed") ? spec.get("seed") : Integer.valueOf(0);
        if (!isInteger(seed) || ((Number) seed).longValue() < 0 || ((Number) seed).longValue() > 255
                || (seed instanceof BigInteger)) {
            return "The checksum of '" + name + "': `seed` is a byte, 0 to 255 (PS-585)";
        }
        Object from = spec.containsKey("from") ? spec.get("from") : Integer.valueOf(0);
        if (!isInteger(from) || (from instanceof BigInteger b ? b.signum() < 0
                : ((Number) from).longValue() < 0)) {
            return "The checksum of '" + name + "': `from` is a byte offset, 0 or more (PS-585)";
        }
        Object onMismatch = spec.containsKey("on_mismatch") ? spec.get("on_mismatch") : "error";
        if (!"error".equals(onMismatch) && !"warn".equals(onMismatch)) {
            return "The checksum of '" + name + "': `on_mismatch` is `error` or `warn` (PS-586)";
        }
        return null;
    }

    // --- PS-586: decode -------------------------------------------------------------------

    /**
     * The declared checksum over {@code data[from:start]} against the value read: the
     * mismatch message, or null where they agree. A range starting after the field is an
     * error (PS-585).
     */
    static String mismatch(String name, Map<String, Object> spec, byte[] data, int start,
                           long carried) {
        int from = from(spec);
        if (from > start) {
            throw new SchemaException.DecodeException("Field '" + name + "': the checksum range "
                    + "starts at byte " + from + ", after the field at byte " + start + " (PS-585)");
        }
        int computed = compute(algo(spec), data, from, start, seed(spec));
        if (computed == carried) return null;
        return "Field '" + name + "': " + algo(spec) + " over bytes " + from + " to " + (start - 1)
                + " is 0x" + Integer.toHexString(computed).toUpperCase() + ", but the frame carries 0x"
                + Long.toHexString(carried).toUpperCase() + " (PS-586)";
    }

    // --- PS-587: encode -------------------------------------------------------------------

    /** A checksum whose covered range is not fully written when it is reached. */
    static String unwritten(String name) {
        return "Field '" + name + "': the bytes its checksum covers are not all written when it "
                + "is reached (PS-587)";
    }

    /**
     * Writes the checksum at {@code pos} over the finished {@code frame}'s bytes from its
     * {@code from} to the byte before it, in {@code little} or big-endian order.
     */
    static void fill(byte[] frame, int pos, Field field, boolean little) {
        Map<String, Object> spec = field.getChecksum();
        int from = from(spec);
        if (from > pos) throw new SchemaException.FieldEncodeException(unwritten(field.getName()));
        long value = compute(algo(spec), frame, from, pos, seed(spec));
        int size = size(field.getChecksumType());
        for (int i = 0; i < size; i++) {
            int shift = (little ? i : size - 1 - i) * 8;
            frame[pos + i] = (byte) (shift >= 64 ? 0 : (value >>> shift) & 0xFF);
        }
    }
}
