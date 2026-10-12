/*
 * schema_ffi.c - FFI wrapper implementation
 *
 * Build:
 *   gcc -shared -fPIC -O2 -I../../include -o libschema.so schema_ffi.c
 *   clang -shared -fPIC -O2 -I../../include -o libschema.dylib schema_ffi.c
 *
 * Windows:
 *   cl /LD /O2 /I..\..\include /DSCHEMA_FFI_EXPORTS schema_ffi.c /Fe:schema.dll
 */

#define SCHEMA_FFI_EXPORTS
#include "schema_ffi.h"
#include "schema_interpreter.h"
#include <stdlib.h>
#include <stdio.h>
#include <string.h>

#define SCHEMA_FFI_VERSION "1.0.0"

/* Internal handle structures */
struct schema_handle {
    schema_t schema;
    int valid;
};

struct result_handle {
    decode_result_t result;
    int valid;
};

/* ============================================
 * Schema Management
 * ============================================ */

SCHEMA_API schema_t_ffi schema_ffi_create_binary(const uint8_t* data, size_t len) {
    if (!data || len == 0) return NULL;
    
    struct schema_handle* h = (struct schema_handle*)calloc(1, sizeof(struct schema_handle));
    if (!h) return NULL;
    
    schema_init(&h->schema);
    int ret = schema_load_binary(&h->schema, data, len);
    if (ret != 0) {
        free(h);
        return NULL;
    }
    
    h->valid = 1;
    return h;
}

SCHEMA_API schema_t_ffi schema_ffi_create_yaml(const char* yaml_str) {
    (void)yaml_str;
    return NULL;
}

SCHEMA_API void schema_ffi_free(schema_t_ffi schema) {
    if (schema) {
        schema->valid = 0;
        free(schema);
    }
}

SCHEMA_API const char* schema_ffi_get_name(schema_t_ffi schema) {
    if (!schema || !schema->valid) return "";
    return schema->schema.name;
}

SCHEMA_API int schema_ffi_get_field_count(schema_t_ffi schema) {
    if (!schema || !schema->valid) return 0;
    return schema->schema.field_count;
}

/* ============================================
 * Decoding
 * ============================================ */

SCHEMA_API result_t_ffi schema_ffi_decode(schema_t_ffi schema,
                                       const uint8_t* payload,
                                       size_t payload_len) {
    if (!schema || !schema->valid || !payload) return NULL;
    
    struct result_handle* h = (struct result_handle*)calloc(1, sizeof(struct result_handle));
    if (!h) return NULL;
    
    int ret = schema_decode(&schema->schema, payload, payload_len, &h->result);
    h->valid = 1;
    
    if (ret != 0) {
        h->result.error_code = ret;
    }
    
    return h;
}

SCHEMA_API void schema_ffi_result_free(result_t_ffi result) {
    if (result) {
        result->valid = 0;
        free(result);
    }
}

/* The interpreter's SCHEMA_ERR_* codes are mapped onto the published SCHEMA_FFI_ERR_*
 * set, so a binding sees only the constants schema_ffi.h declares. Parse and overflow
 * keep their meaning; every other decode failure (short buffer, type, match, lookup,
 * unsupported, missing, direction) is SCHEMA_FFI_ERR_DECODE, and the exact reason is in
 * schema_ffi_result_get_error_msg(). */
static int map_error(int code) {
    if (code == SCHEMA_OK) return SCHEMA_FFI_OK;
    if (code == SCHEMA_ERR_PARSE) return SCHEMA_FFI_ERR_PARSE;
    if (code == SCHEMA_ERR_OVERFLOW) return SCHEMA_FFI_ERR_OVERFLOW;
    return SCHEMA_FFI_ERR_DECODE;
}

SCHEMA_API int schema_ffi_result_get_error(result_t_ffi result) {
    if (!result || !result->valid) return SCHEMA_FFI_ERR_INVALID;
    return map_error(result->result.error_code);
}

SCHEMA_API const char* schema_ffi_result_get_error_msg(result_t_ffi result) {
    if (!result || !result->valid) return "Invalid result handle";
    return result->result.error_msg;
}

SCHEMA_API int schema_ffi_result_get_field_count(result_t_ffi result) {
    if (!result || !result->valid) return 0;
    return result->result.field_count;
}

SCHEMA_API int schema_ffi_result_get_bytes_consumed(result_t_ffi result) {
    if (!result || !result->valid) return 0;
    return result->result.bytes_consumed;
}

/* ============================================
 * Field Access
 * ============================================ */

SCHEMA_API const char* schema_ffi_result_get_field_name(result_t_ffi result, int index) {
    if (!result || !result->valid) return "";
    if (index < 0 || index >= result->result.field_count) return "";
    return result->result.fields[index].name;
}

SCHEMA_API int schema_ffi_result_get_field_schema_index(result_t_ffi result, int index) {
    if (!result || !result->valid) return -1;
    if (index < 0 || index >= result->result.field_count) return -1;
    return result->result.fields[index].field_index;
}

