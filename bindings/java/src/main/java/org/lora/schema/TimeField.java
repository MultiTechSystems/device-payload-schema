package org.lora.schema;

import java.math.BigDecimal;
import java.math.BigInteger;
import java.math.RoundingMode;
import java.util.ArrayList;
import java.util.HashMap;
import java.util.HashSet;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.Set;
import java.util.TreeSet;
import java.util.regex.Matcher;
import java.util.regex.Pattern;

/**
 * CR-2026-186 (PS-610 to PS-615): a numeric field may declare {@code time}, and is then
 * reported as an instant in UTC in place of its number. The value after the field's modifiers
 * counts seconds since {@code epoch} (1970-01-01T00:00:00Z where absent); a {@code type:
 * number} field may derive the instant from {@code parts} decoded earlier (PS-614). An
 * encoder parses the value back in its format (PS-615). Instants are exact decimals, so a
 * fractional value does not drift; a millisecond count, {@code fraction} and %L truncate.
 * Mirrors the CR-2026-186 block of tools/schema_interpreter.py function by function
 * (time_field_error, time_errors, format_time, time_from_parts, time_readback_error,
 * parse_time, time_input_seconds, expand_time_parts); the texts are the spec prototype's.
 */
final class TimeField {
    private TimeField() {}

    static final List<String> TIME_PARTS = List.of("year", "month", "day", "hour", "minute", "second");
    private static final Map<String, List<String>> TIME_FORMATS = new LinkedHashMap<>();
    static {
        TIME_FORMATS.put("unix", List.of("since", "milliseconds"));
        TIME_FORMATS.put("iso8601", List.of("fraction"));
        TIME_FORMATS.put("calendar", List.of("month_labels", "keys"));
        TIME_FORMATS.put("pattern", List.of("pattern"));
    }
    private static final List<String> TIME_COMMON = List.of("format", "epoch", "parts");
    private static final List<String> MONTH_ABBR = List.of("Jan", "Feb", "Mar", "Apr", "May", "Jun",
            "Jul", "Aug", "Sep", "Oct", "Nov", "Dec");
    private static final List<String> DAY_ABBR = List.of("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun");
    private static final Pattern RFC3339 = Pattern.compile(
            "(\\d{4})-(\\d{2})-(\\d{2})T(\\d{2}):(\\d{2}):(\\d{2})(\\.\\d+)?(Z|\\+00:00)");
    private static final Pattern EPOCH = Pattern.compile(
            "\\d{4}-\\d{2}-\\d{2}T\\d{2}:\\d{2}:\\d{2}(\\.\\d+)?Z");
    private static final Pattern PATTERN_TOKEN = Pattern.compile("%(.)", Pattern.DOTALL);
    private static final Pattern BIT_RANGE = Pattern.compile("^[us](\\d+)\\[(\\d+):(\\d+)\\]$");
    /** The integer types of INTEGER_TYPE_INFO. */
    private static final Set<String> INTEGER_TYPES = Set.of(
            "u8", "uint8", "u16", "uint16", "u24", "uint24", "u32", "uint32", "u64", "uint64",
            "u32le16", "s32le16", "u32be16le", "s32be16le",
            "s8", "i8", "int8", "s16", "i16", "int16", "s24", "i24", "int24",
            "s32", "i32", "int32", "s64", "i64", "int64");
    /** The numeric types besides the integers and bit ranges (PS-610). */
    private static final Set<String> NUMERIC_TYPES = Set.of("number", "integer", "f16", "bf16", "f32",
            "f64", "f32le16", "f32be16le", "udec", "sdec", "uflt16", "sflt16", "sflt24");
    /** The types of a part field that reads no bytes (PS-615). */
    private static final Set<String> UNWRITTEN_PART_TYPES = Set.of("number", "integer", "string", "boolean");

    // --- the declaration -------------------------------------------------------------------

    /** The field's {@code time} mapping, or null where it declares none (PS-610). */
    static Map<?, ?> spec(Field field) {
        return field == null ? null : spec(field.getRaw());
    }

    static Map<?, ?> spec(Map<?, ?> raw) {
        return raw != null && raw.get("time") instanceof Map<?, ?> m ? m : null;
    }

    static boolean isTimeField(Field field) {
        return spec(field) != null;
    }

