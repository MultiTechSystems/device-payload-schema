package org.lora.schema;

import java.nio.ByteBuffer;
import java.nio.ByteOrder;
import java.util.ArrayList;
import java.util.Collections;
import java.util.HashMap;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.Set;

public class DecodeContext {
    private final byte[] data;
    private int offset;
    private String endian;
    private final Map<String, Object> variables;
    /**
     * Things worth telling the caller that do not stop the decode. Encoding already had
     * this channel through {@link EncodeResult}; decoding had none, so an unknown TLV tag
     * had nowhere to surface and a payload that stopped short read as a complete decode
     * (PS-301, PS-302).
     */
    private final List<String> warnings;
    /**
     * Per-field reading quality, reported as {@code _quality}: {@code good} or
     * {@code out_of_range} for a field declaring {@code valid_range} (PS-131), and the
     * readings omitted under PS-427/PS-428. Nothing produced it, so a reading outside its
     * declared range came back unmarked and without a warning where the other four
     * implementations flag it. Mirrors Go's DecodeContext.Quality.
     */
    private final Map<String, String> quality = new LinkedHashMap<>();
    /** Omitted readings waiting to learn whether {@code _quality} is produced (PS-182). */
    private final Map<String, String> pendingAbsent = new LinkedHashMap<>();
    /** Enclosing flagged groups; a mark made inside one always waits, as in the reference. */
    private int inFlagged;
    /**
     * Where the readable region ends. A repeat's or tlv's {@code reserve} stops its loop
     * short of the payload's end (PS-350, PS-471), so the region is narrowed while the
     * loop runs and nothing inside it can read the reserved bytes. Positions stay
     * absolute, as they do in the reference's truncated buffer.
     */
    private int limit;
    /** Names declared only inside some repeat's elements (PS-368). */
    private Set<String> repeatOnlyNames = Set.of();
    /** The members of the element being decoded, or null outside any element (PS-368). */
    private Set<String> elementNames;

    public DecodeContext(byte[] data, String endian) {
        this.data = data;
        this.limit = data.length;
        this.offset = 0;
        this.endian = endian != null ? endian : "big";
        this.variables = new HashMap<>();
        this.warnings = new ArrayList<>();
    }

    /** The payload being decoded, whose length a warning counts undecoded bytes against. */
    public byte[] getData() { return data; }

    public int remaining() {
        return limit - offset;
    }

    /** The end of the readable region: the payload's length unless a reserve narrows it. */
    public int getLimit() { return limit; }
    public void setLimit(int limit) { this.limit = limit; }

    void setRepeatOnlyNames(Set<String> names) { this.repeatOnlyNames = names == null ? Set.of() : names; }
    Set<String> getElementNames() { return elementNames; }
    void setElementNames(Set<String> names) { this.elementNames = names; }

    /**
     * The value a {@code $name} reference resolves to, or null where it is unbound.
     *
     * <p>PS-368: a name declared in a repeat's elements has a value only inside the element,
     * and only once it is decoded. A reference breaking either rule is an error rather than
     * the 0 every other unbound reference still reads as. Mirrors {@code _ref} in
     * tools/schema_interpreter.py.
     */
    public Object ref(String name) {
        if (variables.containsKey(name)) return variables.get(name);
        if (elementNames != null && elementNames.contains(name)) {
            throw new SchemaException.DecodeException("$" + name
                    + " refers to a field of this element that is not yet decoded (PS-368)");
        }
        if (repeatOnlyNames.contains(name)) {
            throw new SchemaException.DecodeException("$" + name
                    + " is declared only inside a repeat's elements, so it has no value here (PS-368)");
        }
        return null;
    }

    /** A copy of the variable table, restored once an element's scope ends (PS-368). */
    Map<String, Object> snapshotVariables() { return new HashMap<>(variables); }

    void restoreVariables(Map<String, Object> saved) {
        variables.clear();
        variables.putAll(saved);
    }

    public byte[] read(int n) {
        if (n < 0) {
            // `length: remaining` (PS-014). Guarded here because `new byte[-1]` throws
            // NegativeArraySizeException, which is not a decode error a caller can read.
            n = remaining();
        }
        if (offset + n > limit) {
            throw new SchemaException.DecodeException(
                String.format("Buffer underflow: need %d bytes at offset %d, but only %d remaining",
                    n, offset, remaining()));
        }
        byte[] result = new byte[n];
        System.arraycopy(data, offset, result, 0, n);
        offset += n;
        return result;
    }

    public byte[] peek(int n, int relativeOffset) {
        int pos = offset + relativeOffset;
        if (pos + n > limit) {
            throw new SchemaException.DecodeException(
                String.format("Buffer underflow at peek offset %d", pos));
        }
        byte[] result = new byte[n];
        System.arraycopy(data, pos, result, 0, n);
        return result;
    }

    public int getOffset() { return offset; }
    public void setOffset(int offset) { this.offset = offset; }
    