SCHEMA_API int schema_ffi_result_get_field_type(result_t_ffi result, int index) {
    if (!result || !result->valid) return -1;
    if (index < 0 || index >= result->result.field_count) return -1;
    
    field_type_t t = result->result.fields[index].type;
    switch (t) {
        case FIELD_TYPE_U8:
        case FIELD_TYPE_U16:
        case FIELD_TYPE_U24:
        case FIELD_TYPE_U32:
        case FIELD_TYPE_U64:
        case FIELD_TYPE_S8:
        case FIELD_TYPE_S16:
        case FIELD_TYPE_S24:
        case FIELD_TYPE_S32:
        case FIELD_TYPE_S64:
            return SCHEMA_FFI_VAL_INT;
        case FIELD_TYPE_F16:
        case FIELD_TYPE_F32:
        case FIELD_TYPE_F64:
            return SCHEMA_FFI_VAL_FLOAT;
        case FIELD_TYPE_BOOL:
            return SCHEMA_FFI_VAL_BOOL;
        case FIELD_TYPE_ASCII:
        case FIELD_TYPE_HEX:
            return SCHEMA_FFI_VAL_STRING;
        default:
            return SCHEMA_FFI_VAL_INT;
    }
}

/* A field's value is held in the union member its type tag names: integer types in
 * i64 (u64 in u64), float types - which includes any field carrying a modifier, PS-279 -
 * in f64. Both getters read the member the decoder filled and CONVERT to the type asked
 * for: get_field_int truncates a float toward zero (saturating at the int64 range),
 * get_field_float widens an integer. Use schema_ffi_result_get_field_type() to learn
 * which the field is. */
static int is_float_tag(field_type_t t) {
    return t == FIELD_TYPE_F16 || t == FIELD_TYPE_F32 || t == FIELD_TYPE_F64;
}

SCHEMA_API int64_t schema_ffi_result_get_field_int(result_t_ffi result, int index) {
    if (!result || !result->valid) return 0;
    if (index < 0 || index >= result->result.field_count) return 0;
    const decoded_field_t* f = &result->result.fields[index];
    if (is_float_tag(f->type)) {
        double d = f->value.f64;
        if (d != d) return 0;
        if (d >= 9223372036854775807.0) return INT64_MAX;
        if (d <= -9223372036854775808.0) return INT64_MIN;
        return (int64_t)d;
    }
    return f->value.i64;
}

SCHEMA_API double schema_ffi_result_get_field_float(result_t_ffi result, int index) {
    if (!result || !result->valid) return 0.0;
    if (index < 0 || index >= result->result.field_count) return 0.0;
    const decoded_field_t* f = &result->result.fields[index];
    if (is_float_tag(f->type)) return f->value.f64;
    if (f->type == FIELD_TYPE_U64) return (double)f->value.u64;
    return (double)f->value.i64;
}

SCHEMA_API const char* schema_ffi_result_get_field_string(result_t_ffi result, int index) {
    if (!result || !result->valid) return "";
    if (index < 0 || index >= result->result.field_count) return "";
    return result->result.fields[index].value.str;
}

SCHEMA_API int schema_ffi_result_get_field_bool(result_t_ffi result, int index) {
    if (!result || !result->valid) return 0;
    if (index < 0 || index >= result->result.field_count) return 0;
    return result->result.fields[index].value.b ? 1 : 0;
}

/* ============================================
 * JSON Output
 * ============================================ */

SCHEMA_API char* schema_ffi_result_to_json(result_t_ffi result) {
    if (!result || !result->valid) return NULL;
    /* A nameless schema has no keys to write, and none may be made up (PS-555). */
    if (result->result.nameless) return NULL;
    
    size_t buf_size = 4096;
    char* buf = (char*)malloc(buf_size);
    if (!buf) return NULL;
    
    char* p = buf;
    size_t remaining = buf_size;
    
    p += snprintf(p, remaining, "{");
    remaining = buf_size - (p - buf);
    
    for (int i = 0; i < result->result.field_count; i++) {
        decoded_field_t* f = &result->result.fields[i];
        if (!f->valid) continue;
        
        if (i > 0) {
            p += snprintf(p, remaining, ",");
            remaining = buf_size - (p - buf);
        }
        
        p += snprintf(p, remaining, "\"%s\":", f->name);
        remaining = buf_size - (p - buf);
        
        int vtype = schema_ffi_result_get_field_type(result, i);
        switch (vtype) {
            case SCHEMA_FFI_VAL_INT:
                p += snprintf(p, remaining, "%lld", (long long)f->value.i64);
                break;
            case SCHEMA_FFI_VAL_FLOAT:
                p += snprintf(p, remaining, "%g", f->value.f64);
                break;
            case SCHEMA_FFI_VAL_STRING:
                p += snprintf(p, remaining, "\"%s\"", f->value.str);
                break;
            case SCHEMA_FFI_VAL_BOOL:
                p += snprintf(p, remaining, "%s", f->value.b ? "true" : "false");
                break;
            default:
                p += snprintf(p, remaining, "%lld", (long long)f->value.i64);
                break;
        }
        remaining = buf_size - (p - buf);
    }
    
    snprintf(p, remaining, "}");
    return buf;
}

SCHEMA_API void schema_ffi_free_string(char* str) {
    if (str) free(str);
}

/* ============================================
 * Version Info
 * ============================================ */

SCHEMA_API const char* schema_ffi_version(void) {
    return SCHEMA_FFI_VERSION;
}