    static boolean hasParts(Field field) {
        Map<?, ?> spec = spec(field);
        return spec != null && spec.containsKey("parts");
    }

    // --- civil date arithmetic (proleptic Gregorian, Howard Hinnant's algorithms) -----------

    static long daysFromCivil(long y, long m, long d) {
        y -= m <= 2 ? 1 : 0;
        long era = Math.floorDiv(y, 400);
        long yoe = y - era * 400;
        long doy = (153 * (m + (m > 2 ? -3 : 9)) + 2) / 5 + d - 1;
        long doe = yoe * 365 + yoe / 4 - yoe / 100 + doy;
        return era * 146097 + doe - 719468;
    }

    static long[] civilFromDays(long z) {
        z += 719468;
        long era = Math.floorDiv(z, 146097);
        long doe = z - era * 146097;
        long yoe = (doe - doe / 1460 + doe / 36524 - doe / 146096) / 365;
        long doy = doe - (365 * yoe + yoe / 4 - yoe / 100);
        long mp = (5 * doy + 2) / 153;
        long d = doy - (153 * mp + 2) / 5 + 1;
        long m = mp + (mp < 10 ? 3 : -9);
        return new long[] {yoe + era * 400 + (m <= 2 ? 1 : 0), m, d};
    }

    private static boolean leap(long y) {
        return y % 4 == 0 && (y % 100 != 0 || y % 400 == 0);
    }

    private static long monthLength(long y, long m) {
        if (m == 2) return leap(y) ? 29 : 28;
        return (m == 4 || m == 6 || m == 9 || m == 11) ? 30 : 31;
    }

    static boolean validCivil(long y, long mo, long d, long h, long mi, long s) {
        if (!(1 <= y && y <= 9999 && 1 <= mo && mo <= 12 && 0 <= h && h <= 23 && 0 <= mi && mi <= 59
                && 0 <= s && s <= 59)) {
            return false;
        }
        return 1 <= d && d <= monthLength(y, mo);
    }

    static long civilSeconds(long y, long mo, long d, long h, long mi, long s) {
        return daysFromCivil(y, mo, d) * 86400 + h * 3600 + mi * 60 + s;
    }

    /**
     * An {@code epoch} or {@code since}: an RFC 3339 date-time in UTC with a Z designator, as
     * exact seconds since 1970 (PS-355); null where it is not one.
     */
    static BigDecimal timeInstant(Object text) {
        if (!(text instanceof String s) || !EPOCH.matcher(s).matches()) return null;
        return parseRfc3339(s);
    }

    private static BigDecimal parseRfc3339(Object text) {
        if (!(text instanceof String s)) return null;
        Matcher m = RFC3339.matcher(s);
        if (!m.matches()) return null;
        long[] p = new long[6];
        for (int i = 0; i < 6; i++) p[i] = Long.parseLong(m.group(i + 1));
        if (!validCivil(p[0], p[1], p[2], p[3], p[4], p[5])) return null;
        BigDecimal out = BigDecimal.valueOf(civilSeconds(p[0], p[1], p[2], p[3], p[4], p[5]));
        return m.group(7) != null ? out.add(new BigDecimal("0" + m.group(7))) : out;
    }

    private static BigDecimal epochOf(Map<?, ?> spec, String key) {
        return spec.containsKey(key) ? timeInstant(spec.get(key)) : BigDecimal.ZERO;
    }

    // --- load (PS-610 to PS-614) -------------------------------------------------------------

    private static boolean numeric(Map<?, ?> fm) {
        Object type = fm.get("type");
        String t = type == null ? "" : String.valueOf(type);
        return NUMERIC_TYPES.contains(t) || INTEGER_TYPES.contains(t) || BIT_RANGE.matcher(t).matches();
    }

    private static boolean computes(Map<?, ?> fm) {
        for (String k : new String[] {"value", "compute", "ref", "polynomial"}) {
            if (fm.containsKey(k)) return true;
        }
        return false;
    }

    private static boolean isInt(Object v) {
        return v instanceof Integer || v instanceof Long || v instanceof Short || v instanceof Byte
                || v instanceof BigInteger;
    }

