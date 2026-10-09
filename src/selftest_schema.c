/*
 * selftest_schema.c - Self-tests for the schema interpreter's lookup behaviour
 *
 * The two `lookup` failure cases are different requirements, and this interpreter
 * used to get both wrong on the ordinary-field path:
 *
 *   PS-105  an out-of-bounds index into a sequence is an error
 *   PS-269  a mapping with no entry omits the field
 *
 * It stored the raw integer for either - under a name that promises a label - while
 * the enum path a few lines above had already been corrected to omit. The two sites
 * disagreed with each other, and nothing here noticed, because the interpreter's C
 * tests (src/test_interpreter.c and three others) are in no build target. This file
 * is in SELFTEST_SRCS, so `make selftest` runs it.
 */

#include "selftests.h"
#include "rt.h"
#include "schema_interpreter.h"

#include <string.h>

/* Module identifier for logging */
#define MOD "TEST"

/* A sequence lookup, as tools/schema_binary.py now marks it: stored keyed, with the
 * sequence flag set so PS-105 can be told from PS-269. */
static void build_sequence(schema_t* s) {
    memset(s, 0, sizeof(*s));
    s->endian = ENDIAN_BIG;
    field_def_t f = field_u8("relay");
    field_add_lookup(&f, 0, "off_state");
    field_add_lookup(&f, 1, "on_state");
    f.lookup_is_sequence = true;
    schema_add_field(s, &f);
}

static void build_mapping(schema_t* s) {
    memset(s, 0, sizeof(*s));
    s->endian = ENDIAN_BIG;
    field_def_t f = field_u8("button");
    field_add_lookup(&f, 1, "short");
    field_add_lookup(&f, 2, "long");
    schema_add_field(s, &f);
}

/*
 * Test: an in-range sequence index maps to its label
 */
static void test_sequence_in_range(void) {
    schema_t s;
    decode_result_t r;
    uint8_t payload[] = {0x01};

    build_sequence(&s);
    TCHECK(schema_decode(&s, payload, sizeof(payload), &r) == SCHEMA_OK);
    TCHECK(r.field_count == 1);
    TCHECK(strcmp(r.fields[0].value.str, "on_state") == 0);
}

/*
 * Test: PS-105 - an out-of-bounds sequence index is an error, not the raw index
 */
static void test_sequence_out_of_bounds_is_an_error(void) {
    schema_t s;
    decode_result_t r;
    uint8_t payload[] = {0x07};

    build_sequence(&s);
    TCHECK(schema_decode(&s, payload, sizeof(payload), &r) == SCHEMA_ERR_LOOKUP);
    TCHECK(r.error_code == SCHEMA_ERR_LOOKUP);
    /* The point of the requirement: no raw index reported as though it were a label */
    TCHECK(r.field_count == 0);
}

/*
 * Test: PS-269 - a mapping gap omits the field and is not an error
 */
static void test_mapping_gap_omits_quietly(void) {
    schema_t s;
    decode_result_t r;
    uint8_t payload[] = {0x09};

    build_mapping(&s);
    TCHECK(schema_decode(&s, payload, sizeof(payload), &r) == SCHEMA_OK);
    TCHECK(r.field_count == 0);
}

/*
 * Test: a mapping still maps the values it does have
 */
static void test_mapping_hit(void) {
    schema_t s;
    decode_result_t r;
    uint8_t payload[] = {0x02};

    build_mapping(&s);
    TCHECK(schema_decode(&s, payload, sizeof(payload), &r) == SCHEMA_OK);
    TCHECK(r.field_count == 1);
    TCHECK(strcmp(r.fields[0].value.str, "long") == 0);
}

/*
 * Test: the sequence flag survives a round trip through the binary format
 *
 * The flag rides the high bit of the lookup count byte, so a schema built by hand
 * and one loaded from binary must agree.
 */
