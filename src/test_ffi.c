/*
 * test_ffi.c - bindings/c/schema_ffi.c through its exported schema_ffi_* API only.
 *
 * Includes schema_ffi.h and not schema_interpreter.h, as a Python/Go/Node consumer
 * would, so it also holds the FFI's exported names clear of the interpreter header's
 * (schema_decode and the SCHEMA_* macros used to collide, and the FFI did not compile).
 * Linked with schema_ffi.c by `make test-c`.
 */

#include <stdint.h>
#include <stdio.h>
#include <string.h>
#include "schema_ffi.h"

#define CHECK(cond, msg) do { if (!(cond)) { printf("FAIL: %s\n", msg); return 1; } } while (0)

/* The binary schema from test_binary_schema.c: temperature s16 *0.01, humidity u8 *0.5,
 * battery u16, with a name table. */
static const uint8_t binary_schema[] = {
    0x50, 0x53,       /* Magic: 'PS' */
    0x03,             /* Format version: 3 */
    0x00,             /* Flags: big-endian */
    0x83,             /* Name table follows; field count: 3 */
    
    /* Field 0: temperature (s16, mult=0.01) */
    0x12,             /* Type: SINT(1), Size: 2 */
    0xFE,             /* Mult exp: -2 -> 0.01 */
    0xE7, 0x0C,       /* Field ID: 3303 (temperature) */
    
    /* Field 1: humidity (u8, mult=0.5) */
    0x01,             /* Type: UINT(0), Size: 1 */
    0x81,             /* Mult exp: special 0.5 */
    0xE8, 0x0C,       /* Field ID: 3304 (humidity) */
    
    /* Field 2: battery (u16) */
    0x02,             /* Type: UINT(0), Size: 2 */
    0x00,             /* Mult exp: 0 -> 1.0 */
    0xF4, 0x0C,       /* IPSO object 3316 (voltage) - not the field's name */
    /* Name table (CR-2026-142): the names the schema declares, never derived from
     * the IPSO IDs above */
    11, 't', 'e', 'm', 'p', 'e', 'r', 'a', 't', 'u', 'r', 'e',
    8, 'h', 'u', 'm', 'i', 'd', 'i', 't', 'y',
    7, 'b', 'a', 't', 't', 'e', 'r', 'y',
};

int main(void) {
    CHECK(strcmp(schema_ffi_version(), "1.0.0") == 0, "version");
    CHECK(schema_ffi_create_binary(NULL, 0) == NULL, "null blob is refused");
    CHECK(schema_ffi_create_yaml("name: x") == NULL, "YAML is the gateway tier's work: stub");

    schema_t_ffi s = schema_ffi_create_binary(binary_schema, sizeof(binary_schema));
    CHECK(s != NULL, "binary schema loads");
    CHECK(schema_ffi_get_field_count(s) == 3, "field count");

    /* temp=2345, hum=130, bat=3300 */
    const uint8_t payload[] = {0x09, 0x29, 0x82, 0x0C, 0xE4};
    result_t_ffi r = schema_ffi_decode(s, payload, sizeof(payload));
    CHECK(r != NULL, "decode returns a result");
    CHECK(schema_ffi_result_get_error(r) == 0, "decode succeeds");
    CHECK(schema_ffi_result_get_field_count(r) == 3, "result field count");
    CHECK(schema_ffi_result_get_bytes_consumed(r) == 5, "bytes consumed");
    CHECK(strcmp(schema_ffi_result_get_field_name(r, 0), "temperature") == 0, "name 0");
    CHECK(strcmp(schema_ffi_result_get_field_name(r, 2), "battery") == 0, "name 2");
    CHECK(schema_ffi_result_get_field_int(r, 2) == 3300, "battery value");

    char* json = schema_ffi_result_to_json(r);
    CHECK(json != NULL, "json");
    CHECK(strstr(json, "\"battery\":3300") != NULL, "json carries battery");
    schema_ffi_free_string(json);

    schema_ffi_result_free(r);

    const uint8_t shortp[] = {0x09};
    r = schema_ffi_decode(s, shortp, sizeof(shortp));
    CHECK(r != NULL && schema_ffi_result_get_error(r) != 0, "short payload is an error");
    schema_ffi_result_free(r);

    schema_ffi_free(s);
    printf("schema_ffi tests pass.\n");
    return 0;
}