    /** time_field_error: PS-610 to PS-614 for one field declaring {@code time}; null where valid. */
    static String fieldError(Map<?, ?> fm) {
        String where = "Field '" + Meta.pyStr(fm.get("name")) + "'";
        if (!(fm.get("time") instanceof Map<?, ?> spec) || !(spec.get("format") instanceof String fmt)
                || !TIME_FORMATS.containsKey(fmt)) {
            return where + ": `time` declares a format of unix, iso8601, calendar or pattern (PS-610)";
        }
        Set<String> extra = new TreeSet<>();
        for (Object k : spec.keySet()) {
            String key = String.valueOf(k);
            if (!TIME_COMMON.contains(key) && !TIME_FORMATS.get(fmt).contains(key)) extra.add(key);
        }
        if (!extra.isEmpty()) {
            return where + ": " + String.join(", ", extra) + " does not belong to the " + fmt + " format (PS-610)";
        }
        if (!numeric(fm) || fm.containsKey("lookup")) {
            return where + ": `time` is declared on a numeric field, not " + Meta.pyRepr(fm.get("type"))
                    + " (PS-610)";
        }
        for (String key : new String[] {"epoch", "since"}) {
            if (spec.containsKey(key) && timeInstant(spec.get(key)) == null) {
                return where + ": `" + key + "` " + Meta.pyRepr(spec.get(key)) + " is not an RFC 3339 "
                        + "date-time in UTC with a Z designator (PS-355, PS-610)";
            }
        }
        if (spec.containsKey("milliseconds") && !(spec.get("milliseconds") instanceof Boolean)) {
            return where + ": `milliseconds` is true or false (PS-611)";
        }
        Object fraction = spec.get("fraction");
        if (fraction != null && (!isInt(fraction) || ((Number) fraction).longValue() < 1
                || ((Number) fraction).longValue() > 9)) {
            return where + ": `fraction` is an integer from 1 to 9 (PS-611)";
        }
        Object labels = spec.get("month_labels");
        if (labels != null) {
            boolean ok = labels instanceof List<?> l && l.size() == 12;
            if (ok) for (Object x : (List<?>) labels) ok &= x instanceof String;
            if (!ok) return where + ": month_labels are exactly twelve strings (PS-411, PS-612)";
        }
        Object keys = spec.get("keys");
        if (keys != null) {
            boolean ok = keys instanceof Map<?, ?>;
            if (ok) {
                for (Map.Entry<?, ?> e : ((Map<?, ?>) keys).entrySet()) {
                    ok &= TIME_PARTS.contains(String.valueOf(e.getKey())) && e.getKey() instanceof String
                            && e.getValue() instanceof String v && !v.isEmpty();
                }
            }
            if (!ok) return where + ": keys map part names to member names (PS-412)";
            Set<String> names = new HashSet<>();
            for (String part : TIME_PARTS) {
                Object renamed = ((Map<?, ?>) keys).get(part);
                names.add(renamed != null ? (String) renamed : part);
            }
            if (names.size() != TIME_PARTS.size()) return where + ": keys map two parts to one name (PS-412)";
        }
        if (fmt.equals("pattern")) {
            if (!(spec.get("pattern") instanceof String pattern)) {
                return where + ": a pattern format declares `pattern` (PS-613)";
            }
            List<String> tokens = new ArrayList<>();
            Matcher m = PATTERN_TOKEN.matcher(pattern);
            while (m.find()) tokens.add(m.group(1));
            for (String token : tokens) {
                if (!"YmdHMSLba%".contains(token)) {
                    return where + ": %" + token + " is not a pattern directive (PS-613)";
                }
            }
            long percents = pattern.chars().filter(c -> c == '%').count();
            long literal = tokens.stream().filter("%"::equals).count();
            if (percents != 2 * literal + tokens.size() - literal) {
                return where + ": a pattern ends with a lone % (PS-613)";
            }
        }
        Object parts = spec.get("parts");
        if (parts != null) {
            if (!"number".equals(fm.get("type")) || computes(fm)) {
                return where + ": `parts` belongs to a `type: number` field that computes nothing else (PS-614)";
            }
            boolean ok = parts instanceof Map<?, ?>;
            if (ok) {
                Map<?, ?> pm = (Map<?, ?>) parts;
                for (Map.Entry<?, ?> e : pm.entrySet()) {
                    ok &= TIME_PARTS.contains(String.valueOf(e.getKey()))
                            && e.getValue() instanceof String v && v.startsWith("$");
                }
                ok &= pm.containsKey("year") && pm.containsKey("month") && pm.containsKey("day");
            }
            if (!ok) {
                return where + ": `parts` maps year, month and day, and optionally hour, minute and "
                        + "second, to $references (PS-614)";
            }
            if (spec.containsKey("epoch")) return where + ": `epoch` does not apply to `parts` (PS-614)";
        } else if ("number".equals(fm.get("type")) && !computes(fm)) {
            return where + ": a computed `time` field declares `parts` or a computation (PS-614)";
        }
        return null;
    }