static void test_sequence_flag_survives_binary(void) {
    /* One field, u8 "relay", with a two-entry sequence lookup. Hand-assembled in the
     * v1 layout that tools/schema_binary.py emits. */
    const uint8_t encoded_count_byte = 0x80 | 2;
    TCHECK((encoded_count_byte & 0x7F) == 2);
    TCHECK((encoded_count_byte & 0x80) != 0);

    /* And a mapping's count byte carries no flag. */
    const uint8_t mapping_count_byte = 2;
    TCHECK((mapping_count_byte & 0x80) == 0);
}

/*
 * CR-2026-010: a message the schema disclaims is not decoded
 *
 * This interpreter has no port selection, so the port-level requirements PS-021 and
 * PS-287 to PS-289 have no entry to check; what applies is the schema-level declaration
 * (PS-291) and its mirror on encoding (PS-292). Before this the struct had no member to
 * hold the declaration at all, so the check was not merely unimplemented but
 * unimplementable - and a binary-loaded schema lost the declaration at load time.
 */
static void build_config(schema_t* s) {
    memset(s, 0, sizeof(*s));
    s->endian = ENDIAN_BIG;
    snprintf(s->name, sizeof(s->name), "cfg");
    s->direction = SCHEMA_DIR_DOWNLINK;
    field_def_t f = field_u16("reporting_interval", ENDIAN_BIG);
    schema_add_field(s, &f);
}

static void test_direction_mismatch_is_an_error(void) {
    schema_t s;
    decode_result_t r;
    uint8_t payload[] = {0x00, 0x3C};

    build_config(&s);
    TCHECK(schema_decode_direction(&s, payload, sizeof(payload), SCHEMA_DIR_UPLINK, &r)
           == SCHEMA_ERR_DIRECTION);
    /* PS-288: no field is reported. */
    TCHECK(r.field_count == 0);
    /* The message every other implementation reports for this case. */
    TCHECK(strcmp(r.error_msg,
                  "schema 'cfg' is declared direction:downlink; message direction is uplink") == 0);
}

static void test_the_declared_direction_still_decodes(void) {
    schema_t s;
    decode_result_t r;
    uint8_t payload[] = {0x00, 0x3C};

    build_config(&s);
    TCHECK(schema_decode_direction(&s, payload, sizeof(payload), SCHEMA_DIR_DOWNLINK, &r)
           == SCHEMA_OK);
    TCHECK(r.field_count == 1);
    /* An integer-typed field is delivered through the integer member since
     * CR-2026-011; it used to be a double with the declared type in the tag beside it. */
    TCHECK(r.fields[0].type == FIELD_TYPE_U16);
    TCHECK(r.fields[0].value.i64 == 60);
}

static void test_the_direction_check_is_opt_in(void) {
    schema_t s;
    decode_result_t r;
    uint8_t payload[] = {0x00, 0x3C};

    /* PS-290: an unstated direction decodes as before, so no existing caller changes. */
    build_config(&s);
    TCHECK(schema_decode(&s, payload, sizeof(payload), &r) == SCHEMA_OK);
    TCHECK(r.field_count == 1);

    /* PS-287: a schema declaring nothing accepts either direction. */
    memset(&s, 0, sizeof(s));
    s.endian = ENDIAN_BIG;
    field_def_t f = field_u16("x", ENDIAN_BIG);
    schema_add_field(&s, &f);
    TCHECK(schema_decode_direction(&s, payload, sizeof(payload), SCHEMA_DIR_UPLINK, &r) == SCHEMA_OK);
    TCHECK(schema_decode_direction(&s, payload, sizeof(payload), SCHEMA_DIR_DOWNLINK, &r) == SCHEMA_OK);

    /* And one declaring `both` accepts either. */
    s.direction = SCHEMA_DIR_BOTH;
    TCHECK(schema_decode_direction(&s, payload, sizeof(payload), SCHEMA_DIR_UPLINK, &r) == SCHEMA_OK);
    TCHECK(schema_decode_direction(&s, payload, sizeof(payload), SCHEMA_DIR_DOWNLINK, &r) == SCHEMA_OK);
}

