package org.lora.schema;

import java.util.*;

public class Field {
    private String name;
    private FieldType type = FieldType.U8;
    private int length;
    private int byteOffset;
    private int bitOffset;
    private int bits;
    /**
     * Bytes to advance after reading a bit field. An explicit {@code u8[lo:hi]} range
     * does not advance the cursor by itself, because several fields share one byte and
     * the last of them declares {@code consume}. Zero means this field advances nothing.
     */
    private int consume;
    /**
     * Bytes the bit range is taken from. `u24[4:23]` means bits 4 to 23 of a 24-bit
     * big-endian value, so the whole 3 bytes must be read before masking. Defaults
     * to 1, which is what every `u8[lo:hi]` range needs.
     */
    private int bitBaseBytes = 1;
    private String endian;
    private Double add;
    private Double mult;
    private Double div;
    private List<String> modOrder;
    private List<Transform> transform;
    private Map<Integer, String> lookup;
    /** True when `lookup` was written as a sequence, indexed from zero (PS-104). */
    private boolean lookupIsSequence;
    /** Fallback for a mapping lookup with no entry for the value (PS-269). */
    private String lookupDefault;
    /** Output key template resolved against earlier fields (PS-265). */
    private String nameFrom;
    private String var;
    private Object value;
    private List<Field> fields;
    private String on;
    private List<Case> cases;
    
    // Repeat fields
    private Object count;
    private Object byteLength;
    private String until;
    private int max;
    private int min;
    
    // Bytes format
    private String format;
    private String separator;
    
    // TLV fields
    private int tagSize;
    private int lengthSize;
    private List<Field> tagFields;
    private Object tagKey;
    private Boolean merge;
    private String unknown;
    private Map<String, List<Field>> tlvCases;
    
    // Bitfield string
    private List<List<Object>> parts;
    private String delimiter;
    private String prefix;
    
    // Formula
    private String formula;

    // Computed fields (type: number)
    /** Source of a computed value: {@code $field_name}, or a literal number. */
    private String ref;
    /** Coefficients in descending power order, evaluated by Horner's method. */
    private List<Double> polynomial;
    /** Cross-field binary operation. */
    private Compute compute;
    /** Conditions that must hold, else the field takes the guard's fallback. */
    private Guard guard;
    
    // Flagged construct
    private FlaggedDef flagged;
    
    // TLV inline
    private Field tlvInline;

    /** Inline `- match: {field: $x, cases: {...}}` block. */
    private Field matchInline;

    // Enumeration type (PS-067, PS-068)
    /** Underlying integer type an `enum` reads before mapping. */
    private String base;
    /** Integer-to-name mapping for an `enum` field. */
    private Map<Integer, String> values;
    /** Name an unmapped enum value reports (PS-068). */
    private String enumDefault;

    /** Fields sharing one run of bytes, each read from the group's start. */
    private List<Field> byteGroup;
    /** Bytes the group occupies; the cursor advances by this once, at the end. */
    private int byteGroupSize = 1;

    public Field() {
        this.modOrder = new ArrayList<>();
    }

    // Getters and setters
    public String getName() { return name; }
    public void setName(String name) { this.name = name; }
    
    public FieldType getType() { return type; }
    public void setType(FieldType type) { this.type = type; }
    
    public int getLength() { return length; }
    public void setLength(int length) { this.length = length; }
    
    public int getByteOffset() { return byteOffset; }
    public void setByteOffset(int byteOffset) { this.byteOffset = byteOffset; }
    
    public int getBitOffset() { return bitOffset; }
    public void setBitOffset(int bitOffset) { this.bitOffset = bitOffset; }
    
    public int getBits() { return bits; }
    public void setBits(int bits) { this.bits = bits; }

    public int getConsume() { return consume; }
    public void setConsume(int consume) { this.consume = consume; }

    public int getBitBaseBytes() { return bitBaseBytes; }
    public void setBitBaseBytes(int bitBaseBytes) { this.bitBaseBytes = bitBaseBytes; }

    public String getEndian() { return endian; }
    public void setEndian(String endian) { this.endian = endian; }
    
    public Double getAdd() { return add; }
    public void setAdd(Double add) { this.add = add; }
    
    public Double getMult() { return mult; }
    public void setMult(Double mult) { this.mult = mult; }
    
    public Double getDiv() { return div; }
    public void setDiv(Double div) { this.div = div; }
    
    /** @deprecated Modifier order is fixed by PS-101; no longer read. */
    @Deprecated
    public List<String> getModOrder() { return modOrder; }
    /** @deprecated Modifier order is fixed by PS-101; no longer read. */
    @Deprecated
    public void setModOrder(List<String> modOrder) { this.modOrder = modOrder; }
    