    /** time_errors: throws the first PS-610 to PS-614 violation in the header, fields, ports and definitions. */
    static void check(Map<String, Object> raw) {
        for (String key : new String[] {"header", "fields", "ports", "definitions"}) walk(raw.get(key));
    }

    private static void walk(Object node) {
        if (node instanceof List<?> list) {
            for (Object item : list) walk(item);
        } else if (node instanceof Map<?, ?> map) {
            if (map.containsKey("time") && map.containsKey("type")) {
                String error = fieldError(map);
                if (error != null) throw new SchemaException(error);
            }
            for (Map.Entry<?, ?> e : map.entrySet()) {
                if (!"test_vectors".equals(String.valueOf(e.getKey()))) walk(e.getValue());
            }
        }
    }

    // --- decode ------------------------------------------------------------------------------

    /** A decoded number as an exact decimal: a double by its shortest round-trip text. */
    static BigDecimal decimal(Object value) {
        if (value instanceof BigDecimal b) return b;
        if (value instanceof BigInteger b) return new BigDecimal(b);
        if (value instanceof Double || value instanceof Float) {
            return new BigDecimal(Double.toString(((Number) value).doubleValue()));
        }
        return BigDecimal.valueOf(((Number) value).longValue());
    }

    private static long floor(BigDecimal value) {
        return value.setScale(0, RoundingMode.FLOOR).longValueExact();
    }

    /** {@code count} fractional digits, truncated. */
    private static String fractionDigits(BigDecimal fraction, int count) {
        String digits = Long.toString(floor(fraction.movePointRight(count)));
        return "0".repeat(Math.max(0, count - digits.length())) + digits;
    }

    /** An integer where the value is whole, else a double, as the reference reports it. */
    static Object jsNumber(BigDecimal value) {
        if (value.signum() == 0 || value.stripTrailingZeros().scale() <= 0) {
            BigInteger whole = value.toBigIntegerExact();
            return whole.bitLength() < 64 ? (Object) whole.longValue() : whole;
        }
        return value.doubleValue();
    }

    private static String pad(long v, int width) {
        String s = Long.toString(v);
        return "0".repeat(Math.max(0, width - s.length())) + s;
    }