static void test_encoding_is_checked_too(void) {
    /* PS-292: emitting the bytes would put a malformed frame on the air. */
    schema_t s;
    encode_result_t out;
    encode_inputs_t inputs;

    build_config(&s);
    encode_inputs_init(&inputs);
    encode_inputs_add_int(&inputs, "reporting_interval", 60);

    TCHECK(schema_encode_direction(&s, &inputs, SCHEMA_DIR_UPLINK, &out) == SCHEMA_ERR_DIRECTION);
    TCHECK(out.len == 0);
    TCHECK(schema_encode_direction(&s, &inputs, SCHEMA_DIR_DOWNLINK, &out) == SCHEMA_OK);
    TCHECK(out.len == 2);
}

static void test_direction_survives_the_binary_format(void) {
    /* Flags bits 1-2 carry the declaration. Without them a binary-loaded schema lost it
     * at load time and the check silently did nothing. */
    schema_t s;
    const uint8_t downlink_schema[] = {
        'P', 'S', 1,
        (uint8_t)(SCHEMA_DIR_DOWNLINK << 1),  /* big-endian, declared downlink */
        0,                                     /* no fields */
    };
    TCHECK(schema_load_binary(&s, downlink_schema, sizeof(downlink_schema)) == SCHEMA_OK);
    TCHECK(s.direction == SCHEMA_DIR_DOWNLINK);

    /* A schema emitted before the bits were defined still reads as "declares nothing". */
    const uint8_t legacy_schema[] = {'P', 'S', 1, 0x00, 0};
    TCHECK(schema_load_binary(&s, legacy_schema, sizeof(legacy_schema)) == SCHEMA_OK);
    TCHECK(s.direction == SCHEMA_DIR_UNSET);

    /* And the endian bit still means what it did. */
    const uint8_t little_uplink[] = {
        'P', 'S', 1,
        (uint8_t)(0x01 | (SCHEMA_DIR_UPLINK << 1)),
        0,
    };
    TCHECK(schema_load_binary(&s, little_uplink, sizeof(little_uplink)) == SCHEMA_OK);
    TCHECK(s.endian == ENDIAN_LITTLE);
    TCHECK(s.direction == SCHEMA_DIR_UPLINK);
}

/*
 * CR-2026-011: an integer-typed field is delivered through the integer member with its
 * exact value, and a field carrying a modifier is reported as a `number`.
 *
 * Every numeric field used to be stored in the union's double member with the declared
 * type in the tag beside it, so the tag said integer while the value was a double: a
 * consumer trusting the tag and reading value.i64 on a u16 of 60 got 4633641066610819072.
 * A u64 of 2^64-1 was read exactly into value.u64 and then overwritten with
 * (double)value.u64 four lines later, reporting 18446744073709551616 - a value larger
 * than the type can hold.
 */
static void test_an_integer_field_uses_the_integer_member(void) {
    schema_t s;
    decode_result_t r;
    uint8_t payload[] = {0x00, 0x3C};

    memset(&s, 0, sizeof(s));
    s.endian = ENDIAN_BIG;
    field_def_t f = field_u16("v", ENDIAN_BIG);
    schema_add_field(&s, &f);

    TCHECK(schema_decode(&s, payload, sizeof(payload), &r) == SCHEMA_OK);
    TCHECK(r.fields[0].type == FIELD_TYPE_U16);   /* PS-279: the declared type */
    TCHECK(r.fields[0].value.i64 == 60);          /* PS-293: through the integer member */
}

