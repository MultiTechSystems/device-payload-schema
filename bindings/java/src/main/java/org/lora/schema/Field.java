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
    /** An sN[start:end] range: sign-extended from the range's width (PS-352, PS-353). */
    private boolean signedBits;
    public boolean isSignedBits() { return signedBits; }
    public void setSignedBits(boolean signedBits) { this.signedBits = signedBits; }
    /** A byte_group's own byte order (PS-364), null for the context's. */
    private String groupEndian;
    public String getGroupEndian() { return groupEndian; }
    public void setGroupEndian(String groupEndian) { this.groupEndian = groupEndian; }
    /** A named code on an unsigned integer (PS-422). */
    private String encoding;
    public String getEncoding() { return encoding; }
    public void setEncoding(String encoding) { this.encoding = encoding; }
    /** {@code decimal} or {@code hex} on an ascii field reported as an integer (PS-560). */
    private String numeral;
    public String getNumeral() { return numeral; }
    public void setNumeral(String numeral) { this.numeral = numeral; }
    /** An ascii field's treatment of 0x00 bytes: trailing, stop or drop (PS-534). */
    private String nul;
    public String getNul() { return nul; }
    public void setNul(String nul) { this.nul = nul; }
    /** The byte that ends a variable-length ascii field, in place of length (PS-593). */
    private Integer terminator;
    public Integer getTerminator() { return terminator; }
    public void setTerminator(Integer terminator) { this.terminator = terminator; }
    /** {@code consume_terminator: false} leaves the terminator unread and unwritten (PS-619, PS-620). */
    private boolean keepTerminator;
    public boolean isKeepTerminator() { return keepTerminator; }
    public void setKeepTerminator(boolean keep) { this.keepTerminator = keep; }
    /** {@code end_terminates: true}: the end of the data ends the text too (PS-619). */
    private boolean endTerminates;
    public boolean isEndTerminates() { return endTerminates; }
    public void setEndTerminates(boolean end) { this.endTerminates = end; }
    /** {@code charset: utf-8}: the text is UTF-8 (PS-624, PS-625, CR-2026-190). */
    private boolean utf8;
    public boolean isUtf8() { return utf8; }
    public void setUtf8(boolean utf8) { this.utf8 = utf8; }
    /** {@code source}: a value of the frame on a type: number field (PS-635, CR-2026-196). */
    private String source;
    public String getSource() { return source; }
    public void setSource(String source) { this.source = source; }
    /** CR-2026-199 (PS-641, PS-642): an ascii field's {@code trim: true}. */
    private boolean trim;
    public boolean isTrim() { return trim; }
    public void setTrim(boolean trim) { this.trim = trim; }
    /** A {@code checksum} declaration and the type it sits on (PS-585, CR-2026-174). */
    private Map<String, Object> checksum;
    private String checksumType;
    public Map<String, Object> getChecksum() { return checksum; }
    public String getChecksumType() { return checksumType; }
    public void setChecksum(Map<String, Object> checksum, String type) {
        this.checksum = checksum;
        this.checksumType = type;
    }
    private String endian;
    private Double add;
    private Double mult;
    private Double div;
    private List<Transform> transform;
    /** The lookup's labels, each a String, a Number or a Boolean (PS-106, CR-2026-104). */
    private Map<Integer, Object> lookup;
    /** True when `lookup` was written as a sequence, indexed from zero (PS-104). */
    private boolean lookupIsSequence;
    /** Fallback for a mapping lookup with no entry for the value (PS-269). */
    private Object lookupDefault;
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
    // CR-2026-110: how a name reported by more than one entry combines (PS-525).
    private OnRepeat.Modes repeatModes = OnRepeat.NONE;
    
    // Bitfield string
    private List<List<Object>> parts;
    private String delimiter;
    private String prefix;
    
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

    // 0.5.2 wave 6b: field-level rules (CR-2026-056, -059, -062, -065).
    /** The preceding field whose value is this field's byte count (PS-464), or null. */
    private String lengthRef;
    /** Decoded where its bytes remain, absent where none do (PS-402). */
    private boolean optional;
    /** Raw values meaning "no reading" (PS-427), or null. */
    private List<Long> sentinel;
    /** {@code valid_range} where {@code out_of_range: omit} drops a value outside it (PS-428). */
    private double[] omitOutside;
    /** {@code valid_range}, checked into {@code _quality} with a warning (PS-131). */
    private double[] validRange;
    /** A match on the bytes remaining (PS-414). */
    private boolean matchRemaining;

    public String getLengthRef() { return lengthRef; }
    public void setLengthRef(String lengthRef) { this.lengthRef = lengthRef; }
    public boolean isOptional() { return optional; }
    public void setOptional(boolean optional) { this.optional = optional; }
    public List<Long> getSentinel() { return sentinel; }
    public void setSentinel(List<Long> sentinel) { this.sentinel = sentinel; }
    public double[] getOmitOutside() { return omitOutside; }
    public void setOmitOutside(double[] omitOutside) { this.omitOutside = omitOutside; }
    public double[] getValidRange() { return validRange; }
    public void setValidRange(double[] validRange) { this.validRange = validRange; }
    public boolean isMatchRemaining() { return matchRemaining; }
    public void setMatchRemaining(boolean matchRemaining) { this.matchRemaining = matchRemaining; }

    /**
     * The declaration this field was parsed from, after {@code $ref} splicing, or null for
     * a field built by hand or from the binary format. {@code _meta} is derived from what
     * was declared (PS-493), which the typed fields above no longer carry: an alias is
     * canonicalised and a bit range is parsed into offsets.
     */
    private Map<String, Object> raw;
    public Map<String, Object> getRaw() { return raw; }
    public void setRaw(Map<String, Object> raw) { this.raw = raw; }

    public Field() {
    }

    // Getters and setters
    public String getName() { return name; }
    public void setName(String name) { this.name = name; }
    
    public FieldType getType() { return type; }
    public void setType(FieldType type) { this.type = type; }
    
    public int getLength() { return length; }
    public void setLength(int length) { this.length = length; }

    /**
     * Whether `length` was written as a number, so that `length: 0` - a field of no
     * bytes (PS-392) - is told apart from no length at all, which takes the type's width.
     */
    private boolean lengthDeclared;
    public boolean isLengthDeclared() { return lengthDeclared; }
    public void setLengthDeclared(boolean lengthDeclared) { this.lengthDeclared = lengthDeclared; }
    
    public int getByteOffset() { return byteOffset; }
    public void setByteOffset(int byteOffset) { this.byteOffset = byteOffset; }
    
    /** The `bit:` of a `bool` field, or -1 when the field does not declare one. */
    /** A computed field written {@code type: integer} (PS-283); its type is NUMBER. */
    private boolean integerResult;
    public boolean isIntegerResult() { return integerResult; }
    public void setIntegerResult(boolean integerResult) { this.integerResult = integerResult; }

    private int boolBit = -1;
    public int getBoolBit() { return boolBit; }
    public void setBoolBit(int boolBit) { this.boolBit = boolBit; }

    public int getBitOffset() { return bitOffset; }
    public void setBitOffset(int bitOffset) { this.bitOffset = bitOffset; }
    
    public int getBits() { return bits; }
    public void setBits(int bits) { this.bits = bits; }

    public int getConsume() { return consume; }
    public void setConsume(int consume) { this.consume = consume; }

    /** Whether the schema wrote `consume` at all, so `consume: 0` differs from none. */
    private boolean consumeDeclared;
    public boolean isConsumeDeclared() { return consumeDeclared; }
    public void setConsumeDeclared(boolean declared) { this.consumeDeclared = declared; }

    /**
     * A `bytes`, `hex` or `ascii` field declaring `consume: 0` (PS-588, CR-2026-175): it
     * reads and reports its span and leaves the read position, so later fields read the
     * same bytes. An encoder writes nothing for it (PS-589).
     */
    public boolean isEcho() {
        return consumeDeclared && consume == 0
                && (type == FieldType.BYTES || type == FieldType.HEX || type == FieldType.ASCII
                    || isPeekedNumber());
    }

    /**
     * A whole numeric field declaring `consume: 0` (PS-603, CR-2026-182): an integer, a
     * float, a minifloat, `udec`/`sdec` or a word-ordered variant. It reads its value in
     * place and leaves the read position; an encoder writes nothing for it (PS-604).
     */
    public boolean isPeekedNumber() {
        return consumeDeclared && consume == 0 && wholeNumberSize(type) > 0;
    }

    /** The byte width of a whole numeric type (PS-453), 0 for any other. */
    static int wholeNumberSize(FieldType t) {
        if (t == null) return 0;
        return switch (t) {
            case U8, I8, S8, UDEC, SDEC -> 1;
            case U16, I16, S16, F16, BF16, UFLT16, SFLT16 -> 2;
            case U24, I24, S24, SFLT24 -> 3;
            case U32, I32, S32, F32, U32LE16, S32LE16, F32LE16, U32BE16LE, S32BE16LE, F32BE16LE -> 4;
            case U64, I64, S64, F64 -> 8;
            default -> 0;
        };
    }

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
    
    public List<Transform> getTransform() { return transform; }
    public void setTransform(List<Transform> transform) { this.transform = transform; }
    
    public Map<Integer, Object> getLookup() { return lookup; }
    public boolean isLookupSequence() { return lookupIsSequence; }
    public void setLookupSequence(boolean value) { this.lookupIsSequence = value; }
    public Object getLookupDefault() { return lookupDefault; }
    public void setLookupDefault(Object value) { this.lookupDefault = value; }
    /** A string field's `template` (PS-590, CR-2026-176), or null. */
    private String template;
    public String getTemplate() { return template; }
    public void setTemplate(String value) { this.template = value; }
    /** A `type: string` field declaring `template`: computed, reading no bytes (PS-590). */
    public boolean isTemplate() { return type == FieldType.STRING && template != null; }

    public String getNameFrom() { return nameFrom; }
    public void setNameFrom(String value) { this.nameFrom = value; }
    public void setLookup(Map<Integer, Object> lookup) { this.lookup = lookup; }
    
    public String getVar() { return var; }
    public void setVar(String var) { this.var = var; }

    /**
     * The {@code default} of an Option B match: {@code "error"}, {@code "skip"}, or a
     * field list decoded when no case matches (CR-2026-020). Distinct from
     * {@link Case#isDefault()}, which marks a case keyed {@code default}.
     */
    private Object matchDefault;

    public Object getMatchDefault() { return matchDefault; }
    public void setMatchDefault(Object matchDefault) { this.matchDefault = matchDefault; }

    // 0.5.2 wave 6a: the repeat iterator and reserved trailers (CR-2026-048, -053, -055,
    // -080, -081). See Wave6a for the schema rules.

    /** A repeat's element index name (PS-366). */
    private String index;
    /** A repeat's element count name, bound once the repeat completes (PS-367). */
    private String countAs;
    /** A repeat's element filter, one guard condition (PS-386). */
    private Guard presentIf;
    /** A computed element field's value before the first element (PS-378). */
    private Object carry;
    private boolean hasCarry;
    /** Bytes at the payload's end a repeat or tlv loop must not read (PS-350, PS-471). */
    private int reserve;
    /** Fields decoded from a repeat's reserved bytes (PS-383). */
    private List<Field> trailer;

    /** A {@code records} entry (PS-541): field lists, each yielding one record. */
    private List<List<Field>> records;

    public List<List<Field>> getRecords() { return records; }
    public void setRecords(List<List<Field>> records) { this.records = records; }

    /**
     * A repeat of single values (PS-528): the name its one element field is decoded under,
     * the element's own name or "element". Null for a repeat declaring {@code fields}.
     */
    private String elementName;
    /** The number a repeat's index starts at (PS-530). */
    private long indexBase;

    public String getElementName() { return elementName; }
    public void setElementName(String elementName) { this.elementName = elementName; }

    /**
     * A packed repeat's element width in bits, 0 where it is not packed, and its element
     * type's width and signedness (PS-621, CR-2026-189); its bit offset is getBitOffset().
     */
    private int packedBits;
    private int packedWidth;
    private boolean packedSigned;

    public int getPackedBits() { return packedBits; }
    public int getPackedWidth() { return packedWidth; }
    public boolean isPackedSigned() { return packedSigned; }
    public void setPacked(int bits, int width, boolean signed) {
        this.packedBits = bits;
        this.packedWidth = width;
        this.packedSigned = signed;
    }

    public long getIndexBase() { return indexBase; }
    public void setIndexBase(long indexBase) { this.indexBase = indexBase; }

    public String getIndex() { return index; }
    public void setIndex(String index) { this.index = index; }

    public String getCountAs() { return countAs; }
    public void setCountAs(String countAs) { this.countAs = countAs; }

    public Guard getPresentIf() { return presentIf; }
    public void setPresentIf(Guard presentIf) { this.presentIf = presentIf; }

    public Object getCarry() { return carry; }
    public boolean hasCarry() { return hasCarry; }
    public void setCarry(Object carry) { this.carry = carry; this.hasCarry = true; }

    public int getReserve() { return reserve; }
    public void setReserve(int reserve) { this.reserve = reserve; }

    public List<Field> getTrailer() { return trailer; }
    public void setTrailer(List<Field> trailer) { this.trailer = trailer; }

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
    OnRepeat.Modes getRepeatModes() { return repeatModes; }
    void setRepeatModes(OnRepeat.Modes repeatModes) { this.repeatModes = repeatModes; }
    
    public List<List<Object>> getParts() { return parts; }
    public void setParts(List<List<Object>> parts) { this.parts = parts; }
    
    public String getDelimiter() { return delimiter; }
    public void setDelimiter(String delimiter) { this.delimiter = delimiter; }
    
    public String getPrefix() { return prefix; }
    public void setPrefix(String prefix) { this.prefix = prefix; }
    

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
        if (length < 0) {
            // `length: remaining` sentinel - passed through so DecodeContext.read
            // resolves it against the payload rather than falling back to the type
            // default, which would read a single byte.
            return length;
        }
        // `length: 0` on a type that reads `length` bytes reads nothing (PS-392 for a
        // skip). It was taken as no length and one byte was read, so a zero-length skip
        // in a tlv case swallowed the next entry's tag (milesight gs601's 0xEE request).
        if (lengthDeclared && (type == FieldType.SKIP || type == FieldType.BYTES
                || type == FieldType.ASCII || type == FieldType.STRING
                || type == FieldType.HEX || type == FieldType.BASE64)) {
            return 0;
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
        /** {@code even} (the default), {@code away} from zero or {@code up} toward +infinity, for {@code op: round} (PS-390). */
        private String ties;
        /** Bound stages (PS-115). This binding had none and passed the value through. */
        private Double floor;
        private Double ceiling;

        public String getTies() { return ties; }
        public void setTies(String ties) { this.ties = ties; }
        public Double getFloor() { return floor; }
        public void setFloor(Double floor) { this.floor = floor; }
        public Double getCeiling() { return ceiling; }
        public void setCeiling(Double ceiling) { this.ceiling = ceiling; }

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
        /** The `flagged:` mapping as written, which the decode record locates (PS-572). */
        private Map<String, Object> raw;

        public Map<String, Object> getRaw() { return raw; }
        public void setRaw(Map<String, Object> raw) { this.raw = raw; }

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
        /** The numbers of an {@code in} or {@code not_in} condition (PS-543). */
        private List<Double> operands;

        public List<Double> getOperands() { return operands; }
        public void setOperands(List<Double> operands) { this.operands = operands; }

        public String getField() { return field; }
        public void setField(String field) { this.field = field; }

        public String getOp() { return op; }
        public void setOp(String op) { this.op = op; }

        public Double getOperand() { return operand; }
        public void setOperand(Double operand) { this.operand = operand; }
    }
}