    /** format_time: the instant {@code seconds} after the field's epoch, in its format (PS-610 to PS-613). */
    static Object format(Field field, Object seconds) {
        Map<?, ?> spec = spec(field);
        BigDecimal instant = epochOf(spec, "epoch").add(decimal(seconds));
        String fmt = String.valueOf(spec.get("format"));
        if (fmt.equals("unix")) {
            BigDecimal count = instant.subtract(epochOf(spec, "since"));
            if (Boolean.TRUE.equals(spec.get("milliseconds"))) return floor(count.movePointRight(3));
            return jsNumber(count);
        }
        long whole = floor(instant);
        BigDecimal fraction = instant.subtract(BigDecimal.valueOf(whole));
        long days = Math.floorDiv(whole, 86400);
        long rest = Math.floorMod(whole, 86400);
        long[] ymd = civilFromDays(days);
        long year = ymd[0], month = ymd[1], day = ymd[2];
        if (year < 1 || year > 9999) {
            throw new Omitted("Field '" + field.getName() + "': the time "
                    + instant.toPlainString() + " s is outside years 1 to 9999; the field is omitted (PS-610)");
        }
        long hour = rest / 3600, minute = rest / 60 % 60, second = rest % 60;
        if (fmt.equals("iso8601")) {
            StringBuilder text = new StringBuilder();
            text.append(pad(year, 4)).append('-').append(pad(month, 2)).append('-').append(pad(day, 2))
                    .append('T').append(pad(hour, 2)).append(':').append(pad(minute, 2)).append(':')
                    .append(pad(second, 2));
            if (spec.containsKey("fraction")) {
                text.append('.').append(fractionDigits(fraction, ((Number) spec.get("fraction")).intValue()));
            } else if (fraction.signum() != 0) {
                String plain = fraction.stripTrailingZeros().toPlainString();
                text.append('.').append(plain.substring(plain.indexOf('.') + 1));
            }
            return text.append('Z').toString();
        }
        if (fmt.equals("calendar")) {
            List<?> labels = spec.get("month_labels") instanceof List<?> l && !l.isEmpty() ? l : null;
            Map<?, ?> keys = spec.get("keys") instanceof Map<?, ?> k ? k : Map.of();
            Map<String, Object> values = new HashMap<>();
            values.put("year", year);
            values.put("month", labels != null ? labels.get((int) month - 1) : (Object) month);
            values.put("day", day);
            values.put("hour", hour);
            values.put("minute", minute);
            values.put("second", second);
            Map<String, Object> out = new LinkedHashMap<>();
            for (String part : TIME_PARTS) {
                Object renamed = keys.get(part);
                out.put(renamed != null ? String.valueOf(renamed) : part, values.get(part));
            }
            return out;
        }
        Map<String, String> out = new HashMap<>();
        out.put("Y", pad(year, 4));
        out.put("m", pad(month, 2));
        out.put("d", pad(day, 2));
        out.put("H", pad(hour, 2));
        out.put("M", pad(minute, 2));
        out.put("S", pad(second, 2));
        out.put("L", fractionDigits(fraction, 3));
        out.put("b", MONTH_ABBR.get((int) month - 1));
        out.put("a", DAY_ABBR.get((int) Math.floorMod(days + 3, 7L)));
        out.put("%", "%");
        Matcher m = PATTERN_TOKEN.matcher(String.valueOf(spec.get("pattern")));
        StringBuilder text = new StringBuilder();
        while (m.find()) m.appendReplacement(text, Matcher.quoteReplacement(out.get(m.group(1))));
        m.appendTail(text);
        return text.toString();
    }

    /**
     * PS-610 as amended 2026-10-15: a time field with no instant to report (a year outside 1
     * to 9999). The decoder omits the field, reports the message as a warning, and continues.
     */
    static final class Omitted extends SchemaException.DecodeException {
        Omitted(String message) { super(message); }
    }

    /**
     * time_from_parts: the seconds since 1970 of the UTC instant the referenced parts name, or
     * null where a part has no value (PS-314). A part that is not a whole number, and parts that
     * name no valid date and time, omit the field with a warning naming it, and decoding
     * continues (PS-614 as amended 2026-10-15).
     */
    static Object fromParts(Field field, DecodeContext ctx) {
        Map<?, ?> parts = (Map<?, ?>) spec(field).get("parts");
        Map<String, Long> values = new LinkedHashMap<>();
        for (String part : TIME_PARTS) {
            if (!parts.containsKey(part)) continue;
            String key = String.valueOf(parts.get(part)).substring(1);
            Object value = ctx.getVariable(key);
            if (!(value instanceof Number n) || value instanceof Boolean) return null;
            double d = n.doubleValue();
            if (Double.isNaN(d) || Double.isInfinite(d)) return null;
            if (d != Math.rint(d)) {
                ctx.addWarning("Field '" + field.getName() + "': its " + part
                        + " is " + Meta.pyRepr(d) + ", not a whole number; the field is omitted (PS-614)");
                return null;
            }
            values.put(part, isInt(value) ? n.longValue() : (long) d);
        }
        long[] p = new long[6];
        for (int i = 0; i < 6; i++) p[i] = values.getOrDefault(TIME_PARTS.get(i), 0L);
        if (!validCivil(p[0], p[1], p[2], p[3], p[4], p[5])) {
            List<String> shown = new ArrayList<>();
            for (Map.Entry<String, Long> e : values.entrySet()) shown.add("'" + e.getKey() + "': " + e.getValue());
            ctx.addWarning("Field '" + field.getName() + "': its parts {"
                    + String.join(", ", shown) + "} do not name a valid UTC date and time; the field is omitted (PS-614)");
            return null;
        }
        return civilSeconds(p[0], p[1], p[2], p[3], p[4], p[5]);
    }