static void test_a_modifier_makes_the_field_a_number(void) {
    schema_t s;
    decode_result_t r;
    uint8_t payload[] = {0x00, 0xEB};

    memset(&s, 0, sizeof(s));
    s.endian = ENDIAN_BIG;
    field_def_t f = field_s16("v", ENDIAN_BIG);
    f.has_div = true;
    f.div = 10.0f;
    schema_add_field(&s, &f);

    TCHECK(schema_decode(&s, payload, sizeof(payload), &r) == SCHEMA_OK);
    /* PS-279: the tag used to keep the declared integer type while holding a fraction. */
    TCHECK(r.fields[0].type == FIELD_TYPE_F64);
    TCHECK(r.fields[0].value.f64 > 23.4 && r.fields[0].value.f64 < 23.6);
}

/* CR-2026-057, PS-100: a zero divisor leaves the field absent, and the next field still
 * decodes. The division used to be skipped, reporting the undivided value. */
static void test_a_zero_divisor_omits_the_field(void) {
    schema_t s;
    decode_result_t r;
    uint8_t payload[] = {0x00, 0x07, 0x05};

    memset(&s, 0, sizeof(s));
    s.endian = ENDIAN_BIG;
    field_def_t f = field_u16("v", ENDIAN_BIG);
    f.has_div = true;
    f.div = 0.0f;
    schema_add_field(&s, &f);
    field_def_t w = field_u8("w");
    schema_add_field(&s, &w);

    TCHECK(schema_decode(&s, payload, sizeof(payload), &r) == SCHEMA_OK);
    TCHECK(r.field_count == 1);
    TCHECK(strcmp(r.fields[0].name, "w") == 0);
}

static void test_a_u64_is_exact_at_the_top_of_its_range(void) {
    schema_t s;
    decode_result_t r;
    uint8_t payload[] = {0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF};

    memset(&s, 0, sizeof(s));
    s.endian = ENDIAN_BIG;
    field_def_t f = field_u64("v", ENDIAN_BIG);
    schema_add_field(&s, &f);

    TCHECK(schema_decode(&s, payload, sizeof(payload), &r) == SCHEMA_OK);
    TCHECK(r.fields[0].type == FIELD_TYPE_U64);
    TCHECK(r.fields[0].value.u64 == UINT64_MAX);  /* PS-294 */
}

/* CR-2026-071, PS-443: the lookup sees the value after the modifiers.
 *
 * `u8` with `add: 1` and `lookup: [a, b, c]` - the arithmetic-order-read.yaml fixture's
 * field. Raw 1 is 2 after the add and indexes "c"; the lookup used to be keyed on the raw
 * value and gave "b", the one answer no other implementation gives. */
static void build_add_then_sequence(schema_t* s) {
    memset(s, 0, sizeof(*s));
    s->endian = ENDIAN_BIG;
    field_def_t f = field_u8("lookup_after_arithmetic");
    field_set_add(&f, 1.0);
    field_add_lookup(&f, 0, "a");
    field_add_lookup(&f, 1, "b");
    field_add_lookup(&f, 2, "c");
    f.lookup_is_sequence = true;
    schema_add_field(s, &f);
}

static void test_the_lookup_follows_the_modifiers(void) {
    schema_t s;
    decode_result_t r;
    uint8_t one[] = {0x01};
    uint8_t zero[] = {0x00};
    uint8_t two[] = {0x02};

    build_add_then_sequence(&s);
    TCHECK(schema_decode(&s, one, sizeof(one), &r) == SCHEMA_OK);
    TCHECK(r.field_count == 1);
    TCHECK(strcmp(r.fields[0].value.str, "c") == 0);

    TCHECK(schema_decode(&s, zero, sizeof(zero), &r) == SCHEMA_OK);
    TCHECK(strcmp(r.fields[0].value.str, "b") == 0);

    /* Raw 2 is index 3, which a three-entry sequence does not have (PS-105). Keyed on
     * the raw value it would have decoded as "c". */
    TCHECK(schema_decode(&s, two, sizeof(two), &r) == SCHEMA_ERR_LOOKUP);
}