    public String getEndian() { return endian; }
    public void setEndian(String endian) { this.endian = endian; }
    
    public Map<String, Object> getVariables() { return variables; }

    public List<String> getWarnings() { return Collections.unmodifiableList(warnings); }

    public void addWarning(String warning) { warnings.add(warning); }

    void enterFlagged() { inFlagged++; }
    void exitFlagged() { inFlagged--; }
    boolean inFlagged() { return inFlagged > 0; }

    /**
     * PS-131: a value outside the field's {@code valid_range} is reported, flagged
     * {@code out_of_range} with a warning; one inside is {@code good}. A value that is not
     * a number (a lookup label) is not checked. Mirrors Go's checkValidRange.
     */
    void checkValidRange(Object value, Field field) {
        double[] range = field.getValidRange();
        if (range == null || !(value instanceof Number n) || value instanceof Boolean) return;
        double v = n.doubleValue();
        if (v < range[0] || v > range[1]) {
            warnings.add(field.getName() + ": value " + formatNumber(v) + " outside valid range ["
                    + formatNumber(range[0]) + ", " + formatNumber(range[1]) + "]");
            quality.put(field.getName(), "out_of_range");
        } else {
            quality.put(field.getName(), "good");
        }
    }

    /**
     * PS-427, PS-428: an omitted reading is recorded in {@code _quality} where that is
     * produced. A field declaring {@code valid_range} produces it itself; any other mark
     * waits for the end of the decode (PS-182), and so does one inside a flagged group.
     */
    void markAbsent(Field field, String why) {
        if (field.getValidRange() != null && inFlagged == 0) {
            quality.put(field.getName(), why);
        } else {
            pendingAbsent.put(field.getName(), why);
        }
    }

    /** {@code _quality} as reported: the waiting marks join it only where it is produced. */
    Map<String, String> settledQuality() {
        if (quality.isEmpty()) return quality;
        Map<String, String> out = new LinkedHashMap<>(quality);
        for (Map.Entry<String, String> e : pendingAbsent.entrySet()) {
            out.putIfAbsent(e.getKey(), e.getValue());
        }
        return out;
    }

    /** A number as Go's {@code %v} writes a float64 in the common case: 90, not 90.0. */
    private static String formatNumber(double v) {
        if (v == Math.rint(v) && Math.abs(v) < 1e15) return Long.toString((long) v);
        return Double.toString(v);
    }
    
    public void setVariable(String name, Object value) {
        variables.put(name, value);
    }
    
    public Object getVariable(String name) {
        return variables.get(name);
    }

    public long decodeUnsigned(byte[] bytes, String endian) {
        if (bytes.length == 0) return 0;
        
        long value = 0;
        if ("little".equals(endian)) {
            for (int i = bytes.length - 1; i >= 0; i--) {
                value = (value << 8) | (bytes[i] & 0xFF);
            }
        } else {
            for (byte b : bytes) {
                value = (value << 8) | (b & 0xFF);
            }
        }
        return value;
    }

    public long decodeSigned(byte[] bytes, String endian) {
        long uval = decodeUnsigned(bytes, endian);
        int bits = bytes.length * 8;
        // Java masks a shift count to the operand's width, so `1L << 64` is 1, and the
        // subtraction below wrapped s64 minimum to s64 maximum: 8000000000000000 decoded
        // as 9223372036854775807. At a full 64 bits the pattern decodeUnsigned returns
        // already is the two's-complement value (PS-294).
        if (bits >= 64) {
            return uval;
        }
        long signBit = 1L << (bits - 1);
        if (uval >= signBit) {
            return uval - (1L << bits);
        }
        return uval;
    }

    public double decodeFloat(byte[] bytes, int size, String endian) {
        ByteBuffer buffer = ByteBuffer.wrap(bytes);
        buffer.order("little".equals(endian) ? ByteOrder.LITTLE_ENDIAN : ByteOrder.BIG_ENDIAN);
        
        return switch (size) {
            case 2 -> float16ToDouble(buffer.getShort());
            case 4 -> buffer.getFloat();
            case 8 -> buffer.getDouble();
            default -> throw new SchemaException.DecodeException("Unsupported float size: " + size);
        };
    }

    private double float16ToDouble(short bits) {
        int sign = (bits >> 15) & 0x1;
        int exp = (bits >> 10) & 0x1F;
        int mant = bits & 0x3FF;

        double value;
        if (exp == 0) {
            value = Math.pow(2, -14) * mant / 1024.0;
        } else if (exp == 31) {
            if (mant != 0) {
                return Double.NaN;
            }
            value = Double.POSITIVE_INFINITY;
        } else {
            value = Math.pow(2, exp - 15) * (1 + mant / 1024.0);
        }

        return sign == 1 ? -value : value;
    }

    public int decodeBits(int byteVal, int bitOffset, int numBits) {
        int mask = (1 << numBits) - 1;
        return (byteVal >> bitOffset) & mask;
    }
}
