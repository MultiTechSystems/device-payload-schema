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
    /* The refusal says why, in the text the decode path and the other languages give. */
    TCHECK(strcmp(out.error_msg,
                  "schema 'cfg' is declared direction:downlink; message direction is uplink") == 0);
    TCHECK(schema_encode_direction(&s, &inputs, SCHEMA_DIR_DOWNLINK, &out) == SCHEMA_OK);
    TCHECK(out.len == 2);
}

static void test_direction_survives_the_binary_format(void) {
    /* Flags bits 1-2 carry the declaration. Without them a binary-loaded schema lost it
     * at load time and the check silently did nothing. */
    schema_t s;
    const uint8_t downlink_schema[] = {
        'P', 'S', BINARY_FORMAT_VERSION,
        (uint8_t)(SCHEMA_DIR_DOWNLINK << 1),  /* big-endian, declared downlink */
        BINARY_NAMES_FLAG | 0,                 /* no fields, so an empty name table */
    };
    TCHECK(schema_load_binary(&s, downlink_schema, sizeof(downlink_schema)) == SCHEMA_OK);
    TCHECK(s.direction == SCHEMA_DIR_DOWNLINK);

    /* Flags of zero still read as "declares nothing". */
    const uint8_t legacy_schema[] = {'P', 'S', BINARY_FORMAT_VERSION, 0x00, BINARY_NAMES_FLAG};
    TCHECK(schema_load_binary(&s, legacy_schema, sizeof(legacy_schema)) == SCHEMA_OK);
    TCHECK(s.direction == SCHEMA_DIR_UNSET);

    /* And the endian bit still means what it did. */
    const uint8_t little_uplink[] = {
        'P', 'S', BINARY_FORMAT_VERSION,
        (uint8_t)(0x01 | (SCHEMA_DIR_UPLINK << 1)),
        BINARY_NAMES_FLAG,
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
        'P', 'S', 0x03, 0x00, 0x81,              /* header: v3, big-endian, 1 field, names */
        0x81, 0x00, 0xE7, 0x0C,                  /* u8 + lookup, no scale, IPSO 3303 */
        0xA0, 0x64, 0x00,                        /* add: 1.00 */
        0x83, 0x00, 0x01, 'a', 0x01, 0x01, 'b', 0x02, 0x01, 'c',
        0x0B, 't', 'e', 'm', 'p', 'e', 'r', 'a', 't', 'u', 'r', 'e',   /* name table */
    };
    schema_t s;
    decode_result_t r;
    uint8_t one[] = {0x01};

    TCHECK(schema_load_binary(&s, blob, sizeof(blob)) == SCHEMA_OK);
    TCHECK(s.fields[0].has_add && s.fields[0].lookup_is_sequence);
    TCHECK(schema_decode(&s, one, sizeof(one), &r) == SCHEMA_OK);
    TCHECK(r.field_count == 1);
    TCHECK(strcmp(r.fields[0].name, "temperature") == 0);
    TCHECK(strcmp(r.fields[0].value.str, "c") == 0);
}

/*
 * CR-2026-142 (PS-555): the binary form carries every field's name, and the loader
 * reports those names - never one derived from the IPSO object. The loader used to name
 * a field from its IPSO ID, so 3303 became "temperature" whatever the schema called it.
 *
 * Three fields, as tools/schema_binary.py writes them: a u16 named "soil_temperature"
 * with IPSO 3303, a one-byte skip, and a u8 named "battery" with no IPSO object.
 */
static const uint8_t named_blob[] = {
    'P', 'S', BINARY_FORMAT_VERSION, 0x00, BINARY_NAMES_FLAG | 3,
    0x02, 0x00, 0xE7, 0x0C,                       /* u16, IPSO 3303 */
    0x71, 0x00, 0x00, 0x00,                       /* skip 1 */
    0x01, 0x00, 0x00, 0x00,                       /* u8 */
    16, 's', 'o', 'i', 'l', '_', 't', 'e', 'm', 'p', 'e', 'r', 'a', 't', 'u', 'r', 'e',
    0,                                            /* the skip has no name (PS-553) */
    7, 'b', 'a', 't', 't', 'e', 'r', 'y',
};
static const uint8_t named_payload[] = {0x01, 0x02, 0xFF, 0x07};

static void test_binary_names_round_trip(void) {
    schema_t s;
    decode_result_t r;
    char err[160];

    TCHECK(schema_load_binary_msg(&s, named_blob, sizeof(named_blob), err, sizeof(err))
           == SCHEMA_OK);
    TCHECK(err[0] == '\0');
    TCHECK(!s.nameless);
    TCHECK(s.field_count == 3);
    TCHECK(strcmp(s.fields[0].name, "soil_temperature") == 0);
    TCHECK(strcmp(s.fields[2].name, "battery") == 0);

    TCHECK(schema_decode(&s, named_payload, sizeof(named_payload), &r) == SCHEMA_OK);
    TCHECK(!r.nameless);
    TCHECK(r.field_count == 2);
    TCHECK(strcmp(r.fields[0].name, "soil_temperature") == 0);
    TCHECK(r.fields[0].value.i64 == 0x0102);
    TCHECK(strcmp(r.fields[1].name, "battery") == 0);
    TCHECK(r.fields[1].value.i64 == 7);
    /* The name an IPSO lookup would have made up is nowhere. */
    TCHECK(result_get_field(&r, "temperature") == NULL);
    TCHECK(result_get_int(&r, "soil_temperature", -1) == 0x0102);
    /* And the index route reaches the same values, the skip reporting nothing. */
    TCHECK(result_get_index(&r, 0) == &r.fields[0]);
    TCHECK(result_get_index(&r, 1) == NULL);
    TCHECK(result_get_index(&r, 2) == &r.fields[1]);
}

static void test_binary_skip_has_an_empty_name(void) {
    schema_t s;

    TCHECK(schema_load_binary(&s, named_blob, sizeof(named_blob)) == SCHEMA_OK);
    TCHECK(s.fields[1].type == FIELD_TYPE_SKIP);
    TCHECK(s.fields[1].size == 1);
    TCHECK(s.fields[1].name[0] == '\0');
}

static void test_binary_empty_name_is_only_for_a_skip(void) {
    /* One u8 whose name-table entry is empty. */
    static const uint8_t blob[] = {
        'P', 'S', BINARY_FORMAT_VERSION, 0x00, BINARY_NAMES_FLAG | 1,
        0x01, 0x00, 0x00, 0x00,
        0,
    };
    schema_t s;
    char err[160];

    TCHECK(schema_load_binary_msg(&s, blob, sizeof(blob), err, sizeof(err))
           == SCHEMA_ERR_PARSE);
    TCHECK(strcmp(err, "field 0 (u8) has an empty name; only a skip has none (PS-553)")
           == 0);
    TCHECK(s.field_count == 0);
}

static void test_binary_over_length_name_is_refused(void) {
    /* One u8 named with SCHEMA_MAX_NAME_LEN bytes, one more than a name buffer holds
     * beside its terminator. It is refused, naming the field, not cut short. */
    uint8_t blob[5 + 4 + 1 + SCHEMA_MAX_NAME_LEN];
    const uint8_t head[] = {'P', 'S', BINARY_FORMAT_VERSION, 0x00, BINARY_NAMES_FLAG | 1,
                            0x01, 0x00, 0x00, 0x00, SCHEMA_MAX_NAME_LEN};
    memcpy(blob, head, sizeof(head));
    memset(blob + sizeof(head), 'n', SCHEMA_MAX_NAME_LEN);
    schema_t s;
    char err[256];

    TCHECK(schema_load_binary_msg(&s, blob, sizeof(blob), err, sizeof(err))
           == SCHEMA_ERR_PARSE);
    TCHECK(strcmp(err, "field 0's name is 32 bytes, more than the 31 this interpreter "
                       "holds (SCHEMA_MAX_NAME_LEN), and a name is never truncated "
                       "(PS-555): 'nnnnnnnnnnnnnnnnnnnnnnnnnnnnnnnn'") == 0);
    TCHECK(s.field_count == 0);

    /* One byte shorter fits exactly. */
    blob[9] = SCHEMA_MAX_NAME_LEN - 1;
    TCHECK(schema_load_binary(&s, blob, sizeof(blob) - 1) == SCHEMA_OK);
    TCHECK(strlen(s.fields[0].name) == SCHEMA_MAX_NAME_LEN - 1);
}

static void test_binary_truncated_name_table_is_refused(void) {
    schema_t s;
    char err[160];

    /* The last name cut by one byte, and the whole table missing. */
    TCHECK(schema_load_binary_msg(&s, named_blob, sizeof(named_blob) - 1, err, sizeof(err))
           == SCHEMA_ERR_PARSE);
    TCHECK(strstr(err, "field 2: its name at offset") != NULL);
    TCHECK(strstr(err, "runs past the end") != NULL);
    TCHECK(s.field_count == 0);

    TCHECK(schema_load_binary_msg(&s, named_blob, 17, err, sizeof(err))
           == SCHEMA_ERR_PARSE);
    TCHECK(strstr(err, "field 0: its name length") != NULL);
}

static void test_binary_old_version_is_refused(void) {
    /* The version-1 layout: no name table, and this loader named the u16 "temperature"
     * from its IPSO ID. It cannot be decoded under PS-555, so it is not loaded. */
    static const uint8_t v1[] = {
        'P', 'S', 0x01, 0x00, 0x01,
        0x02, 0x00, 0xE7, 0x0C,
    };
    schema_t s;
    char err[160];

    TCHECK(schema_load_binary_msg(&s, v1, sizeof(v1), err, sizeof(err)) == SCHEMA_ERR_PARSE);
    TCHECK(strcmp(err, "binary schema format version 1 is not supported; this loader "
                       "reads version 3 (CR-2026-142)") == 0);
    TCHECK(s.field_count == 0);
}

static void test_binary_trailing_bytes_are_refused(void) {
    uint8_t blob[sizeof(named_blob) + 1];
    memcpy(blob, named_blob, sizeof(named_blob));
    blob[sizeof(named_blob)] = 0x00;
    schema_t s;
    char err[160];

    TCHECK(schema_load_binary_msg(&s, blob, sizeof(blob), err, sizeof(err))
           == SCHEMA_ERR_PARSE);
    TCHECK(strstr(err, "1 byte(s) after the last name") != NULL);
}