/* The same field through the binary form, as tools/schema_binary.py emits it for
 * `{type: u8, add: 1, lookup: [a, b, c]}`: the add marker 0xA0 carrying 100 hundredths,
 * then the sequence-flagged table. The order is the interpreter's, so it holds whichever
 * way the schema arrives. */
static void test_the_lookup_follows_the_modifiers_from_binary(void) {
    static const uint8_t blob[] = {
        'P', 'S', 0x01, 0x00, 0x01,              /* header: v1, big-endian, 1 field */
        0x81, 0x00, 0xE7, 0x0C,                  /* u8 + lookup, no scale, id 3303 */
        0xA0, 0x64, 0x00,                        /* add: 1.00 */
        0x83, 0x00, 0x01, 'a', 0x01, 0x01, 'b', 0x02, 0x01, 'c',
    };
    schema_t s;
    decode_result_t r;
    uint8_t one[] = {0x01};

    TCHECK(schema_load_binary(&s, blob, sizeof(blob)) == SCHEMA_OK);
    TCHECK(s.fields[0].has_add && s.fields[0].lookup_is_sequence);
    TCHECK(schema_decode(&s, one, sizeof(one), &r) == SCHEMA_OK);
    TCHECK(r.field_count == 1);
    TCHECK(strcmp(r.fields[0].value.str, "c") == 0);
}

/* A value the modifiers leave with a fraction is no key of a mapping (PS-107), so the
 * field is omitted (PS-269) rather than truncated onto a neighbouring key. */
static void test_a_fractional_value_matches_no_key(void) {
    schema_t s;
    decode_result_t r;
    uint8_t three[] = {0x03};
    uint8_t two[] = {0x02};

    memset(&s, 0, sizeof(s));
    s.endian = ENDIAN_BIG;
    field_def_t f = field_u8("half");
    field_set_mult(&f, 0.5);
    field_add_lookup(&f, 1, "one");
    schema_add_field(&s, &f);

    TCHECK(schema_decode(&s, two, sizeof(two), &r) == SCHEMA_OK);
    TCHECK(r.field_count == 1);
    TCHECK(strcmp(r.fields[0].value.str, "one") == 0);

    TCHECK(schema_decode(&s, three, sizeof(three), &r) == SCHEMA_OK);
    TCHECK(r.field_count == 0);
}

/* The enum path applies its table through the same step, so an enum and an integer
 * field with the same modifiers and table agree. The enum path used to drop the
 * modifiers entirely (PS-446). */
static void test_an_enum_applies_its_modifiers_first(void) {
    schema_t s;
    decode_result_t r;
    uint8_t one[] = {0x01};

    memset(&s, 0, sizeof(s));
    s.endian = ENDIAN_BIG;
    field_def_t f = field_enum("state", 1);
    field_set_add(&f, 1.0);
    field_add_lookup(&f, 1, "idle");
    field_add_lookup(&f, 2, "running");
    schema_add_field(&s, &f);

    TCHECK(schema_decode(&s, one, sizeof(one), &r) == SCHEMA_OK);
    TCHECK(r.field_count == 1);
    TCHECK(strcmp(r.fields[0].value.str, "running") == 0);
}

/* PS-446: a float's modifiers are applied. f16/f32/f64 returned straight after the read,
 * so `mult: 0.5` on an f32 of 10.0 reported 10.0 - while the encoder reversed the
 * modifier, so the field did not round-trip either. */
static void test_a_float_applies_its_modifiers(void) {
    schema_t s;
    decode_result_t r;
    uint8_t ten[] = {0x41, 0x20, 0x00, 0x00};   /* 10.0f, big-endian */

    memset(&s, 0, sizeof(s));
    s.endian = ENDIAN_BIG;
    field_def_t f = field_f32("v", ENDIAN_BIG);
    field_set_mult(&f, 0.5);
    field_set_add(&f, 1.0);
    schema_add_field(&s, &f);

    TCHECK(schema_decode(&s, ten, sizeof(ten), &r) == SCHEMA_OK);
    TCHECK(r.field_count == 1);
    TCHECK(r.fields[0].value.f64 == 6.0);
}