    public List<Transform> getTransform() { return transform; }
    public void setTransform(List<Transform> transform) { this.transform = transform; }
    
    public Map<Integer, String> getLookup() { return lookup; }
    public boolean isLookupSequence() { return lookupIsSequence; }
    public void setLookupSequence(boolean value) { this.lookupIsSequence = value; }
    public String getLookupDefault() { return lookupDefault; }
    public void setLookupDefault(String value) { this.lookupDefault = value; }
    public String getNameFrom() { return nameFrom; }
    public void setNameFrom(String value) { this.nameFrom = value; }
    public void setLookup(Map<Integer, String> lookup) { this.lookup = lookup; }
    
    public String getVar() { return var; }
    public void setVar(String var) { this.var = var; }
    
    public Object getValue() { return value; }
    public void setValue(Object value) { this.value = value; }
    
    public List<Field> getFields() { return fields; }
    public void setFields(List<Field> fields) { this.fields = fields; }
    
    public String getOn() { return on; }
    public void setOn(String on) { this.on = on; }
    
    public List<Case> getCases() { return cases; }
    public void setCases(List<Case> cases) { this.cases = cases; }
    
    public Object getCount() { return count; }
    public void setCount(Object count) { this.count = count; }
    
    public Object getByteLength() { return byteLength; }
    public void setByteLength(Object byteLength) { this.byteLength = byteLength; }
    
    public String getUntil() { return until; }
    public void setUntil(String until) { this.until = until; }
    
    public int getMax() { return max; }
    public void setMax(int max) { this.max = max; }
    
    public int getMin() { return min; }
    public void setMin(int min) { this.min = min; }
    
    public String getFormat() { return format; }
    public void setFormat(String format) { this.format = format; }
    
    public String getSeparator() { return separator; }
    public void setSeparator(String separator) { this.separator = separator; }
    
    public int getTagSize() { return tagSize; }
    public void setTagSize(int tagSize) { this.tagSize = tagSize; }
    
    public int getLengthSize() { return lengthSize; }
    public void setLengthSize(int lengthSize) { this.lengthSize = lengthSize; }
    
    public List<Field> getTagFields() { return tagFields; }
    public void setTagFields(List<Field> tagFields) { this.tagFields = tagFields; }
    
    public Object getTagKey() { return tagKey; }
    public void setTagKey(Object tagKey) { this.tagKey = tagKey; }
    
    public Boolean getMerge() { return merge; }
    public void setMerge(Boolean merge) { this.merge = merge; }
    
    public String getUnknown() { return unknown; }
    public void setUnknown(String unknown) { this.unknown = unknown; }
    
    public Map<String, List<Field>> getTlvCases() { return tlvCases; }
    public void setTlvCases(Map<String, List<Field>> tlvCases) { this.tlvCases = tlvCases; }
    
    public List<List<Object>> getParts() { return parts; }
    public void setParts(List<List<Object>> parts) { this.parts = parts; }
    
    public String getDelimiter() { return delimiter; }
    public void setDelimiter(String delimiter) { this.delimiter = delimiter; }
    
    public String getPrefix() { return prefix; }
    public void setPrefix(String prefix) { this.prefix = prefix; }
    
    public String getFormula() { return formula; }
    public void setFormula(String formula) { this.formula = formula; }

    public String getRef() { return ref; }
    public void setRef(String ref) { this.ref = ref; }

    public List<Double> getPolynomial() { return polynomial; }
    public void setPolynomial(List<Double> polynomial) { this.polynomial = polynomial; }

    public Compute getCompute() { return compute; }
    public void setCompute(Compute compute) { this.compute = compute; }

    public Guard getGuard() { return guard; }
    public void setGuard(Guard guard) { this.guard = guard; }
    
    public FlaggedDef getFlagged() { return flagged; }
    public void setFlagged(FlaggedDef flagged) { this.flagged = flagged; }
    
    public Field getTlvInline() { return tlvInline; }
    public void setTlvInline(Field tlvInline) { this.tlvInline = tlvInline; }

    public Field getMatchInline() { return matchInline; }
    public void setMatchInline(Field matchInline) { this.matchInline = matchInline; }

    public String getBase() { return base; }
    public void setBase(String base) { this.base = base; }

    public Map<Integer, String> getValues() { return values; }
    public void setValues(Map<Integer, String> values) { this.values = values; }

    public String getEnumDefault() { return enumDefault; }
    public void setEnumDefault(String enumDefault) { this.enumDefault = enumDefault; }

    public List<Field> getByteGroup() { return byteGroup; }
    public void setByteGroup(List<Field> byteGroup) { this.byteGroup = byteGroup; }

