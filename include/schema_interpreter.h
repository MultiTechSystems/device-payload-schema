/*
 * schema_interpreter.h - Runtime Payload Schema Interpreter
 *
 * Decodes LoRaWAN payloads using schema definitions at runtime.
 * Portable C99, no dynamic memory allocation option available.
 *
 * Supports:
 * - Programmatic schema building (field_u8, field_s16, etc.)
 * - Binary schema loading (schema_load_binary)
 *
 * Binary schema format 3 (compact, ~4 bytes/field plus names; see "Binary Schema
 * Loading" below and tools/schema_binary.py):
 *   Header: 'P' 'S' 3 flags count
 *     flags bit 0    : 1 = little-endian
 *     flags bits 1-2 : declared direction, 0 unset / 1 uplink / 2 downlink / 3 both
 *     count bit 7    : a name table follows; bits 0-6 the field count
 *   Per field: type_byte mult_exp ipso_obj[2] [options]
 *   Name table: per field, length byte + UTF-8 name (PS-555)
 *
 * Usage (programmatic):
 *   schema_t schema;
 *   schema_init(&schema);
 *   schema_add_field(&schema, &field_s16("temperature", ENDIAN_BIG));
 *   
 * Usage (binary):
 *   schema_t schema;
 *   schema_load_binary(&schema, binary_data, binary_len);
 *   
 *   decode_result_t result;
 *   schema_decode(&schema, payload, payload_len, &result);
 */

#ifndef SCHEMA_INTERPRETER_H
#define SCHEMA_INTERPRETER_H

#include <stdint.h>
#include <stddef.h>
#include <stdbool.h>
#include <string.h>
#include <stdlib.h>
/* snprintf and vsnprintf format error messages, schema_load_binary_msg's among them.
 * The header once used snprintf without declaring it, so it only compiled where the
 * including file happened to have pulled in <stdio.h> already. */
#include <stdio.h>
#include <stdarg.h>