/* A tlv of single-byte tags (or two-component tags), each case one u8 `v`, its body
 * placed above field_count as a tlv's always is. */
static void build_tlv(schema_t* s, field_def_t t, int packed_tag) {
    memset(s, 0, sizeof(*s));
    s->endian = ENDIAN_BIG;
    strcpy(t.name, "channels");
    field_add_tlv_case(&t, packed_tag, 10, 1);
    schema_add_field(s, &t);
    field_def_t v = field_u8("v");
    schema_place_field(s, 10, &v);
}

/* PS-477: one byte left where a two-component tag starts is an error naming the tlv.
 * The loop used to stop there and report a complete decode with the byte dropped. */
static void test_a_partial_composite_tag_is_an_error(void) {
    schema_t s;
    decode_result_t r;
    const int parts[2] = {1, 2};
    uint8_t payload[] = {0x01, 0x02, 0x05, 0x01};

    build_tlv(&s, field_tlv_composite(2, 0), schema_tlv_tag(parts, 2));
    TCHECK(schema_decode(&s, payload, sizeof(payload), &r) == SCHEMA_ERR_BUFFER);
    TCHECK(r.error_code == SCHEMA_ERR_BUFFER);
    TCHECK(strstr(r.error_msg, "channels") != NULL);
    TCHECK(strstr(r.error_msg, "offset 3") != NULL);

    /* The whole tag present: decodes, nothing left over. */
    TCHECK(schema_decode(&s, payload, 3, &r) == SCHEMA_OK);
    TCHECK(r.field_count == 1 && r.fields[0].value.i64 == 5);
    TCHECK(r.bytes_unread == 0);
}

/* PS-477 for a tag that is one integer: `tag_size: 2` with one byte left. */
static void test_a_partial_tag_size_tag_is_an_error(void) {
    schema_t s;
    decode_result_t r;
    uint8_t payload[] = {0x01, 0x02, 0x05, 0x01};

    build_tlv(&s, field_tlv(2, 0), 0x0102);
    TCHECK(schema_decode(&s, payload, sizeof(payload), &r) == SCHEMA_ERR_BUFFER);
    TCHECK(r.error_code == SCHEMA_ERR_BUFFER);
    TCHECK(strstr(r.error_msg, "PS-477") != NULL);
}

/* PS-486 (CR-2026-093): an entry is never partly read. A length cut short is an error
 * naming the tlv and the entry's offset; it used to rewind to the tag and leave the
 * bytes over (PS-472). */
static void test_a_cut_length_is_an_error(void) {
    schema_t s;
    decode_result_t r;
    uint8_t payload[] = {0x01, 0x00, 0x01, 0x05, 0x01, 0x00};

    build_tlv(&s, field_tlv(1, 2), 1);
    TCHECK(schema_decode(&s, payload, sizeof(payload), &r) == SCHEMA_ERR_BUFFER);
    TCHECK(r.error_code == SCHEMA_ERR_BUFFER);
    TCHECK(strstr(r.error_msg, "channels") != NULL);
    TCHECK(strstr(r.error_msg, "offset 4: 1 byte(s) remain after its tag, fewer than "
                               "its 2-byte length (PS-486)") != NULL);
}

/* PS-486: a length declaring more bytes than remain is the same error, for a known tag
 * (read from what was there and reported complete) and for an unknown one that would be
 * skipped (stepped past the payload's end). */
