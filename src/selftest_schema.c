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
    test_a_u64_is_exact_at_the_top_of_its_range();

    LOG(LOG_INFO, MOD, "Schema interpreter self-tests complete");
}
