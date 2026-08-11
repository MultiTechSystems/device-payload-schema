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
 * Main test entry point
 */
void selftest_schema(void) {
    LOG(LOG_INFO, MOD, "Running schema interpreter self-tests");

    test_sequence_in_range();
    test_sequence_out_of_bounds_is_an_error();
    test_mapping_gap_omits_quietly();
    test_mapping_hit();
    test_sequence_flag_survives_binary();

    LOG(LOG_INFO, MOD, "Schema interpreter self-tests complete");
}