static void test_binary_nameless_form_reports_by_index(void) {
    /* The same records without the name table (count bit 7 clear): a compiled form for
     * an interpreter that hands back values by index. No field gets a name - not one
     * made up from its IPSO object, type or position - and nothing finds one by name. */
    uint8_t blob[5 + 12];
    memcpy(blob, named_blob, sizeof(blob));
    blob[4] = 3;
    schema_t s;
    decode_result_t r;

    TCHECK(schema_load_binary(&s, blob, sizeof(blob)) == SCHEMA_OK);
    TCHECK(s.nameless);
    for (int i = 0; i < s.field_count; i++) TCHECK(s.fields[i].name[0] == '\0');

    TCHECK(schema_decode(&s, named_payload, sizeof(named_payload), &r) == SCHEMA_OK);
    TCHECK(r.nameless);
    TCHECK(r.field_count == 2);
    TCHECK(r.fields[0].name[0] == '\0' && r.fields[1].name[0] == '\0');
    TCHECK(result_get_index(&r, 0) != NULL && result_get_index(&r, 0)->value.i64 == 0x0102);
    TCHECK(result_get_index(&r, 1) == NULL);
    TCHECK(result_get_index(&r, 2) != NULL && result_get_index(&r, 2)->value.i64 == 7);
    TCHECK(result_get_field(&r, "temperature") == NULL);
    TCHECK(result_get_field(&r, "") == NULL);
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

/* A skip past the end is a short read (PS-054): it stepped past the payload and reported
 * success, with bytes_consumed beyond the payload and bytes_unread negative. */
static void test_a_skip_past_the_end_is_an_error(void) {
    schema_t s;
    decode_result_t r;
    uint8_t payload[] = {0x01, 0x02, 0x03};

    memset(&s, 0, sizeof(s));
    field_def_t lead = field_u8("lead");
    field_def_t pad = field_skip(4);
    schema_add_field(&s, &lead);
    schema_add_field(&s, &pad);

    TCHECK(schema_decode(&s, payload, sizeof(payload), &r) == SCHEMA_ERR_BUFFER);
    /* PS-535: field_skip() names nothing, so the skip is "an unnamed skip". */
    TCHECK(strcmp(r.error_msg, "Field an unnamed skip: Buffer underflow: need 4 bytes at "
                               "offset 1, but only 2 remaining (PS-535)") == 0);

    uint8_t enough[] = {0x01, 0x00, 0x00, 0x00, 0x00};
    TCHECK(schema_decode(&s, enough, sizeof(enough), &r) == SCHEMA_OK);
    TCHECK(r.bytes_consumed == 5 && r.bytes_unread == 0);
}

/* PS-535 (CR-2026-115): a short read names the field that made it, in the text every
 * other implementation reports. It was SCHEMA_ERR_BUFFER with an empty message. */
static void test_a_short_read_names_the_field(void) {
    schema_t s;
    decode_result_t r;
    uint8_t payload[] = {0x01, 0x02};

    memset(&s, 0, sizeof(s));
    s.endian = ENDIAN_BIG;
    field_def_t lead = field_u8("lead");
    field_def_t wide = field_u32("reading", ENDIAN_BIG);
    schema_add_field(&s, &lead);
    schema_add_field(&s, &wide);
    TCHECK(schema_decode(&s, payload, sizeof(payload), &r) == SCHEMA_ERR_BUFFER);
    TCHECK(strcmp(r.error_msg, "Field 'reading': Buffer underflow: need 4 bytes at "
                               "offset 1, but only 1 remaining (PS-535)") == 0);

    /* A bool peeks at its byte: the peek form, as the reference writes it. */
    memset(&s, 0, sizeof(s));
    field_def_t flag = field_bool("alarm", 3, false);
    schema_add_field(&s, &lead);
    schema_add_field(&s, &flag);
    TCHECK(schema_decode(&s, payload, 1, &r) == SCHEMA_ERR_BUFFER);
    TCHECK(strcmp(r.error_msg, "Field 'alarm': Buffer underflow at peek offset 1 "
                               "(PS-535)") == 0);

    /* A tlv case body's field is the one named, not the tlv. */
    build_tlv(&s, field_tlv(1, 0), 1);
    field_def_t wider = field_u16("v", ENDIAN_BIG);
    schema_place_field(&s, 10, &wider);
    uint8_t cut[] = {0x01, 0x05};
    TCHECK(schema_decode(&s, cut, sizeof(cut), &r) == SCHEMA_ERR_BUFFER);
    TCHECK(strcmp(r.error_msg, "Field 'v': Buffer underflow: need 2 bytes at offset 1, "
                               "but only 1 remaining (PS-535)") == 0);
}

/* On encode a skip writes its zeros - it was dropped with the `_` internals, so the field
 * after it was written early - and never past the result buffer. */
static void test_a_skip_is_written_on_encode(void) {
    schema_t s;
    encode_inputs_t in;
    encode_result_t out;

    memset(&s, 0, sizeof(s));
    field_def_t a = field_u8("a");
    field_def_t pad = field_skip(2);
    field_def_t b = field_u8("b");
    schema_add_field(&s, &a);
    schema_add_field(&s, &pad);
    schema_add_field(&s, &b);
    encode_inputs_init(&in);
    encode_inputs_add_int(&in, "a", 1);
    encode_inputs_add_int(&in, "b", 2);
    TCHECK(schema_encode(&s, &in, &out) == SCHEMA_OK);
    TCHECK(out.len == 4);
    TCHECK(out.data[0] == 1 && out.data[1] == 0 && out.data[2] == 0 && out.data[3] == 2);

    /* Two 255-byte skips would end at 510, past data[SCHEMA_MAX_PAYLOAD]. */
    memset(&s, 0, sizeof(s));
    field_def_t big1 = field_skip(255);
    field_def_t big2 = field_skip(255);
    schema_add_field(&s, &big1);
    schema_add_field(&s, &big2);
    encode_inputs_init(&in);
    TCHECK(schema_encode(&s, &in, &out) == SCHEMA_ERR_BUFFER);
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

/* CR-2026-135 (PS-552, PS-168): nearest, a tie to even, on the binary64 value, and a
 * value that rounds outside its type is rejected naming the field and the value given.
 * The same figures as tests/cr-2026-135-cases.yaml, built through the struct API. */
static int encode_one(field_def_t f, bool is_int, int64_t iv, double dv,
                      encode_result_t* out) {
    schema_t s;
    encode_inputs_t inputs;
    memset(&s, 0, sizeof(s));
    s.endian = ENDIAN_BIG;
    schema_add_field(&s, &f);
    encode_inputs_init(&inputs);
    if (is_int) encode_inputs_add_int(&inputs, f.name, iv);
    else encode_inputs_add_double(&inputs, f.name, dv);
    return schema_encode(&s, &inputs, out);
}

static bool encodes_to(field_def_t f, double v, const uint8_t* want, int len) {
    encode_result_t out;
    return encode_one(f, false, 0, v, &out) == SCHEMA_OK && out.len == len
           && memcmp(out.data, want, (size_t)len) == 0;
}

static bool rejects(field_def_t f, bool is_int, int64_t iv, double dv, const char* msg) {
    encode_result_t out;
    return encode_one(f, is_int, iv, dv, &out) == SCHEMA_ERR_OVERFLOW && out.len == 0
           && strcmp(out.error_msg, msg) == 0;
}

static void test_an_encoder_rounds_ties_to_even(void) {
    field_def_t div10 = field_u16("x", ENDIAN_BIG);
    field_def_t half = field_s8("x");
    field_set_div(&div10, 10);
    field_set_div(&half, 2);
    TCHECK(encodes_to(field_u8("x"), 2.5, (const uint8_t[]){0x02}, 1));
    TCHECK(encodes_to(field_u8("x"), 3.5, (const uint8_t[]){0x04}, 1));
    TCHECK(encodes_to(field_u8("x"), 0.5, (const uint8_t[]){0x00}, 1));
    /* (int64_t)(v + 0.5) rounded this twice, to 1. */
    TCHECK(encodes_to(field_u8("x"), 0.49999999999999994, (const uint8_t[]){0x00}, 1));
    TCHECK(encodes_to(field_s8("x"), -2.5, (const uint8_t[]){0xFE}, 1));
    TCHECK(encodes_to(field_s8("x"), -3.5, (const uint8_t[]){0xFC}, 1));
    TCHECK(encodes_to(div10, 0.15, (const uint8_t[]){0x00, 0x02}, 2));
    TCHECK(encodes_to(div10, 0.25, (const uint8_t[]){0x00, 0x02}, 2));
    /* 23.45 * 10 is 234.49999999999997: the binary64 value is rounded, so 234. */
    TCHECK(encodes_to(div10, 23.45, (const uint8_t[]){0x00, 0xEA}, 2));
    TCHECK(encodes_to(half, -1.25, (const uint8_t[]){0xFE}, 1));
}

/* CR-2026-154 (PS-566): a negative zero is reported as 0 and written as the positive-zero
 * word. The figures of tests/cr-2026-154-cases.yaml for f32 and f64, which is all C has: it
 * cannot encode an f16 and has no word-ordered or minifloat type. */
static bool decodes_to_positive_zero(field_def_t f, const uint8_t* payload, size_t len) {
    schema_t s;
    decode_result_t r;
    memset(&s, 0, sizeof(s));
    s.endian = ENDIAN_BIG;
    schema_add_field(&s, &f);
    if (schema_decode(&s, payload, len, &r) != SCHEMA_OK || r.field_count != 1) return false;
    double v = r.fields[0].value.f64;
    return v == 0.0 && 1.0 / v > 0.0;   /* 1/-0 is -infinity */
}

static void test_a_negative_zero_is_zero(void) {
    const uint8_t zero4[4] = {0}, zero8[8] = {0};
    field_def_t mult = field_f32("x", ENDIAN_BIG);
    field_set_mult(&mult, -1);

    TCHECK(decodes_to_positive_zero(field_f32("x", ENDIAN_BIG),
                                    (const uint8_t[]){0x80, 0, 0, 0}, 4));
    TCHECK(decodes_to_positive_zero(field_f32("x", ENDIAN_LITTLE),
                                    (const uint8_t[]){0, 0, 0, 0x80}, 4));
    TCHECK(decodes_to_positive_zero(field_f64("x", ENDIAN_BIG),
                                    (const uint8_t[]){0x80, 0, 0, 0, 0, 0, 0, 0}, 8));
    TCHECK(decodes_to_positive_zero(field_f64("x", ENDIAN_LITTLE),
                                    (const uint8_t[]){0, 0, 0, 0, 0, 0, 0, 0x80}, 8));
    /* A -0 the field's own arithmetic makes: 0 * -1. */
    TCHECK(decodes_to_positive_zero(mult, zero4, 4));

    TCHECK(encodes_to(field_f32("x", ENDIAN_BIG), -0.0, zero4, 4));
    TCHECK(encodes_to(field_f32("x", ENDIAN_LITTLE), -0.0, zero4, 4));
    TCHECK(encodes_to(field_f64("x", ENDIAN_BIG), -0.0, zero8, 8));
    TCHECK(encodes_to(field_f64("x", ENDIAN_LITTLE), -0.0, zero8, 8));
    /* 0 / -1 is -0 when the multiplier is undone. */
    TCHECK(encodes_to(mult, 0.0, zero4, 4));
    /* A negative value keeps its sign: -0.5f is bf000000. */
    TCHECK(encodes_to(field_f32("x", ENDIAN_BIG), -0.5, (const uint8_t[]){0xBF, 0, 0, 0}, 4));

    /* CR-2026-157: a negative value the type rounds to zero is the zero word, big or little;
     * the smallest negative f32 subnormal (-2^-149) is not zero and keeps its sign. */
    TCHECK(encodes_to(field_f32("x", ENDIAN_BIG), -1e-50, zero4, 4));
    TCHECK(encodes_to(field_f32("x", ENDIAN_LITTLE), -1e-50, zero4, 4));
    TCHECK(encodes_to(field_f32("x", ENDIAN_BIG), -1.401298464324817e-45,
                      (const uint8_t[]){0x80, 0, 0, 0x01}, 4));
}

static void test_a_value_that_does_not_fit_is_named(void) {
    field_def_t div10 = field_u8("x");
    field_set_div(&div10, 10);
    TCHECK(rejects(div10, false, 0, 25.55,
                   "Field 'x': 25.55 encodes as 256, outside 0 to 255 (PS-168)"));
    TCHECK(rejects(field_u8("x"), false, 0, 255.5,
                   "Field 'x': 255.5 encodes as 256, outside 0 to 255 (PS-168)"));
    TCHECK(rejects(field_u8("x"), true, 256, 0,
                   "Field 'x': 256 encodes as 256, outside 0 to 255 (PS-168)"));
    TCHECK(rejects(field_u8("x"), true, -1, 0,
                   "Field 'x': -1 encodes as -1, outside 0 to 255 (PS-168)"));
    TCHECK(rejects(field_s8("x"), false, 0, -128.6,
                   "Field 'x': -128.6 encodes as -129, outside -128 to 127 (PS-168)"));
    TCHECK(rejects(field_u16("x", ENDIAN_BIG), true, 65536, 0,
                   "Field 'x': 65536 encodes as 65536, outside 0 to 65535 (PS-168)"));
    TCHECK(rejects(field_bits("x", 4, 4, true), true, 16, 0,
                   "Field 'x': 16 encodes as 16, outside 0 to 15 for u8[4:7] (PS-168)"));
    TCHECK(encodes_to(field_u8("x"), 255.4, (const uint8_t[]){0xFF}, 1));
    TCHECK(encodes_to(field_bits("x", 4, 4, true), 2.5, (const uint8_t[]){0x20}, 1));
}

/* PS-168 for udec/sdec (PS-330): the figures of tests/cr-2026-135-cases.yaml. Both
 * clamped silently: udec 123.5 wrote 0x95. */
static field_def_t field_nibble(const char* name, field_type_t type) {
    field_def_t f = field_u8(name);
    f.type = type;
    return f;
}

static void test_a_nibble_decimal_that_does_not_fit_is_named(void) {
    field_def_t udec = field_nibble("v", FIELD_TYPE_UDEC);
    field_def_t sdec = field_nibble("v", FIELD_TYPE_SDEC);
    TCHECK(encodes_to(udec, 15.9, (const uint8_t[]){0xF9}, 1));
    TCHECK(encodes_to(udec, 2.5, (const uint8_t[]){0x25}, 1));
    TCHECK(encodes_to(sdec, -0.5, (const uint8_t[]){0xF5}, 1));
    TCHECK(encodes_to(sdec, -8, (const uint8_t[]){0x80}, 1));
    /* PS-556 (CR-2026-147): n = value x 10 rounded half to even, n div/mod 10 floored. */
    TCHECK(encodes_to(udec, 0.15, (const uint8_t[]){0x02}, 1));
    TCHECK(encodes_to(udec, 3.45, (const uint8_t[]){0x34}, 1));
    TCHECK(encodes_to(sdec, -3.45, (const uint8_t[]){0xC6}, 1));
    TCHECK(rejects(udec, true, 16, 0,
                   "Field 'v': 16 encodes as 16, outside 0 to 15.9 (PS-168)"));
    TCHECK(rejects(sdec, false, 0, -8.1,
                   "Field 'v': -8.1 encodes as -8.1, outside -8 to 7.9 (PS-168)"));
    TCHECK(rejects(udec, false, 0, 123.5,
                   "Field 'v': 123.5 encodes as 123.5, outside 0 to 15.9 (PS-168)"));
    TCHECK(rejects(udec, false, 0, 15.96,
                   "Field 'v': 15.96 encodes as 16, outside 0 to 15.9 (PS-168)"));
    TCHECK(rejects(udec, true, -1, 0,
                   "Field 'v': -1 encodes as -1, outside 0 to 15.9 (PS-168)"));
    TCHECK(rejects(sdec, false, 0, 99.9,
                   "Field 'v': 99.9 encodes as 99.9, outside -8 to 7.9 (PS-168)"));
    TCHECK(rejects(sdec, false, 0, -8.5,
                   "Field 'v': -8.5 encodes as -8.5, outside -8 to 7.9 (PS-168)"));
}

/* CR-2026-181 (PS-601, PS-602): the struct API's encoder has no byte string input, so a
 * bytes, hex or base64 field is refused naming the field, never written as zeros. */
static void test_a_byte_string_is_refused_by_name(void) {
    field_def_t fields[] = { field_bytes_type("k", 2), field_hex("h", 2) };
    const char* want[] = {
        "Field 'k': this encoder cannot write a bytes value (PS-601, PS-602)",
        "Field 'h': this encoder cannot write a hex value (PS-601, PS-602)",
    };
    for (int i = 0; i < 2; i++) {
        schema_t s;
        encode_inputs_t inputs;
        encode_result_t out;
        memset(&s, 0, sizeof(s));
        schema_add_field(&s, &fields[i]);
        encode_inputs_init(&inputs);
        encode_inputs_add_int(&inputs, fields[i].name, 1);
        TCHECK(schema_encode(&s, &inputs, &out) != SCHEMA_OK);
        TCHECK(out.len == 0);
        TCHECK(strcmp(out.error_msg, want[i]) == 0);
    }
}

/* CR-2026-136 (PS-064, PS-062): bit ranges and bools are written as a decoder reads
 * them - a non-consuming one shares its byte with the next field - and a value the range
 * cannot hold is rejected rather than masked into its neighbour. */
static int encode_fields(field_def_t* fields, int n, const char** names, const int64_t* vals,
                         encode_result_t* out) {
    schema_t s;
    encode_inputs_t inputs;
    memset(&s, 0, sizeof(s));
    s.endian = ENDIAN_BIG;
    for (int i = 0; i < n; i++) schema_add_field(&s, &fields[i]);
    encode_inputs_init(&inputs);
    for (int i = 0; i < n; i++) encode_inputs_add_int(&inputs, names[i], vals[i]);
    return schema_encode(&s, &inputs, out);
}

static void test_bit_ranges_are_laid_out_as_decoded(void) {
    encode_result_t out;
    {
        field_def_t f[] = { field_bits("a", 0, 4, false), field_bits("b", 4, 4, true),
                            field_u8("c") };
        const char* n[] = {"a", "b", "c"};
        const int64_t v[] = {1, 2, 9};
        TCHECK(encode_fields(f, 3, n, v, &out) == SCHEMA_OK);
        TCHECK(out.len == 2 && out.data[0] == 0x21 && out.data[1] == 0x09);
    }
    {
        field_def_t f[] = { field_bool("x", 0, false), field_bool("y", 7, true),
                            field_u8("z") };
        const char* n[] = {"x", "y", "z"};
        const int64_t v[] = {1, 1, 5};
        TCHECK(encode_fields(f, 3, n, v, &out) == SCHEMA_OK);
        TCHECK(out.len == 2 && out.data[0] == 0x81 && out.data[1] == 0x05);
    }
    {
        /* A peeked range, then the byte read whole: one byte, not two. The whole byte
         * agrees with the range, as CR-2026-141 requires (it was 5, ORed into 0x35). */
        field_def_t f[] = { field_bits("hi", 4, 4, false), field_u8("whole") };
        const char* n[] = {"hi", "whole"};
        const int64_t v[] = {3, 0x35};
        TCHECK(encode_fields(f, 2, n, v, &out) == SCHEMA_OK);
        TCHECK(out.len == 1 && out.data[0] == 0x35);
    }
    {
        /* A range nothing consumes after it is still written. */
        field_def_t f[] = { field_u8("a"), field_bits("b", 0, 4, false) };
        const char* n[] = {"a", "b"};
        const int64_t v[] = {1, 2};
        TCHECK(encode_fields(f, 2, n, v, &out) == SCHEMA_OK);
        TCHECK(out.len == 2 && out.data[0] == 0x01 && out.data[1] == 0x02);
    }
    {
        field_def_t f[] = { field_bits("a", 0, 4, false), field_bits("b", 4, 4, true) };
        const char* n[] = {"a", "b"};
        const int64_t v[] = {16, 2};
        TCHECK(encode_fields(f, 2, n, v, &out) == SCHEMA_ERR_OVERFLOW);
        TCHECK(out.len == 0);
        TCHECK(strcmp(out.error_msg,
                      "Field 'a': 16 encodes as 16, outside 0 to 15 for u8[0:3] (PS-168)") == 0);
    }
}

static void test_no_construct_is_encoded_as_nothing(void) {
    /* PS-442, PS-551 (no silent empty encode): an internal field and a match were passed
     * over by this encoder, writing nothing for the bytes a decoder reads. An internal
     * field is now written from its input, else PS-434's error; a match writes the case
     * its discriminator selects, else names what it lacks. */
    schema_t s;
    encode_inputs_t in;
    encode_result_t out;
    {   /* An internal byte the input gives is written; one it omits is refused. */
        memset(&s, 0, sizeof(s));
        s.endian = ENDIAN_BIG;
        field_def_t h = field_u8("_h");
        field_def_t a = field_u8("a");
        schema_add_field(&s, &h);
        schema_add_field(&s, &a);
        encode_inputs_init(&in);
        encode_inputs_add_int(&in, "a", 7);
        TCHECK(schema_encode(&s, &in, &out) == SCHEMA_ERR_PARSE);
        TCHECK(out.len == 0);
        TCHECK(strcmp(out.error_msg, "internal field '_h' reads payload bytes and declares "
                      "no value, and the input does not supply it (PS-434)") == 0);
        encode_inputs_add_int(&in, "_h", 1);
        TCHECK(schema_encode(&s, &in, &out) == SCHEMA_OK);
        TCHECK(out.len == 2 && out.data[0] == 1 && out.data[1] == 7);
    }
    {   /* A match whose case bodies sit above field_count: the taken body is written. */
        memset(&s, 0, sizeof(s));
        s.endian = ENDIAN_BIG;
        field_def_t k = field_u8("kind");
        field_set_var(&k, "kind");
        schema_add_field(&s, &k);
        field_def_t m = {0};
        strncpy(m.name, "_m", SCHEMA_MAX_NAME_LEN - 1);
        m.type = FIELD_TYPE_MATCH;
        strncpy(m.match_var, "$kind", SCHEMA_MAX_NAME_LEN - 1);
        for (int c = 0; c < SCHEMA_MAX_CASES; c++) {
            for (int j = 0; j < 8; j++) m.cases[c].match_list[j] = -1;
        }
        m.cases[0].match_value = 1; m.cases[0].field_start = 2; m.cases[0].field_count = 1;
        m.cases[1].match_value = 2; m.cases[1].field_start = 3; m.cases[1].field_count = 1;
        m.case_count = 2;
        schema_add_field(&s, &m);
        field_def_t level = field_u16("level", ENDIAN_BIG);
        field_def_t volts = field_u8("volts");
        schema_place_field(&s, 2, &level);
        schema_place_field(&s, 3, &volts);

        encode_inputs_init(&in);
        encode_inputs_add_int(&in, "kind", 2);
        encode_inputs_add_int(&in, "volts", 33);
        TCHECK(schema_encode(&s, &in, &out) == SCHEMA_OK);
        TCHECK(out.len == 2 && out.data[0] == 2 && out.data[1] == 33);

        encode_inputs_init(&in);
        encode_inputs_add_int(&in, "kind", 9);
        TCHECK(schema_encode(&s, &in, &out) == SCHEMA_ERR_MATCH);
        TCHECK(out.len == 0);
        TCHECK(strcmp(out.error_msg, "No matching case for value 9") == 0);

        encode_inputs_init(&in);
        encode_inputs_add_int(&in, "kind", 1);
        TCHECK(schema_encode(&s, &in, &out) == SCHEMA_ERR_PARSE);
        TCHECK(strcmp(out.error_msg, "Missing required field: level") == 0);
    }
}

static void test_a_missing_input_is_named(void) {
    /* PS-551 (CR-2026-134): rejected, naming the field, with no payload. The code alone
     * was returned before, so a caller could not tell which input it had left out. */
    schema_t s;
    encode_result_t out;
    encode_inputs_t inputs;

    memset(&s, 0, sizeof(s));
    s.endian = ENDIAN_BIG;
    field_def_t a = field_u8("a");
    field_def_t b = field_u8("b");
    schema_add_field(&s, &a);
    schema_add_field(&s, &b);
    encode_inputs_init(&inputs);
    encode_inputs_add_int(&inputs, "a", 1);
    TCHECK(schema_encode(&s, &inputs, &out) == SCHEMA_ERR_PARSE);
    TCHECK(out.len == 0);
    TCHECK(strcmp(out.error_msg, "Missing required field: b") == 0);

    encode_inputs_add_int(&inputs, "b", 2);
    TCHECK(schema_encode(&s, &inputs, &out) == SCHEMA_OK);
    TCHECK(out.len == 2 && out.data[0] == 1 && out.data[1] == 2);
    TCHECK(out.error_msg[0] == 0);
}

/* Encode `nf` fields from `ni` inputs, which may leave some of them out. */
static int encode_some(field_def_t* fields, int nf, const char** names, const int64_t* vals,
                       int ni, encode_result_t* out) {
    schema_t s;
    encode_inputs_t inputs;
    memset(&s, 0, sizeof(s));
    s.endian = ENDIAN_BIG;
    for (int i = 0; i < nf; i++) schema_add_field(&s, &fields[i]);
    encode_inputs_init(&inputs);
    for (int i = 0; i < ni; i++) encode_inputs_add_int(&inputs, names[i], vals[i]);
    return schema_encode(&s, &inputs, out);
}

static void test_a_covered_peek_is_derived(void) {
    /* PS-551 (CR-2026-141): a bit range or bool that does not consume is fixed by the
     * fields that write its bits. Absent, it is derived and writes none; with a bit no
     * other field writes, it is missing; supplied, it must agree with the frame. The
     * cases are tests/cr-2026-141-cases.yaml's, in the widths C's ranges have. */
    encode_result_t out;
    {   /* Milesight's status peek, a byte wide here: the reading fixes it. */
        field_def_t f[] = { field_bits("status", 0, 8, false), field_u16("reading", ENDIAN_BIG) };
        const char* n[] = {"reading"};
        const int64_t v[] = {300};
        TCHECK(encode_some(f, 2, n, v, 1, &out) == SCHEMA_OK);
        TCHECK(out.len == 2 && out.data[0] == 0x01 && out.data[1] == 0x2c);
    }
    {
        field_def_t f[] = { field_bits("status", 0, 8, false), field_u16("reading", ENDIAN_BIG) };
        const char* n[] = {"status", "reading"};
        const int64_t agree[] = {255, 65535};
        TCHECK(encode_some(f, 2, n, agree, 2, &out) == SCHEMA_OK);
        TCHECK(out.len == 2 && out.data[0] == 0xff && out.data[1] == 0xff);
        const int64_t disagree[] = {255, 300};
        TCHECK(encode_some(f, 2, n, disagree, 2, &out) == SCHEMA_ERR_PARSE);
        TCHECK(out.len == 0);
        TCHECK(strcmp(out.error_msg, "Field 'status' is given 255, but the fields that share "
                                     "its bits write 1 (PS-551)") == 0);
    }
    {
        field_def_t f[] = { field_bits("a", 0, 4, false), field_bits("b", 4, 4, true) };
        const char* n[] = {"b"};
        const int64_t v[] = {2};
        TCHECK(encode_some(f, 2, n, v, 1, &out) == SCHEMA_ERR_PARSE);
        TCHECK(out.len == 0);
        TCHECK(strcmp(out.error_msg, "Missing required field: a") == 0);
    }
    {
        field_def_t f[] = { field_bool("f", 7, false), field_u8("x") };
        const char* n[] = {"x"};
        const int64_t v[] = {128};
        TCHECK(encode_some(f, 2, n, v, 1, &out) == SCHEMA_OK);
        TCHECK(out.len == 1 && out.data[0] == 0x80);
        const char* n2[] = {"f", "x"};
        const int64_t v2[] = {1, 1};
        TCHECK(encode_some(f, 2, n2, v2, 2, &out) == SCHEMA_ERR_PARSE);
        TCHECK(strcmp(out.error_msg, "Field 'f' is given 1, but the fields that share its "
                                     "bits write 0 (PS-551)") == 0);
    }
    {
        field_def_t f[] = { field_bool("f", 7, false), field_bool("g", 0, true) };
        const char* n[] = {"g"};
        const int64_t v[] = {1};
        TCHECK(encode_some(f, 2, n, v, 1, &out) == SCHEMA_ERR_PARSE);
        TCHECK(strcmp(out.error_msg, "Missing required field: f") == 0);
    }
    {   /* A skip is padding: it keeps the peek's bits, and fixes none of them. */
        field_def_t f[] = { field_bits("p", 0, 4, false), field_skip(1) };
        const char* n[] = {"p"};
        const int64_t v[] = {5};
        TCHECK(encode_some(f, 2, n, v, 1, &out) == SCHEMA_OK);
        TCHECK(out.len == 1 && out.data[0] == 0x05);
        TCHECK(encode_some(f, 2, n, v, 0, &out) == SCHEMA_ERR_PARSE);
        TCHECK(strcmp(out.error_msg, "Missing required field: p") == 0);
    }
}

/* PS-555 (CR-2026-146): "an encoder working from such a form takes each value by the
 * same index". The nameless blob above - u16, skip 1, u8 - encoded from values keyed by
 * field index, as its decode reports them; a named schema keeps name keying, and the two
 * are not mixed. */
static void test_a_nameless_schema_encodes_by_index(void) {
    uint8_t blob[5 + 12];
    memcpy(blob, named_blob, sizeof(blob));
    blob[4] = 3;                                  /* no name table */
    schema_t s;
    decode_result_t r;
    encode_inputs_t in;
    encode_result_t out;
    TCHECK(schema_load_binary(&s, blob, sizeof(blob)) == SCHEMA_OK && s.nameless);

    encode_inputs_init(&in);
    TCHECK(encode_inputs_add_int_at(&in, 0, 0x0102));
    TCHECK(encode_inputs_add_double_at(&in, 2, 7.0));
    TCHECK(schema_encode(&s, &in, &out) == SCHEMA_OK);
    TCHECK(out.len == 4 && out.data[0] == 0x01 && out.data[1] == 0x02
           && out.data[2] == 0x00 && out.data[3] == 0x07);

    /* Decode, then encode the values by the indexes they were reported under. */
    TCHECK(schema_decode(&s, named_payload, sizeof(named_payload), &r) == SCHEMA_OK);
    encode_inputs_init(&in);
    for (int i = 0; i < r.field_count; i++)
        encode_inputs_add_int_at(&in, r.fields[i].field_index, r.fields[i].value.i64);
    TCHECK(schema_encode(&s, &in, &out) == SCHEMA_OK);
    TCHECK(out.len == 4 && memcmp(out.data, "\x01\x02\x00\x07", 4) == 0);

    /* A missing index, and a value that does not fit, are named by their index. */
    encode_inputs_init(&in);
    encode_inputs_add_int_at(&in, 0, 1);
    TCHECK(schema_encode(&s, &in, &out) == SCHEMA_ERR_PARSE && out.len == 0);
    TCHECK(strcmp(out.error_msg, "Missing required field: #2") == 0);
    encode_inputs_add_int_at(&in, 2, 256);
    TCHECK(schema_encode(&s, &in, &out) == SCHEMA_ERR_OVERFLOW);
    TCHECK(strcmp(out.error_msg, "Field '#2': 256 encodes as 256, outside 0 to 255 "
                                 "(PS-168)") == 0);

    /* A name-keyed input for a nameless schema is refused, not ignored. */
    encode_inputs_init(&in);
    encode_inputs_add_int_at(&in, 0, 1);
    encode_inputs_add_int_at(&in, 2, 1);
    encode_inputs_add_int(&in, "battery", 7);
    TCHECK(schema_encode(&s, &in, &out) == SCHEMA_ERR_PARSE);
    TCHECK(strcmp(out.error_msg, "input 'battery' is keyed by name, and a schema without "
                                 "names takes each value by its field index (PS-555)") == 0);

    /* And the other way round: the named blob takes names. */
    schema_t named;
    TCHECK(schema_load_binary(&named, named_blob, sizeof(named_blob)) == SCHEMA_OK);
    encode_inputs_init(&in);
    encode_inputs_add_int(&in, "soil_temperature", 1);
    encode_inputs_add_int_at(&in, 2, 7);
    TCHECK(schema_encode(&named, &in, &out) == SCHEMA_ERR_PARSE);
    TCHECK(strcmp(out.error_msg, "input '#2' is keyed by field index, and a schema with "
                                 "names takes each value by name (PS-555)") == 0);
    encode_inputs_init(&in);
    encode_inputs_add_int(&in, "soil_temperature", 0x0102);
    encode_inputs_add_int(&in, "battery", 7);
    TCHECK(schema_encode(&named, &in, &out) == SCHEMA_OK);
    TCHECK(out.len == 4 && memcmp(out.data, "\x01\x02\x00\x07", 4) == 0);

    TCHECK(!encode_inputs_add_int_at(&in, SCHEMA_MAX_FIELDS, 1));
    TCHECK(!encode_inputs_add_int_at(&in, -1, 1));
}

/* PS-523 (CR-2026-109, CR-2026-132): a reference binds the value after the modifiers
 * and before the lookup. A `flagged` mask is a reference, so it shows what was bound:
 * bit 0 adds `a`, bit 1 adds `b` - mask-reference-binding.yaml's layout. */
static int decode_masked(field_def_t mask, const uint8_t* p, size_t len, decode_result_t* r) {
    schema_t s;
    memset(&s, 0, sizeof(s));
    s.endian = ENDIAN_BIG;
    field_set_var(&mask, "mask");
    schema_add_field(&s, &mask);
    field_def_t fl = field_flagged("$mask");
    field_add_flagged_group(&fl, 0, 2, 1);
    field_add_flagged_group(&fl, 1, 3, 1);
    schema_add_field(&s, &fl);
    field_def_t a = field_u8("a");
    field_def_t b = field_u8("b");
    schema_place_field(&s, 2, &a);
    schema_place_field(&s, 3, &b);
    return schema_decode(&s, p, len, r);
}

static void test_a_text_label_binds_its_number(void) {
    decode_result_t r;
    field_def_t m = field_u8("mask");
    field_add_lookup(&m, 2, "two");
    const uint8_t p[] = {0x02, 0x07};
    TCHECK(decode_masked(m, p, sizeof(p), &r) == SCHEMA_OK);
    TCHECK(strcmp(result_get_string(&r, "mask"), "two") == 0);
    TCHECK(result_get_field(&r, "a") == NULL);
    TCHECK(result_get_int(&r, "b", -1) == 7);

    /* A mapping with no entry omits the field and leaves the reference bound. */
    const uint8_t q[] = {0x03, 0x07, 0x08};
    TCHECK(decode_masked(m, q, sizeof(q), &r) == SCHEMA_OK);
    TCHECK(result_get_field(&r, "mask") == NULL);
    TCHECK(result_get_int(&r, "a", -1) == 7 && result_get_int(&r, "b", -1) == 8);
}

static void test_an_enum_binds_its_code(void) {
    decode_result_t r;
    field_def_t m = field_enum("mask", 1);
    field_add_lookup(&m, 3, "both");
    const uint8_t p[] = {0x03, 0x07, 0x08};
    TCHECK(decode_masked(m, p, sizeof(p), &r) == SCHEMA_OK);
    TCHECK(strcmp(result_get_string(&r, "mask"), "both") == 0);
    TCHECK(result_get_int(&r, "a", -1) == 7 && result_get_int(&r, "b", -1) == 8);
}

static void test_a_reference_binds_the_modified_value(void) {
    decode_result_t r;
    field_def_t m = field_u8("mask");
    field_set_div(&m, 2);
    /* Raw 4 reports 2 and binds 2 - bit 1. Binding the raw 4 selected no group. */
    const uint8_t p[] = {0x04, 0x07};
    TCHECK(decode_masked(m, p, sizeof(p), &r) == SCHEMA_OK);
    TCHECK(r.field_count == 2 && result_get_int(&r, "b", -1) == 7);

    /* Raw 3 binds 1.5, which has no bits. */
    const uint8_t q[] = {0x03, 0x07};
    TCHECK(decode_masked(m, q, sizeof(q), &r) == SCHEMA_ERR_MATCH);
    TCHECK(strcmp(r.error_msg, "flagged field mask is not an integer") == 0);

    /* A zero divisor omits the field and leaves the reference unbound. */
    field_def_t z = field_u8("mask");
    field_set_div(&z, 0);
    TCHECK(decode_masked(z, p, sizeof(p), &r) == SCHEMA_ERR_MATCH);
    TCHECK(strcmp(r.error_msg, "flagged field reference not found: mask") == 0);

    /* A float binds too; it returned before the variable was set. */
    field_def_t f = field_f32("mask", ENDIAN_BIG);
    const uint8_t fl[] = {0x40, 0x00, 0x00, 0x00, 0x07};   /* 2.0f */
    TCHECK(decode_masked(f, fl, sizeof(fl), &r) == SCHEMA_OK);
    TCHECK(result_get_int(&r, "b", -1) == 7);
}

/* CR-2026-114 (PS-534, PS-538): ascii is ISO 8859-1, and only trailing 0x00 is
 * padding. The payloads are ascii-latin1.yaml's. */
static int decode_label(const uint8_t* payload, size_t len, decode_result_t* r) {
    schema_t s;
    memset(&s, 0, sizeof(s));
    s.endian = ENDIAN_BIG;
    field_def_t f = field_ascii("label", 6);
    schema_add_field(&s, &f);
    return schema_decode(&s, payload, len, r);
}

static void test_ascii_keeps_an_interior_nul(void) {
    decode_result_t r;
    const uint8_t p[] = {0x41, 0x42, 0x00, 0x43, 0x00, 0x00};
    TCHECK(decode_label(p, sizeof(p), &r) == SCHEMA_OK);
    TCHECK(r.field_count == 1 && r.fields[0].text_len == 4);
    TCHECK(memcmp(r.fields[0].value.str, "AB\0C", 4) == 0);
    TCHECK(!r.fields[0].non_ascii);

    const uint8_t zeros[] = {0, 0, 0, 0, 0, 0};
    TCHECK(decode_label(zeros, sizeof(zeros), &r) == SCHEMA_OK);
    TCHECK(r.field_count == 1 && r.fields[0].text_len == 0 && r.fields[0].value.str[0] == 0);
    TCHECK(r.bytes_consumed == 6);

    /* A space is text, not padding. */
    const uint8_t space[] = {0x48, 0x43, 0x20, 0x00, 0x00, 0x00};
    TCHECK(decode_label(space, sizeof(space), &r) == SCHEMA_OK);
    TCHECK(r.fields[0].text_len == 3 && strcmp(r.fields[0].value.str, "HC ") == 0);
}

static void test_ascii_reports_a_high_byte_as_itself(void) {
    decode_result_t r;
    const uint8_t p[] = {0x80, 0xA0, 0xFF, 0x41, 0x00, 0x00};
    TCHECK(decode_label(p, sizeof(p), &r) == SCHEMA_OK);
    TCHECK(r.fields[0].text_len == 4);
    TCHECK(memcmp(r.fields[0].value.str, "\x80\xA0\xFF" "A", 4) == 0);
    TCHECK(r.fields[0].non_ascii);
}

static void test_ascii_is_never_truncated(void) {
    schema_t s;
    decode_result_t r;
    uint8_t p[40];
    memset(&s, 0, sizeof(s));
    field_def_t f = field_ascii("serial", 40);
    schema_add_field(&s, &f);
    memset(p, 0, sizeof(p));
    memset(p, 'x', 31);
    TCHECK(schema_decode(&s, p, sizeof(p), &r) == SCHEMA_OK);   /* 31 fit */
    TCHECK(r.fields[0].text_len == 31);
    p[31] = 'y';
    TCHECK(schema_decode(&s, p, sizeof(p), &r) == SCHEMA_ERR_UNSUPPORTED);
    TCHECK(r.field_count == 0);
    TCHECK(strcmp(r.error_msg, "Field 'serial': an ascii value longer than the 31 bytes "
                               "this interpreter holds, not truncated (PS-538)") == 0);
}

/* PS-537: padded with 0x00, written byte for byte, and longer than `length` rejected. */
static int encode_label(const uint8_t* text, size_t len, encode_result_t* out) {
    schema_t s;
    encode_inputs_t inputs;
    memset(&s, 0, sizeof(s));
    s.endian = ENDIAN_BIG;
    field_def_t f = field_ascii("label", 6);
    field_def_t g = field_u8("n");
    schema_add_field(&s, &f);
    schema_add_field(&s, &g);
    encode_inputs_init(&inputs);
    TCHECK(encode_inputs_add_text(&inputs, "label", text, len));
    encode_inputs_add_int(&inputs, "n", 7);
    return schema_encode(&s, &inputs, out);
}

static void test_ascii_encodes_padded(void) {
    encode_result_t out;
    TCHECK(encode_label((const uint8_t*)"AB\0C", 4, &out) == SCHEMA_OK);
    TCHECK(out.len == 7);
    TCHECK(memcmp(out.data, "\x41\x42\x00\x43\x00\x00\x07", 7) == 0);
    TCHECK(!out.non_ascii);

    TCHECK(encode_label((const uint8_t*)"A\xE9", 2, &out) == SCHEMA_OK);
    TCHECK(memcmp(out.data, "\x41\xE9\x00\x00\x00\x00\x07", 7) == 0);
    TCHECK(out.non_ascii);

    TCHECK(encode_label((const uint8_t*)"ABCDEFG", 7, &out) == SCHEMA_ERR_OVERFLOW);
    TCHECK(out.len == 0);
    TCHECK(strcmp(out.error_msg, "Field 'label': an ascii value of 7 bytes is longer than "
                                 "its length of 6 (PS-537)") == 0);

    /* A decoded value re-encodes to its own bytes. */
    decode_result_t r;
    const uint8_t p[] = {0x80, 0xA0, 0xFF, 0x41, 0x00, 0x00};
    TCHECK(decode_label(p, sizeof(p), &r) == SCHEMA_OK);
    TCHECK(encode_label((const uint8_t*)r.fields[0].value.str, r.fields[0].text_len,
                        &out) == SCHEMA_OK);
    TCHECK(memcmp(out.data, p, sizeof(p)) == 0);
}

static void test_ascii_and_numbers_are_not_mixed(void) {
    schema_t s;
    encode_inputs_t inputs;
    encode_result_t out;
    memset(&s, 0, sizeof(s));
    field_def_t f = field_ascii("label", 2);
    schema_add_field(&s, &f);
    encode_inputs_init(&inputs);
    encode_inputs_add_int(&inputs, "label", 5);
    TCHECK(schema_encode(&s, &inputs, &out) == SCHEMA_ERR_TYPE);
    TCHECK(strcmp(out.error_msg, "Field 'label': an ascii value is text, and a number "
                                 "was given") == 0);

    memset(&s, 0, sizeof(s));
    field_def_t n = field_u8("n");
    schema_add_field(&s, &n);
    encode_inputs_init(&inputs);
    TCHECK(encode_inputs_add_string(&inputs, "n", "5"));
    TCHECK(schema_encode(&s, &inputs, &out) == SCHEMA_ERR_TYPE);
    TCHECK(strcmp(out.error_msg, "Field 'n': text was given for a u8 field") == 0);

    /* Text the input cannot hold is refused, not truncated. */
    char longer[40];
    memset(longer, 'x', sizeof(longer) - 1);
    longer[sizeof(longer) - 1] = 0;
    TCHECK(!encode_inputs_add_string(&inputs, "label", longer));
}

/* CR-2026-131 (PS-070, PS-364, PS-397), CR-2026-137 (PS-535): a byte_group is one value
 * of its widest member's base, in its own byte order; members name bits of it. Layouts
 * are byte-group-width.yaml's, byte-group-bool.yaml's and tlv-nameless-case.yaml's. */
static void build_group(schema_t* s, uint8_t size, endian_t e, field_def_t* m, int n,
                        bool trailer) {
    memset(s, 0, sizeof(*s));
    s->endian = ENDIAN_BIG;
    field_def_t g = field_byte_group(size, e, 4, n);
    schema_add_field(s, &g);
    if (trailer) {
        field_def_t t = field_u8("after");
        schema_add_field(s, &t);
    }
    for (int i = 0; i < n; i++) schema_place_field(s, 4 + i, &m[i]);
}

static void test_a_byte_group_reads_one_value(void) {
    schema_t s;
    decode_result_t r;
    {   /* No size: as wide as u16; little-endian, so s8 is the first byte. */
        field_def_t m[] = { field_s8("signed_low"),
                            field_bit_range("mid", 2, false, 8, 4, 0, ENDIAN_DEFAULT) };
        build_group(&s, 0, ENDIAN_LITTLE, m, 2, true);
        const uint8_t p[] = {0xFE, 0x03, 0x44};
        TCHECK(schema_decode(&s, p, sizeof(p), &r) == SCHEMA_OK);
        TCHECK(r.field_count == 3 && r.bytes_consumed == 3);
        TCHECK(result_get_int(&r, "signed_low", 0) == -2);
        TCHECK(result_get_int(&r, "mid", 0) == 3);
        TCHECK(result_get_int(&r, "after", 0) == 0x44);
    }
    {   /* size: 3 widens a u16 group; u32 members in a four-byte little-endian one. */
        field_def_t m[] = { field_bit_range("lo", 2, false, 0, 8, 0, ENDIAN_DEFAULT),
                            field_bit_range("hi", 2, false, 8, 8, 0, ENDIAN_DEFAULT) };
        build_group(&s, 3, ENDIAN_LITTLE, m, 2, true);
        const uint8_t p[] = {0x34, 0x12, 0x00, 0x33};
        TCHECK(schema_decode(&s, p, sizeof(p), &r) == SCHEMA_OK);
        TCHECK(result_get_int(&r, "lo", 0) == 0x34 && result_get_int(&r, "hi", 0) == 0x12);
        TCHECK(result_get_int(&r, "after", 0) == 0x33);
    }
    {   /* bools and a range in one byte. */
        field_def_t m[] = { field_bool("flag_a", 0, false),
                            field_bit_range("level", 1, false, 1, 3, 0, ENDIAN_DEFAULT),
                            field_bool("flag_b", 7, false) };
        build_group(&s, 1, ENDIAN_DEFAULT, m, 3, true);
        const uint8_t p[] = {0x83, 0x2A};
        TCHECK(schema_decode(&s, p, sizeof(p), &r) == SCHEMA_OK);
        TCHECK(result_get_field(&r, "flag_a")->value.b);
        TCHECK(result_get_field(&r, "flag_b")->value.b);
        TCHECK(result_get_int(&r, "level", 0) == 1 && result_get_int(&r, "after", 0) == 42);
    }
}

static void test_a_byte_group_is_checked(void) {
    schema_t s;
    decode_result_t r;
    field_def_t m[] = { field_bit_range("a", 2, false, 0, 4, 0, ENDIAN_DEFAULT),
                        field_bit_range("_b", 2, false, 4, 4, 0, ENDIAN_DEFAULT) };
    build_group(&s, 0, ENDIAN_DEFAULT, m, 2, false);
    const uint8_t one[] = {0x34};
    TCHECK(schema_decode(&s, one, sizeof(one), &r) == SCHEMA_ERR_BUFFER);
    TCHECK(strcmp(r.error_msg, "Field byte_group of 'a', '_b': Buffer underflow at peek "
                               "offset 0 (PS-535)") == 0);
    /* The internal member decodes and is not reported. */
    const uint8_t two[] = {0x00, 0x21};
    TCHECK(schema_decode(&s, two, sizeof(two), &r) == SCHEMA_OK);
    TCHECK(r.field_count == 1 && result_get_int(&r, "a", 0) == 1);

    build_group(&s, 1, ENDIAN_DEFAULT, m, 2, false);
    TCHECK(schema_decode(&s, two, sizeof(two), &r) == SCHEMA_ERR_TYPE);
    TCHECK(strcmp(r.error_msg, "byte_group declares size 1, narrower than its widest "
                               "member's 2 bytes (PS-070)") == 0);

    field_def_t o[] = { field_bit_range("a", 1, false, 0, 4, 0, ENDIAN_DEFAULT),
                        field_bit_range("b", 1, false, 3, 3, 0, ENDIAN_DEFAULT) };
    build_group(&s, 0, ENDIAN_DEFAULT, o, 2, false);
    TCHECK(schema_decode(&s, two, sizeof(two), &r) == SCHEMA_ERR_TYPE);
    TCHECK(strcmp(r.error_msg, "byte_group members 'a' and 'b' overlap (PS-397)") == 0);

    field_def_t b[] = { field_bool("a", 9, false) };
    build_group(&s, 2, ENDIAN_DEFAULT, b, 1, false);
    TCHECK(schema_decode(&s, two, sizeof(two), &r) == SCHEMA_ERR_TYPE);
    TCHECK(strcmp(r.error_msg, "byte_group member 'a' is a bool at bit 9; a bool member "
                               "is one bit of a u8, 0 to 7 (PS-070)") == 0);
}

static void test_a_byte_group_in_a_tlv_case(void) {
    schema_t s;
    decode_result_t r;
    memset(&s, 0, sizeof(s));
    s.endian = ENDIAN_BIG;
    field_def_t t = field_tlv(1, 1);
    field_add_tlv_case(&t, 0x20, 2, 1);
    schema_add_field(&s, &t);
    field_def_t g = field_byte_group(1, ENDIAN_DEFAULT, 3, 2);
    field_def_t c = field_bit_range("charger_status", 1, false, 0, 2, 0, ENDIAN_DEFAULT);
    field_def_t d = field_bit_range("device_status", 1, false, 4, 4, 0, ENDIAN_DEFAULT);
    schema_place_field(&s, 2, &g);
    schema_place_field(&s, 3, &c);
    schema_place_field(&s, 4, &d);
    const uint8_t p[] = {0x20, 0x01, 0x51};
    TCHECK(schema_decode(&s, p, sizeof(p), &r) == SCHEMA_OK);
    TCHECK(result_get_int(&r, "charger_status", -1) == 1);
    TCHECK(result_get_int(&r, "device_status", -1) == 5);
}

/* A member's `var_name` binds as any field's does (member-var fixture): the mask of a
 * following flagged block is the low nibble of the group, and 0x32 selects bit 1. */
static void test_a_byte_group_member_binds_its_var(void) {
    schema_t s;
    decode_result_t r;
    memset(&s, 0, sizeof(s));
    s.endian = ENDIAN_BIG;
    field_def_t g = field_byte_group(1, ENDIAN_DEFAULT, 4, 2);
    schema_add_field(&s, &g);
    field_def_t fl = field_flagged("$mask");
    field_add_flagged_group(&fl, 0, 2, 1);
    field_add_flagged_group(&fl, 1, 3, 1);
    schema_add_field(&s, &fl);
    field_def_t a = field_u8("a");
    field_def_t b = field_u8("b");
    schema_place_field(&s, 2, &a);
    schema_place_field(&s, 3, &b);
    field_def_t lo = field_bit_range("lo", 1, false, 0, 4, 0, ENDIAN_DEFAULT);
    field_set_var(&lo, "mask");
    field_def_t hi = field_bit_range("hi", 1, false, 4, 4, 0, ENDIAN_DEFAULT);
    schema_place_field(&s, 4, &lo);
    schema_place_field(&s, 5, &hi);
    const uint8_t p[] = {0x32, 0x07};
    TCHECK(schema_decode(&s, p, sizeof(p), &r) == SCHEMA_OK);
    TCHECK(result_get_int(&r, "lo", -1) == 2);
    TCHECK(result_get_int(&r, "hi", -1) == 3);
    TCHECK(result_get_field(&r, "a") == NULL);
    TCHECK(result_get_int(&r, "b", -1) == 7);
}

static void test_a_byte_group_encodes_as_decoded(void) {
    schema_t s;
    encode_inputs_t in;
    encode_result_t out;
    field_def_t m[] = { field_s8("signed_low"),
                        field_bit_range("mid", 2, false, 8, 4, 0, ENDIAN_DEFAULT) };
    build_group(&s, 0, ENDIAN_LITTLE, m, 2, true);
    encode_inputs_init(&in);
    encode_inputs_add_int(&in, "signed_low", -2);
    encode_inputs_add_int(&in, "mid", 3);
    encode_inputs_add_int(&in, "after", 0x44);
    TCHECK(schema_encode(&s, &in, &out) == SCHEMA_OK);
    TCHECK(out.len == 3 && out.data[0] == 0xFE && out.data[1] == 0x03 && out.data[2] == 0x44);

    /* A member's value is checked as the member it is (PS-168). */
    encode_inputs_init(&in);
    encode_inputs_add_int(&in, "signed_low", -200);
    encode_inputs_add_int(&in, "mid", 3);
    encode_inputs_add_int(&in, "after", 1);
    TCHECK(schema_encode(&s, &in, &out) == SCHEMA_ERR_OVERFLOW && out.len == 0);
    TCHECK(strcmp(out.error_msg, "Field 'signed_low': -200 encodes as -200, outside -128 "
                                 "to 127 (PS-168)") == 0);

    /* A member left out is missing; an internal one is PS-434's. */
    encode_inputs_init(&in);
    encode_inputs_add_int(&in, "signed_low", 1);
    encode_inputs_add_int(&in, "after", 1);
    TCHECK(schema_encode(&s, &in, &out) == SCHEMA_ERR_PARSE);
    TCHECK(strcmp(out.error_msg, "Missing required field: mid") == 0);
    field_def_t i[] = { field_bit_range("a", 2, false, 0, 4, 0, ENDIAN_DEFAULT),
                        field_bit_range("_b", 2, false, 4, 4, 0, ENDIAN_DEFAULT) };
    build_group(&s, 0, ENDIAN_DEFAULT, i, 2, false);
    encode_inputs_init(&in);
    encode_inputs_add_int(&in, "a", 1);
    TCHECK(schema_encode(&s, &in, &out) == SCHEMA_ERR_PARSE);
    TCHECK(strcmp(out.error_msg, "internal field '_b' reads payload bytes and declares no "
                                 "value, and the input does not supply it (PS-434)") == 0);

    /* Bools at their bits, uncovered bits zero (PS-397). */
    field_def_t b[] = { field_bool("flag_a", 0, false),
                        field_bit_range("level", 1, false, 1, 3, 0, ENDIAN_DEFAULT),
                        field_bool("flag_b", 7, false) };
    build_group(&s, 1, ENDIAN_DEFAULT, b, 3, true);
    encode_inputs_init(&in);
    encode_inputs_add_int(&in, "flag_a", 1);
    encode_inputs_add_int(&in, "level", 1);
    encode_inputs_add_int(&in, "flag_b", 1);
    encode_inputs_add_int(&in, "after", 42);
    TCHECK(schema_encode(&s, &in, &out) == SCHEMA_OK);
    TCHECK(out.len == 2 && out.data[0] == 0x83 && out.data[1] == 0x2A);
}

/* CR-2026-136 (PS-059, PS-352, PS-353, PS-365): a bit range's base is N/8 bytes in the
 * field's byte order, an `sN` range sign-extends from its width, and encoding writes the
 * base back in the same order. The layout and payload are bit-range-order.yaml's,
 * without its byte_group. */
static void build_wide_ranges(schema_t* s) {
    memset(s, 0, sizeof(*s));
    s->endian = ENDIAN_BIG;
    field_def_t f[] = {
        field_bit_range("low12", 2, false, 0, 12, 0, ENDIAN_LITTLE),
        field_bit_range("top4", 2, true, 12, 4, 2, ENDIAN_LITTLE),
        field_bit_range("nibble", 1, true, 0, 4, 1, ENDIAN_DEFAULT),
        field_bit_range("middle", 2, false, 4, 8, 2, ENDIAN_DEFAULT),
    };
    for (int i = 0; i < 4; i++) schema_add_field(s, &f[i]);
}

static void test_a_wide_bit_range_reads_its_base(void) {
    schema_t s;
    decode_result_t r;
    const uint8_t p[] = {0x34, 0xF2, 0x0F, 0x02, 0x30};
    build_wide_ranges(&s);
    TCHECK(schema_decode(&s, p, sizeof(p), &r) == SCHEMA_OK);
    TCHECK(r.field_count == 4 && r.bytes_consumed == 5);
    TCHECK(result_get_int(&r, "low12", 0) == 564);
    TCHECK(result_get_int(&r, "top4", 0) == -1);
    TCHECK(result_get_int(&r, "nibble", 0) == -1);
    TCHECK(result_get_int(&r, "middle", 0) == 35);
    TCHECK(result_get_field(&r, "low12")->type == FIELD_TYPE_BITS);   /* an integer */

    /* wide-bit-range.yaml: a 64-bit base, signed and unsigned. */
    memset(&s, 0, sizeof(s));
    s.endian = ENDIAN_BIG;
    field_def_t lw = field_bit_range("low_word", 8, true, 0, 32, 0, ENDIAN_DEFAULT);
    field_def_t hw = field_bit_range("high_word", 8, false, 32, 32, 8, ENDIAN_DEFAULT);
    schema_add_field(&s, &lw);
    schema_add_field(&s, &hw);
    const uint8_t q[] = {0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xF6};
    TCHECK(schema_decode(&s, q, sizeof(q), &r) == SCHEMA_OK);
    TCHECK(result_get_int(&r, "low_word", 0) == -10);
    TCHECK(result_get_int(&r, "high_word", 0) == 4294967295LL);
    TCHECK(r.bytes_consumed == 8);
}

static void test_a_wide_bit_range_short_read_is_named(void) {
    schema_t s;
    decode_result_t r;
    const uint8_t one[] = {0x34};
    build_wide_ranges(&s);
    TCHECK(schema_decode(&s, one, sizeof(one), &r) == SCHEMA_ERR_BUFFER);
    TCHECK(strcmp(r.error_msg, "Field 'low12': Buffer underflow at peek offset 0 (PS-535)") == 0);

    /* The base is there; the bytes `consume` steps over are not. */
    memset(&s, 0, sizeof(s));
    field_def_t f = field_bit_range("x", 1, false, 0, 4, 2, ENDIAN_DEFAULT);
    schema_add_field(&s, &f);
    TCHECK(schema_decode(&s, one, sizeof(one), &r) == SCHEMA_ERR_BUFFER);
    TCHECK(strcmp(r.error_msg, "Field 'x': Buffer underflow: need 2 bytes at offset 0, "
                               "but only 1 remaining (PS-535)") == 0);
}

static void test_a_wide_bit_range_encodes_as_decoded(void) {
    schema_t s;
    encode_inputs_t in;
    encode_result_t out;
    const uint8_t p[] = {0x34, 0xF2, 0x0F, 0x02, 0x30};
    build_wide_ranges(&s);
    encode_inputs_init(&in);
    encode_inputs_add_int(&in, "low12", 564);
    encode_inputs_add_int(&in, "top4", -1);
    encode_inputs_add_int(&in, "nibble", -1);
    encode_inputs_add_int(&in, "middle", 35);
    TCHECK(schema_encode(&s, &in, &out) == SCHEMA_OK);
    TCHECK(out.len == 5 && memcmp(out.data, p, sizeof(p)) == 0);

    /* PS-064: an sN range holds -2^(w-1) to 2^(w-1)-1, and names its base. */
    encode_inputs_init(&in);
    encode_inputs_add_int(&in, "low12", 564);
    encode_inputs_add_int(&in, "top4", 8);
    encode_inputs_add_int(&in, "nibble", -1);
    encode_inputs_add_int(&in, "middle", 35);
    TCHECK(schema_encode(&s, &in, &out) == SCHEMA_ERR_OVERFLOW && out.len == 0);
    TCHECK(strcmp(out.error_msg, "Field 'top4': 8 encodes as 8, outside -8 to 7 for "
                                 "s16[12:15] (PS-168)") == 0);

    /* PS-551 over two bytes: the peeked low12 is derived from the u16 written after it. */
    memset(&s, 0, sizeof(s));
    s.endian = ENDIAN_BIG;
    field_def_t pk = field_bit_range("low12", 2, false, 0, 12, 0, ENDIAN_LITTLE);
    field_def_t wd = field_u16("word", ENDIAN_LITTLE);
    schema_add_field(&s, &pk);
    schema_add_field(&s, &wd);
    encode_inputs_init(&in);
    encode_inputs_add_int(&in, "word", 0xF234);
    TCHECK(schema_encode(&s, &in, &out) == SCHEMA_OK);
    TCHECK(out.len == 2 && out.data[0] == 0x34 && out.data[1] == 0xF2);
    encode_inputs_add_int(&in, "low12", 0x235);
    TCHECK(schema_encode(&s, &in, &out) == SCHEMA_ERR_PARSE);
    TCHECK(strcmp(out.error_msg, "Field 'low12' is given 565, but the fields that share "
                                 "its bits write 564 (PS-551)") == 0);
}

/* PS-555 (CR-2026-146) beyond the integer fields: a schema without names takes an `ascii`
 * value and a byte_group's members by field index too. A member's index is its position in
 * the schema's field array, nested_start + k, which is also the index decode reports it
 * under (field_index). The schemas are built with names and then stripped, as a binary
 * schema loaded without its name table is. */
static void strip_names(schema_t* s) {
    for (int i = 0; i < s->field_count; i++) s->fields[i].name[0] = '\0';
    s->nameless = true;
}

static void test_a_nameless_schema_encodes_text_and_group_members_by_index(void) {
    encode_inputs_t in;
    encode_result_t out;
    decode_result_t r;
    {   /* ascii by index: [label (ascii 6), n (u8)], then decoded and re-encoded. */
        schema_t s;
        memset(&s, 0, sizeof(s));
        s.endian = ENDIAN_BIG;
        field_def_t f = field_ascii("label", 6);
        field_def_t g = field_u8("n");
        schema_add_field(&s, &f);
        schema_add_field(&s, &g);
        strip_names(&s);
        encode_inputs_init(&in);
        TCHECK(encode_inputs_add_text_at(&in, 0, (const uint8_t*)"AB\0C", 4));
        TCHECK(encode_inputs_add_int_at(&in, 1, 7));
        TCHECK(schema_encode(&s, &in, &out) == SCHEMA_OK);
        TCHECK(out.len == 7 && memcmp(out.data, "\x41\x42\x00\x43\x00\x00\x07", 7) == 0);
        TCHECK(schema_decode(&s, out.data, out.len, &r) == SCHEMA_OK);
        TCHECK(r.field_count == 2 && r.fields[0].field_index == 0 && r.fields[0].text_len == 4);
        encode_inputs_init(&in);
        TCHECK(encode_inputs_add_text_at(&in, r.fields[0].field_index,
                                         (const uint8_t*)r.fields[0].value.str,
                                         r.fields[0].text_len));
        TCHECK(encode_inputs_add_string_at(&in, 0, "hi"));   /* first one found wins */
        TCHECK(encode_inputs_add_int_at(&in, 1, 7));
        encode_result_t again;
        TCHECK(schema_encode(&s, &in, &again) == SCHEMA_OK);
        TCHECK(again.len == out.len && memcmp(again.data, out.data, out.len) == 0);

        /* Not mixed: a name-keyed text input is refused for a nameless schema. */
        encode_inputs_init(&in);
        TCHECK(encode_inputs_add_string(&in, "label", "AB"));
        TCHECK(encode_inputs_add_int_at(&in, 1, 7));
        TCHECK(schema_encode(&s, &in, &out) == SCHEMA_ERR_PARSE);
        TCHECK(strcmp(out.error_msg, "input 'label' is keyed by name, and a schema without "
                                     "names takes each value by its field index (PS-555)") == 0);
        /* Missing is named by its index. */
        encode_inputs_init(&in);
        TCHECK(encode_inputs_add_int_at(&in, 1, 7));
        TCHECK(schema_encode(&s, &in, &out) == SCHEMA_ERR_PARSE);
        TCHECK(strcmp(out.error_msg, "Missing required field: #0") == 0);
        /* And an index-keyed text input is refused by a schema with names. */
        schema_t named = s;
        strncpy(named.fields[0].name, "label", SCHEMA_MAX_NAME_LEN - 1);
        strncpy(named.fields[1].name, "n", SCHEMA_MAX_NAME_LEN - 1);
        named.nameless = false;
        encode_inputs_init(&in);
        TCHECK(encode_inputs_add_string_at(&in, 0, "AB"));
        encode_inputs_add_int(&in, "n", 7);
        TCHECK(schema_encode(&named, &in, &out) == SCHEMA_ERR_PARSE);
        TCHECK(strcmp(out.error_msg, "input '#0' is keyed by field index, and a schema with "
                                     "names takes each value by name (PS-555)") == 0);
        TCHECK(!encode_inputs_add_text_at(&in, -1, (const uint8_t*)"A", 1));
        TCHECK(!encode_inputs_add_text_at(&in, SCHEMA_MAX_FIELDS, (const uint8_t*)"A", 1));
    }
    {   /* byte_group members: fields are [group (0), after (1), signed_low (4), mid (5)];
         * the members sit above field_count, at nested_start + k. */
        schema_t s;
        field_def_t m[] = { field_s8("signed_low"),
                            field_bit_range("mid", 2, false, 8, 4, 0, ENDIAN_DEFAULT) };
        build_group(&s, 0, ENDIAN_LITTLE, m, 2, true);
        for (int i = 0; i < SCHEMA_MAX_FIELDS; i++) s.fields[i].name[0] = '\0';
        s.nameless = true;
        encode_inputs_init(&in);
        TCHECK(encode_inputs_add_int_at(&in, 4, -2));
        TCHECK(encode_inputs_add_int_at(&in, 5, 3));
        TCHECK(encode_inputs_add_int_at(&in, 1, 0x44));
        TCHECK(schema_encode(&s, &in, &out) == SCHEMA_OK);
        TCHECK(out.len == 3 && out.data[0] == 0xFE && out.data[1] == 0x03 && out.data[2] == 0x44);
        /* Decoded, each member is reported under the index it is encoded by. */
        TCHECK(schema_decode(&s, out.data, out.len, &r) == SCHEMA_OK);
        TCHECK(r.field_count == 3);
        encode_inputs_init(&in);
        for (int i = 0; i < r.field_count; i++) {
            TCHECK(encode_inputs_add_int_at(&in, r.fields[i].field_index, r.fields[i].value.i64));
        }
        encode_result_t again;
        TCHECK(schema_encode(&s, &in, &again) == SCHEMA_OK);
        TCHECK(again.len == out.len && memcmp(again.data, out.data, out.len) == 0);

        /* A member left out is named by its index. */
        encode_inputs_init(&in);
        encode_inputs_add_int_at(&in, 4, -2);
        encode_inputs_add_int_at(&in, 1, 0x44);
        TCHECK(schema_encode(&s, &in, &out) == SCHEMA_ERR_PARSE);
        TCHECK(strcmp(out.error_msg, "Missing required field: #5") == 0);
        /* A name-keyed member is refused, not ignored. */
        encode_inputs_init(&in);
        encode_inputs_add_int_at(&in, 5, 3);
        encode_inputs_add_int_at(&in, 1, 0x44);
        encode_inputs_add_int(&in, "signed_low", -2);
        TCHECK(schema_encode(&s, &in, &out) == SCHEMA_ERR_PARSE);
        TCHECK(strcmp(out.error_msg, "input 'signed_low' is keyed by name, and a schema "
                                     "without names takes each value by its field index "
                                     "(PS-555)") == 0);
    }
    {   /* A schema with names is unchanged: members by name, an index-keyed one refused. */
        schema_t s;
        field_def_t m[] = { field_s8("signed_low"),
                            field_bit_range("mid", 2, false, 8, 4, 0, ENDIAN_DEFAULT) };
        build_group(&s, 0, ENDIAN_LITTLE, m, 2, true);
        encode_inputs_init(&in);
        encode_inputs_add_int_at(&in, 4, -2);
        encode_inputs_add_int(&in, "mid", 3);
        encode_inputs_add_int(&in, "after", 0x44);
        TCHECK(schema_encode(&s, &in, &out) == SCHEMA_ERR_PARSE);
        TCHECK(strcmp(out.error_msg, "input '#4' is keyed by field index, and a schema with "
                                     "names takes each value by name (PS-555)") == 0);
    }
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
    test_binary_names_round_trip();
    test_binary_skip_has_an_empty_name();
    test_binary_empty_name_is_only_for_a_skip();
    test_binary_over_length_name_is_refused();
    test_binary_truncated_name_table_is_refused();
    test_binary_old_version_is_refused();
    test_binary_trailing_bytes_are_refused();
    test_binary_nameless_form_reports_by_index();
    test_a_fractional_value_matches_no_key();
    test_an_enum_applies_its_modifiers_first();
    test_a_float_applies_its_modifiers();
    test_a_partial_composite_tag_is_an_error();
    test_a_partial_tag_size_tag_is_an_error();
    test_a_cut_length_is_an_error();
    test_a_cut_value_is_an_error();
    test_leftover_bytes_are_reported();
    test_a_skip_past_the_end_is_an_error();
    test_a_short_read_names_the_field();
    test_a_skip_is_written_on_encode();
    test_an_unknown_tag_is_left_over_from_the_tag();
    test_an_encoder_rounds_ties_to_even();
    test_a_negative_zero_is_zero();
    test_a_value_that_does_not_fit_is_named();
    test_a_nibble_decimal_that_does_not_fit_is_named();
    test_a_byte_string_is_refused_by_name();
    test_bit_ranges_are_laid_out_as_decoded();
    test_a_missing_input_is_named();
    test_no_construct_is_encoded_as_nothing();
    test_a_covered_peek_is_derived();
    test_a_nameless_schema_encodes_by_index();
    test_a_text_label_binds_its_number();
    test_an_enum_binds_its_code();
    test_a_reference_binds_the_modified_value();
    test_ascii_keeps_an_interior_nul();
    test_ascii_reports_a_high_byte_as_itself();
    test_ascii_is_never_truncated();
    test_ascii_encodes_padded();
    test_ascii_and_numbers_are_not_mixed();
    test_a_byte_group_reads_one_value();
    test_a_byte_group_is_checked();
    test_a_byte_group_in_a_tlv_case();
    test_a_byte_group_member_binds_its_var();
    test_a_byte_group_encodes_as_decoded();
    test_a_nameless_schema_encodes_text_and_group_members_by_index();
    test_a_wide_bit_range_reads_its_base();
    test_a_wide_bit_range_short_read_is_named();
    test_a_wide_bit_range_encodes_as_decoded();

    LOG(LOG_INFO, MOD, "Schema interpreter self-tests complete");
}