    public int getByteGroupSize() { return byteGroupSize; }
    public void setByteGroupSize(int byteGroupSize) { this.byteGroupSize = byteGroupSize; }

    public int getEffectiveLength() {
        if (length > 0) {
            return length;
        }
        return type.defaultLength();
    }

    public String getEffectiveEndian(String defaultEndian) {
        return endian != null ? endian : defaultEndian;
    }

    public static class Transform {
        private Double add;
        private Double mult;
        private Double div;
        /** Named operation form, e.g. {@code {op: round, decimals: 2}}. */
        private String op;
        private Integer decimals;
        /**
         * Unary maths stages. {@code sqrt}, {@code abs}, {@code log10} and
         * {@code log} are flags; {@code pow} carries its exponent. Boxed so an
         * absent key stays distinguishable from {@code pow: 0}.
         */
        private Boolean sqrt;
        private Boolean abs;
        private Boolean log10;
        private Boolean log;
        private Double pow;

        public Boolean getSqrt() { return sqrt; }
        public void setSqrt(Boolean sqrt) { this.sqrt = sqrt; }

        public Boolean getAbs() { return abs; }
        public void setAbs(Boolean abs) { this.abs = abs; }

        public Boolean getLog10() { return log10; }
        public void setLog10(Boolean log10) { this.log10 = log10; }

        public Boolean getLog() { return log; }
        public void setLog(Boolean log) { this.log = log; }

        public Double getPow() { return pow; }
        public void setPow(Double pow) { this.pow = pow; }

        public Double getAdd() { return add; }
        public void setAdd(Double add) { this.add = add; }

        public Double getMult() { return mult; }
        public void setMult(Double mult) { this.mult = mult; }

        public Double getDiv() { return div; }
        public void setDiv(Double div) { this.div = div; }

        public String getOp() { return op; }
        public void setOp(String op) { this.op = op; }

        public Integer getDecimals() { return decimals; }
        public void setDecimals(Integer decimals) { this.decimals = decimals; }
    }

    public static class Case {
        private Object caseValue;
        private boolean isDefault;
        private List<Field> fields;

        public Object getCaseValue() { return caseValue; }
        public void setCaseValue(Object caseValue) { this.caseValue = caseValue; }
        
        public boolean isDefault() { return isDefault; }
        public void setDefault(boolean isDefault) { this.isDefault = isDefault; }
        
        public List<Field> getFields() { return fields; }
        public void setFields(List<Field> fields) { this.fields = fields; }
    }

    public static class FlaggedDef {
        private String field;
        private List<FlaggedGroup> groups;

        public String getField() { return field; }
        public void setField(String field) { this.field = field; }
        
        public List<FlaggedGroup> getGroups() { return groups; }
        public void setGroups(List<FlaggedGroup> groups) { this.groups = groups; }
    }

    public static class FlaggedGroup {
        private int bit;
        private List<Field> fields;

        public int getBit() { return bit; }
        public void setBit(int bit) { this.bit = bit; }

        public List<Field> getFields() { return fields; }
        public void setFields(List<Field> fields) { this.fields = fields; }
    }

    /** A cross-field binary operation: {@code {op: div, a: $numerator, b: $denominator}}. */
    public static class Compute {
        private String op = "add";
        private Object a;
        private Object b;

        public String getOp() { return op; }
        public void setOp(String op) { this.op = op; }

        public Object getA() { return a; }
        public void setA(Object a) { this.a = a; }

        public Object getB() { return b; }
        public void setB(Object b) { this.b = b; }
    }

    /**
     * Conditions a computed field requires, with the value to report when one fails:
     * {@code {when: [{field: $x, gt: 0}], else: null}}. Conditions are checked before
     * the computation runs, so a guarded division by zero never happens.
     */
    public static class Guard {
        private List<Condition> when = new ArrayList<>();
        private Object elseValue;
        private boolean hasElse;

        public List<Condition> getWhen() { return when; }
        public void setWhen(List<Condition> when) { this.when = when; }

        public Object getElseValue() { return elseValue; }
        public void setElseValue(Object elseValue) { this.elseValue = elseValue; }

        public boolean hasElse() { return hasElse; }
        public void setHasElse(boolean hasElse) { this.hasElse = hasElse; }
    }

    /** One comparison in a guard: a field reference and a single operator. */
    public static class Condition {
        private String field;
        private String op;
        private Double operand;

        public String getField() { return field; }
        public void setField(String field) { this.field = field; }

        public String getOp() { return op; }
        public void setOp(String op) { this.op = op; }

        public Double getOperand() { return operand; }
        public void setOperand(Double operand) { this.operand = operand; }
    }
}