    // --- encode (PS-615) ---------------------------------------------------------------------

    /** time_readback_error: a pattern that cannot be parsed back to an instant; null where it can be. */
    static String readbackError(Field field) {
        Map<?, ?> spec = spec(field);
        if (!"pattern".equals(spec.get("format"))) return null;
        String pattern = String.valueOf(spec.get("pattern"));
        Set<String> tokens = new HashSet<>();
        Matcher m = PATTERN_TOKEN.matcher(pattern);
        while (m.find()) tokens.add(m.group(1));
        if (tokens.containsAll(List.of("Y", "d", "H", "M", "S")) && (tokens.contains("m") || tokens.contains("b"))) {
            return null;
        }
        return "Field '" + field.getName() + "': its pattern " + Meta.pyRepr(pattern) + " cannot be read "
                + "back to an instant; the schema decodes but is not encoded (PS-615)";
    }

    /** parse_time: a time field's value in its format, as exact seconds since 1970 (UTC). */
    static BigDecimal parse(Field field, Object value) {
        Map<?, ?> spec = spec(field);
        String fmt = String.valueOf(spec.get("format"));
        String head = "Field '" + field.getName() + "': " + Meta.pyRepr(value) + " is not a time in its "
                + fmt + " format";
        if (fmt.equals("unix")) {
            if (!(value instanceof Number) || value instanceof Boolean) throw bad(head, "");
            BigDecimal count = decimal(value);
            if (Boolean.TRUE.equals(spec.get("milliseconds"))) count = count.movePointLeft(3);
            return count.add(epochOf(spec, "since"));
        }
        if (fmt.equals("iso8601")) {
            BigDecimal parsed = parseRfc3339(value);
            if (parsed == null) throw bad(head, "");
            return parsed;
        }
        if (fmt.equals("calendar")) {
            if (!(value instanceof Map<?, ?> given)) throw bad(head, "");
            Map<?, ?> keys = spec.get("keys") instanceof Map<?, ?> k ? k : Map.of();
            Map<String, Object> got = new HashMap<>();
            for (String part : TIME_PARTS) {
                Object renamed = keys.get(part);
                got.put(part, given.get(renamed != null ? String.valueOf(renamed) : part));
            }
            if (spec.get("month_labels") instanceof List<?> labels && !labels.isEmpty()) {
                int index = labels.indexOf(got.get("month"));
                if (index < 0 || !(got.get("month") instanceof String)) {
                    throw bad(head, "; month " + Meta.pyRepr(got.get("month")) + " is not a label");
                }
                got.put("month", (long) index + 1);
            }
            long[] p = new long[6];
            for (int i = 0; i < 6; i++) {
                Object v = got.get(TIME_PARTS.get(i));
                if (!isInt(v)) throw bad(head, "");
                p[i] = ((Number) v).longValue();
            }
            return instant(head, p, BigDecimal.ZERO);
        }
        String error = readbackError(field);
        if (error != null) throw new SchemaException.EncodeException(error);
        String pattern = String.valueOf(spec.get("pattern"));
        StringBuilder regex = new StringBuilder();
        List<String> groups = new ArrayList<>();
        int pos = 0;
        Matcher m = PATTERN_TOKEN.matcher(pattern);
        while (m.find()) {
            if (m.start() > pos) regex.append(Pattern.quote(pattern.substring(pos, m.start())));
            String token = m.group(1);
            if (token.equals("%")) {
                regex.append('%');
            } else {
                regex.append(switch (token) {
                    case "Y" -> "(\\d{4})";
                    case "L" -> "(\\d{3})";
                    case "b" -> "(" + String.join("|", MONTH_ABBR) + ")";
                    case "a" -> "(" + String.join("|", DAY_ABBR) + ")";
                    default -> "(\\d{2})";
                });
                groups.add(token);
            }
            pos = m.end();
        }
        if (pos < pattern.length()) regex.append(Pattern.quote(pattern.substring(pos)));
        Matcher match = value instanceof String s ? Pattern.compile(regex.toString()).matcher(s) : null;
        if (match == null || !match.matches()) throw bad(head, "");
        Map<String, String> got = new HashMap<>();
        for (int i = 0; i < groups.size(); i++) {
            String token = groups.get(i);
            String text = match.group(i + 1);
            if (got.containsKey(token) && !got.get(token).equals(text)) throw bad(head, "");
            got.put(token, text);
        }
        long month = got.containsKey("b") ? MONTH_ABBR.indexOf(got.get("b")) + 1 : Long.parseLong(got.get("m"));
        if (got.containsKey("m") && got.containsKey("b") && Long.parseLong(got.get("m")) != month) {
            throw bad(head, "; its month and month name disagree");
        }
        BigDecimal frac = got.containsKey("L") ? new BigDecimal(got.get("L")).movePointLeft(3) : BigDecimal.ZERO;
        long[] p = {Long.parseLong(got.get("Y")), month, Long.parseLong(got.get("d")),
                Long.parseLong(got.get("H")), Long.parseLong(got.get("M")), Long.parseLong(got.get("S"))};
        BigDecimal result = instant(head, p, frac);
        if (got.containsKey("a")) {
            String weekday = DAY_ABBR.get((int) Math.floorMod(Math.floorDiv(floor(result), 86400L) + 3, 7L));
            if (!weekday.equals(got.get("a"))) throw bad(head, "; " + got.get("a") + " is not that date's weekday");
        }
        return result;
    }