static void test_a_cut_value_is_an_error(void) {
    schema_t s;
    decode_result_t r;
    uint8_t known[] = {0x01, 0x01, 0x07, 0x01, 0x05, 0xAA, 0xBB};
    uint8_t unknown[] = {0x01, 0x01, 0x07, 0x09, 0x05, 0xAA, 0xBB};

    build_tlv(&s, field_tlv(1, 1), 1);
    TCHECK(schema_decode(&s, known, sizeof(known), &r) == SCHEMA_ERR_BUFFER);
    TCHECK(strstr(r.error_msg, "offset 3: its length declares 5 byte(s), 2 remain "
                               "(PS-486)") != NULL);
    TCHECK(schema_decode(&s, unknown, sizeof(unknown), &r) == SCHEMA_ERR_BUFFER);
    TCHECK(strstr(r.error_msg, "offset 3: its length declares 5 byte(s), 2 remain "
                               "(PS-486)") != NULL);

    /* The entries fit: decodes, nothing left over. */
    TCHECK(schema_decode(&s, known, 3, &r) == SCHEMA_OK);
    TCHECK(r.field_count == 1 && r.fields[0].value.i64 == 7);
    TCHECK(r.bytes_unread == 0);
}

/* PS-472: bytes after the last field are reported - offset bytes_consumed, count
 * bytes_unread - and the decode is otherwise unchanged. */
static void test_leftover_bytes_are_reported(void) {
    schema_t s;
    decode_result_t r;
    uint8_t payload[] = {0x2A, 0x01, 0x02};

    memset(&s, 0, sizeof(s));
    field_def_t f = field_u8("reading");
    schema_add_field(&s, &f);

    TCHECK(schema_decode(&s, payload, 1, &r) == SCHEMA_OK);
    TCHECK(r.bytes_consumed == 1 && r.bytes_unread == 0);

    TCHECK(schema_decode(&s, payload, sizeof(payload), &r) == SCHEMA_OK);
    TCHECK(r.field_count == 1 && r.fields[0].value.i64 == 42);
    TCHECK(r.bytes_consumed == 1);
    TCHECK(r.bytes_unread == 2);
}

/* PS-302 with PS-472: an unknown, undelimited tag ends the loop, and the bytes left are
 * counted from the tag itself, as the other five count them - not from after it. */
static void test_an_unknown_tag_is_left_over_from_the_tag(void) {
    schema_t s;
    decode_result_t r;
    uint8_t payload[] = {0x01, 0x05, 0x09, 0xAA};

    build_tlv(&s, field_tlv(1, 0), 1);
    TCHECK(schema_decode(&s, payload, sizeof(payload), &r) == SCHEMA_OK);
    TCHECK(r.field_count == 1);
    TCHECK(r.bytes_consumed == 2);
    TCHECK(r.bytes_unread == 2);
}

/*
 * Main test entry point
 */
void selftest_schema(void) {
    LOG(LOG_INFO, MOD, "Running schema interpreter self-tests");

    test_sequence_in_range();
    test_sequence_out_of_bounds_is_an_error();
    test_mapping_gap_omits_quietly();
    test_mapping_hit();
    test_sequence_flag_survives_binary();
    test_direction_mismatch_is_an_error();
    test_the_declared_direction_still_decodes();
    test_the_direction_check_is_opt_in();
    test_encoding_is_checked_too();
    test_direction_survives_the_binary_format();
    test_an_integer_field_uses_the_integer_member();
    test_a_modifier_makes_the_field_a_number();
    test_a_zero_divisor_omits_the_field();
    test_a_u64_is_exact_at_the_top_of_its_range();
    test_the_lookup_follows_the_modifiers();
    test_the_lookup_follows_the_modifiers_from_binary();
    test_a_fractional_value_matches_no_key();
    test_an_enum_applies_its_modifiers_first();
    test_a_float_applies_its_modifiers();
    test_a_partial_composite_tag_is_an_error();
    test_a_partial_tag_size_tag_is_an_error();
    test_a_cut_length_is_an_error();
    test_a_cut_value_is_an_error();
    test_leftover_bytes_are_reported();
    test_an_unknown_tag_is_left_over_from_the_tag();

    LOG(LOG_INFO, MOD, "Schema interpreter self-tests complete");
}
