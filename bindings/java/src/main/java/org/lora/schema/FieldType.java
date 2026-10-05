package org.lora.schema;

public enum FieldType {
    // Unsigned integers
    U8, U16, U24, U32, U64,
    /** Two 16-bit big-endian units, least significant unit first (PS-271). */
    U32LE16, S32LE16,
    /** The word-ordered float (PS-362) and the fourth ordering (PS-363). */
    F32LE16, U32BE16LE, S32BE16LE, F32BE16LE,
    /** The MCCI minifloats (PS-417 to PS-419). */
    UFLT16, SFLT16, SFLT24,
    // Signed integers
    I8, I16, I24, I32, I64, S8, S16, S24, S32, S64,
    // Floating point
    F16, F32, F64,
    // Boolean and bits
    BOOL, BITS,
    // String types
    ASCII, HEX, BASE64, STRING,
    // Byte types
    BYTES, SKIP,
    // Complex types
    OBJECT, MATCH, SWITCH, TLV, REPEAT,
    // Nibble-decimal (PS-329)
    UDEC, SDEC,
    // Computed; INTEGER reports its result as an integer (PS-283)
    NUMBER, INTEGER,
    // Enumeration
    ENUM,
    // Bitfield string
    BITFIELD_STRING,
    // Legacy names
    BYTE, UINT, SINT, BINT, FLOAT16, FLOAT32, FLOAT64;

    /**
     * The field type a schema's `type:` string names.
     *
     * <p>The vocabulary is closed (CR-2026-037). A spelling outside clause 2 is a schema
     * error naming the field and the spelling (PS-327, PS-328); the alias table is
     * exhaustive (PS-326); and names are case-sensitive, never lowercased or rewritten
     * (PS-333). This lowercased its input and turned {@code -} into {@code _}, so
     * {@code U8}, {@code FLOAT32} and {@code Ctrl-Switch} all resolved here and nowhere
     * else, and it accepted a private vocabulary ({@code byte}, {@code float16},
     * {@code boolean}, {@code switch}, {@code uint}, {@code bits}) no other
     * implementation reads, while missing seven of the ten PS-049 aliases.
     *
     * <p>{@code null} is still U8: a field carrying a construct instead ({@code tlv:},
     * {@code match:}, {@code flagged:}, {@code byte_group:}, {@code $ref}) declares no
     * type, and {@link Schema} rejects a field that has neither (PS-334) before calling
     * this. An empty string is a field that declares no type.
     *
     * <p>A {@code u8[lo:hi]} bit range never reaches here - {@link Schema} matches the
     * bracket form before calling this, because this method cannot recognise it.
     */
    public static FieldType fromString(String type) {
        if (type == null) {
            return U8;
        }
        return switch (type) {
            case "u8", "uint8" -> U8;
            case "u16", "uint16" -> U16;
            case "u24", "uint24" -> U24;
            case "u32", "uint32" -> U32;
            case "u64", "uint64" -> U64;
            case "u32le16" -> U32LE16;
            case "f32le16" -> F32LE16;
            case "u32be16le" -> U32BE16LE;
            case "s32be16le" -> S32BE16LE;
            case "f32be16le" -> F32BE16LE;
            case "uflt16" -> UFLT16;
            case "sflt16" -> SFLT16;
            case "sflt24" -> SFLT24;
            case "s32le16" -> S32LE16;
            case "s8", "i8", "int8" -> I8;
            case "s16", "i16", "int16" -> I16;
            case "s24", "i24", "int24" -> I24;
            case "s32", "i32", "int32" -> I32;
            case "s64", "i64", "int64" -> I64;
            case "f16" -> F16;
            case "f32" -> F32;
            case "f64" -> F64;
            case "udec" -> UDEC;
            case "sdec" -> SDEC;
            case "bool" -> BOOL;
            case "ascii" -> ASCII;
            case "hex" -> HEX;
            case "base64" -> BASE64;
            case "string" -> STRING;
            case "bytes" -> BYTES;
            case "skip" -> SKIP;
            case "object" -> OBJECT;
            case "match" -> MATCH;
            case "repeat" -> REPEAT;
            case "number" -> NUMBER;
            case "integer" -> INTEGER;
            case "enum" -> ENUM;
            case "bitfield_string" -> BITFIELD_STRING;
            case "" -> throw new SchemaException("Field declares no type");
            default -> throw new SchemaException("Unknown field type: " + type);
        };
    }

    public int defaultLength() {
        return switch (this) {
            case U8, I8, S8, BOOL, BYTE -> 1;
            case U16, I16, S16, F16 -> 2;
            case U24, I24, S24 -> 3;
            case U32, I32, S32, F32, UINT, SINT, BINT, U32LE16, S32LE16,
                 F32LE16, U32BE16LE, S32BE16LE, F32BE16LE -> 4;
            case U64, I64, S64, F64 -> 8;
            default -> 1;
        };
    }

    public boolean isInteger() {
        return switch (this) {
            case U8, U16, U24, U32, U64, I8, I16, I24, I32, I64, S8, S16, S24, S32, S64,
                 BYTE, UINT, SINT, BINT, BITS, U32LE16, S32LE16, U32BE16LE, S32BE16LE -> true;
            default -> false;
        };
    }

    public boolean isSigned() {
        return switch (this) {
            case I8, I16, I24, I32, I64, S8, S16, S24, S32, S64, SINT, S32LE16, S32BE16LE -> true;
            default -> false;
        };
    }

    public boolean isFloat() {
        return switch (this) {
            case F16, F32, F64, FLOAT16, FLOAT32, FLOAT64 -> true;
            default -> false;
        };
    }
}