#ifdef __cplusplus
extern "C" {
#endif

/* Configuration - adjust for your platform */
#ifndef SCHEMA_MAX_FIELDS
#define SCHEMA_MAX_FIELDS 32
#define SCHEMA_MAX_PAYLOAD 256
#endif

#ifndef SCHEMA_MAX_NAME_LEN
#define SCHEMA_MAX_NAME_LEN 32
#endif

#ifndef SCHEMA_MAX_CASES
#define SCHEMA_MAX_CASES 16
#endif

#ifndef SCHEMA_MAX_LOOKUP
#define SCHEMA_MAX_LOOKUP 16
#endif

/* ============================================
 * Type Definitions
 * ============================================ */

typedef enum {
    FIELD_TYPE_U8 = 0,
    FIELD_TYPE_U16,
    FIELD_TYPE_U24,
    FIELD_TYPE_U32,
    /* Two 16-bit big-endian units, least significant unit first (PS-271). Appended near
     * its siblings rather than at the end because this enum is not serialised - the
     * binary format writes its own type bytes. */
    FIELD_TYPE_U32LE16,
    FIELD_TYPE_S32LE16,
    FIELD_TYPE_U64,
    FIELD_TYPE_S8,
    FIELD_TYPE_S16,
    FIELD_TYPE_S24,
    FIELD_TYPE_S32,
    FIELD_TYPE_S64,
    FIELD_TYPE_F16,     /* IEEE 754 half-precision */
    FIELD_TYPE_F32,
    FIELD_TYPE_F64,
    FIELD_TYPE_BOOL,
    FIELD_TYPE_BITS,
    FIELD_TYPE_SKIP,
    FIELD_TYPE_ASCII,
    FIELD_TYPE_HEX,
    FIELD_TYPE_BASE64,
    FIELD_TYPE_BYTES,
    FIELD_TYPE_OBJECT,
    FIELD_TYPE_MATCH,
    FIELD_TYPE_ENUM,
    FIELD_TYPE_BYTE_GROUP,
    FIELD_TYPE_UDEC,    /* Nibble-decimal: upper=whole, lower=tenths */
    FIELD_TYPE_SDEC,    /* Signed nibble-decimal */
    FIELD_TYPE_UNKNOWN,
    /* Appended after UNKNOWN on purpose: UNKNOWN is parse_type_string's sentinel and
     * inserting before it would renumber it (CR-2026-033). */
    FIELD_TYPE_TLV,
    FIELD_TYPE_FLAGGED
} field_type_t;

typedef enum {
    ENDIAN_DEFAULT = 0,    /* Not explicitly set - use schema default */
    ENDIAN_BIG = 1,
    ENDIAN_LITTLE = 2
} endian_t;

typedef union {
    int64_t  i64;
    uint64_t u64;
    double   f64;
    bool     b;
    char     str[SCHEMA_MAX_NAME_LEN];
    uint8_t  bytes[SCHEMA_MAX_NAME_LEN];
} field_value_t;

typedef struct {
    int key;
    char value[SCHEMA_MAX_NAME_LEN];
} lookup_entry_t;

typedef struct field_def field_def_t;

typedef struct {
    int match_value;           /* Single value or -1 for default */
    int match_list[8];         /* List of values, -1 terminated */
    int range_min, range_max;  /* Range match */
    bool is_default;
    int field_start;           /* Index into fields array */
    int field_count;
} case_def_t;

struct field_def {
    char name[SCHEMA_MAX_NAME_LEN];
    field_type_t type;
    uint8_t size;              /* Size in bytes; for a bit range, its base's (0 is 1) */
    uint8_t bit_start;         /* Bit position for bitfields */
    uint8_t bit_width;         /* Bit width for bitfields */
    /* Bytes a bit range or bool advances by (`consume: N`, PS-016/PS-060), 0 for none.
     * A bool once; `true` is still 1, so a builder passing a bool keeps its meaning. */
    uint8_t consume;
    /* A bit range on an `sN` base: sign-extended from the range's width (PS-352,
     * PS-353). Sits in what was padding before `endian`, so field_def_t keeps its size. */
    bool bit_signed;
    /* CR-2026-174 (PS-586): `on_mismatch: warn` on a checksum, else a mismatch is an
     * error. In what was padding after bit_signed, so field_def_t keeps its size. */
    bool checksum_warn;
    endian_t endian;
    
    /* Modifiers */
    double mult;
    double div;
    double add;
    bool has_mult, has_div, has_add;
    
    /* Variable storage */
    char var_name[SCHEMA_MAX_NAME_LEN];
    
    /* Lookup table */
    lookup_entry_t lookup[SCHEMA_MAX_LOOKUP];
    int lookup_count;
    /* True where the table came from a YAML sequence rather than a mapping. Both
     * forms are stored keyed, so without this the two failure cases cannot be told
     * apart: a mapping gap omits the field (PS-269) while an out-of-bounds sequence
     * index is an error (PS-105). Defaults to false, so a table built by hand with
     * field_add_lookup keeps the mapping behaviour. */
    bool lookup_is_sequence;
    
    /* For match type */
    char match_var[SCHEMA_MAX_NAME_LEN];
    case_def_t cases[SCHEMA_MAX_CASES];
    int case_count;
    
    /* For nested objects */
    int nested_start;
    int nested_count;

    /* For tlv (CR-2026-033). Last members, so existing initializers leave them zero,
     * which is the "not a tlv" state.
     *
     * A tag is packed into case_def_t.match_value: a one-byte tag is its own value, and a
     * two-component tag is (first << 8) | second. Every tag component in the corpus is a
     * u8 and no tag has more than two, so the packing is exact rather than a hash - and
     * `tlv_tag_parts` records which form was meant so a mismatch cannot be silent.
     *
     * `merge` is not represented: this interpreter reports a flat field list, which is
     * merge:true, and no corpus schema sets merge at all. A schema that did would need a
     * channel list decode_result_t cannot express, so the harness skips it rather than
     * reporting a wrong shape. */
    uint8_t tlv_tag_size;      /* bytes, when the tag is one integer */
    uint8_t tlv_tag_parts;     /* 0 = use tlv_tag_size, else the component count */
    uint8_t tlv_length_size;   /* bytes of length after the tag, 0 for none */
    uint8_t tlv_unknown;       /* SCHEMA_TLV_UNKNOWN_* */

    /* CR-2026-174 (PS-585): a checksum over the frame's bytes from `checksum_from` up to
     * the byte before this field. SCHEMA_CHECKSUM_NONE (0) for an ordinary field, so
     * every existing initializer leaves it off. Set with field_set_checksum(), which
     * applies PS-585's rules. In what was the struct's tail padding: field_def_t keeps
     * its size (1640 bytes on LP64). */
    uint8_t checksum_algo;     /* SCHEMA_CHECKSUM_* */
    uint8_t checksum_seed;     /* sum8 and xor8 only */
    uint16_t checksum_from;    /* an absolute offset into the payload */
};

/* The checksum algorithms of PS-585 (CR-2026-174). */
#define SCHEMA_CHECKSUM_NONE          0
#define SCHEMA_CHECKSUM_SUM8          1
#define SCHEMA_CHECKSUM_XOR8          2
#define SCHEMA_CHECKSUM_CRC8_SMBUS    3
#define SCHEMA_CHECKSUM_CRC16_KERMIT  4

/* What to do with a tag no case describes (PS-155, PS-301..PS-304). `raw` is absent
 * deliberately: capturing the bytes needs somewhere to put them, and decode_result_t has
 * no channel for it - claiming support would be worse than not having it. */
#define SCHEMA_TLV_UNKNOWN_SKIP  0
#define SCHEMA_TLV_UNKNOWN_ERROR 1

typedef struct {
    char name[SCHEMA_MAX_NAME_LEN];
    field_value_t value;
    field_type_t type;
    bool valid;
    /* The index, in schema->fields, of the field that produced this value. It is how a
     * value is found where the schema carries no names - a binary schema loaded without
     * its name table (PS-555, CR-2026-142) - and `name` is then empty, never made up.
     * See result_get_index(). Last member, so existing initializers leave it zero. */
    int field_index;
    /* An `ascii` value's length in bytes. `value.str` is NUL-terminated as well, but a
     * 0x00 followed by other text is kept (PS-534), so the C string stops early where
     * the value does not: read `text_len` bytes. Each byte is the ISO 8859-1 character
     * of the same number (PS-538) - 0xE9 is U+00E9 - so a writer of JSON or UTF-8 maps
     * a byte above 0x7F to that code point rather than copying it. */
    uint8_t text_len;
    /* PS-073's warning, as a flag: the value holds a byte above 0x7F. This interpreter
     * has no warning list, so it reports the fact and a caller renders the text the
     * other five give, "Field '{name}': a byte above 0x7F in an ascii value (PS-073,
     * PS-538)". */
    bool non_ascii;
    /* PS-586's warning, as a flag (CR-2026-174): this field's checksum, declared
     * `on_mismatch: warn`, did not match. An internal (`_`) checksum is not reported, so
     * the decode result carries the same fact - see decode_result_t.checksum_warnings.
     * In what was padding: decoded_field_t keeps its size. */
    bool checksum_mismatch;
} decoded_field_t;

/* Which way a message travels, for a schema or a decode call.
 *
 * SCHEMA_DIR_UNSET is zero, so a schema built before this existed reads as "declares
 * nothing", which PS-287 treats as `both`: the check is opt-in and no existing schema
 * changes meaning. There is deliberately no spelling for the withdrawn `bidirectional`
 * value - an enum cannot express it, so the case the other implementations detect and
 * reject at runtime is unrepresentable here. */
typedef enum {
    SCHEMA_DIR_UNSET = 0,
    SCHEMA_DIR_UPLINK,
    SCHEMA_DIR_DOWNLINK,
    SCHEMA_DIR_BOTH
} schema_direction_t;

typedef struct {
    char name[SCHEMA_MAX_NAME_LEN];
    int version;
    endian_t endian;
    field_def_t fields[SCHEMA_MAX_FIELDS];
    int field_count;
    /* This interpreter has no port selection, so a direction can only be declared for
     * the schema as a whole (PS-291). Last member, so existing initializers leave it
     * SCHEMA_DIR_UNSET. */
    schema_direction_t direction;
    /* Set by schema_load_binary for a blob with no name table (CR-2026-142). Its fields
     * have empty names, and a decode reports each value by field index only: PS-555
     * forbids reporting the decoded object from a form that carries no names, and
     * forbids deriving a name from the IPSO object, type or position. */
    bool nameless;
} schema_t;

typedef struct {
    decoded_field_t fields[SCHEMA_MAX_FIELDS];
    int field_count;
    int bytes_consumed;
    int error_code;
    /* 152 rather than 64: the point of a shared message text is that every
     * implementation reports the same string, and the build treats a possible
     * truncation as an error, so the buffer has to hold the worst case rather than the
     * typical one. The longest is PS-486's length cut short, at 146 bytes with a
     * SCHEMA_MAX_NAME_LEN tlv name and three full-width numbers; PS-288's, which names
     * the schema and both directions, is 103. 256 since CR-2026-193: PS-630 wraps a
     * PS-535 message (141 at most) in the entry's tag, offset and length, 258 at most. */
    char error_msg[272];
    /* PS-472: the bytes after the last field that nothing decoded. The other five
     * implementations report these as a warning; this interpreter has no warning list
     * (a fixed-size, allocation-free result has nowhere to keep text), so it reports the
     * warning's two numbers instead: the offset of the first byte not decoded is
     * `bytes_consumed` and the count to the end is this. Set only on a successful
     * decode, and never changes what was decoded. It covers the bytes PS-302 leaves
     * after an unknown, undelimited tlv tag too, counted from that tag as the other
     * five count them. Last member, so existing initializers leave it zero. */
    int bytes_unread;
    /* The schema carried no names (schema_t.nameless). Every value is then reachable
     * by result_get_index() and none by name, and a writer of the decoded object - such
     * as bindings/c's schema_ffi_result_to_json() - must refuse (PS-555). */
    bool nameless;
    /* PS-586's warning (CR-2026-174), as numbers, as `bytes_unread` is PS-472's: how many
     * checksums declared `on_mismatch: warn` did not match, and for the first of them
     * the index in schema->fields of the field and the offset it was read at.
     * schema_checksum_warning() renders the text the other five implementations give,
     * "Field '{name}': {algo} over bytes {from} to {at-1} is 0x{computed}, but the frame
     * carries 0x{carried} (PS-586)", from these and the payload. In what was the
     * struct's tail padding: decode_result_t keeps its size. */
    uint8_t checksum_warnings;
    int16_t checksum_warning_field;
    uint16_t checksum_warning_at;
} decode_result_t;

/* Variable storage for match conditions.
 *
 * What a variable holds is PS-523's binding (CR-2026-109, CR-2026-132): the field's value
 * after its modifiers and before its lookup - so a text label binds the number under it,
 * and an enum its code. That value may have a fraction (`div: 2` on a raw 3 binds 1.5),
 * which an integer cannot hold, so `number` carries it and `integral` says whether
 * `value` is the same number exactly. */
typedef struct {
    char name[SCHEMA_MAX_NAME_LEN];
    int64_t value;      /* the binding, where `integral` */
    double number;      /* the binding, always */
    bool integral;
} variable_t;

typedef struct {
    variable_t vars[SCHEMA_MAX_FIELDS];
    int count;
} var_context_t;

/* ============================================
 * Error Codes
 * ============================================ */

#define SCHEMA_OK              0
#define SCHEMA_ERR_PARSE      -1
#define SCHEMA_ERR_BUFFER     -2
#define SCHEMA_ERR_OVERFLOW   -3
#define SCHEMA_ERR_TYPE       -4
#define SCHEMA_ERR_MATCH      -5
#define SCHEMA_ERR_UNSUPPORTED -6
#define SCHEMA_ERR_MISSING    -7
#define SCHEMA_ERR_LOOKUP     -8   /* Out-of-bounds sequence lookup index (PS-105) */
#define SCHEMA_ERR_DIRECTION  -9   /* Message travels a direction the schema disclaims (PS-288) */
#define SCHEMA_ERR_CHECKSUM  -10   /* A checksum disagrees with its frame (PS-586), or its
                                    * range is not before it (PS-585, PS-587) */

/* ============================================
 * Byte Reading Utilities
 * ============================================ */

static inline uint8_t read_u8(const uint8_t* buf) {
    return buf[0];
}

static inline uint16_t read_u16_be(const uint8_t* buf) {
    return ((uint16_t)buf[0] << 8) | buf[1];
}

static inline uint16_t read_u16_le(const uint8_t* buf) {
    return ((uint16_t)buf[1] << 8) | buf[0];
}

static inline uint32_t read_u24_be(const uint8_t* buf) {
    return ((uint32_t)buf[0] << 16) | ((uint32_t)buf[1] << 8) | buf[2];
}

static inline uint32_t read_u24_le(const uint8_t* buf) {
    return ((uint32_t)buf[2] << 16) | ((uint32_t)buf[1] << 8) | buf[0];
}

static inline uint32_t read_u32_be(const uint8_t* buf) {
    return ((uint32_t)buf[0] << 24) | ((uint32_t)buf[1] << 16) |
           ((uint32_t)buf[2] << 8) | buf[3];
}

static inline uint32_t read_u32_le(const uint8_t* buf) {
    return ((uint32_t)buf[3] << 24) | ((uint32_t)buf[2] << 16) |
           ((uint32_t)buf[1] << 8) | buf[0];
}

static inline int8_t read_s8(const uint8_t* buf) {
    return (int8_t)buf[0];
}

static inline int16_t read_s16_be(const uint8_t* buf) {
    return (int16_t)read_u16_be(buf);
}

static inline int16_t read_s16_le(const uint8_t* buf) {
    return (int16_t)read_u16_le(buf);
}

static inline int32_t read_s24_be(const uint8_t* buf) {
    uint32_t val = read_u24_be(buf);
    if (val & 0x800000) val |= 0xFF000000;  /* Sign extend */
    return (int32_t)val;
}

static inline int32_t read_s24_le(const uint8_t* buf) {
    uint32_t val = read_u24_le(buf);
    if (val & 0x800000) val |= 0xFF000000;
    return (int32_t)val;
}

static inline int32_t read_s32_be(const uint8_t* buf) {
    return (int32_t)read_u32_be(buf);
}

static inline int32_t read_s32_le(const uint8_t* buf) {
    return (int32_t)read_u32_le(buf);
}

static inline uint64_t read_u64_be(const uint8_t* buf) {
    return ((uint64_t)read_u32_be(buf) << 32) | read_u32_be(buf + 4);
}

static inline uint64_t read_u64_le(const uint8_t* buf) {
    return ((uint64_t)read_u32_le(buf + 4) << 32) | read_u32_le(buf);
}

static inline int64_t read_s64_be(const uint8_t* buf) {
    return (int64_t)read_u64_be(buf);
}

static inline int64_t read_s64_le(const uint8_t* buf) {
    return (int64_t)read_u64_le(buf);
}

static inline float read_f32_be(const uint8_t* buf) {
    union { uint32_t u; float f; } conv;
    conv.u = read_u32_be(buf);
    return conv.f;
}

static inline float read_f32_le(const uint8_t* buf) {
    union { uint32_t u; float f; } conv;
    conv.u = read_u32_le(buf);
    return conv.f;
}

static inline double read_f64_be(const uint8_t* buf) {
    union { uint64_t u; double d; } conv;
    conv.u = read_u64_be(buf);
    return conv.d;
}

static inline double read_f64_le(const uint8_t* buf) {
    union { uint64_t u; double d; } conv;
    conv.u = read_u64_le(buf);
    return conv.d;
}

/* IEEE 754 half-precision float decode */
static inline float read_f16_be(const uint8_t* buf) {
    uint16_t h = read_u16_be(buf);
    uint32_t sign = (h >> 15) & 1;
    uint32_t exp = (h >> 10) & 0x1F;
    uint32_t frac = h & 0x3FF;
    
    if (exp == 0) {
        if (frac == 0) return sign ? -0.0f : 0.0f;
        /* Subnormal */
        float val = (float)frac / 1024.0f;
        val *= 6.103515625e-05f;  /* 2^-14 */
        return sign ? -val : val;
    }
    if (exp == 31) {
        if (frac == 0) return sign ? -1.0f/0.0f : 1.0f/0.0f;
        return 0.0f/0.0f;  /* NaN */
    }
    
    float val = (1.0f + (float)frac / 1024.0f);
    int actual_exp = (int)exp - 15;
    if (actual_exp > 0) {
        for (int i = 0; i < actual_exp; i++) val *= 2.0f;
    } else {
        for (int i = 0; i < -actual_exp; i++) val /= 2.0f;
    }
    return sign ? -val : val;
}

static inline float read_f16_le(const uint8_t* buf) {
    uint8_t swapped[2] = {buf[1], buf[0]};
    return read_f16_be(swapped);
}

/* ============================================
 * Bitfield Extraction
 * ============================================ */

static inline uint8_t extract_bits(uint8_t byte, uint8_t start, uint8_t width) {
    return (byte >> start) & ((1 << width) - 1);
}

/* CR-2026-136: a bit range's base is `size` bytes - one for `u8[...]`, two for
 * `u16[...]` - assembled in the field's effective byte order (PS-059), and its bits are
 * numbered from the least significant bit of that value (PS-058). This interpreter read
 * a range from one byte only, and unsigned. */
static inline int bits_base_bytes(const field_def_t* field) {
    return field->size ? field->size : 1;
}

static inline uint64_t bits_width_mask(int width) {
    return width >= 64 ? ~(uint64_t)0 : (((uint64_t)1 << width) - 1);
}

static inline uint64_t bits_read_base(const uint8_t* p, int n, endian_t endian) {
    uint64_t v = 0;
    for (int i = 0; i < n; i++) {
        v = (v << 8) | p[endian == ENDIAN_LITTLE ? n - 1 - i : i];
    }
    return v;
}

/* The bits byte `b` of an `n`-byte base holds of a value-space mask. */
static inline uint8_t bits_byte_mask(uint64_t mask, int n, int b, endian_t endian) {
    int shift = 8 * (endian == ENDIAN_LITTLE ? b : n - 1 - b);
    return (uint8_t)((mask >> shift) & 0xFF);
}

/* The range's value from an assembled base, sign-extended from its width for an `sN`
 * base (PS-353). */
static inline int64_t bits_extract(uint64_t base, int start, int width, bool is_signed) {
    uint64_t v = (base >> start) & bits_width_mask(width);
    if (is_signed && width < 64 && ((v >> (width - 1)) & 1)) v |= ~bits_width_mask(width);
    return (int64_t)v;
}

/* ============================================
 * Type Parsing from String
 * ============================================ */

static inline field_type_t parse_type_string(const char* type_str, 
                                              uint8_t* bit_start,
                                              uint8_t* bit_width) {
    *bit_start = 0;
    *bit_width = 0;
    
    /* Bitfield range u8[3:5] - bits 3..5 inclusive.
     *
     * The only bitfield spelling. CR-2026-006 withdrew the Verilog part-select
     * u8[3+:2], the C++ template bits<3,2>, the @ notation bits:2@3 and the
     * sequential u8:2, which were carried while the working group chose between
     * them. Two of the four were not really implemented here anyway: the
     * sequential form set a bit_start sentinel of 255 that no decode path ever
     * read.
     */
    {
        int start, end;
        if (sscanf(type_str, "u8[%d:%d]", &start, &end) == 2) {
            *bit_start = (uint8_t)start;
            *bit_width = (uint8_t)(end - start + 1);
            return FIELD_TYPE_BITS;
        }
        if (sscanf(type_str, "u16[%d:%d]", &start, &end) == 2) {
            *bit_start = (uint8_t)start;
            *bit_width = (uint8_t)(end - start + 1);
            return FIELD_TYPE_BITS;
        }
    }
    
    /* Standard types */
    if (strcmp(type_str, "u8") == 0 || strcmp(type_str, "uint8") == 0) 
        return FIELD_TYPE_U8;
    if (strcmp(type_str, "u16") == 0 || strcmp(type_str, "uint16") == 0) 
        return FIELD_TYPE_U16;
    if (strcmp(type_str, "u24") == 0 || strcmp(type_str, "uint24") == 0) 
        return FIELD_TYPE_U24;
    if (strcmp(type_str, "u32") == 0 || strcmp(type_str, "uint32") == 0) 
        return FIELD_TYPE_U32;
    if (strcmp(type_str, "u64") == 0 || strcmp(type_str, "uint64") == 0) 
        return FIELD_TYPE_U64;
    if (strcmp(type_str, "s8") == 0 || strcmp(type_str, "i8") == 0 || 
        strcmp(type_str, "int8") == 0) 
        return FIELD_TYPE_S8;
    if (strcmp(type_str, "s16") == 0 || strcmp(type_str, "i16") == 0 || 
        strcmp(type_str, "int16") == 0) 
        return FIELD_TYPE_S16;
    if (strcmp(type_str, "s24") == 0 || strcmp(type_str, "i24") == 0 || 
        strcmp(type_str, "int24") == 0) 
        return FIELD_TYPE_S24;
    if (strcmp(type_str, "s32") == 0 || strcmp(type_str, "i32") == 0 || 
        strcmp(type_str, "int32") == 0) 
        return FIELD_TYPE_S32;
    if (strcmp(type_str, "s64") == 0 || strcmp(type_str, "i64") == 0 || 
        strcmp(type_str, "int64") == 0) 
        return FIELD_TYPE_S64;
    if (strcmp(type_str, "f16") == 0) return FIELD_TYPE_F16;
    /* `float`, `double`, `UDec` and `SDec` are not types of the specification, and the
     * vocabulary is closed and case-sensitive (CR-2026-037: PS-326, PS-331, PS-333). */
    if (strcmp(type_str, "f32") == 0) return FIELD_TYPE_F32;
    if (strcmp(type_str, "f64") == 0) return FIELD_TYPE_F64;
    if (strcmp(type_str, "bool") == 0) return FIELD_TYPE_BOOL;
    if (strcmp(type_str, "skip") == 0) return FIELD_TYPE_SKIP;
    if (strcmp(type_str, "ascii") == 0 || strcmp(type_str, "string") == 0) 
        return FIELD_TYPE_ASCII;
    if (strcmp(type_str, "hex") == 0) return FIELD_TYPE_HEX;
    if (strcmp(type_str, "base64") == 0) return FIELD_TYPE_BASE64;
    if (strcmp(type_str, "bytes") == 0) return FIELD_TYPE_BYTES;
    if (strcmp(type_str, "object") == 0) return FIELD_TYPE_OBJECT;
    if (strcmp(type_str, "match") == 0) return FIELD_TYPE_MATCH;
    if (strcmp(type_str, "enum") == 0) return FIELD_TYPE_ENUM;
    if (strcmp(type_str, "udec") == 0) return FIELD_TYPE_UDEC;
    if (strcmp(type_str, "sdec") == 0) return FIELD_TYPE_SDEC;
    
    return FIELD_TYPE_UNKNOWN;
}

/* ============================================
 * Variable Context Management
 * ============================================ */

/* The slot holding `name`, made where it is new; NULL where the table is full. */
static inline variable_t* var_slot(var_context_t* ctx, const char* name) {
    for (int i = 0; i < ctx->count; i++) {
        if (strcmp(ctx->vars[i].name, name) == 0) return &ctx->vars[i];
    }
    if (ctx->count >= SCHEMA_MAX_FIELDS) return NULL;
    variable_t* v = &ctx->vars[ctx->count++];
    memset(v, 0, sizeof(*v));
    strncpy(v->name, name, SCHEMA_MAX_NAME_LEN - 1);
    return v;
}

static inline void var_set(var_context_t* ctx, const char* name, int64_t value) {
    variable_t* v = var_slot(ctx, name);
    if (!v) return;
    v->value = value;
    v->number = (double)value;
    v->integral = true;
}

/* Bind a number that may have a fraction (PS-523). An integral one inside int64 is held
 * exactly in `value` as well, so a `match` and a `flagged` mask read it as before. */
static inline void var_set_number(var_context_t* ctx, const char* name, double number) {
    variable_t* v = var_slot(ctx, name);
    if (!v) return;
    v->number = number;
    v->integral = number >= -9223372036854775808.0 && number < 9223372036854775808.0
                  && (double)(int64_t)number == number;
    v->value = v->integral ? (int64_t)number : 0;
}

/* Leave `name` unbound: a field a step before its lookup omits binds nothing (PS-523),
 * and a binding left from an earlier entry - a tlv repeats its cases - must not stand in
 * for it. */
static inline void var_unset(var_context_t* ctx, const char* name) {
    for (int i = 0; i < ctx->count; i++) {
        if (strcmp(ctx->vars[i].name, name) == 0) {
            ctx->vars[i] = ctx->vars[--ctx->count];
            return;
        }
    }
}

/* The binding of `name`, or NULL where it is unbound. */
static inline const variable_t* var_find(const var_context_t* ctx, const char* name) {
    for (int i = 0; i < ctx->count; i++) {
        if (strcmp(ctx->vars[i].name, name) == 0) return &ctx->vars[i];
    }
    return NULL;
}

/* Whether a variable was ever set. `var_get` returns 0 for a miss, which for a `flagged`
 * mask is indistinguishable from "no bits set" - so the construct would decode nothing and
 * report success. The reference interpreter raises instead (CR-2026-034). */
static inline bool var_has(const var_context_t* ctx, const char* name) {
    for (int i = 0; i < ctx->count; i++) {
        if (strcmp(ctx->vars[i].name, name) == 0) return true;
    }
    return false;
}

static inline int64_t var_get(var_context_t* ctx, const char* name) {
    for (int i = 0; i < ctx->count; i++) {
        if (strcmp(ctx->vars[i].name, name) == 0) {
            return ctx->vars[i].value;
        }
    }
    return 0;
}

/* ============================================
 * Schema Initialization
 * ============================================ */

static inline void schema_init(schema_t* schema) {
    memset(schema, 0, sizeof(schema_t));
    schema->endian = ENDIAN_BIG;
}

/* A tlv whose tag is one integer of `tag_size` bytes. */
static inline field_def_t field_tlv(uint8_t tag_size, uint8_t length_size) {
    field_def_t f;
    memset(&f, 0, sizeof(f));
    f.type = FIELD_TYPE_TLV;
    f.tlv_tag_size = tag_size ? tag_size : 1;
    f.tlv_tag_parts = 0;
    f.tlv_length_size = length_size;
    f.tlv_unknown = SCHEMA_TLV_UNKNOWN_SKIP;
    return f;
}

/* A tlv whose tag is `parts` single-byte components, read in order. */
static inline field_def_t field_tlv_composite(uint8_t parts, uint8_t length_size) {
    field_def_t f = field_tlv(1, length_size);
    f.tlv_tag_parts = parts;
    return f;
}

/* Pack a tag into the integer a case is keyed by. Mirrors the decode side exactly, so a
 * builder and the interpreter cannot disagree about the encoding. */
static inline int schema_tlv_tag(const int* parts, int count) {
    int packed = 0;
    for (int i = 0; i < count; i++) packed = (packed << 8) | (parts[i] & 0xFF);
    return packed;
}

/* A `flagged` construct: a previously decoded field read as a bitmask, with a group of
 * fields per bit (PS-158..PS-163).
 *
 * `flags_field` names the field holding the mask. **That field must carry `var_name`**, or
 * the mask cannot be found: this interpreter records a value in the variable table only
 * where a field declares one, and `flagged` refers to a field by name rather than by
 * variable. A builder that forgets it gets SCHEMA_ERR_MATCH rather than a silent decode of
 * nothing (CR-2026-034).
 *
 * Reuses `match_var` and `cases[]`: a group is a case whose match_value is its bit
 * position, and whose field_start/field_count point at a body placed above field_count. */
static inline field_def_t field_flagged(const char* flags_field) {
    field_def_t f;
    memset(&f, 0, sizeof(f));
    f.type = FIELD_TYPE_FLAGGED;
    strncpy(f.match_var, flags_field, SCHEMA_MAX_NAME_LEN - 1);
    return f;
}

/* Add a flagged group: the bit that selects it, and the range of fields it contributes.
 * Groups are evaluated in the order added, which is the order their bytes appear (PS-160). */
static inline bool field_add_flagged_group(field_def_t* f, int bit,
                                           int field_start, int field_count) {
    if (f->case_count >= SCHEMA_MAX_CASES) return false;
    if (bit < 0 || bit > 63) return false;
    case_def_t* c = &f->cases[f->case_count];
    memset(c, 0, sizeof(*c));
    c->match_value = bit;
    c->match_list[0] = -1;
    c->field_start = field_start;
    c->field_count = field_count;
    f->case_count++;
    return true;
}

/* Add a tlv case: its packed tag, and the range of schema fields holding its body. */
static inline bool field_add_tlv_case(field_def_t* f, int packed_tag,
                                      int field_start, int field_count) {
    if (f->case_count >= SCHEMA_MAX_CASES) return false;
    case_def_t* c = &f->cases[f->case_count];
    memset(c, 0, sizeof(*c));
    c->match_value = packed_tag;
    c->match_list[0] = -1;
    c->field_start = field_start;
    c->field_count = field_count;
    f->case_count++;
    return true;
}

static inline void schema_add_field(schema_t* schema, const field_def_t* field) {
    if (schema->field_count < SCHEMA_MAX_FIELDS) {
        schema->fields[schema->field_count++] = *field;
    }
}

/* Place a field the top-level loop must NOT walk, at an explicit index above
 * `field_count` - a tlv or match case body, reached only through
 * `case_def_t.field_start` (CR-2026-033).
 *
 * The alternative, which src/test_comprehensive.c's match test does, is to add case bodies
 * as counted fields after the construct. Then the top-level loop decodes each of them a
 * second time, from wherever the position happens to be: that test passes only because it
 * asserts `field_count >= 2` rather than what was decoded. A tlv cannot be built that way
 * at all - its bodies are re-read as garbage and the payload runs out.
 *
 * Returns false where the index is outside the array, so a caller cannot silently lose a
 * case body. */
static inline bool schema_place_field(schema_t* schema, int index,
                                      const field_def_t* field) {
    if (index < 0 || index >= SCHEMA_MAX_FIELDS) return false;
    schema->fields[index] = *field;
    return true;
}

/* ============================================
 * Decode Single Field
 * ============================================ */

/* Whether this field's declared type selects `integer` in the clause 1 table and nothing
 * in the field turns it into a `number` (PS-279, PS-293).
 *
 * Every numeric field used to be stored in the union's double member with the declared
 * type in the tag beside it, so the tag said integer while the value was a double: a
 * consumer trusting the tag and reading value.i64 on a u16 of 60 got
 * 4633641066610819072, the bit pattern of 60.0. */
static inline bool field_reports_as_integer(const field_def_t* field) {
    switch (field->type) {
        case FIELD_TYPE_U8:  case FIELD_TYPE_U16: case FIELD_TYPE_U24:
        case FIELD_TYPE_U32: case FIELD_TYPE_U64:
        case FIELD_TYPE_S8:  case FIELD_TYPE_S16: case FIELD_TYPE_S24:
        case FIELD_TYPE_S32: case FIELD_TYPE_S64:
        case FIELD_TYPE_U32LE16: case FIELD_TYPE_S32LE16:
        /* A bit range is an integer of its base's signedness; it was reported through
         * the double member, tagged FIELD_TYPE_F64, though nothing scaled it. */
        case FIELD_TYPE_BITS:
            break;
        default:
            return false;
    }
    return !field->has_mult && !field->has_div && !field->has_add;
}

/* Everything after a numeric field's source, in the order PS-443 fixes: the modifiers
 * mult, div, add (PS-101), then the lookup. This interpreter has no `transform` stage, so
 * nothing sits between the two; a schema declaring one must be refused before it gets
 * here (PS-446) - tools/schema_binary.py does that for the binary form.
 *
 * Returns true where it settled the field: a zero divisor omits it (PS-100), a lookup hit
 * writes the label into `.str`, a mapping gap omits it (PS-269), and an index a sequence
 * does not have is SCHEMA_ERR_LOOKUP (PS-105), in `*rc`. False means the caller stores
 * `*value`, already modified, in whichever member its type uses.
 *
 * This is the one place a lookup is applied. There were two - the ordinary-field path and
 * the enum path - and the ordinary one keyed the table on the *raw* value while the
 * modifiers it had just computed went unused, so `u8` with `add: 1` and
 * `lookup: [a, b, c]` decoded raw 1 as "b" where every other implementation gives "c".
 * The float, u64, udec/sdec and enum paths each returned early with only part of this
 * (the floats with no modifiers at all, the enum with none, udec/sdec with no lookup),
 * which PS-446 forbids: a declared modifier or lookup is applied, or the schema is
 * rejected, never skipped. */
static inline bool finish_numeric(
    const field_def_t* field,
    double* value,
    decoded_field_t* out,
    int* rc,
    var_context_t* vars,
    const int64_t* exact
) {
    *rc = SCHEMA_OK;
    if (field->has_mult) *value *= field->mult;
    if (field->has_div) {
        if (field->div == 0) {
            /* A zero divisor omits the field (PS-100), and a step before the lookup
             * that omits the field leaves its reference unbound (PS-523). */
            out->valid = false;
            if (vars && field->var_name[0]) var_unset(vars, field->var_name);
            return true;
        }
        *value /= field->div;
    }
    if (field->has_add) *value += field->add;
    /* A negative zero is reported as 0 (PS-566), whichever float type, modifier or arithmetic
     * made it; the comparison is true for either zero and the assignment is the positive one. */
    if (*value == 0.0) *value = 0.0;

    /* PS-523 (CR-2026-109, CR-2026-132): a reference binds the value here, after the
     * modifiers and before the lookup - so a text label binds the number under it, an
     * enum its code, and a mapping with no entry (PS-269) omits the field below while
     * the reference stays bound. The raw value used to be bound before any of this, so
     * `div: 10` on a variable bound ten times the value the field reported. `exact` is
     * the integer read, where there is one, so an unmodified s64 binds without passing
     * through a double. A lookup that reports a number binds that number instead; this
     * interpreter holds every label as text (PS-106, a known C gap), so it has none. */
    if (vars && field->var_name[0]) {
        if (exact && !field->has_mult && !field->has_div && !field->has_add) {
            var_set(vars, field->var_name, *exact);
        } else {
            var_set_number(vars, field->var_name, *value);
        }
    }

    if (field->lookup_count == 0) return false;

    /* The key is the value after the arithmetic. An integral one is its key; one with a
     * fraction is no key of any table (PS-107), so it falls to the miss below. */
    bool integral = *value >= -2147483648.0 && *value <= 2147483647.0
                    && (double)(int64_t)*value == *value;
    if (integral) {
        int key = (int)(int64_t)*value;
        for (int i = 0; i < field->lookup_count; i++) {
            if (field->lookup[i].key == key) {
                strncpy(out->value.str, field->lookup[i].value, SCHEMA_MAX_NAME_LEN - 1);
                out->value.str[SCHEMA_MAX_NAME_LEN - 1] = '\0';
                out->valid = true;
                return true;
            }
        }
    }
    /* An index a sequence does not have is an error (PS-105); a mapping gap omits the
     * field (PS-269). */
    out->valid = false;
    if (field->lookup_is_sequence) *rc = SCHEMA_ERR_LOOKUP;
    return true;
}

/* ============================================
 * Checksum fields (CR-2026-174, PS-585 to PS-587)
 * ============================================
 *
 * A field of an unsigned integer type may declare a checksum over the frame's bytes from
 * `from` (an absolute offset, 0 by default) up to the byte before the field. A decoder
 * computes it and compares it with the value read, before any modifier (PS-586); an
 * encoder computes it over the finished frame and writes it, never taking it from the
 * input (PS-587). The four algorithms, with the catalogue's check values over the ASCII
 * bytes "123456789":
 *
 *   sum8          (seed + every byte) & 0xFF
 *   xor8          seed XOR every byte
 *   crc8/smbus    poly 0x07, init 0, not reflected, no final XOR          check 0xF4
 *   crc16/kermit  poly 0x1021 reflected (0x8408), init 0, no final XOR   check 0x2189
 */

static inline const char* schema_checksum_name(uint8_t algo) {
    switch (algo) {
        case SCHEMA_CHECKSUM_SUM8: return "sum8";
        case SCHEMA_CHECKSUM_XOR8: return "xor8";
        case SCHEMA_CHECKSUM_CRC8_SMBUS: return "crc8/smbus";
        case SCHEMA_CHECKSUM_CRC16_KERMIT: return "crc16/kermit";
        default: return "none";
    }
}

/* The bits an algorithm's result needs, 0 for none. */
static inline int schema_checksum_bits(uint8_t algo) {
    switch (algo) {
        case SCHEMA_CHECKSUM_SUM8: case SCHEMA_CHECKSUM_XOR8:
        case SCHEMA_CHECKSUM_CRC8_SMBUS: return 8;
        case SCHEMA_CHECKSUM_CRC16_KERMIT: return 16;
        default: return 0;
    }
}

/* The bytes of an unsigned integer type a checksum may be declared on, 0 for any other
 * type: a bit range, a signed or word-ordered integer, a float (PS-585). */
static inline int schema_checksum_type_bytes(field_type_t type) {
    switch (type) {
        case FIELD_TYPE_U8: return 1;
        case FIELD_TYPE_U16: return 2;
        case FIELD_TYPE_U24: return 3;
        case FIELD_TYPE_U32: return 4;
        case FIELD_TYPE_U64: return 8;
        default: return 0;
    }
}

static inline uint32_t schema_checksum_compute(uint8_t algo, const uint8_t* data,
                                               size_t n, uint8_t seed) {
    uint32_t v = 0;
    switch (algo) {
        case SCHEMA_CHECKSUM_SUM8:
            v = seed;
            for (size_t i = 0; i < n; i++) v += data[i];
            return v & 0xFF;
        case SCHEMA_CHECKSUM_XOR8:
            v = seed;
            for (size_t i = 0; i < n; i++) v ^= data[i];
            return v;
        case SCHEMA_CHECKSUM_CRC8_SMBUS:
            for (size_t i = 0; i < n; i++) {
                v ^= data[i];
                for (int b = 0; b < 8; b++) {
                    v = (v & 0x80) ? ((v << 1) ^ 0x07) & 0xFF : (v << 1) & 0xFF;
                }
            }
            return v;
        case SCHEMA_CHECKSUM_CRC16_KERMIT:
            for (size_t i = 0; i < n; i++) {
                v ^= data[i];
                for (int b = 0; b < 8; b++) {
                    v = (v & 1) ? (v >> 1) ^ 0x8408 : v >> 1;
                }
            }
            return v;
        default:
            return 0;
    }
}

/* PS-608 (CR-2026-185): `from` counts from the frame's first byte (PS-585), so a checksum
 * field is refused as a byte_group member or within a tlv entry - the reference rejects
 * both at load, and this interpreter, which has no load step for the struct API, where it
 * reaches one. The reference's text. */
static inline void schema_checksum_placement(const field_def_t* field, const char* inside,
                                             char* err, size_t err_len) {
    snprintf(err, err_len, "Field '%.*s': a checksum field cannot sit within %s; its "
             "`from` counts from the frame (PS-608)", SCHEMA_MAX_NAME_LEN - 1, field->name,
             inside);
}

/* PS-586: whether `field`'s checksum agrees with the frame, where it is read at `at`.
 * SCHEMA_OK where it does, or where the field's bytes are not all there (the read reports
 * that, PS-535). SCHEMA_ERR_CHECKSUM where the range starts at or after the field, so it
 * covers nothing (PS-608, CR-2026-185), or
 * the value disagrees under `on_mismatch: error`; under `warn` the disagreement sets
 * `*mismatch` and the field decodes. SCHEMA_ERR_TYPE for a checksum on a type that cannot
 * hold one, which field_set_checksum() never builds. */
static inline int schema_checksum_check(const field_def_t* field, const uint8_t* buf,
                                        size_t len, size_t at, endian_t endian,
                                        bool* mismatch) {
    const int width = schema_checksum_type_bytes(field->type);
    if (width == 0) return SCHEMA_ERR_TYPE;
    if (at + (size_t)width > len) return SCHEMA_OK;
    if (field->checksum_from >= at) return SCHEMA_ERR_CHECKSUM;   /* PS-608: covers nothing */
    const uint64_t carried = bits_read_base(buf + at, width, endian);
    const uint32_t computed = schema_checksum_compute(
        field->checksum_algo, buf + field->checksum_from, at - field->checksum_from,
        field->checksum_seed);
    if ((uint64_t)computed == carried) return SCHEMA_OK;
    if (field->checksum_warn) {
        *mismatch = true;
        return SCHEMA_OK;
    }
    return SCHEMA_ERR_CHECKSUM;
}

/* The text of a checksum read at `at` that schema_checksum_check() did not accept: the
 * range starting at or after the field (PS-608), else PS-586's mismatch - the error text, and
 * under `on_mismatch: warn` the warning's. The other five implementations give the same
 * strings. The worst case, a 31-character name, crc16/kermit, two five-digit offsets and
 * a sixteen-digit carried value, is 140 characters and its NUL. */
static inline void schema_checksum_message(char* msg, size_t msg_len,
                                           const field_def_t* field, const uint8_t* buf,
                                           size_t at, endian_t endian) {
    if (field->checksum_from >= at) {
        snprintf(msg, msg_len, "Field '%.*s': the checksum range starts at byte %u, at or "
                 "after the field at byte %u, so it covers nothing (PS-608)",
                 SCHEMA_MAX_NAME_LEN - 1, field->name, (unsigned)field->checksum_from,
                 (unsigned)at);
        return;
    }
    const int width = schema_checksum_type_bytes(field->type);
    const uint64_t carried = width ? bits_read_base(buf + at, width, endian) : 0;
    const uint32_t computed = schema_checksum_compute(
        field->checksum_algo, buf + field->checksum_from, at - field->checksum_from,
        field->checksum_seed);
    snprintf(msg, msg_len, "Field '%.*s': %s over bytes %u to %d is 0x%X, but the frame "
             "carries 0x%llX (PS-586)", SCHEMA_MAX_NAME_LEN - 1, field->name,
             schema_checksum_name(field->checksum_algo), (unsigned)field->checksum_from,
             (int)at - 1, (unsigned)computed, (unsigned long long)carried);
}

/* Records a field's PS-586 warning in the decode result, from the flag decode_field set
 * on its value; `end` is the position after it. */
static inline void schema_note_checksum(decode_result_t* result, const decoded_field_t* out,
                                        const field_def_t* field, int index, size_t end) {
    if (!out->checksum_mismatch) return;
    if (result->checksum_warnings == 0) {
        result->checksum_warning_field = (int16_t)index;
        result->checksum_warning_at =
            (uint16_t)(end - (size_t)schema_checksum_type_bytes(field->type));
    }
    if (result->checksum_warnings < 255) result->checksum_warnings++;
}

static inline int decode_field(
    const field_def_t* field,
    const uint8_t* buf,
    size_t len,
    size_t* pos,
    decoded_field_t* out,
    var_context_t* vars,
    endian_t default_endian
) {
    endian_t endian = (field->endian != ENDIAN_DEFAULT) ? field->endian : default_endian;
    int64_t raw_value = 0;
    double final_value = 0;
    
    out->valid = false;
    strncpy(out->name, field->name, SCHEMA_MAX_NAME_LEN - 1);
    out->type = field->type;
    out->checksum_mismatch = false;

    /* PS-586 (CR-2026-174): the check value against the bytes it covers, before the
     * field is read, so a mismatch under `on_mismatch: error` leaves `pos` at the field
     * for the message. A field whose bytes are not all there is the read's error. */
    if (field->checksum_algo != SCHEMA_CHECKSUM_NONE) {
        int crc = schema_checksum_check(field, buf, len, *pos, endian,
                                        &out->checksum_mismatch);
        if (crc != SCHEMA_OK) return crc;
    }
    
    switch (field->type) {
        case FIELD_TYPE_U8:
            if (*pos + 1 > len) return SCHEMA_ERR_BUFFER;
            raw_value = read_u8(buf + *pos);
            *pos += 1;
            break;
            
        case FIELD_TYPE_U16:
            if (*pos + 2 > len) return SCHEMA_ERR_BUFFER;
            raw_value = endian == ENDIAN_BIG ? 
                read_u16_be(buf + *pos) : read_u16_le(buf + *pos);
            *pos += 2;
            break;
            
        case FIELD_TYPE_U24:
            if (*pos + 3 > len) return SCHEMA_ERR_BUFFER;
            raw_value = endian == ENDIAN_BIG ? 
                read_u24_be(buf + *pos) : read_u24_le(buf + *pos);
            *pos += 3;
            break;
            
        case FIELD_TYPE_U32:
            if (*pos + 4 > len) return SCHEMA_ERR_BUFFER;
            raw_value = endian == ENDIAN_BIG ? 
                read_u32_be(buf + *pos) : read_u32_le(buf + *pos);
            *pos += 4;
            break;

        /* The type fixes both the unit order and the byte order within a unit, so the
         * endian setting is deliberately not consulted (PS-272). */
        case FIELD_TYPE_U32LE16:
        case FIELD_TYPE_S32LE16: {
            if (*pos + 4 > len) return SCHEMA_ERR_BUFFER;
            uint32_t low = read_u16_be(buf + *pos);
            uint32_t high = read_u16_be(buf + *pos + 2);
            uint32_t combined = low | (high << 16);
            if (field->type == FIELD_TYPE_S32LE16 && combined >= 0x80000000u) {
                raw_value = (int64_t)combined - 0x100000000LL;
            } else {
                raw_value = (int64_t)combined;
            }
            *pos += 4;
            break;
        }
            
        case FIELD_TYPE_S8:
            if (*pos + 1 > len) return SCHEMA_ERR_BUFFER;
            raw_value = read_s8(buf + *pos);
            *pos += 1;
            break;
            
        case FIELD_TYPE_S16:
            if (*pos + 2 > len) return SCHEMA_ERR_BUFFER;
            raw_value = endian == ENDIAN_BIG ? 
                read_s16_be(buf + *pos) : read_s16_le(buf + *pos);
            *pos += 2;
            break;
            
        case FIELD_TYPE_S24:
            if (*pos + 3 > len) return SCHEMA_ERR_BUFFER;
            raw_value = endian == ENDIAN_BIG ? 
                read_s24_be(buf + *pos) : read_s24_le(buf + *pos);
            *pos += 3;
            break;
            
        case FIELD_TYPE_S32:
            if (*pos + 4 > len) return SCHEMA_ERR_BUFFER;
            raw_value = endian == ENDIAN_BIG ? 
                read_s32_be(buf + *pos) : read_s32_le(buf + *pos);
            *pos += 4;
            break;
            
        case FIELD_TYPE_U64:
            if (*pos + 8 > len) return SCHEMA_ERR_BUFFER;
            out->value.u64 = endian == ENDIAN_BIG ?
                read_u64_be(buf + *pos) : read_u64_le(buf + *pos);
            *pos += 8;
            {
                /* Bound exactly where int64 holds it; past 2^63 through a double. */
                const int64_t as_int = (int64_t)out->value.u64;
                const int64_t* exact = out->value.u64 <= (uint64_t)INT64_MAX ? &as_int : NULL;
                if (field_reports_as_integer(field) && field->lookup_count == 0) {
                    /* PS-294: the exact value is already in value.u64. It used to be
                     * overwritten with (double)value.u64 four lines further on, which is
                     * how a u64 of 2^64-1 came back as 18446744073709551616. */
                    if (field->var_name[0]) {
                        if (exact) var_set(vars, field->var_name, as_int);
                        else var_set_number(vars, field->var_name, (double)out->value.u64);
                    }
                    out->valid = true;
                    return SCHEMA_OK;
                }
                /* Carrying a modifier makes the field a `number` (PS-279). */
                final_value = (double)out->value.u64;
                int rc;
                if (finish_numeric(field, &final_value, out, &rc, vars, exact)) return rc;
            }
            out->value.f64 = final_value;
            out->type = FIELD_TYPE_F64;
            out->valid = true;
            return SCHEMA_OK;
            
        case FIELD_TYPE_S64:
            if (*pos + 8 > len) return SCHEMA_ERR_BUFFER;
            raw_value = endian == ENDIAN_BIG ?
                read_s64_be(buf + *pos) : read_s64_le(buf + *pos);
            *pos += 8;
            break;
            
        /* A float's modifiers and lookup apply as an integer's do (PS-443). These three
         * returned straight after the read, so `f32` with `mult: 0.1` reported the
         * unscaled reading - while encode_field, below, did reverse the modifiers, so a
         * float field did not even round-trip. */
        case FIELD_TYPE_F16:
            if (*pos + 2 > len) return SCHEMA_ERR_BUFFER;
            final_value = endian == ENDIAN_BIG ?
                read_f16_be(buf + *pos) : read_f16_le(buf + *pos);
            *pos += 2;
            goto finish_float;

        case FIELD_TYPE_F32:
            if (*pos + 4 > len) return SCHEMA_ERR_BUFFER;
            final_value = endian == ENDIAN_BIG ?
                read_f32_be(buf + *pos) : read_f32_le(buf + *pos);
            *pos += 4;
            goto finish_float;

        case FIELD_TYPE_F64:
            if (*pos + 8 > len) return SCHEMA_ERR_BUFFER;
            final_value = endian == ENDIAN_BIG ?
                read_f64_be(buf + *pos) : read_f64_le(buf + *pos);
            *pos += 8;
        finish_float: {
            /* A float, udec or sdec binds like any field (PS-523); these returned before
             * the variable was ever set, so a reference to one was unbound. */
            int rc;
            if (finish_numeric(field, &final_value, out, &rc, vars, NULL)) return rc;
            out->value.f64 = final_value;
            out->valid = true;
            return SCHEMA_OK;
        }
            
        case FIELD_TYPE_BOOL:
            if (*pos + 1 > len) return SCHEMA_ERR_BUFFER;
            {
                uint8_t byte = buf[*pos];
                uint8_t bit_pos = field->bit_start;
                out->value.b = ((byte >> bit_pos) & 1) != 0;
            }
            /* `consume: N` advances N bytes (PS-016); past the payload it is a short read
             * of N (PS-535), reported by schema_underflow_message. */
            if (field->consume) {
                if (*pos + field->consume > len) return SCHEMA_ERR_BUFFER;
                *pos += field->consume;
            }
            out->valid = true;
            if (field->var_name[0]) var_set(vars, field->var_name, out->value.b ? 1 : 0);
            return SCHEMA_OK;
            
        case FIELD_TYPE_BITS: {
            /* CR-2026-136 (PS-059, PS-352, PS-353): the base is `uN`/`sN`, N/8 bytes in
             * the field's effective byte order; a signed range sign-extends from its own
             * width. It was one byte, unsigned, whatever the type said. */
            const int base = bits_base_bytes(field);
            if (field->bit_width == 0 || base > 8
                || field->bit_start + field->bit_width > base * 8) {
                return SCHEMA_ERR_TYPE;
            }
            if (*pos + (size_t)base > len) return SCHEMA_ERR_BUFFER;
            uint64_t assembled = bits_read_base(buf + *pos, base, endian);
            raw_value = bits_extract(assembled, field->bit_start, field->bit_width,
                                     field->bit_signed);
            if (!field->bit_signed && field->bit_width == 64 && raw_value < 0) {
                /* A full u64 range past 2^63 has no int64 to be reported in. */
                return SCHEMA_ERR_UNSUPPORTED;
            }
            if (field->consume) {
                if (*pos + field->consume > len) return SCHEMA_ERR_BUFFER;
                *pos += field->consume;
            }
            break;
        }
            
        /* A skip past the end is a short read like any other. It stepped *pos past len
         * and reported success, so bytes_consumed exceeded the payload and bytes_unread
         * went negative. `length: 0` advances by nothing (PS-392): a size of 0 was read
         * as one byte, so zero-length.yaml's second field ran past the payload. The
         * absent-length default of one byte is the builder's (field_skip). */
        case FIELD_TYPE_SKIP: {
            size_t width = field->size;
            if (*pos + width > len) return SCHEMA_ERR_BUFFER;
            *pos += width;
            return SCHEMA_OK;
        }
            
        /* CR-2026-114. Only the trailing 0x00 bytes are padding (PS-534): a 0x00 with
         * text after it is kept, which a C string cannot show, so `text_len` carries the
         * length. Every byte is reported as itself, the ISO 8859-1 character of the same
         * number (PS-538), and one above 0x7F raises the PS-073 flag. This copied the
         * field into a C string and called that the value, so "AB\0C" read as "AB".
         *
         * A value is never cut short to fit: one longer than SCHEMA_MAX_NAME_LEN - 1
         * bytes, once its padding is dropped, is SCHEMA_ERR_UNSUPPORTED (PS-538 forbids
         * dropping a byte). It used to be truncated to 31 bytes and reported. */
        case FIELD_TYPE_ASCII: {
            size_t width = field->size;
            if (*pos + width > len) return SCHEMA_ERR_BUFFER;
            size_t n = width;
            while (n > 0 && buf[*pos + n - 1] == 0x00) n--;
            if (n > SCHEMA_MAX_NAME_LEN - 1) return SCHEMA_ERR_UNSUPPORTED;
            memcpy(out->value.str, buf + *pos, n);
            out->value.str[n] = '\0';
            out->text_len = (uint8_t)n;
            out->non_ascii = false;
            for (size_t c = 0; c < n; c++) {
                if (buf[*pos + c] > 0x7F) out->non_ascii = true;
            }
            *pos += width;
            out->valid = true;
            return SCHEMA_OK;
        }
            
        case FIELD_TYPE_HEX:
            if (*pos + field->size > len) return SCHEMA_ERR_BUFFER;
            {
                int hex_len = field->size < (SCHEMA_MAX_NAME_LEN / 2) ? field->size : (SCHEMA_MAX_NAME_LEN / 2 - 1);
                for (int h = 0; h < hex_len; h++) {
                    static const char hex_chars[] = "0123456789ABCDEF";
                    out->value.str[h * 2] = hex_chars[(buf[*pos + h] >> 4) & 0x0F];
                    out->value.str[h * 2 + 1] = hex_chars[buf[*pos + h] & 0x0F];
                }
                out->value.str[hex_len * 2] = '\0';
            }
            *pos += field->size;
            out->valid = true;
            out->type = FIELD_TYPE_HEX;
            return SCHEMA_OK;
            
        case FIELD_TYPE_BASE64:
            if (*pos + field->size > len) return SCHEMA_ERR_BUFFER;
            {
                static const char b64[] = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/";
                int in_len = field->size;
                int out_idx = 0;
                for (int b = 0; b < in_len && out_idx < SCHEMA_MAX_NAME_LEN - 4; b += 3) {
                    uint32_t triple = ((uint32_t)buf[*pos + b]) << 16;
                    if (b + 1 < in_len) triple |= ((uint32_t)buf[*pos + b + 1]) << 8;
                    if (b + 2 < in_len) triple |= buf[*pos + b + 2];
                    out->value.str[out_idx++] = b64[(triple >> 18) & 0x3F];
                    out->value.str[out_idx++] = b64[(triple >> 12) & 0x3F];
                    out->value.str[out_idx++] = (b + 1 < in_len) ? b64[(triple >> 6) & 0x3F] : '=';
                    out->value.str[out_idx++] = (b + 2 < in_len) ? b64[triple & 0x3F] : '=';
                }
                out->value.str[out_idx] = '\0';
            }
            *pos += field->size;
            out->valid = true;
            out->type = FIELD_TYPE_BASE64;
            return SCHEMA_OK;
            
        case FIELD_TYPE_BYTES:
            if (*pos + field->size > len) return SCHEMA_ERR_BUFFER;
            {
                int copy_len = field->size < SCHEMA_MAX_NAME_LEN ? field->size : SCHEMA_MAX_NAME_LEN;
                memcpy(out->value.bytes, buf + *pos, copy_len);
            }
            *pos += field->size;
            out->valid = true;
            return SCHEMA_OK;
            
        case FIELD_TYPE_ENUM:
            /* Decode as underlying integer then apply enum lookup */
            if (*pos + field->size > len) return SCHEMA_ERR_BUFFER;
            {
                int esize = field->size ? field->size : 1;
                if (esize == 1) {
                    raw_value = read_u8(buf + *pos);
                } else if (esize == 2) {
                    raw_value = endian == ENDIAN_BIG ? read_u16_be(buf + *pos) : read_u16_le(buf + *pos);
                } else {
                    raw_value = read_u8(buf + *pos);
                }
                *pos += esize;
            }
            /* The table goes through finish_numeric like any other lookup, so an enum
             * and an integer field carrying the same table and modifiers agree - the two
             * sites used to be separate copies and disagreed twice. A sequence index out
             * of bounds is an error (PS-105); a mapping gap omits the field rather than
             * reporting "unknown(N)" (PS-269). The variable is bound to the code either
             * way, by finish_numeric, so $references keep working (PS-523). */
            if (field->lookup_count > 0) {
                int rc;
                final_value = (double)raw_value;
                finish_numeric(field, &final_value, out, &rc, vars, &raw_value);
                return rc;
            }
            break;
            
        case FIELD_TYPE_UDEC:
            /* Nibble-decimal: upper nibble = whole, lower = tenths */
            if (*pos + 1 > len) return SCHEMA_ERR_BUFFER;
            {
                uint8_t byte = buf[*pos];
                final_value = (byte >> 4) + (byte & 0x0F) * 0.1;
            }
            *pos += 1;
            goto finish_float;
            
        case FIELD_TYPE_SDEC:
            /* Signed nibble-decimal: sign-extend upper nibble */
            if (*pos + 1 > len) return SCHEMA_ERR_BUFFER;
            {
                uint8_t byte = buf[*pos];
                int8_t whole = (int8_t)(byte >> 4);
                if (whole >= 8) whole -= 16;  /* Sign extend 4-bit */
                final_value = whole + (byte & 0x0F) * 0.1;
            }
            *pos += 1;
            goto finish_float;
            
        default:
            return SCHEMA_ERR_TYPE;
    }
    
    /* The modifiers, the variable's binding, then the lookup on the modified value
     * (PS-443, PS-523). A zero divisor omits the field (PS-100); see finish_numeric for
     * the lookup's two misses. */
    final_value = (double)raw_value;
    {
        int rc;
        if (finish_numeric(field, &final_value, out, &rc, vars, &raw_value)) return rc;
    }
    if (field_reports_as_integer(field)) {
        /* PS-293: an integer-typed field is delivered through the integer member, so the
         * type tag and the value channel agree. */
        out->value.i64 = raw_value;
    } else {
        /* A field carrying a modifier is a `number`, and the tag says so (PS-279). It
         * used to keep the declared integer type, so a scaled field was reported as an
         * integer while holding a fraction. */
        out->value.f64 = final_value;
        out->type = FIELD_TYPE_F64;
    }
    
    out->valid = true;
    return SCHEMA_OK;
}

/* ============================================
 * PS-535: a short read names its field
 * ============================================ */

/* The spelling of a type in an unnamed field's PS-535 message ("an unnamed skip"). A
 * bit range's own spelling (`u8[3:4]`) is not kept by the binary schema, so it reads
 * "an unnamed bit range". */
static inline const char* schema_type_label(field_type_t type) {
    switch (type) {
        case FIELD_TYPE_U8: return "u8";
        case FIELD_TYPE_U16: return "u16";
        case FIELD_TYPE_U24: return "u24";
        case FIELD_TYPE_U32: return "u32";
        case FIELD_TYPE_U32LE16: return "u32le16";
        case FIELD_TYPE_S32LE16: return "s32le16";
        case FIELD_TYPE_U64: return "u64";
        case FIELD_TYPE_S8: return "s8";
        case FIELD_TYPE_S16: return "s16";
        case FIELD_TYPE_S24: return "s24";
        case FIELD_TYPE_S32: return "s32";
        case FIELD_TYPE_S64: return "s64";
        case FIELD_TYPE_F16: return "f16";
        case FIELD_TYPE_F32: return "f32";
        case FIELD_TYPE_F64: return "f64";
        case FIELD_TYPE_BOOL: return "bool";
        case FIELD_TYPE_BITS: return "bit range";
        case FIELD_TYPE_SKIP: return "skip";
        case FIELD_TYPE_ASCII: return "ascii";
        case FIELD_TYPE_HEX: return "hex";
        case FIELD_TYPE_BASE64: return "base64";
        case FIELD_TYPE_BYTES: return "bytes";
        case FIELD_TYPE_ENUM: return "enum";
        case FIELD_TYPE_UDEC: return "udec";
        case FIELD_TYPE_SDEC: return "sdec";
        default: return "field";
    }
}

/* The bytes a field's read needs, as decode_field checks them; 0 for a bool or a bit
 * range, which peek at their byte without consuming it. */
static inline size_t schema_read_need(const field_def_t* field) {
    switch (field->type) {
        case FIELD_TYPE_U8: case FIELD_TYPE_S8:
        case FIELD_TYPE_UDEC: case FIELD_TYPE_SDEC: return 1;
        case FIELD_TYPE_U16: case FIELD_TYPE_S16: case FIELD_TYPE_F16: return 2;
        case FIELD_TYPE_U24: case FIELD_TYPE_S24: return 3;
        case FIELD_TYPE_U32: case FIELD_TYPE_S32: case FIELD_TYPE_F32:
        case FIELD_TYPE_U32LE16: case FIELD_TYPE_S32LE16: return 4;
        case FIELD_TYPE_U64: case FIELD_TYPE_S64: case FIELD_TYPE_F64: return 8;
        case FIELD_TYPE_SKIP: return field->size ? field->size : 1;
        default: return field->size;
    }
}

/* CR-2026-115 (la-payload-schema main c932961): a read or skip past the end of the
 * payload is an error identifying the field, in the text the other five implementations
 * report, so a cross-language diff compares equal strings:
 *
 *   Field 'name': Buffer underflow: need N bytes at offset O, but only R remaining (PS-535)
 *   Field 'name': Buffer underflow at peek offset O (PS-535)          (a bool, a bit range)
 *   Field an unnamed skip: Buffer underflow: ...                      (no name)
 *
 * decode_field returns SCHEMA_ERR_BUFFER before it moves `pos`, so `pos` is the field's
 * offset. The worst case is 141 characters and its NUL - a quoted 31-character name and
 * three ten-digit numbers - inside error_msg's 152, checked with -Wformat-truncation=2. */
static inline void schema_underflow_message(
    decode_result_t* result,
    const field_def_t* field,
    size_t pos,
    size_t len
) {
    char label[SCHEMA_MAX_NAME_LEN + 2];       /* a quoted name, or "an unnamed <type>" */
    /* field_skip() gives a skip the placeholder name `_skip`; the schema named nothing. */
    const bool placeholder = field->type == FIELD_TYPE_SKIP &&
                             strcmp(field->name, "_skip") == 0;
    if (field->name[0] && !placeholder) {
        snprintf(label, sizeof(label), "'%s'", field->name);
    } else {
        snprintf(label, sizeof(label), "an unnamed %s", schema_type_label(field->type));
    }
    size_t need = schema_read_need(field);
    if (field->type == FIELD_TYPE_BOOL || field->type == FIELD_TYPE_BITS) {
        size_t peek = field->type == FIELD_TYPE_BITS ? (size_t)bits_base_bytes(field) : 1;
        if (pos + peek > len) {
            snprintf(result->error_msg, sizeof(result->error_msg),
                     "Field %s: Buffer underflow at peek offset %u (PS-535)",
                     label, (unsigned)pos);
            return;
        }
        need = field->consume;     /* the base was there; its `consume` was not */
    }
    snprintf(result->error_msg, sizeof(result->error_msg),
             "Field %s: Buffer underflow: need %u bytes at offset %u, but only %u "
             "remaining (PS-535)",
             label, (unsigned)need, (unsigned)pos,
             (unsigned)(pos < len ? len - pos : 0));
}

/* The error of a field that failed to decode: its code, and for a short read its
 * message (PS-535). */
static inline int schema_field_failed(
    decode_result_t* result,
    int rc,
    const field_def_t* field,
    const uint8_t* buf,
    size_t pos,
    size_t len,
    endian_t default_endian
) {
    result->error_code = rc;
    if (rc == SCHEMA_ERR_CHECKSUM && result->error_msg[0] == '\0') {
        /* PS-585, PS-586 (CR-2026-174), verbatim: nothing prefixes it. */
        schema_checksum_message(result->error_msg, sizeof(result->error_msg), field, buf,
                                pos, field->endian != ENDIAN_DEFAULT ? field->endian
                                                                     : default_endian);
    }
    if (rc == SCHEMA_ERR_TYPE && field->checksum_algo != SCHEMA_CHECKSUM_NONE
        && result->error_msg[0] == '\0') {
        snprintf(result->error_msg, sizeof(result->error_msg),
                 "Field '%.*s': a checksum is declared on an unsigned integer type (u8 to "
                 "u64), not '%s' (PS-585)", SCHEMA_MAX_NAME_LEN - 1, field->name,
                 schema_type_label(field->type));
    }
    if (rc == SCHEMA_ERR_BUFFER && result->error_msg[0] == '\0') {
        schema_underflow_message(result, field, pos, len);
    }
    if (rc == SCHEMA_ERR_UNSUPPORTED && field->type == FIELD_TYPE_ASCII
        && result->error_msg[0] == '\0') {
        /* A capacity of this interpreter, not of the payload: say which. */
        snprintf(result->error_msg, sizeof(result->error_msg),
                 "Field '%.*s': an ascii value longer than the %d bytes this "
                 "interpreter holds, not truncated (PS-538)",
                 SCHEMA_MAX_NAME_LEN - 1, field->name, SCHEMA_MAX_NAME_LEN - 1);
    }
    return rc;
}

/* PS-630 (CR-2026-193): a short read inside a tlv entry with a length is an error naming
 * the entry - its tag as the spec prototype writes it (a one-part tag a decimal, a two-part
 * one `[a, b]`) and its offset in the frame - and the entry's length, around the PS-535
 * message. Any other failure, or an entry with no length, is returned unchanged. */
static inline int schema_tlv_entry_overrun(
    decode_result_t* result,
    int rc,
    const field_def_t* tlv,
    int packed,
    size_t entry_start,
    int entry_len
) {
    if (rc != SCHEMA_ERR_BUFFER || entry_len < 0) return rc;
    char inner[sizeof(result->error_msg)];
    memcpy(inner, result->error_msg, sizeof(inner));
    inner[sizeof(inner) - 1] = '\0';
    char tag[32];
    if (tlv->tlv_tag_parts == 2) {
        snprintf(tag, sizeof(tag), "[%d, %d]", (packed >> 8) & 0xFF, packed & 0xFF);
    } else {
        snprintf(tag, sizeof(tag), "%u", (unsigned)packed);
    }
    snprintf(result->error_msg, sizeof(result->error_msg),
             "tlv entry tag %.12s at offset %u: %.141s; the case reads past the entry's %u "
             "value byte(s) (PS-630)", tag, (unsigned)entry_start, inner, (unsigned)entry_len);
    return rc;
}

/* ============================================
 * byte_group (CR-2026-131, PS-070, PS-364, PS-397)
 * ============================================
 *
 * A group is read as one value: `size` bytes - at least its widest member's base, which
 * is the width where `size` is 0 - assembled in the group's effective byte order, and
 * every member names bits of that value whatever its own base: a bit range `uN[a:b]` /
 * `sN[a:b]` its bits a..b, a whole integer (`u16` is `u16[0:15]`) bits 0 up, a `bool`
 * its `bit`, 0 to 7. FIELD_TYPE_BYTE_GROUP was an enum value with no decode path, so a
 * schema using the construct could not be expressed in C at all.
 *
 * The members are fields [nested_start, nested_start + nested_count) of the schema,
 * placed above field_count with schema_place_field, as a tlv case body is. */
static inline field_def_t field_byte_group(uint8_t size, endian_t endian,
                                           int member_start, int member_count) {
    field_def_t f;
    memset(&f, 0, sizeof(f));
    f.type = FIELD_TYPE_BYTE_GROUP;
    f.size = size;
    f.endian = endian;
    f.nested_start = member_start;
    f.nested_count = member_count;
    return f;
}

/* A member's base in bytes, and the bits of the group's value it names; false for a
 * type that cannot be a member. */
static inline bool byte_group_member_bits(const field_def_t* m, int* base, int* start,
                                          int* width, bool* is_signed) {
    *is_signed = false;
    *start = 0;
    switch (m->type) {
        case FIELD_TYPE_BITS:
            *base = bits_base_bytes(m); *start = m->bit_start; *width = m->bit_width;
            *is_signed = m->bit_signed;
            return true;
        case FIELD_TYPE_BOOL: *base = 1; *start = m->bit_start; *width = 1; return true;
        case FIELD_TYPE_S8: *is_signed = true; /* fall through */
        case FIELD_TYPE_U8: *base = 1; break;
        case FIELD_TYPE_S16: *is_signed = true; /* fall through */
        case FIELD_TYPE_U16: *base = 2; break;
        case FIELD_TYPE_S24: *is_signed = true; /* fall through */
        case FIELD_TYPE_U24: *base = 3; break;
        case FIELD_TYPE_S32: *is_signed = true; /* fall through */
        case FIELD_TYPE_U32: *base = 4; break;
        case FIELD_TYPE_S64: *is_signed = true; /* fall through */
        case FIELD_TYPE_U64: *base = 8; break;
        default: return false;
    }
    *width = *base * 8;
    return true;
}

/* The group's width, or a message in `err` and -1 where the group is invalid: a member
 * that cannot be one, a `bool` past bit 7, bits outside the member's own base or the
 * group, or a `size` narrower than a member (PS-070). */
static inline int byte_group_width(const schema_t* schema, const field_def_t* group,
                                   char* err, size_t err_len) {
    int widest = 0;
    for (int k = 0; k < group->nested_count; k++) {
        int idx = group->nested_start + k;
        if (idx < 0 || idx >= SCHEMA_MAX_FIELDS) {
            snprintf(err, err_len, "byte_group member %d is outside the schema", idx);
            return -1;
        }
        const field_def_t* m = &schema->fields[idx];
        int base, start, width;
        bool is_signed;
        if (!byte_group_member_bits(m, &base, &start, &width, &is_signed)) {
            snprintf(err, err_len, "byte_group member '%.*s' is a %s, which a byte_group "
                     "cannot hold (PS-070)", SCHEMA_MAX_NAME_LEN - 1, m->name,
                     schema_type_label(m->type));
            return -1;
        }
        if (m->type == FIELD_TYPE_BOOL && m->bit_start > 7) {
            snprintf(err, err_len, "byte_group member '%.*s' is a bool at bit %d; a bool "
                     "member is one bit of a u8, 0 to 7 (PS-070)",
                     SCHEMA_MAX_NAME_LEN - 1, m->name, m->bit_start);
            return -1;
        }
        if (width < 1 || base > 8 || start + width > base * 8) {
            snprintf(err, err_len, "byte_group member '%.*s' names bits outside its base",
                     SCHEMA_MAX_NAME_LEN - 1, m->name);
            return -1;
        }
        if (m->checksum_algo != SCHEMA_CHECKSUM_NONE) {
            /* PS-608 (CR-2026-185): a checksum field is not a byte_group member. */
            schema_checksum_placement(m, "a byte_group", err, err_len);
            return -1;
        }
        if (base > widest) widest = base;
    }
    if (group->size && group->size < widest) {
        snprintf(err, err_len, "byte_group declares size %d, narrower than its widest "
                 "member's %d bytes (PS-070)", group->size, widest);
        return -1;
    }
    int w = group->size ? group->size : widest;
    if (w < 1 || w > 8) {
        snprintf(err, err_len, "a byte_group of %d bytes; this interpreter assembles 1 "
                 "to 8", w);
        return -1;
    }
    /* PS-397: members' bits, in the group's value, do not overlap. */
    uint64_t masks[SCHEMA_MAX_FIELDS];
    for (int k = 0; k < group->nested_count && k < SCHEMA_MAX_FIELDS; k++) {
        const field_def_t* m = &schema->fields[group->nested_start + k];
        int base, start, width;
        bool is_signed;
        byte_group_member_bits(m, &base, &start, &width, &is_signed);
        masks[k] = bits_width_mask(width) << start;
        for (int j = 0; j < k; j++) {
            if (masks[j] & masks[k]) {
                snprintf(err, err_len, "byte_group members '%.*s' and '%.*s' overlap "
                         "(PS-397)", SCHEMA_MAX_NAME_LEN - 1,
                         schema->fields[group->nested_start + j].name,
                         SCHEMA_MAX_NAME_LEN - 1, m->name);
                return -1;
            }
        }
    }
    return w;
}

/* PS-535 as CR-2026-137 words it for a group: by every named member, `_` ones
 * included, in declaration order - "byte_group of 'a', 'b'" - else "an unnamed
 * byte_group". */
static inline void byte_group_underflow(decode_result_t* result, const schema_t* schema,
                                        const field_def_t* group, size_t pos) {
    /* As many names as fit beside the rest of the message in error_msg; a list cut
     * short ends at a whole name. */
    char label[92];
    size_t used = 0;
    label[0] = '\0';
    for (int k = 0; k < group->nested_count; k++) {
        const char* n = schema->fields[group->nested_start + k].name;
        if (!n[0]) continue;
        int w = snprintf(label + used, sizeof(label) - used, "%s'%s'",
                         used ? ", " : "byte_group of ", n);
        if (w < 0 || (size_t)w >= sizeof(label) - used) {
            label[used] = '\0';
            break;
        }
        used += (size_t)w;
    }
    snprintf(result->error_msg, sizeof(result->error_msg),
             "Field %s: Buffer underflow at peek offset %u (PS-535)",
             used ? label : "an unnamed byte_group", (unsigned)pos);
}

/* Decode a byte_group at *pos into result's next slots, advancing past its width. Each
 * member is decoded as a bit range over the group's assembled value, through
 * decode_field, so its sign, modifiers, lookup and variable are applied as any field's
 * are; a bool member reports a boolean. A member is reported unless it is internal. */
static inline int schema_decode_byte_group(
    const schema_t* schema,
    const field_def_t* group,
    const uint8_t* buf,
    size_t len,
    size_t* pos,
    decode_result_t* result,
    var_context_t* vars
) {
    const int width = byte_group_width(schema, group, result->error_msg,
                                       sizeof(result->error_msg));
    if (width < 0) {
        result->error_code = SCHEMA_ERR_TYPE;
        return SCHEMA_ERR_TYPE;
    }
    if (*pos + (size_t)width > len) {
        result->error_code = SCHEMA_ERR_BUFFER;
        byte_group_underflow(result, schema, group, *pos);
        return SCHEMA_ERR_BUFFER;
    }
    const endian_t endian = group->endian != ENDIAN_DEFAULT ? group->endian : schema->endian;
    const uint64_t value = bits_read_base(buf + *pos, width, endian);

    for (int k = 0; k < group->nested_count; k++) {
        const int idx = group->nested_start + k;
        const field_def_t* m = &schema->fields[idx];
        if (result->field_count >= SCHEMA_MAX_FIELDS) break;
        decoded_field_t* out = &result->fields[result->field_count];
        int base, start, bits;
        bool is_signed;
        byte_group_member_bits(m, &base, &start, &bits, &is_signed);

        if (m->type == FIELD_TYPE_BOOL) {
            memset(out, 0, sizeof(*out));
            strncpy(out->name, m->name, SCHEMA_MAX_NAME_LEN - 1);
            out->type = FIELD_TYPE_BOOL;
            out->value.b = ((value >> start) & 1) != 0;
            out->valid = true;
            if (m->var_name[0]) var_set(vars, m->var_name, out->value.b ? 1 : 0);
        } else {
            /* The member as a bit range of the whole group: the group's bytes, in the
             * group's order, read without advancing. */
            field_def_t as_range = *m;
            as_range.type = FIELD_TYPE_BITS;
            as_range.size = (uint8_t)width;
            as_range.bit_start = (uint8_t)start;
            as_range.bit_width = (uint8_t)bits;
            as_range.bit_signed = is_signed;
            as_range.consume = 0;
            as_range.endian = endian;
            size_t at = *pos;
            int rc = decode_field(&as_range, buf, len, &at, out, vars, endian);
            if (rc != SCHEMA_OK) return schema_field_failed(result, rc, m, buf, *pos, len, endian);
        }
        out->field_index = idx;
        if (out->valid && (m->name[0] ? m->name[0] != '_' : schema->nameless)) {
            result->field_count++;
        }
    }
    *pos += (size_t)width;
    return SCHEMA_OK;
}

/* ============================================
 * Main Decode Function
 * ============================================ */

/* SCHEMA_ERR_DIRECTION where the message travels the way the schema says it does not,
 * SCHEMA_OK where no check applies: the caller stated nothing (PS-290), or the schema
 * declares SCHEMA_DIR_BOTH, or it declares nothing, which PS-287 reads as `both`.
 *
 * Writes the message the other implementations report, so a schema's declaration reads
 * the same whichever language decodes it. */
static inline int schema_check_direction_msg(
    const schema_t* schema,
    schema_direction_t direction,
    char* msg,
    size_t msg_size
) {
    if (direction == SCHEMA_DIR_UNSET) return SCHEMA_OK;
    if (schema->direction == SCHEMA_DIR_UNSET) return SCHEMA_OK;
    if (schema->direction == SCHEMA_DIR_BOTH) return SCHEMA_OK;
    if (schema->direction == direction) return SCHEMA_OK;

    if (msg && msg_size > 0) {
        const char* declared = schema->direction == SCHEMA_DIR_UPLINK ? "uplink" : "downlink";
        const char* travelling = direction == SCHEMA_DIR_UPLINK ? "uplink" : "downlink";
        snprintf(msg, msg_size,
                 "schema '%s' is declared direction:%s; message direction is %s",
                 schema->name, declared, travelling);
    }
    return SCHEMA_ERR_DIRECTION;
}

static inline int schema_check_direction(
    const schema_t* schema,
    schema_direction_t direction,
    decode_result_t* result
) {
    return schema_check_direction_msg(schema, direction,
                                      result ? result->error_msg : NULL,
                                      result ? sizeof(result->error_msg) : 0);
}

/* schema_decode with the direction of the message supplied.
 *
 * A message the schema disclaims is not decoded at all (PS-021): its bytes read through
 * the wrong field definitions produce numbers with no relationship to what the device
 * sent, and nothing in the output would mark them as such, so no field is reported
 * (PS-288). Pass SCHEMA_DIR_UNSET to skip the check. */
static inline int schema_decode_direction(
    const schema_t* schema,
    const uint8_t* buf,
    size_t len,
    schema_direction_t direction,
    decode_result_t* result
) {
    memset(result, 0, sizeof(decode_result_t));
    result->nameless = schema->nameless;

    int direction_err = schema_check_direction(schema, direction, result);
    if (direction_err != SCHEMA_OK) {
        result->error_code = direction_err;
        return direction_err;
    }
    
    size_t pos = 0;
    var_context_t vars = {0};
    
    for (int i = 0; i < schema->field_count; i++) {
        const field_def_t* field = &schema->fields[i];
        
        /* A `flagged` construct: the bits of an already-decoded field select which groups
         * contribute (PS-158). Groups are walked in the order they were added, which is the
         * order their bytes appear (PS-160); a clear bit consumes nothing (PS-162) and a set
         * one decodes its body into the flat result (PS-163). Added by CR-2026-034.
         *
         * The mask is read from the variable table, so the field holding it must declare
         * `var_name` - see field_flagged(). A missing reference is an error rather than a
         * mask of zero, which would decode nothing and report success. */
        if (field->type == FIELD_TYPE_FLAGGED) {
            const char* flags_name = field->match_var;
            if (flags_name[0] == '$') flags_name++;
            if (!var_has(&vars, flags_name)) {
                result->error_code = SCHEMA_ERR_MATCH;
                snprintf(result->error_msg, sizeof(result->error_msg),
                         "flagged field reference not found: %s", flags_name);
                return SCHEMA_ERR_MATCH;
            }
            const variable_t* mask = var_find(&vars, flags_name);
            if (!mask->integral) {
                /* A mask bound to a fraction (PS-523: a scaled field binds its scaled
                 * value) has no bits to test. */
                result->error_code = SCHEMA_ERR_MATCH;
                snprintf(result->error_msg, sizeof(result->error_msg),
                         "flagged field %s is not an integer", flags_name);
                return SCHEMA_ERR_MATCH;
            }
            const int64_t flags = mask->value;

            for (int c = 0; c < field->case_count; c++) {
                const case_def_t* group = &field->cases[c];
                if (((flags >> group->match_value) & 1) == 0) continue;

                for (int f = 0; f < group->field_count; f++) {
                    int field_idx = group->field_start + f;
                    /* Bounded by the array: a group body is placed above field_count so
                     * the top-level loop does not walk it, exactly as a tlv case is. */
                    if (field_idx < 0 || field_idx >= SCHEMA_MAX_FIELDS) break;
                    if (result->field_count >= SCHEMA_MAX_FIELDS) break;
                    if (schema->fields[field_idx].type == FIELD_TYPE_BYTE_GROUP) {
                        int grc = schema_decode_byte_group(schema, &schema->fields[field_idx],
                                                           buf, len, &pos, result, &vars);
                        if (grc != SCHEMA_OK) return grc;
                        continue;
                    }

                    int rc = decode_field(
                        &schema->fields[field_idx],
                        buf, len, &pos,
                        &result->fields[result->field_count],
                        &vars, schema->endian
                    );
                    if (rc != SCHEMA_OK) {
                        return schema_field_failed(result, rc, &schema->fields[field_idx],
                                                   buf, pos, len, schema->endian);
                    }
                    result->fields[result->field_count].field_index = field_idx;
                    schema_note_checksum(result, &result->fields[result->field_count],
                                         &schema->fields[field_idx], field_idx, pos);
                    if (result->fields[result->field_count].valid) {
                        result->field_count++;
                    }
                }
            }
            continue;
        }

        /* A tlv loop: read a tag, decode the case describing it, repeat to the end of the
         * payload (PS-153, PS-154). Added by CR-2026-033; before it this interpreter had
         * no field type for the construct at all, so 79 of the corpus's schemas could not
         * be expressed, let alone decoded.
         *
         * Bounded by the payload rather than by a case count: a tag no case describes ends
         * the loop under SCHEMA_TLV_UNKNOWN_SKIP where nothing delimits it, because there
         * is no length to step over (PS-302). This interpreter has no warning channel, so
         * the position goes back to that tag and the bytes from it on are counted in
         * `bytes_unread` (PS-472) - the one report it can make.
         *
         * PS-477: a tag is never partly read. Bytes left at the start of an entry but
         * fewer than its tag are an error identifying the tlv; this used to stop the loop
         * and report a complete decode, the fragment dropped in silence. The code is
         * SCHEMA_ERR_BUFFER, what every other read past the payload's end returns, so no
         * new one. PS-486 extends it to the rest of the entry: a length cut short, or a
         * length declaring more bytes than remain, is the same error, for a known tag
         * and for an unknown one that would be skipped. Before it, a length cut short
         * rewound to the tag and left the bytes over, and a value cut short was read
         * from what was there and reported as a complete decode. */
        if (field->type == FIELD_TYPE_TLV) {
            const int parts = field->tlv_tag_parts ? field->tlv_tag_parts : 0;
            const int tag_bytes = parts ? parts : (field->tlv_tag_size ? field->tlv_tag_size : 1);

            while (pos < len) {
                const size_t entry_start = pos;
                if (pos + tag_bytes > len) {   /* PS-477 */
                    result->error_code = SCHEMA_ERR_BUFFER;
                    snprintf(result->error_msg, sizeof(result->error_msg),
                             "tlv %s: entry at offset %u: %u byte(s) remain, fewer than "
                             "its %d-byte tag (PS-477)",
                             field->name[0] ? field->name : "(unnamed)",
                             (unsigned)pos, (unsigned)(len - pos), tag_bytes);
                    return SCHEMA_ERR_BUFFER;
                }

                int packed = 0;
                for (int b = 0; b < tag_bytes; b++) {
                    packed = (packed << 8) | buf[pos + b];
                }
                pos += tag_bytes;

                int entry_len = -1;
                if (field->tlv_length_size) {
                    if (pos + field->tlv_length_size > len) {   /* PS-486 */
                        result->error_code = SCHEMA_ERR_BUFFER;
                        snprintf(result->error_msg, sizeof(result->error_msg),
                                 "tlv %s: entry at offset %u: %u byte(s) remain after its "
                                 "tag, fewer than its %d-byte length (PS-486)",
                                 field->name[0] ? field->name : "(unnamed)",
                                 (unsigned)entry_start, (unsigned)(len - pos),
                                 (int)field->tlv_length_size);
                        return SCHEMA_ERR_BUFFER;
                    }
                    entry_len = 0;
                    for (int b = 0; b < field->tlv_length_size; b++) {
                        entry_len = (entry_len << 8) | buf[pos + b];
                    }
                    pos += field->tlv_length_size;
                    if ((size_t)entry_len > len - pos) {   /* PS-486 */
                        result->error_code = SCHEMA_ERR_BUFFER;
                        snprintf(result->error_msg, sizeof(result->error_msg),
                                 "tlv %s: entry at offset %u: its length declares %d "
                                 "byte(s), %u remain (PS-486)",
                                 field->name[0] ? field->name : "(unnamed)",
                                 (unsigned)entry_start, entry_len, (unsigned)(len - pos));
                        return SCHEMA_ERR_BUFFER;
                    }
                }

                const case_def_t* chosen = NULL;
                for (int c = 0; c < field->case_count; c++) {
                    if (field->cases[c].match_value == packed) {
                        chosen = &field->cases[c];
                        break;
                    }
                }

                if (chosen == NULL) {
                    if (field->tlv_unknown == SCHEMA_TLV_UNKNOWN_ERROR) {
                        result->error_code = SCHEMA_ERR_MATCH;
                        snprintf(result->error_msg, sizeof(result->error_msg),
                                 "unknown TLV tag: 0x%X", (unsigned)packed);
                        return SCHEMA_ERR_MATCH;
                    }
                    if (entry_len >= 0) {
                        pos += entry_len;   /* delimited: step over it and carry on */
                        continue;
                    }
                    pos = entry_start;      /* nothing to skip over (PS-302): the */
                    break;                  /* bytes from the tag on are left over */
                }

                /* PS-630, PS-631 (CR-2026-193): an entry with a length decodes its case
                 * within its value bytes, a field reading past them is an error naming
                 * the entry, and the next entry begins after them whatever the case read.
                 * This interpreter has no warning list, so the unread bytes PS-631 warns
                 * about are skipped without one - a C gap the harness counts. */
                const size_t entry_end = entry_len >= 0 ? pos + (size_t)entry_len : len;
                for (int f = 0; f < chosen->field_count; f++) {
                    int field_idx = chosen->field_start + f;
                    /* Bounded by the array, not by field_count: a case body is placed
                     * above field_count on purpose, so that the top-level loop does not
                     * walk it. The match block's guard is `>= field_count` because its
                     * bodies are counted fields; copying it here rejected every body this
                     * construct owns and the loop decoded nothing at all. */
                    if (field_idx < 0 || field_idx >= SCHEMA_MAX_FIELDS) break;
                    if (result->field_count >= SCHEMA_MAX_FIELDS) break;
                    if (schema->fields[field_idx].checksum_algo != SCHEMA_CHECKSUM_NONE) {
                        result->error_code = SCHEMA_ERR_CHECKSUM;     /* PS-608 */
                        schema_checksum_placement(&schema->fields[field_idx], "a tlv entry",
                                                  result->error_msg,
                                                  sizeof(result->error_msg));
                        return SCHEMA_ERR_CHECKSUM;
                    }
                    if (schema->fields[field_idx].type == FIELD_TYPE_BYTE_GROUP) {
                        int grc = schema_decode_byte_group(schema, &schema->fields[field_idx],
                                                           buf, entry_end, &pos, result, &vars);
                        if (grc != SCHEMA_OK) {
                            return schema_tlv_entry_overrun(result, grc, field, packed,
                                                            entry_start, entry_len);
                        }
                        continue;
                    }

                    int rc = decode_field(
                        &schema->fields[field_idx],
                        buf, entry_end, &pos,
                        &result->fields[result->field_count],
                        &vars, schema->endian
                    );
                    if (rc != SCHEMA_OK) {
                        rc = schema_field_failed(result, rc, &schema->fields[field_idx],
                                                 buf, pos, entry_end, schema->endian);
                        return schema_tlv_entry_overrun(result, rc, field, packed,
                                                        entry_start, entry_len);
                    }
                    result->fields[result->field_count].field_index = field_idx;
                    schema_note_checksum(result, &result->fields[result->field_count],
                                         &schema->fields[field_idx], field_idx, pos);
                    if (result->fields[result->field_count].valid) {
                        result->field_count++;
                    }
                }
                if (entry_len >= 0 && pos < entry_end) pos = entry_end;   /* PS-631 */
            }
            continue;
        }

        /* Handle match type */
        if (field->type == FIELD_TYPE_MATCH) {
            /* Get match variable value */
            const char* var_name = field->match_var;
            if (var_name[0] == '$') var_name++;
            /* The value PS-523 binds. One with a fraction equals no integer key, so only
             * a default case can take it (PS-524); it used to be truncated to a key. */
            const variable_t* bound = var_find(&vars, var_name);
            const bool integral = bound == NULL || bound->integral;
            int64_t match_val = bound ? bound->value : 0;
            
            /* Find matching case */
            for (int c = 0; c < field->case_count; c++) {
                const case_def_t* cs = &field->cases[c];
                bool matched = false;
                
                if (cs->is_default) {
                    matched = true;
                } else if (!integral) {
                    matched = false;
                } else if (cs->match_value == (int)match_val) {
                    matched = true;
                } else if (cs->range_min != cs->range_max) {
                    matched = (match_val >= cs->range_min && match_val <= cs->range_max);
                } else {
                    /* Check list */
                    for (int j = 0; cs->match_list[j] != -1 && j < 8; j++) {
                        if (cs->match_list[j] == (int)match_val) {
                            matched = true;
                            break;
                        }
                    }
                }
                
                if (matched) {
                    /* Decode case fields */
                    for (int f = 0; f < cs->field_count; f++) {
                        int field_idx = cs->field_start + f;
                        if (field_idx >= schema->field_count) break;
                        
                        int rc = decode_field(
                            &schema->fields[field_idx],
                            buf, len, &pos,
                            &result->fields[result->field_count],
                            &vars, schema->endian
                        );
                        if (rc != SCHEMA_OK) {
                            return schema_field_failed(result, rc,
                                                       &schema->fields[field_idx], buf, pos,
                                                       len, schema->endian);
                        }
                        result->fields[result->field_count].field_index = field_idx;
                        schema_note_checksum(result, &result->fields[result->field_count],
                                             &schema->fields[field_idx], field_idx, pos);
                        if (result->fields[result->field_count].valid) {
                            result->field_count++;
                        }
                    }
                    break;
                }
            }
            continue;
        }
        
        if (field->type == FIELD_TYPE_BYTE_GROUP) {
            int grc = schema_decode_byte_group(schema, field, buf, len, &pos, result, &vars);
            if (grc != SCHEMA_OK) return grc;
            continue;
        }

        /* Regular field */
        int rc = decode_field(
            field, buf, len, &pos,
            &result->fields[result->field_count],
            &vars, schema->endian
        );
        
        if (rc != SCHEMA_OK) {
            return schema_field_failed(result, rc, field, buf, pos, len, schema->endian);
        }
        schema_note_checksum(result, &result->fields[result->field_count], field, i, pos);
        
        /* A named field is reported unless it is internal. A schema without names (a
         * binary schema loaded without its name table) reports each value by its
         * index instead, so every field that reads a value counts; its name stays
         * empty, never one made up from the IPSO object, type or position (PS-555). */
        result->fields[result->field_count].field_index = i;
        if (result->fields[result->field_count].valid &&
            (field->name[0] ? field->name[0] != '_'
                            : schema->nameless && field->type != FIELD_TYPE_SKIP)) {
            result->field_count++;
        }
    }
    
    result->bytes_consumed = (int)pos;
    result->bytes_unread = (int)(len - pos);   /* PS-472 */
    return SCHEMA_OK;
}

/* The direction of the message is unstated, so no direction check runs and PS-021 is
 * not satisfied. Use schema_decode_direction where the caller knows which way the
 * message was travelling. */
static inline int schema_decode(
    const schema_t* schema,
    const uint8_t* buf,
    size_t len,
    decode_result_t* result
) {
    return schema_decode_direction(schema, buf, len, SCHEMA_DIR_UNSET, result);
}

/* ============================================
 * Encoder: Values → Payload Bytes
 * ============================================ */

typedef struct {
    uint8_t data[SCHEMA_MAX_PAYLOAD];
    int len;
    int error_code;
    /* The text every other encoder gives for the error, where there is one: PS-551's
     * "Missing required field: {name}" where an input is missing, and PS-168's
     * "Field '{name}': {value} encodes as {n}, outside {low} to {high} (PS-168)". */
    char error_msg[160];
    /* PS-537's warning, as a flag: an `ascii` value held a character from U+0080 to
     * U+00FF, written as the byte of the same number. The other encoders warn "Field
     * '{name}': a character above U+007F in an ascii value (PS-537)"; this one has no
     * warning list. Last member, so existing initializers leave it false. */
    bool non_ascii;
} encode_result_t;

/* Input values for encoding */
typedef struct {
    char name[SCHEMA_MAX_NAME_LEN];
    field_value_t value;
    bool is_int;               /* value.i64 holds it (encode_inputs_add_int), else value.f64 */
    /* Keyed by field index rather than by name (encode_inputs_add_int_at), for a schema
     * that carries no names (PS-555, CR-2026-146). `name` is then "#<index>", the key
     * the encoder looks it up by. Last member, so existing initializers leave it false. */
    bool by_index;
    /* value.str holds `text_len` bytes of text (encode_inputs_add_text), for an `ascii`
     * field. Last members, so existing initializers leave them unset. */
    bool is_text;
    uint8_t text_len;
} encode_input_t;

typedef struct {
    encode_input_t inputs[SCHEMA_MAX_FIELDS];
    int count;
} encode_inputs_t;

static inline void encode_inputs_init(encode_inputs_t* inputs) {
    memset(inputs, 0, sizeof(*inputs));
}

static inline void encode_inputs_add_int(encode_inputs_t* inputs, const char* name, int64_t val) {
    if (inputs->count >= SCHEMA_MAX_FIELDS) return;
    strncpy(inputs->inputs[inputs->count].name, name, SCHEMA_MAX_NAME_LEN - 1);
    inputs->inputs[inputs->count].value.i64 = val;
    inputs->inputs[inputs->count].is_int = true;
    inputs->count++;
}

static inline void encode_inputs_add_double(encode_inputs_t* inputs, const char* name, double val) {
    if (inputs->count >= SCHEMA_MAX_FIELDS) return;
    strncpy(inputs->inputs[inputs->count].name, name, SCHEMA_MAX_NAME_LEN - 1);
    inputs->inputs[inputs->count].value.f64 = val;
    inputs->inputs[inputs->count].is_int = false;
    inputs->count++;
}

/* PS-555 (CR-2026-146): a schema loaded without its names - schema_t.nameless - is
 * encoded from values keyed by field index, in schema order, as its decode reports them
 * (result_get_index); "an encoder working from such a form takes each value by the same
 * index". A schema with names keeps name keying. The two are not mixed: schema_encode
 * refuses index-keyed inputs for a named schema and name-keyed ones for a nameless one,
 * since either would otherwise be ignored and the field reported missing - or, worse,
 * matched by a name that happened to look like an index.
 *
 * Returns false, adding nothing, where the inputs are full or `index` is outside the
 * schema's field array. */
static inline bool encode_inputs_add_at(encode_inputs_t* inputs, int index,
                                        bool is_int, int64_t iv, double dv) {
    if (inputs->count >= SCHEMA_MAX_FIELDS) return false;
    if (index < 0 || index >= SCHEMA_MAX_FIELDS) return false;
    encode_input_t* in = &inputs->inputs[inputs->count];
    memset(in, 0, sizeof(*in));
    snprintf(in->name, sizeof(in->name), "#%d", index);
    if (is_int) in->value.i64 = iv; else in->value.f64 = dv;
    in->is_int = is_int;
    in->by_index = true;
    inputs->count++;
    return true;
}

static inline bool encode_inputs_add_int_at(encode_inputs_t* inputs, int index, int64_t val) {
    return encode_inputs_add_at(inputs, index, true, val, 0.0);
}

static inline bool encode_inputs_add_double_at(encode_inputs_t* inputs, int index, double val) {
    return encode_inputs_add_at(inputs, index, false, 0, val);
}

/* Text for an `ascii` field: `len` bytes, each the ISO 8859-1 character of the same
 * number (PS-538) - the form decode reports in value.str and text_len, so a decoded value
 * re-encodes to the same bytes. A 0x00 inside the text is kept. Text held as Unicode is
 * the caller's to convert: a character from U+0080 to U+00FF is the byte of the same
 * number, and one above U+00FF has no byte and MUST be rejected naming the field
 * (PS-537) - which this input cannot carry, so the rejection belongs to that conversion.
 *
 * Returns false, adding nothing, where the inputs are full or the text is longer than
 * SCHEMA_MAX_NAME_LEN - 1 bytes; nothing is truncated. The encode then reports the field
 * as missing (PS-551). */
static inline bool encode_inputs_add_text_keyed(encode_inputs_t* inputs, const char* name,
                                                int index, const uint8_t* text, size_t len) {
    if (inputs->count >= SCHEMA_MAX_FIELDS) return false;
    if (len > SCHEMA_MAX_NAME_LEN - 1 || (len && !text)) return false;
    if (!name && (index < 0 || index >= SCHEMA_MAX_FIELDS)) return false;
    encode_input_t* in = &inputs->inputs[inputs->count];
    memset(in, 0, sizeof(*in));
    if (name) strncpy(in->name, name, SCHEMA_MAX_NAME_LEN - 1);
    else { snprintf(in->name, sizeof(in->name), "#%d", index); in->by_index = true; }
    if (len) memcpy(in->value.str, text, len);
    in->value.str[len] = '\0';
    in->is_text = true;
    in->text_len = (uint8_t)len;
    inputs->count++;
    return true;
}

static inline bool encode_inputs_add_text(encode_inputs_t* inputs, const char* name,
                                          const uint8_t* text, size_t len) {
    return encode_inputs_add_text_keyed(inputs, name, 0, text, len);
}

/* The same text, keyed by field index for a schema without names (PS-555, CR-2026-146),
 * numbered as encode_inputs_add_int_at is. It joins the mixed-keying rejection: given to
 * a named schema it is refused by schema_encode. */
static inline bool encode_inputs_add_text_at(encode_inputs_t* inputs, int index,
                                             const uint8_t* text, size_t len) {
    return encode_inputs_add_text_keyed(inputs, NULL, index, text, len);
}

/* encode_inputs_add_text for a NUL-terminated string. */
static inline bool encode_inputs_add_string(encode_inputs_t* inputs, const char* name,
                                            const char* text) {
    return encode_inputs_add_text(inputs, name, (const uint8_t*)text, text ? strlen(text) : 0);
}

/* encode_inputs_add_text_at for a NUL-terminated string. */
static inline bool encode_inputs_add_string_at(encode_inputs_t* inputs, int index,
                                               const char* text) {
    return encode_inputs_add_text_at(inputs, index, (const uint8_t*)text,
                                     text ? strlen(text) : 0);
}

/* Find input value by name */
static inline const encode_input_t* find_input(const encode_inputs_t* inputs, const char* name) {
    for (int i = 0; i < inputs->count; i++) {
        if (strcmp(inputs->inputs[i].name, name) == 0) {
            return &inputs->inputs[i];
        }
    }
    return NULL;
}

/* Write integer to buffer. ORed in, not assigned (CR-2026-136): a bit range or bool
 * that did not consume has already filled bits of this byte, which a decoder reads as the
 * same byte. The result buffer starts zeroed, so a fresh byte is unaffected. */
static inline void write_int(uint8_t* buf, size_t* pos, int64_t val, int size, endian_t endian) {
    uint64_t uval = (uint64_t)val;
    if (endian == ENDIAN_BIG) {
        for (int i = size - 1; i >= 0; i--) {
            buf[*pos + i] |= uval & 0xFF;
            uval >>= 8;
        }
    } else {
        for (int i = 0; i < size; i++) {
            buf[*pos + i] |= uval & 0xFF;
            uval >>= 8;
        }
    }
    *pos += size;
}

/* PS-552: the nearest integer to a binary64 value, a tie to even. No libm: the fraction
 * v - trunc(v) is exact, so the tie is detected without rounding twice. The encoder used
 * (int64_t)(v + 0.5), which rounds a tie away from zero and, because the addition itself
 * rounds, took 0.49999999999999994 to 1 and 23.45 * 10 (234.49999999999997) to 235.
 * Valid for |v| < 2^63; a caller checks the range first. */
static inline double schema_round_half_even(double v) {
    double t = (double)(int64_t)v;
    double frac = v - t;
    int odd = (int)((int64_t)t & 1);
    if (frac > 0.5 || (frac == 0.5 && odd)) return t + 1.0;
    if (frac < -0.5 || (frac == -0.5 && odd)) return t - 1.0;
    return t;
}

/* A number as JavaScript's String(number) writes it, for the PS-168 message: integral
 * values without a fraction, others in the fewest digits that read back exactly. */
static inline void schema_format_number(char* out, size_t len, double v, bool is_int,
                                        int64_t iv) {
    if (is_int) { snprintf(out, len, "%lld", (long long)iv); return; }
    if (v == (double)(int64_t)v && v > -1e18 && v < 1e18) {
        snprintf(out, len, "%lld", (long long)(int64_t)v);
        return;
    }
    for (int p = 1; p <= 17; p++) {
        snprintf(out, len, "%.*g", p, v);
        if (strtod(out, NULL) == v) return;
    }
}

/* PS-168, PS-552: the rounded value fits [lo, hi] - a bit range's being its width - or
 * the input is rejected naming the field and the value given. It was masked or wrapped
 * silently: u8 256 wrote 00, u8 -1 wrote ff, s8 -128.6 wrote 7f. Checked as doubles, so
 * a value past int64 is caught before any cast. */
static inline int schema_check_fits(double rounded, double lo, double hi,
                                    const field_def_t* field, const encode_input_t* input,
                                    char* err, size_t err_len) {
    if (rounded >= lo && rounded <= hi) return SCHEMA_OK;
    if (err && err_len) {
        char given[40];
        schema_format_number(given, sizeof(given),
                             input && !input->is_int ? input->value.f64 : 0.0,
                             input && input->is_int, input ? input->value.i64 : 0);
        if (field->type == FIELD_TYPE_BITS) {
            /* PS-064 (CR-2026-136): a bit range names its type, as the prototype does. */
            snprintf(err, err_len,
                     "Field '%s': %s encodes as %.0f, outside %.0f to %.0f for %c%d[%d:%d] (PS-168)",
                     field->name, given, rounded, lo, hi, field->bit_signed ? 's' : 'u',
                     bits_base_bytes(field) * 8, field->bit_start,
                     field->bit_start + field->bit_width - 1);
        } else {
            snprintf(err, err_len,
                     "Field '%s': %s encodes as %.0f, outside %.0f to %.0f (PS-168)",
                     field->name, given, rounded, lo, hi);
        }
    }
    return SCHEMA_ERR_OVERFLOW;
}

/* PS-168 for udec/sdec (PS-330), in the reference's check_nibble_decimal text: "Field
 * 'v': 123.5 encodes as 123.5, outside 0 to 15.9 (PS-168)", the value encoded given to one
 * decimal. Writes the byte where it fits. No libm: the floor is taken by hand. */
static inline int schema_encode_nibble_decimal(double v, bool is_signed,
                                               const field_def_t* field,
                                               const encode_input_t* input, uint8_t* out,
                                               char* err, size_t err_len) {
    long long lo = is_signed ? -80 : 0, hi = is_signed ? 79 : 159;
    long long tenths_total;
    if (v > -1e15 && v < 1e15) {
        /* PS-556 (CR-2026-147): n is the value times 10, rounded half to even on the
         * binary64 product (PS-552). Rounding the fraction after splitting off the whole
         * part wrote 3.45 as 0x35. */
        tenths_total = (long long)schema_round_half_even(v * 10.0);
    } else {
        tenths_total = v < 0 ? lo - 1 : hi + 1;
    }
    if (tenths_total >= lo && tenths_total <= hi) {
        long long whole = tenths_total >= 0 ? tenths_total / 10 : -((-tenths_total + 9) / 10);
        long long part = tenths_total - whole * 10;
        *out |= (uint8_t)(((whole & 0x0F) << 4) | (part & 0x0F));   /* ORed: see write_int */
        return SCHEMA_OK;
    }
    if (err && err_len) {
        char given[40], encoded[48];
        schema_format_number(given, sizeof(given),
                             input && !input->is_int ? input->value.f64 : 0.0,
                             input && input->is_int, input ? input->value.i64 : 0);
        if (v > -1e15 && v < 1e15) {
            long long a = tenths_total < 0 ? -tenths_total : tenths_total;
            if (a % 10)
                snprintf(encoded, sizeof(encoded), "%s%lld.%lld", tenths_total < 0 ? "-" : "",
                         a / 10, a % 10);
            else
                snprintf(encoded, sizeof(encoded), "%s%lld", tenths_total < 0 ? "-" : "", a / 10);
        } else {
            snprintf(encoded, sizeof(encoded), "%s", given);
        }
        snprintf(err, err_len, "Field '%s': %s encodes as %s, outside %s to %s (PS-168)",
                 field->name, given, encoded, is_signed ? "-8" : "0", is_signed ? "7.9" : "15.9");
    }
    return SCHEMA_ERR_OVERFLOW;
}

/* Encode single field */
static inline int encode_field(
    const field_def_t* field,
    const encode_inputs_t* inputs,
    uint8_t* buf,
    size_t* pos,
    endian_t schema_endian,
    char* err,
    size_t err_len
) {
    const encode_input_t* input = find_input(inputs, field->name);
    if (!input && field->type != FIELD_TYPE_SKIP) {
        return SCHEMA_ERR_PARSE;  /* Missing input */
    }

    /* PS-537 (CR-2026-114): text written byte for byte, padded with 0x00 to `length`; a
     * value longer than `length` is rejected naming the field. It had no case here, so an
     * ascii field could not be encoded at all (SCHEMA_ERR_UNSUPPORTED). */
    if (field->type == FIELD_TYPE_ASCII) {
        if (!input->is_text) {
            if (err && err_len) {
                snprintf(err, err_len, "Field '%s': an ascii value is text, and a "
                         "number was given", field->name);
            }
            return SCHEMA_ERR_TYPE;
        }
        if (input->text_len > field->size) {
            if (err && err_len) {
                snprintf(err, err_len, "Field '%s': an ascii value of %u bytes is longer "
                         "than its length of %u (PS-537)", field->name,
                         (unsigned)input->text_len, (unsigned)field->size);
            }
            return SCHEMA_ERR_OVERFLOW;
        }
        if (*pos + field->size > SCHEMA_MAX_PAYLOAD) return SCHEMA_ERR_BUFFER;
        memcpy(buf + *pos, input->value.str, input->text_len);
        memset(buf + *pos + input->text_len, 0, field->size - input->text_len);
        *pos += field->size;
        return SCHEMA_OK;
    }
    if (field->type == FIELD_TYPE_BYTES || field->type == FIELD_TYPE_HEX
            || field->type == FIELD_TYPE_BASE64) {
        /* CR-2026-181 (PS-601, PS-602): this encoder takes native values and has no byte
         * string input, so it writes none rather than zeros or a guess. It returned
         * SCHEMA_ERR_UNSUPPORTED with no message; the refusal now names the field. */
        if (err && err_len) {
            snprintf(err, err_len, "Field '%s': this encoder cannot write a %s value "
                     "(PS-601, PS-602)", field->name, schema_type_label(field->type));
        }
        return SCHEMA_ERR_UNSUPPORTED;
    }
    if (input && input->is_text) {
        /* Text for a numeric field: its bytes are not a number, and reading them as one
         * would write whatever the union's other member happens to hold. */
        if (err && err_len) {
            snprintf(err, err_len, "Field '%s': text was given for a %s field",
                     field->name, schema_type_label(field->type));
        }
        return SCHEMA_ERR_TYPE;
    }
    
    /* An integer input is read from i64. Reading f64 for every input reinterpreted the
     * integer's bits as a double - 1 became 5e-324 - so encode_inputs_add_int encoded
     * every value as 0. */
    double raw_val = !input ? 0.0 :
        input->is_int ? (double)input->value.i64 : input->value.f64;
    
    /* Reverse modifiers: encode_formula or inverse of mult/add */
    if (field->has_add) {
        raw_val -= field->add;
    }
    if (field->has_mult && field->mult != 0) {
        raw_val /= field->mult;
    }
    if (field->has_div) {
        raw_val *= field->div;
    }
    /* PS-566: an input of -0.0, or a -0 the reversed arithmetic made (0 / -1), is written as
     * the positive-zero word. */
    if (raw_val == 0.0) raw_val = 0.0;

    /* PS-552: nearest, a tie to even, on the binary64 value; then PS-168. */
    bool exact_int = input && input->is_int && !field->has_add && !field->has_mult
                     && !field->has_div;
    double rounded = exact_int ? (double)input->value.i64
                               : (raw_val > -9.2e18 && raw_val < 9.2e18
                                  ? schema_round_half_even(raw_val) : raw_val);
    double lo = 0.0, hi = -1.0;     /* hi < lo: no integer range to check */
    switch (field->type) {
        case FIELD_TYPE_U8:  lo = 0; hi = 255.0; break;
        case FIELD_TYPE_S8:  lo = -128.0; hi = 127.0; break;
        case FIELD_TYPE_U16: lo = 0; hi = 65535.0; break;
        case FIELD_TYPE_S16: lo = -32768.0; hi = 32767.0; break;
        case FIELD_TYPE_U24: lo = 0; hi = 16777215.0; break;
        case FIELD_TYPE_S24: lo = -8388608.0; hi = 8388607.0; break;
        case FIELD_TYPE_U32: lo = 0; hi = 4294967295.0; break;
        case FIELD_TYPE_S32: lo = -2147483648.0; hi = 2147483647.0; break;
        case FIELD_TYPE_U64: lo = 0; hi = 18446744073709551615.0; break;
        case FIELD_TYPE_S64: lo = -9223372036854775808.0; hi = 9223372036854775807.0; break;
        case FIELD_TYPE_BITS:     /* a bit range's type is its width (PS-064) */
            if (field->bit_signed) {
                lo = -(double)((uint64_t)1 << (field->bit_width - 1));
                hi = (double)(((uint64_t)1 << (field->bit_width - 1)) - 1);
            } else {
                lo = 0; hi = (double)bits_width_mask(field->bit_width);
            }
            break;
        default: break;
    }
    /* An exact int64 input always fits s64, and fits u64 unless negative; checking it as a
     * double would round 2^63 - 1 up past the bound. */
    bool exact_64 = exact_int && (field->type == FIELD_TYPE_S64
                                  || (field->type == FIELD_TYPE_U64 && input->value.i64 >= 0));
    if (hi >= lo && !exact_64) {
        int rc = schema_check_fits(rounded, lo, hi, field, input, err, err_len);
        if (rc != SCHEMA_OK) return rc;
    }
    int64_t int_val = exact_int ? input->value.i64
                    : (rounded >= 9.2e18 ? (int64_t)(uint64_t)rounded : (int64_t)rounded);
    endian_t endian = (field->endian != ENDIAN_DEFAULT) ? field->endian : schema_endian;
    
    switch (field->type) {
        case FIELD_TYPE_U8:
        case FIELD_TYPE_S8:
            buf[*pos] |= (uint8_t)(int_val & 0xFF);     /* ORed: see write_int */
            (*pos)++;
            break;
            
        case FIELD_TYPE_U16:
        case FIELD_TYPE_S16:
            write_int(buf, pos, int_val, 2, endian);
            break;
            
        case FIELD_TYPE_U24:
        case FIELD_TYPE_S24:
            write_int(buf, pos, int_val, 3, endian);
            break;
            
        case FIELD_TYPE_U32:
        case FIELD_TYPE_S32:
            write_int(buf, pos, int_val, 4, endian);
            break;
            
        case FIELD_TYPE_U64:
        case FIELD_TYPE_S64:
            write_int(buf, pos, int_val, 8, endian);
            break;
            
        case FIELD_TYPE_F32: {
            float fval = (float)raw_val;
            uint32_t bits;
            memcpy(&bits, &fval, sizeof(bits));
            /* CR-2026-157 (PS-566): the word is tested, not the input - -1e-50 rounds to the
             * sign word, which is written as the zero word. */
            if (bits == 0x80000000u) bits = 0;
            write_int(buf, pos, bits, 4, endian);
            break;
        }
        
        case FIELD_TYPE_F64: {
            uint64_t dbits;
            memcpy(&dbits, &raw_val, sizeof(dbits));
            write_int(buf, pos, (int64_t)dbits, 8, endian);
            break;
        }
        
        case FIELD_TYPE_BOOL:
            /* Its `bit` of the byte it peeks, advancing only where it consumes (PS-389,
             * PS-066, CR-2026-136). It was written as a whole byte of 0 or 1. */
            if (int_val) buf[*pos] |= (uint8_t)(1u << field->bit_start);
            *pos += field->consume;
            break;
            
        case FIELD_TYPE_BITS: {
            /* PS-062, PS-365 (CR-2026-136): the checked value, its two's complement kept
             * to the range's width, ORed into a base of `size` bytes written in the order
             * the decoder reads it. It was one byte, whatever the base. */
            const int base = bits_base_bytes(field);
            if (field->bit_width == 0 || base > 8
                || field->bit_start + field->bit_width > base * 8) {
                return SCHEMA_ERR_TYPE;
            }
            if (*pos + (size_t)base > SCHEMA_MAX_PAYLOAD) return SCHEMA_ERR_BUFFER;
            uint64_t placed = ((uint64_t)int_val & bits_width_mask(field->bit_width))
                              << field->bit_start;
            for (int b = 0; b < base; b++) {
                buf[*pos + b] |= bits_byte_mask(placed, base, b, endian);
            }
            *pos += field->consume;
            break;
        }
        
        /* The width decode reads (size 0 is no bytes, PS-392), and never past the result
         * buffer: a skip is up to 255 bytes, so a few of them overran
         * data[SCHEMA_MAX_PAYLOAD]. */
        case FIELD_TYPE_SKIP: {
            size_t width = field->size;
            if (*pos + width > SCHEMA_MAX_PAYLOAD) return SCHEMA_ERR_BUFFER;
            memset(buf + *pos, 0, width);
            *pos += width;
            break;
        }
            
        case FIELD_TYPE_UDEC:
        case FIELD_TYPE_SDEC: {
            /* The inverse of PS-330, as the reference writes it: the whole part floored, so
             * the tenths stay non-negative (-1.5 is -2 + 0.5), the tenths rounded half to
             * even. PS-168: one byte holds 0 to 15.9, or -8 to 7.9 signed. Both clamped
             * silently - udec 123.5 wrote 0x95, and udec 10 wrote 9.0. */
            bool is_signed = field->type == FIELD_TYPE_SDEC;
            int rc = schema_encode_nibble_decimal(raw_val, is_signed, field, input,
                                                  buf + *pos, err, err_len);
            if (rc != SCHEMA_OK) return rc;
            (*pos)++;
            break;
        }

        default:
            return SCHEMA_ERR_UNSUPPORTED;
    }
    
    return SCHEMA_OK;
}

/* Encode a byte_group (PS-063, PS-365, PS-397): every member's value placed in one
 * value of the group's width, written once in the group's byte order; bits no member
 * names are zero. Each member is checked and reduced to its bits by encode_field as
 * the member it is - so a value outside it is the PS-168 error naming the member and its
 * own type - and then moved to its place in the group. A member the input omits is
 * missing (PS-551); an internal one is PS-434's error, this interpreter having no
 * `value` for it to write. */
static inline int schema_encode_byte_group(
    const schema_t* schema,
    const field_def_t* group,
    const encode_inputs_t* inputs,
    uint8_t* buf,
    size_t* pos,
    char* err,
    size_t err_len
) {
    char why[sizeof(((encode_result_t*)0)->error_msg)];
    const int width = byte_group_width(schema, group, why, sizeof(why));
    if (width < 0) {
        if (err && err_len) snprintf(err, err_len, "%s", why);
        return SCHEMA_ERR_TYPE;
    }
    if (*pos + (size_t)width > SCHEMA_MAX_PAYLOAD) return SCHEMA_ERR_BUFFER;
    const endian_t endian = group->endian != ENDIAN_DEFAULT ? group->endian : schema->endian;
    uint64_t value = 0;

    for (int k = 0; k < group->nested_count; k++) {
        /* A member of a group in a schema without names is keyed "#<n>", n its position in
         * the schema's field array, nested_start + k: the index decode reports it under
         * (field_index), numbered as every other field is (PS-555). */
        field_def_t keyed_member = schema->fields[group->nested_start + k];
        if (schema->nameless) {
            snprintf(keyed_member.name, sizeof(keyed_member.name), "#%d",
                     group->nested_start + k);
        }
        const field_def_t* m = &keyed_member;
        const encode_input_t* input = find_input(inputs, m->name);
        if (!input) {
            if (err && err_len) {
                if (m->name[0] == '_') {
                    snprintf(err, err_len, "internal field '%s' reads payload bytes and "
                             "declares no value, and the input does not supply it "
                             "(PS-434)", m->name);
                } else {
                    snprintf(err, err_len, "Missing required field: %s", m->name);
                }
            }
            return SCHEMA_ERR_PARSE;
        }
        int base, start, bits;
        bool is_signed;
        byte_group_member_bits(m, &base, &start, &bits, &is_signed);
        uint64_t pattern;
        if (m->type == FIELD_TYPE_BOOL) {
            pattern = (input->is_int ? input->value.i64 != 0 : input->value.f64 != 0.0);
        } else {
            uint8_t own[8] = {0};
            size_t at = 0;
            field_def_t member = *m;
            member.consume = 0;
            member.endian = ENDIAN_DEFAULT;
            int rc = encode_field(&member, inputs, own, &at, endian, err, err_len);
            if (rc != SCHEMA_OK) return rc;
            pattern = (uint64_t)bits_extract(bits_read_base(own, base, endian), start, bits,
                                             false);
        }
        value |= (pattern & bits_width_mask(bits)) << start;
    }
    for (int b = 0; b < width; b++) {
        buf[*pos + b] |= bits_byte_mask(value, width, b, endian);
    }
    *pos += (size_t)width;
    return SCHEMA_OK;
}

/* Main encode function */
/* schema_encode with the direction the message will travel supplied.
 *
 * The mirror of the decode check (PS-292): encoding against a schema that disclaims this
 * direction produces bytes the far end reads against different field definitions, so
 * nothing is encoded. Pass SCHEMA_DIR_UNSET to skip the check. */
static inline int schema_encode_direction(
    const schema_t* schema,
    const encode_inputs_t* inputs,
    schema_direction_t direction,
    encode_result_t* result
);

static inline int schema_encode(
    const schema_t* schema,
    const encode_inputs_t* inputs,
    encode_result_t* result
) {
    return schema_encode_direction(schema, inputs, SCHEMA_DIR_UNSET, result);
}

/* PS-587 (CR-2026-174): a checksum placed while encoding - zero bytes at `pos`, filled
 * once the whole frame is written (schema_fill_checksums). */
typedef struct {
    size_t pos;
    const field_def_t* field;
    endian_t endian;
} schema_checksum_slot_t;

static inline int schema_checksum_unwritten(const field_def_t* field, char* err,
                                            size_t err_len) {
    snprintf(err, err_len, "Field '%.*s': the bytes its checksum covers are not all "
             "written when it is reached (PS-587)", SCHEMA_MAX_NAME_LEN - 1, field->name);
    return SCHEMA_ERR_CHECKSUM;
}

/* PS-587: a checksum field writes zero bytes where it is reached, never its input, and is
 * never required (PS-551 does not apply); its value is computed over the finished frame.
 * Bits a bit range or bool left pending past `pos` would be ORed into it, so the bytes it
 * covers are not all written there: rejected, as the reference rejects it. */
static inline int schema_place_checksum(const field_def_t* field, endian_t schema_endian,
                                        size_t* pos, size_t filled,
                                        schema_checksum_slot_t* slots, int* slot_count,
                                        char* err, size_t err_len) {
    const int width = schema_checksum_type_bytes(field->type);
    if (width == 0) {
        snprintf(err, err_len, "Field '%.*s': a checksum is declared on an unsigned integer "
                 "type (u8 to u64), not '%s' (PS-585)", SCHEMA_MAX_NAME_LEN - 1,
                 field->name, schema_type_label(field->type));
        return SCHEMA_ERR_TYPE;
    }
    if (filled > *pos) return schema_checksum_unwritten(field, err, err_len);
    if (*pos + (size_t)width > SCHEMA_MAX_PAYLOAD || *slot_count >= SCHEMA_MAX_FIELDS) {
        return SCHEMA_ERR_BUFFER;
    }
    slots[*slot_count].pos = *pos;
    slots[*slot_count].field = field;
    slots[*slot_count].endian = field->endian != ENDIAN_DEFAULT ? field->endian
                                                                : schema_endian;
    (*slot_count)++;
    *pos += (size_t)width;
    return SCHEMA_OK;
}

/* PS-587: each checksum over the finished frame, from its `from` to the byte before it,
 * earliest first, so an outer checksum covers an inner one's value. A range starting
 * after the field is rejected, and one starting where it is covers nothing (PS-608). */
static inline int schema_fill_checksums(uint8_t* data, schema_checksum_slot_t* slots,
                                        int slot_count, char* err, size_t err_len) {
    for (int a = 1; a < slot_count; a++) {             /* by position, stable */
        schema_checksum_slot_t t = slots[a];
        int b = a - 1;
        for (; b >= 0 && slots[b].pos > t.pos; b--) slots[b + 1] = slots[b];
        slots[b + 1] = t;
    }
    for (int k = 0; k < slot_count; k++) {
        const field_def_t* f = slots[k].field;
        if (f->checksum_from > slots[k].pos) return schema_checksum_unwritten(f, err, err_len);
        if (f->checksum_from == slots[k].pos) {        /* PS-608 (CR-2026-185) */
            snprintf(err, err_len, "Field '%.*s': the checksum range starts at byte %u, where "
                     "the field is, so it covers nothing (PS-608)", SCHEMA_MAX_NAME_LEN - 1,
                     f->name, (unsigned)f->checksum_from);
            return SCHEMA_ERR_CHECKSUM;
        }
        uint32_t value = schema_checksum_compute(f->checksum_algo, data + f->checksum_from,
                                                 slots[k].pos - f->checksum_from,
                                                 f->checksum_seed);
        size_t at = slots[k].pos;
        write_int(data, &at, (int64_t)value, schema_checksum_type_bytes(f->type),
                  slots[k].endian);
    }
    return SCHEMA_OK;
}

/* Encode a match (PS-442, PS-551): the case its discriminator selects. The
 * discriminator is the input to the field the match's variable names, which the loop
 * writes on its own; absent, the match cannot be written, and a value no case lists,
 * with no default case, is the error a decoder gives it. A body placed above
 * field_count is written here; a counted body is written by the loop, and the counted
 * bodies of the other cases are marked in `passed` for it to pass over. */
static inline int schema_encode_match_case(
    const schema_t* schema,
    const field_def_t* match,
    int index,
    const encode_inputs_t* inputs,
    uint8_t* buf,
    size_t* pos,
    bool* passed,
    size_t filled,
    schema_checksum_slot_t* checksums,
    int* checksum_count,
    char* err,
    size_t err_len
) {
    const char* var = match->match_var[0] == '$' ? match->match_var + 1 : match->match_var;
    char key[SCHEMA_MAX_NAME_LEN];
    snprintf(key, sizeof(key), "%s", var);
    for (int d = 0; d < schema->field_count; d++) {
        const field_def_t* src = &schema->fields[d];
        if (src->type == FIELD_TYPE_MATCH) continue;
        if (strcmp(src->var_name, var) == 0 || strcmp(src->name, var) == 0) {
            if (schema->nameless) snprintf(key, sizeof(key), "#%d", d);
            else snprintf(key, sizeof(key), "%s", src->name);
            break;
        }
    }
    const encode_input_t* disc = find_input(inputs, key);
    if (!disc || disc->is_text) {
        snprintf(err, err_len, "Missing required field: %s", key);
        return SCHEMA_ERR_PARSE;
    }
    int64_t value = disc->is_int ? disc->value.i64 : (int64_t)disc->value.f64;
    bool integral = disc->is_int || (double)value == disc->value.f64;
    int taken = -1;
    for (int c = 0; c < match->case_count && taken < 0; c++) {
        const case_def_t* cs = &match->cases[c];
        if (cs->is_default) { taken = c; break; }
        if (!integral) continue;
        if (cs->match_value == (int)value) { taken = c; break; }
        if (cs->range_min != cs->range_max) {
            if (value >= cs->range_min && value <= cs->range_max) taken = c;
            continue;
        }
        for (int j = 0; j < 8 && cs->match_list[j] != -1; j++) {
            if (cs->match_list[j] == (int)value) { taken = c; break; }
        }
    }
    if (taken < 0) {
        if (integral) snprintf(err, err_len, "No matching case for value %lld", (long long)value);
        else snprintf(err, err_len, "No matching case for value %g", disc->value.f64);
        return SCHEMA_ERR_MATCH;
    }
    for (int c = 0; c < match->case_count; c++) {
        const case_def_t* cs = &match->cases[c];
        for (int k = 0; k < cs->field_count; k++) {
            int at = cs->field_start + k;
            if (at < 0 || at >= SCHEMA_MAX_FIELDS) continue;
            if (c != taken && at > index && at < schema->field_count) passed[at] = true;
        }
    }
    const case_def_t* cs = &match->cases[taken];
    for (int k = 0; k < cs->field_count; k++) {
        int at = cs->field_start + k;
        if (at < schema->field_count || at >= SCHEMA_MAX_FIELDS) continue;   /* the loop's */
        field_def_t member = schema->fields[at];
        if (schema->nameless) snprintf(member.name, sizeof(member.name), "#%d", at);
        if (member.type == FIELD_TYPE_SKIP) {
            if (*pos + member.size > SCHEMA_MAX_PAYLOAD) return SCHEMA_ERR_BUFFER;
            *pos += member.size;
            continue;
        }
        if (member.checksum_algo != SCHEMA_CHECKSUM_NONE) {     /* PS-587 */
            int rc = schema_place_checksum(&schema->fields[at], schema->endian, pos,
                                           filled, checksums, checksum_count, err, err_len);
            if (rc != SCHEMA_OK) return rc;
            continue;
        }
        if (member.type == FIELD_TYPE_BITS || member.type == FIELD_TYPE_BOOL
            || member.type == FIELD_TYPE_MATCH || member.type == FIELD_TYPE_BYTE_GROUP
            || member.type == FIELD_TYPE_TLV || member.type == FIELD_TYPE_FLAGGED) {
            snprintf(err, err_len, "Field '%s': a case body field of this kind cannot be "
                     "encoded by this interpreter (PS-442)", member.name);
            return SCHEMA_ERR_UNSUPPORTED;
        }
        if (!find_input(inputs, member.name)) {
            if (member.name[0] == '_') {
                snprintf(err, err_len, "internal field '%s' reads payload bytes and "
                         "declares no value, and the input does not supply it (PS-434)",
                         member.name);
            } else {
                snprintf(err, err_len, "Missing required field: %s", member.name);
            }
            return SCHEMA_ERR_PARSE;
        }
        int rc = encode_field(&member, inputs, buf, pos, schema->endian, err, err_len);
        if (rc != SCHEMA_OK) return rc;
    }
    return SCHEMA_OK;
}

static inline int schema_encode_direction(
    const schema_t* schema,
    const encode_inputs_t* inputs,
    schema_direction_t direction,
    encode_result_t* result
) {
    memset(result, 0, sizeof(*result));

    int direction_err = schema_check_direction_msg(schema, direction, result->error_msg,
                                                   sizeof(result->error_msg));
    if (direction_err != SCHEMA_OK) {
        return direction_err;
    }

    size_t pos = 0;
    size_t filled = 0;      /* bytes a non-consuming bit range or bool has filled */
    /* CR-2026-141 (PS-551): per byte, the bits some field wrote; and each bit range or
     * bool placed, with the value it was given, or derived where it was absent. A bit
     * range or bool that does not consume shares its bytes with the fields after it,
     * which fix its value: absent, it writes no bits and is missing only where a bit is
     * left unwritten; supplied, it must read back from the finished frame.
     *
     * A peek covers its base's bytes - one for a bool or a `u8[...]`, N/8 for a
     * `uN[...]` (CR-2026-136) - so its mask is kept in the assembled value's bit space,
     * with the base's width and byte order to map it onto bytes. */
    uint8_t covered[SCHEMA_MAX_PAYLOAD];
    uint8_t scratch[SCHEMA_MAX_PAYLOAD];
    struct {
        size_t pos; uint64_t mask; uint8_t base; uint8_t start; uint8_t width;
        bool is_signed; bool given; endian_t endian; int64_t raw; const char* name;
    } peeks[SCHEMA_MAX_FIELDS];
    int peek_count = 0;
    memset(covered, 0, sizeof(covered));
    memset(scratch, 0, sizeof(scratch));

    /* PS-555: a nameless schema takes its values by field index, a named one by name;
     * an input of the other kind is refused rather than ignored. */
    for (int k = 0; k < inputs->count; k++) {
        if (inputs->inputs[k].by_index == schema->nameless) continue;
        snprintf(result->error_msg, sizeof(result->error_msg),
                 schema->nameless
                     ? "input '%s' is keyed by name, and a schema without names takes "
                       "each value by its field index (PS-555)"
                     : "input '%s' is keyed by field index, and a schema with names takes "
                       "each value by name (PS-555)",
                 inputs->inputs[k].name);
        result->error_code = SCHEMA_ERR_PARSE;
        return SCHEMA_ERR_PARSE;
    }
    /* Each field's key where the schema has no names: "#<index>", the name an
     * index-keyed input carries, so a message names the field by it ("Missing required
     * field: #3") rather than by an empty name. */
    char index_keys[SCHEMA_MAX_FIELDS][12];
    field_def_t keyed;
    schema_checksum_slot_t checksums[SCHEMA_MAX_FIELDS];     /* PS-587 (CR-2026-174) */
    int checksum_count = 0;
    bool case_passed[SCHEMA_MAX_FIELDS];
    memset(case_passed, 0, sizeof(case_passed));

    for (int i = 0; i < schema->field_count; i++) {
        const field_def_t* field = &schema->fields[i];
        if (schema->nameless && field->type != FIELD_TYPE_SKIP) {
            snprintf(index_keys[i], sizeof(index_keys[i]), "#%d", i);
            keyed = *field;
            memcpy(keyed.name, index_keys[i], sizeof(index_keys[i]));
            field = &keyed;
        }
        /* Peeks are checked after the loop, so their names must outlive `keyed`. */
        const char* key = schema->nameless && field->type != FIELD_TYPE_SKIP
                          ? index_keys[i] : schema->fields[i].name;

        if (case_passed[i]) continue;      /* the counted body of a case not taken */
        if (field->type == FIELD_TYPE_MATCH) {
            /* PS-442, PS-551: a match was passed over here, and so was every internal
             * field, so the bytes a decoder reads for them were never written and nothing
             * said so. The discriminator is the input to the field its variable names;
             * the case it selects is written - a body placed above field_count here, a
             * counted one by this loop - and every other case's counted body is passed
             * over. */
            int rc = schema_encode_match_case(schema, field, i, inputs, result->data, &pos,
                                              case_passed, filled, checksums,
                                              &checksum_count, result->error_msg,
                                              sizeof(result->error_msg));
            if (rc != SCHEMA_OK) {
                result->error_code = rc;
                memset(result->data, 0, sizeof(result->data));   /* no partial payload */
                return rc;
            }
            continue;
        }
        if (field->checksum_algo != SCHEMA_CHECKSUM_NONE) {
            /* PS-587 (CR-2026-174): zero bytes now, never the input's value, never
             * required; computed once the frame is finished. */
            const size_t at = pos;
            int rc = schema_place_checksum(&schema->fields[i], schema->endian, &pos, filled,
                                           checksums, &checksum_count, result->error_msg,
                                           sizeof(result->error_msg));
            if (rc != SCHEMA_OK) {
                result->error_code = rc;
                memset(result->data, 0, sizeof(result->data));   /* no partial payload */
                return rc;
            }
            for (size_t b = at; b < pos; b++) covered[b] = 0xFF;
            continue;
        }
        bool bitlike = field->type == FIELD_TYPE_BITS || field->type == FIELD_TYPE_BOOL;
        const int base = field->type == FIELD_TYPE_BITS ? bits_base_bytes(field) : 1;
        const uint8_t width = field->type == FIELD_TYPE_BOOL ? 1 : field->bit_width;
        const uint64_t mask = width && width <= 64 && field->bit_start < 64
            ? bits_width_mask(width) << field->bit_start : 0;
        const endian_t fendian = field->endian != ENDIAN_DEFAULT ? field->endian
                                                                 : schema->endian;

        if (field->type != FIELD_TYPE_SKIP && field->type != FIELD_TYPE_BYTE_GROUP
            && !find_input(inputs, field->name)) {
            if (bitlike && !field->consume && pos + (size_t)base <= SCHEMA_MAX_PAYLOAD) {
                /* PS-551 (CR-2026-141): derived from the fields that write its bits. */
                peeks[peek_count].pos = pos;
                peeks[peek_count].mask = mask;
                peeks[peek_count].base = (uint8_t)base;
                peeks[peek_count].start = field->bit_start;
                peeks[peek_count].width = width;
                peeks[peek_count].is_signed = field->type == FIELD_TYPE_BITS && field->bit_signed;
                peeks[peek_count].given = false;
                peeks[peek_count].endian = fendian;
                peeks[peek_count].raw = 0;
                peeks[peek_count].name = key;
                peek_count++;
                if (pos + base > filled) filled = pos + base;
                continue;
            }
            /* PS-551: rejected, naming the field, and no payload. encode_field reported
             * the code alone; nothing said which field was missing. An internal field
             * is PS-434's error: this interpreter has no `value` for it to write, and it
             * was passed over, writing nothing for the bytes a decoder reads. */
            if (schema->fields[i].name[0] == '_') {
                snprintf(result->error_msg, sizeof(result->error_msg),
                         "internal field '%s' reads payload bytes and declares no value, "
                         "and the input does not supply it (PS-434)", field->name);
            } else {
                snprintf(result->error_msg, sizeof(result->error_msg),
                         "Missing required field: %s", field->name);
            }
            result->error_code = SCHEMA_ERR_PARSE;
            return SCHEMA_ERR_PARSE;
        }
        /* Encoded alone, so its own bits can be told from those already placed. */
        size_t at = pos;
        int rc = field->type == FIELD_TYPE_BYTE_GROUP
            ? schema_encode_byte_group(schema, field, inputs, scratch, &pos,
                                       result->error_msg, sizeof(result->error_msg))
            : encode_field(field, inputs, scratch, &pos, schema->endian,
                           result->error_msg, sizeof(result->error_msg));
        size_t end = bitlike && at + (size_t)base > pos ? at + base : pos;
        if (rc == SCHEMA_OK && end > SCHEMA_MAX_PAYLOAD) rc = SCHEMA_ERR_BUFFER;
        /* A skip is padding: its zeros fix no peek's bits and contradict none. */
        bool padding = field->type == FIELD_TYPE_SKIP;
        if (rc == SCHEMA_OK && !bitlike && !padding) {
            for (size_t b = at; b < end; b++) {
                if (!(result->data[b] & (uint8_t)~scratch[b])) continue;
                /* PS-551: it would OR over a peek's different bits, changing both. */
                snprintf(result->error_msg, sizeof(result->error_msg),
                         "A field writes bits a peek before it set otherwise (PS-551)");
                for (int k = 0; k < peek_count; k++) {
                    if (!peeks[k].given || peeks[k].pos >= end
                        || peeks[k].pos + peeks[k].base <= at) continue;
                    int64_t found = bits_extract(
                        bits_read_base(scratch + peeks[k].pos, peeks[k].base,
                                       peeks[k].endian),
                        peeks[k].start, peeks[k].width, peeks[k].is_signed);
                    if (found == peeks[k].raw) continue;
                    snprintf(result->error_msg, sizeof(result->error_msg),
                             "Field '%s' is given %lld, but the fields that share its bits "
                             "write %lld (PS-551)", peeks[k].name,
                             (long long)peeks[k].raw, (long long)found);
                    break;
                }
                rc = SCHEMA_ERR_PARSE;
                break;
            }
        }
        if (rc != SCHEMA_OK) {
            result->error_code = rc;
            memset(result->data, 0, sizeof(result->data));   /* no partial payload */
            return rc;
        }
        if (field->type == FIELD_TYPE_ASCII) {
            for (size_t b = at; b < end; b++) {
                if (scratch[b] > 0x7F) result->non_ascii = true;   /* PS-537's warning */
            }
        }
        if (bitlike) {
            peeks[peek_count].pos = at;
            peeks[peek_count].mask = mask;
            peeks[peek_count].base = (uint8_t)base;
            peeks[peek_count].start = field->bit_start;
            peeks[peek_count].width = width;
            peeks[peek_count].is_signed = field->type == FIELD_TYPE_BITS && field->bit_signed;
            peeks[peek_count].given = true;
            peeks[peek_count].endian = fendian;
            peeks[peek_count].raw = bits_extract(bits_read_base(scratch + at, base, fendian),
                                                 field->bit_start, width,
                                                 peeks[peek_count].is_signed);
            peeks[peek_count].name = key;
            peek_count++;
            for (int b = 0; b < base; b++) {
                covered[at + b] |= bits_byte_mask(mask, base, b, fendian);
            }
        }
        for (size_t b = at; b < end; b++) {
            result->data[b] |= scratch[b];
            if (!bitlike && !padding) covered[b] = 0xFF;
            scratch[b] = 0;
        }
        /* A bit range or bool that did not consume its base filled those bytes: the next
         * field shares them, and if none does they are still written (CR-2026-136). */
        if (bitlike && at + base > filled) {
            filled = at + base;
        }
    }

    for (int k = 0; k < peek_count; k++) {
        int64_t found = bits_extract(
            bits_read_base(result->data + peeks[k].pos, peeks[k].base, peeks[k].endian),
            peeks[k].start, peeks[k].width, peeks[k].is_signed);
        bool uncovered = false;
        for (int b = 0; b < peeks[k].base; b++) {
            uint8_t m = bits_byte_mask(peeks[k].mask, peeks[k].base, b, peeks[k].endian);
            if ((covered[peeks[k].pos + b] & m) != m) uncovered = true;
        }
        if (!peeks[k].given && uncovered) {
            snprintf(result->error_msg, sizeof(result->error_msg),
                     "Missing required field: %s", peeks[k].name);
        } else if (peeks[k].given && found != peeks[k].raw) {
            snprintf(result->error_msg, sizeof(result->error_msg),
                     "Field '%s' is given %lld, but the fields that share its bits write "
                     "%lld (PS-551)", peeks[k].name, (long long)peeks[k].raw,
                     (long long)found);
        } else {
            continue;
        }
        result->error_code = SCHEMA_ERR_PARSE;
        memset(result->data, 0, sizeof(result->data));
        return SCHEMA_ERR_PARSE;
    }

    {
        int rc = schema_fill_checksums(result->data, checksums, checksum_count,
                                       result->error_msg, sizeof(result->error_msg));
        if (rc != SCHEMA_OK) {
            result->error_code = rc;
            memset(result->data, 0, sizeof(result->data));
            return rc;
        }
    }

    result->len = (int)(pos > filled ? pos : filled);
    if (result->len == 0) {
        /* PS-442: no bytes, where the input gives a field that reads them. Unreachable
         * while every such field is required; the last line, as in the other encoders. */
        for (int i = 0; i < schema->field_count; i++) {
            const field_def_t* f = &schema->fields[i];
            if (f->type == FIELD_TYPE_SKIP || f->type == FIELD_TYPE_MATCH
                || f->name[0] == '_' || schema->nameless) continue;
            if (!find_input(inputs, f->name)) continue;
            snprintf(result->error_msg, sizeof(result->error_msg),
                     "Encoding wrote no bytes, though the input gives '%s', which the "
                     "schema reads from the payload (PS-442)", f->name);
            result->error_code = SCHEMA_ERR_PARSE;
            return SCHEMA_ERR_PARSE;
        }
    }
    return SCHEMA_OK;
}

/* ============================================
 * Convenience: Build Schema Programmatically
 * ============================================ */

static inline field_def_t field_u8(const char* name) {
    field_def_t f = {0};
    strncpy(f.name, name, SCHEMA_MAX_NAME_LEN - 1);
    f.type = FIELD_TYPE_U8;
    f.size = 1;
    return f;
}

static inline field_def_t field_u16(const char* name, endian_t endian) {
    field_def_t f = {0};
    strncpy(f.name, name, SCHEMA_MAX_NAME_LEN - 1);
    f.type = FIELD_TYPE_U16;
    f.size = 2;
    f.endian = endian;
    return f;
}

static inline field_def_t field_s16(const char* name, endian_t endian) {
    field_def_t f = {0};
    strncpy(f.name, name, SCHEMA_MAX_NAME_LEN - 1);
    f.type = FIELD_TYPE_S16;
    f.size = 2;
    f.endian = endian;
    return f;
}

/* A bit range on a multi-byte or signed base (CR-2026-136): `uN[start:end]` is
 * field_bit_range(name, N / 8, false, start, end - start + 1, consume, endian), and
 * `sN[...]` the same with `is_signed`. `consume` is the bytes it advances (PS-016), 0 to
 * peek; `endian` is the base's byte order, ENDIAN_DEFAULT for the schema's (PS-059). */
static inline field_def_t field_bit_range(const char* name, uint8_t base_bytes,
                                          bool is_signed, uint8_t start, uint8_t width,
                                          uint8_t consume, endian_t endian) {
    field_def_t f = {0};
    strncpy(f.name, name, SCHEMA_MAX_NAME_LEN - 1);
    f.type = FIELD_TYPE_BITS;
    f.size = base_bytes;
    f.bit_signed = is_signed;
    f.bit_start = start;
    f.bit_width = width;
    f.consume = consume;
    f.endian = endian;
    return f;
}

static inline field_def_t field_bits(const char* name, uint8_t start, uint8_t width, bool consume) {
    field_def_t f = {0};
    strncpy(f.name, name, SCHEMA_MAX_NAME_LEN - 1);
    f.type = FIELD_TYPE_BITS;
    f.bit_start = start;
    f.bit_width = width;
    f.consume = consume;
    return f;
}

static inline void field_set_mult(field_def_t* f, double mult) {
    f->mult = mult;
    f->has_mult = true;
}

static inline void field_set_add(field_def_t* f, double add) {
    f->add = add;
    f->has_add = true;
}

static inline void field_set_var(field_def_t* f, const char* var_name) {
    strncpy(f->var_name, var_name, SCHEMA_MAX_NAME_LEN - 1);
}

static inline void field_set_div(field_def_t* f, double div) {
    f->div = div;
    f->has_div = true;
}

static inline void field_add_lookup(field_def_t* f, int key, const char* value) {
    if (f->lookup_count < SCHEMA_MAX_LOOKUP) {
        f->lookup[f->lookup_count].key = key;
        strncpy(f->lookup[f->lookup_count].value, value, SCHEMA_MAX_NAME_LEN - 1);
        f->lookup_count++;
    }
}

/* CR-2026-174 (PS-585): declares `f` a checksum field, its `checksum:` mapping given as
 * arguments - `algo` its name ("sum8", "xor8", "crc8/smbus", "crc16/kermit"; NULL where
 * the schema gives none), `seed` where `has_seed`, `from` (0 where the schema gives
 * none), and `on_mismatch` ("error" or "warn"; NULL for the default, error). Call it
 * after the field's type is set. SCHEMA_OK, or SCHEMA_ERR_PARSE with PS-585's text in
 * `err` and `f` unchanged - the first rule broken, in the order and words of the other
 * five implementations:
 *
 *   Field '{name}': a checksum is declared on an unsigned integer type (u8 to u64), not '{type}' (PS-585)
 *   The checksum of '{name}': `algo` is one of crc16/kermit, crc8/smbus, sum8, xor8, not '{algo}' (PS-585)
 *   Field '{name}': {type} cannot hold a {algo} result (PS-585)
 *   The checksum of '{name}': `seed` applies to sum8 and xor8, not {algo} (PS-585)
 *   The checksum of '{name}': `seed` is a byte, 0 to 255 (PS-585)
 *   The checksum of '{name}': `from` is a byte offset, 0 or more (PS-585)
 *   The checksum of '{name}': `on_mismatch` is `error` or `warn` (PS-586)
 *
 * Two rules of PS-585 are about the mapping as written - that it is one, and that it has
 * no other key - and are the caller's, which has the mapping. A `from` past 65535 is
 * refused as this interpreter's limit (it holds the offset in 16 bits); a payload here
 * is at most SCHEMA_MAX_PAYLOAD bytes, so such a range would start after any field. */
static inline int field_set_checksum(field_def_t* f, const char* algo, bool has_seed,
                                     long seed, long from, const char* on_mismatch,
                                     char* err, size_t err_len) {
    const char* name = f->name[0] ? f->name : "<unnamed>";
    char type[24];
    if (f->type == FIELD_TYPE_BITS) {
        int base = f->size ? f->size : 1;
        snprintf(type, sizeof(type), "%c%d[%d:%d]", f->bit_signed ? 's' : 'u', base * 8,
                 f->bit_start, f->bit_start + f->bit_width - 1);
    } else {
        snprintf(type, sizeof(type), "%s", schema_type_label(f->type));
    }
    const int bytes = schema_checksum_type_bytes(f->type);
    if (bytes == 0) {
        if (err) snprintf(err, err_len, "Field '%s': a checksum is declared on an unsigned "
                          "integer type (u8 to u64), not '%s' (PS-585)", name, type);
        return SCHEMA_ERR_PARSE;
    }
    uint8_t code = SCHEMA_CHECKSUM_NONE;
    for (uint8_t c = SCHEMA_CHECKSUM_SUM8; c <= SCHEMA_CHECKSUM_CRC16_KERMIT; c++) {
        if (algo && strcmp(algo, schema_checksum_name(c)) == 0) code = c;
    }
    if (code == SCHEMA_CHECKSUM_NONE) {
        if (err) {
            if (algo) snprintf(err, err_len, "The checksum of '%s': `algo` is one of "
                               "crc16/kermit, crc8/smbus, sum8, xor8, not '%s' (PS-585)",
                               name, algo);
            else snprintf(err, err_len, "The checksum of '%s': `algo` is one of "
                          "crc16/kermit, crc8/smbus, sum8, xor8, not None (PS-585)", name);
        }
        return SCHEMA_ERR_PARSE;
    }
    if (bytes * 8 < schema_checksum_bits(code)) {
        if (err) snprintf(err, err_len, "Field '%s': %s cannot hold a %s result (PS-585)",
                          name, type, algo);
        return SCHEMA_ERR_PARSE;
    }
    if (has_seed && code != SCHEMA_CHECKSUM_SUM8 && code != SCHEMA_CHECKSUM_XOR8) {
        if (err) snprintf(err, err_len, "The checksum of '%s': `seed` applies to sum8 and "
                          "xor8, not %s (PS-585)", name, algo);
        return SCHEMA_ERR_PARSE;
    }
    if (has_seed && (seed < 0 || seed > 255)) {
        if (err) snprintf(err, err_len, "The checksum of '%s': `seed` is a byte, 0 to 255 "
                          "(PS-585)", name);
        return SCHEMA_ERR_PARSE;
    }
    if (from < 0) {
        if (err) snprintf(err, err_len, "The checksum of '%s': `from` is a byte offset, 0 "
                          "or more (PS-585)", name);
        return SCHEMA_ERR_PARSE;
    }
    if (on_mismatch && strcmp(on_mismatch, "error") != 0 && strcmp(on_mismatch, "warn") != 0) {
        if (err) snprintf(err, err_len, "The checksum of '%s': `on_mismatch` is `error` or "
                          "`warn` (PS-586)", name);
        return SCHEMA_ERR_PARSE;
    }
    if (from > 0xFFFF) {
        if (err) snprintf(err, err_len, "The checksum of '%s': a `from` of %ld is past the "
                          "65535 this interpreter holds", name, from);
        return SCHEMA_ERR_PARSE;
    }
    f->checksum_algo = code;
    f->checksum_seed = has_seed ? (uint8_t)seed : 0;
    f->checksum_from = (uint16_t)from;
    f->checksum_warn = on_mismatch && strcmp(on_mismatch, "warn") == 0;
    return SCHEMA_OK;
}

/* PS-586's warning text for the first checksum a decode found wrong under
 * `on_mismatch: warn` (decode_result_t.checksum_warnings), rendered from the result and
 * the payload it was decoded from. False where there is none. */
static inline bool schema_checksum_warning(const schema_t* schema,
                                           const decode_result_t* result,
                                           const uint8_t* buf, size_t len,
                                           char* out, size_t out_len) {
    if (result->checksum_warnings == 0) return false;
    const int index = result->checksum_warning_field;
    if (index < 0 || index >= SCHEMA_MAX_FIELDS) return false;
    const field_def_t* field = &schema->fields[index];
    const size_t at = result->checksum_warning_at;
    if (at + (size_t)schema_checksum_type_bytes(field->type) > len) return false;
    schema_checksum_message(out, out_len, field, buf, at,
                            field->endian != ENDIAN_DEFAULT ? field->endian : schema->endian);
    return true;
}

static inline field_def_t field_u24(const char* name, endian_t endian) {
    field_def_t f = {0};
    strncpy(f.name, name, SCHEMA_MAX_NAME_LEN - 1);
    f.type = FIELD_TYPE_U24;
    f.size = 3;
    f.endian = endian;
    return f;
}

static inline field_def_t field_u32(const char* name, endian_t endian) {
    field_def_t f = {0};
    strncpy(f.name, name, SCHEMA_MAX_NAME_LEN - 1);
    f.type = FIELD_TYPE_U32;
    f.size = 4;
    f.endian = endian;
    return f;
}

static inline field_def_t field_s8(const char* name) {
    field_def_t f = {0};
    strncpy(f.name, name, SCHEMA_MAX_NAME_LEN - 1);
    f.type = FIELD_TYPE_S8;
    f.size = 1;
    return f;
}

static inline field_def_t field_s24(const char* name, endian_t endian) {
    field_def_t f = {0};
    strncpy(f.name, name, SCHEMA_MAX_NAME_LEN - 1);
    f.type = FIELD_TYPE_S24;
    f.size = 3;
    f.endian = endian;
    return f;
}

static inline field_def_t field_s32(const char* name, endian_t endian) {
    field_def_t f = {0};
    strncpy(f.name, name, SCHEMA_MAX_NAME_LEN - 1);
    f.type = FIELD_TYPE_S32;
    f.size = 4;
    f.endian = endian;
    return f;
}

static inline field_def_t field_u64(const char* name, endian_t endian) {
    field_def_t f = {0};
    strncpy(f.name, name, SCHEMA_MAX_NAME_LEN - 1);
    f.type = FIELD_TYPE_U64;
    f.size = 8;
    f.endian = endian;
    return f;
}

static inline field_def_t field_s64(const char* name, endian_t endian) {
    field_def_t f = {0};
    strncpy(f.name, name, SCHEMA_MAX_NAME_LEN - 1);
    f.type = FIELD_TYPE_S64;
    f.size = 8;
    f.endian = endian;
    return f;
}

static inline field_def_t field_f16(const char* name, endian_t endian) {
    field_def_t f = {0};
    strncpy(f.name, name, SCHEMA_MAX_NAME_LEN - 1);
    f.type = FIELD_TYPE_F16;
    f.size = 2;
    f.endian = endian;
    return f;
}

static inline field_def_t field_f32(const char* name, endian_t endian) {
    field_def_t f = {0};
    strncpy(f.name, name, SCHEMA_MAX_NAME_LEN - 1);
    f.type = FIELD_TYPE_F32;
    f.size = 4;
    f.endian = endian;
    return f;
}

static inline field_def_t field_f64(const char* name, endian_t endian) {
    field_def_t f = {0};
    strncpy(f.name, name, SCHEMA_MAX_NAME_LEN - 1);
    f.type = FIELD_TYPE_F64;
    f.size = 8;
    f.endian = endian;
    return f;
}

static inline field_def_t field_bool(const char* name, uint8_t bit_pos, bool consume) {
    field_def_t f = {0};
    strncpy(f.name, name, SCHEMA_MAX_NAME_LEN - 1);
    f.type = FIELD_TYPE_BOOL;
    f.bit_start = bit_pos;
    f.consume = consume;
    return f;
}

static inline field_def_t field_enum(const char* name, uint8_t base_size) {
    field_def_t f = {0};
    strncpy(f.name, name, SCHEMA_MAX_NAME_LEN - 1);
    f.type = FIELD_TYPE_ENUM;
    f.size = base_size;
    return f;
}

static inline field_def_t field_hex(const char* name, uint8_t length) {
    field_def_t f = {0};
    strncpy(f.name, name, SCHEMA_MAX_NAME_LEN - 1);
    f.type = FIELD_TYPE_HEX;
    f.size = length;
    return f;
}

static inline field_def_t field_ascii(const char* name, uint8_t length) {
    field_def_t f = {0};
    strncpy(f.name, name, SCHEMA_MAX_NAME_LEN - 1);
    f.type = FIELD_TYPE_ASCII;
    f.size = length;
    return f;
}

static inline field_def_t field_skip(uint8_t length) {
    field_def_t f = {0};
    strncpy(f.name, "_skip", SCHEMA_MAX_NAME_LEN - 1);
    f.type = FIELD_TYPE_SKIP;
    f.size = length;
    return f;
}

static inline field_def_t field_bytes_type(const char* name, uint8_t length) {
    field_def_t f = {0};
    strncpy(f.name, name, SCHEMA_MAX_NAME_LEN - 1);
    f.type = FIELD_TYPE_BYTES;
    f.size = length;
    return f;
}

/* ============================================
 * Result Access Helpers
 * ============================================ */

static inline const decoded_field_t* result_get_field(
    const decode_result_t* result, 
    const char* name
) {
    /* No value is found by a name the schema did not carry: an empty name matches
     * nothing, and a schema without names has none to match (PS-555). */
    if (!name || !name[0] || result->nameless) return NULL;
    for (int i = 0; i < result->field_count; i++) {
        if (strcmp(result->fields[i].name, name) == 0) {
            return &result->fields[i];
        }
    }
    return NULL;
}

/* The value the schema's field `index` produced, in schema order, or NULL where that
 * field reported nothing (an internal or skipped field, an omitted value). This is the
 * only way to reach a value decoded with a schema that carries no names (CR-2026-142),
 * and works for a named one too. */
static inline const decoded_field_t* result_get_index(
    const decode_result_t* result,
    int index
) {
    for (int i = 0; i < result->field_count; i++) {
        if (result->fields[i].field_index == index) {
            return &result->fields[i];
        }
    }
    return NULL;
}

static inline double result_get_double(
    const decode_result_t* result, 
    const char* name, 
    double default_val
) {
    const decoded_field_t* f = result_get_field(result, name);
    return f ? f->value.f64 : default_val;
}

static inline int64_t result_get_int(
    const decode_result_t* result, 
    const char* name, 
    int64_t default_val
) {
    const decoded_field_t* f = result_get_field(result, name);
    return f ? f->value.i64 : default_val;
}

static inline const char* result_get_string(
    const decode_result_t* result, 
    const char* name
) {
    const decoded_field_t* f = result_get_field(result, name);
    return f ? f->value.str : NULL;
}

/* ============================================
 * Binary Schema Loading
 * ============================================
 *
 * The form tools/schema_binary.py writes, format version 3 (CR-2026-142):
 *
 *   Header (5 bytes): 'P' 'S' format_version flags count
 *     format_version : 3. Any other is refused: version 1 put the schema's own
 *                      `version` here and carried no names, and 2 is
 *                      tools/binary_schema_v2.py's different layout under the same magic.
 *     flags bit 0    : 1 = little-endian
 *     flags bits 1-2 : declared direction, 0 unset / 1 uplink / 2 downlink / 3 both
 *     count bit 7    : a name table follows the field records
 *     count bits 0-6 : field count
 *   Per field record:
 *     type_byte: bit 7 lookup follows; bits 4-6 type; bits 0-3 size
 *     mult_exp:  signed exponent, mult = 10^exp, or 0x81/0x82/0x84 for 1/2, 1/4, 1/16
 *     ipso_obj:  2 bytes little-endian, the field's IPSO object or 0. An annotation:
 *                no name is ever derived from it (PS-555).
 *     [options]: bitfield info, add value, lookup table
 *   Name table, where count bit 7 is set - one entry per record, in field order:
 *     length (1 byte), then that many bytes of UTF-8. 0 only for a skip.
 *
 * Without the name table the fields keep empty names, schema_t.nameless is set, and a
 * decode reports each value by field index (result_get_index) - never under a name
 * made up from the IPSO object, type or position, which this loader used to do.
 *
 * Type codes:
 *   0x0 = uint, 0x1 = sint, 0x2 = float, 0x3 = bytes,
 *   0x4 = bool, 0x5 = enum, 0x6 = bitfield, 0x7 = skip
 */

#define BINARY_FORMAT_VERSION 3
#define BINARY_NAMES_FLAG     0x80

#define BINARY_TYPE_UINT     0x0
#define BINARY_TYPE_SINT     0x1
#define BINARY_TYPE_FLOAT    0x2
#define BINARY_TYPE_BYTES    0x3
#define BINARY_TYPE_BOOL     0x4
#define BINARY_TYPE_ENUM     0x5
#define BINARY_TYPE_BITFIELD 0x6
/* 0x8 until format version 3, where the lookup flag in bit 7 shadowed it. 0x7 was a
 * `match` record that this loader never parsed and the writer refused. */
#define BINARY_TYPE_SKIP     0x7

/* Convert type code + size to field_type_t; FIELD_TYPE_UNKNOWN for no type. */
static inline field_type_t binary_type_to_field_type(uint8_t type_code, uint8_t size) {
    switch (type_code) {
        case BINARY_TYPE_UINT:
            switch (size) {
                case 1: return FIELD_TYPE_U8;
                case 2: return FIELD_TYPE_U16;
                case 3: return FIELD_TYPE_U24;
                case 4: return FIELD_TYPE_U32;
                case 8: return FIELD_TYPE_U64;
            }
            break;
        case BINARY_TYPE_SINT:
            switch (size) {
                case 1: return FIELD_TYPE_S8;
                case 2: return FIELD_TYPE_S16;
                case 3: return FIELD_TYPE_S24;
                case 4: return FIELD_TYPE_S32;
                case 8: return FIELD_TYPE_S64;
            }
            break;
        case BINARY_TYPE_FLOAT:
            if (size == 2) return FIELD_TYPE_F16;
            if (size == 4) return FIELD_TYPE_F32;
            if (size == 8) return FIELD_TYPE_F64;
            break;
        case BINARY_TYPE_BOOL:
            return FIELD_TYPE_BOOL;
        case BINARY_TYPE_ENUM:
            /* One byte, its table carried as a lookup. */
            if (size == 1) return FIELD_TYPE_U8;
            break;
        case BINARY_TYPE_BITFIELD:
            return FIELD_TYPE_BITS;
        case BINARY_TYPE_SKIP:
            return FIELD_TYPE_SKIP;
        case BINARY_TYPE_BYTES:
            return FIELD_TYPE_BYTES;
    }
    return FIELD_TYPE_UNKNOWN;
}

/* Convert exponent byte to multiplier */
static inline double binary_exp_to_mult(uint8_t exp) {
    if (exp == 0) return 1.0;

    /* Special cases for 0.5-based */
    if (exp == 0x81) return 0.5;
    if (exp == 0x82) return 0.25;
    if (exp == 0x84) return 0.0625;

    /* Signed exponent for power of 10 */
    int8_t signed_exp = (exp > 127) ? (int8_t)(exp - 256) : (int8_t)exp;

    double mult = 1.0;
    if (signed_exp > 0) {
        for (int i = 0; i < signed_exp; i++) mult *= 10.0;
    } else {
        for (int i = 0; i < -signed_exp; i++) mult /= 10.0;
    }
    return mult;
}

/* Write a load error's text where the caller gave room for one; returns `rc`. */
static inline int binary_load_error(char* err, size_t err_len, int rc, const char* fmt, ...) {
    if (err && err_len) {
        va_list ap;
        va_start(ap, fmt);
        vsnprintf(err, err_len, fmt, ap);
        va_end(ap);
    }
    return rc;
}

/* Load a schema from the binary form, writing the reason for a refusal to `err` (which
 * may be NULL). Every refusal is SCHEMA_ERR_PARSE and leaves no usable schema:
 *   - a format version other than BINARY_FORMAT_VERSION;
 *   - a record, lookup table or name that runs past the end of the blob, or bytes left
 *     after the last name (or record, without names);
 *   - more fields than SCHEMA_MAX_FIELDS or lookup entries than SCHEMA_MAX_LOOKUP;
 *   - a type and size naming no type;
 *   - an empty name on anything but a skip (PS-553), a name holding a NUL byte, or a
 *     name or label longer than SCHEMA_MAX_NAME_LEN - 1 bytes. Nothing is truncated:
 *     a cut name would report a key the schema does not have (PS-555). */
static inline int schema_load_binary_msg(
    schema_t* schema,
    const uint8_t* data,
    size_t len,
    char* err,
    size_t err_len
) {
    if (err && err_len) err[0] = '\0';
    if (!schema) return binary_load_error(err, err_len, SCHEMA_ERR_PARSE, "no schema");
    schema_init(schema);
    if (!data || len < 5) {
        return binary_load_error(err, err_len, SCHEMA_ERR_PARSE,
                                 "binary schema too short: %u byte(s)", (unsigned)len);
    }
    if (data[0] != 'P' || data[1] != 'S') {
        return binary_load_error(err, err_len, SCHEMA_ERR_PARSE, "not a binary schema");
    }
    if (data[2] != BINARY_FORMAT_VERSION) {
        return binary_load_error(err, err_len, SCHEMA_ERR_PARSE,
                                 "binary schema format version %u is not supported; this "
                                 "loader reads version %d (CR-2026-142)",
                                 (unsigned)data[2], BINARY_FORMAT_VERSION);
    }

    schema->endian = (data[3] & 0x01) ? ENDIAN_LITTLE : ENDIAN_BIG;
    /* Bits 1-2 carry the schema's declared direction. Without this a binary-loaded
     * schema lost its declaration at load time and the check silently did nothing -
     * the same failure mode CR-2026-009 found when the binary format flattened a
     * sequence lookup into a mapping. Zero means unset. */
    schema->direction = (schema_direction_t)((data[3] >> 1) & 0x03);

    const bool has_names = (data[4] & BINARY_NAMES_FLAG) != 0;
    const int field_count = data[4] & 0x7F;
    if (field_count > SCHEMA_MAX_FIELDS) {
        schema_init(schema);
        return binary_load_error(err, err_len, SCHEMA_ERR_PARSE,
                                 "%d fields; this interpreter holds %d (SCHEMA_MAX_FIELDS)",
                                 field_count, SCHEMA_MAX_FIELDS);
    }
    size_t offset = 5;

#define BINARY_NEED(n, what)                                                          \
    do {                                                                              \
        if (offset + (size_t)(n) > len) {                                             \
            schema_init(schema);                                                      \
            return binary_load_error(err, err_len, SCHEMA_ERR_PARSE,                  \
                                     "field %d: %s at offset %u runs past the end "   \
                                     "of the %u-byte blob", i, (what),                \
                                     (unsigned)offset, (unsigned)len);                \
        }                                                                             \
    } while (0)

    for (int i = 0; i < field_count; i++) {
        field_def_t f;
        memset(&f, 0, sizeof(f));

        BINARY_NEED(4, "its record");
        uint8_t type_byte = data[offset];
        bool has_lookup = (type_byte & 0x80) != 0;
        uint8_t type_code = (type_byte >> 4) & 0x07;
        uint8_t size = type_byte & 0x0F;

        f.type = binary_type_to_field_type(type_code, size);
        if (f.type == FIELD_TYPE_UNKNOWN) {
            schema_init(schema);
            return binary_load_error(err, err_len, SCHEMA_ERR_PARSE,
                                     "field %d: type code %u with size %u names no type",
                                     i, (unsigned)type_code, (unsigned)size);
        }
        /* A bitfield record's size nibble is its width, which the bit-range byte below
         * carries too; its base is one byte, the only one this format can name
         * (CR-2026-136 made `size` a bit range's base width). */
        f.size = type_code == BINARY_TYPE_BITFIELD ? 1 : size;

        double mult = binary_exp_to_mult(data[offset + 1]);
        if (mult != 1.0) {
            f.mult = mult;
            f.has_mult = true;
        }
        /* data[offset + 2..3] is the IPSO object: carried, never turned into a name. */
        offset += 4;

        if (type_code == BINARY_TYPE_BITFIELD) {
            BINARY_NEED(1, "its bit range");
            uint8_t bf_byte = data[offset++];
            f.bit_start = (bf_byte >> 4) & 0x0F;
            f.bit_width = bf_byte & 0x0F;
            if (offset < len && data[offset] == 0x01) {
                f.consume = true;
                offset++;
            }
        }

        /* Add marker (0xA0) */
        if (offset < len && data[offset] == 0xA0) {
            BINARY_NEED(3, "its add");
            int16_t add_val = (int16_t)(data[offset + 1] | (data[offset + 2] << 8));
            f.add = add_val / 100.0;
            f.has_add = true;
            offset += 3;
        }

        if (has_lookup) {
            /* The count byte's high bit marks a table that came from a sequence, so
             * PS-105 can be told from PS-269 after both forms are stored keyed. */
            BINARY_NEED(1, "its lookup count");
            uint8_t lookup_header = data[offset++];
            int lookup_count = lookup_header & 0x7F;
            f.lookup_is_sequence = (lookup_header & 0x80) != 0;
            if (lookup_count > SCHEMA_MAX_LOOKUP) {
                schema_init(schema);
                return binary_load_error(err, err_len, SCHEMA_ERR_PARSE,
                                         "field %d: %d lookup entries; this interpreter "
                                         "holds %d (SCHEMA_MAX_LOOKUP)",
                                         i, lookup_count, SCHEMA_MAX_LOOKUP);
            }
            for (int j = 0; j < lookup_count; j++) {
                BINARY_NEED(2, "a lookup entry");
                uint8_t key = data[offset];
                uint8_t str_len = data[offset + 1];
                offset += 2;
                BINARY_NEED(str_len, "a lookup label");
                if (str_len > SCHEMA_MAX_NAME_LEN - 1) {
                    schema_init(schema);
                    return binary_load_error(err, err_len, SCHEMA_ERR_PARSE,
                                             "field %d: a %u-byte lookup label; this "
                                             "interpreter holds %d",
                                             i, (unsigned)str_len, SCHEMA_MAX_NAME_LEN - 1);
                }
                f.lookup[f.lookup_count].key = key;
                memcpy(f.lookup[f.lookup_count].value, &data[offset], str_len);
                f.lookup[f.lookup_count].value[str_len] = '\0';
                f.lookup_count++;
                offset += str_len;
            }
        }

        schema->fields[schema->field_count++] = f;
    }

    if (has_names) {
        for (int i = 0; i < field_count; i++) {
            field_def_t* f = &schema->fields[i];
            BINARY_NEED(1, "its name length");
            uint8_t name_len = data[offset++];
            BINARY_NEED(name_len, "its name");
            if (name_len == 0 && f->type != FIELD_TYPE_SKIP) {
                schema_init(schema);
                return binary_load_error(err, err_len, SCHEMA_ERR_PARSE,
                                         "field %d (%s) has an empty name; only a skip "
                                         "has none (PS-553)",
                                         i, schema_type_label(f->type));
            }
            if (name_len > SCHEMA_MAX_NAME_LEN - 1) {
                schema_init(schema);
                return binary_load_error(err, err_len, SCHEMA_ERR_PARSE,
                                         "field %d's name is %u bytes, more than the "
                                         "%d this interpreter holds "
                                         "(SCHEMA_MAX_NAME_LEN), and a name is never "
                                         "truncated (PS-555): '%.*s'",
                                         i, (unsigned)name_len, SCHEMA_MAX_NAME_LEN - 1,
                                         (int)name_len, (const char*)&data[offset]);
            }
            if (memchr(&data[offset], 0, name_len) != NULL) {
                schema_init(schema);
                return binary_load_error(err, err_len, SCHEMA_ERR_PARSE,
                                         "field %d's name holds a NUL byte, which a C "
                                         "string cannot carry", i);
            }
            memcpy(f->name, &data[offset], name_len);
            f->name[name_len] = '\0';
            offset += name_len;
        }
    } else {
        schema->nameless = true;
    }
#undef BINARY_NEED

    if (offset != len) {
        unsigned extra = (unsigned)(len - offset);
        unsigned at = (unsigned)offset;
        schema_init(schema);
        return binary_load_error(err, err_len, SCHEMA_ERR_PARSE,
                                 "%u byte(s) after the last %s, from offset %u",
                                 extra, has_names ? "name" : "field record", at);
    }
    return SCHEMA_OK;
}

/* Load schema from binary format; schema_load_binary_msg says why one is refused. */
static inline int schema_load_binary(
    schema_t* schema,
    const uint8_t* data,
    size_t len
) {
    return schema_load_binary_msg(schema, data, len, NULL, 0);
}

#ifdef __cplusplus
}
#endif

#endif /* SCHEMA_INTERPRETER_H */