    private static SchemaException bad(String head, String why) {
        return new SchemaException.EncodeException(head + why + " (PS-615)");
    }

    private static BigDecimal instant(String head, long[] p, BigDecimal frac) {
        if (!validCivil(p[0], p[1], p[2], p[3], p[4], p[5])) throw bad(head, "");
        return BigDecimal.valueOf(civilSeconds(p[0], p[1], p[2], p[3], p[4], p[5])).add(frac);
    }

    /**
     * time_input_seconds: the number PS-610 reads for a time field's value - seconds since its
     * epoch - which the field's modifiers then invert.
     */
    static Object inputSeconds(Field field, Object value) {
        return jsNumber(parse(field, value).subtract(epochOf(spec(field), "epoch")));
    }

    /**
     * expand_time_parts: a {@code parts} time field's value gives its part fields' values,
     * which the encoder then writes. A part that reads no bytes cannot be written, and the
     * field is refused for encoding. The input itself is not modified.
     */
    static Map<String, Object> expandParts(List<Field> fields, Map<String, Object> data) {
        if (data == null) return data;
        Map<String, Field> known = new HashMap<>();
        for (Field f : fields) if (f.getName() != null) known.putIfAbsent(f.getName(), f);
        Map<String, Object> out = null;
        for (Field field : fields) {
            if (!hasParts(field)) continue;
            String name = field.getName();
            if (data.get(name) == null) continue;
            long whole = floor(parse(field, data.get(name)));
            long days = Math.floorDiv(whole, 86400L);
            long rest = Math.floorMod(whole, 86400L);
            long[] ymd = civilFromDays(days);
            Map<String, Long> moment = new HashMap<>();
            moment.put("year", ymd[0]);
            moment.put("month", ymd[1]);
            moment.put("day", ymd[2]);
            moment.put("hour", rest / 3600);
            moment.put("minute", rest / 60 % 60);
            moment.put("second", rest % 60);
            if (out == null) out = new LinkedHashMap<>(data);
            Map<?, ?> parts = (Map<?, ?>) spec(field).get("parts");
            for (String part : TIME_PARTS) {
                if (!parts.containsKey(part)) continue;
                String key = String.valueOf(parts.get(part)).substring(1);
                Field source = known.get(key);
                if (source != null && source.getRaw() != null
                        && UNWRITTEN_PART_TYPES.contains(String.valueOf(source.getRaw().get("type")))) {
                    throw new SchemaException.EncodeException("Field '" + name + "': its " + part + " part '"
                            + key + "' reads no bytes, so the schema decodes but is not encoded (PS-615)");
                }
                long given = moment.get(part);
                Object supplied = out.get(key);
                if (supplied != null && !(supplied instanceof Number n && !(supplied instanceof Boolean)
                        && n.doubleValue() == given)) {
                    throw new SchemaException.EncodeException("Field '" + name + "': its " + part + " is "
                            + given + ", but '" + key + "' is given " + Meta.pyRepr(supplied) + " (PS-615)");
                }
                out.put(key, given);
            }
        }
        return out == null ? data : out;
    }
}
