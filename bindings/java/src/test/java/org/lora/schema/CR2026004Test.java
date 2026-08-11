// Copyright (c) 2024-2026 Multitech Systems, Inc.
// SPDX-License-Identifier: MIT

package org.lora.schema;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertThrows;
import static org.junit.jupiter.api.Assertions.assertTrue;

import java.util.Map;
import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;

/**
 * CR-2026-004 (PS-265 .. PS-270): computed field names, sparse lookup mappings, and
 * negated or wildcard TLV case keys.
 *
 * <p>The sequence form of {@code lookup} was also unparsed here, so a schema written
 * {@code lookup: ["off", "on"]} decoded a raw integer while the mapping form worked.
 */
class CR2026004Test {

    private static Map<String, Object> decode(String yaml, byte[] payload) {
        return Schema.fromYaml(yaml).decode(payload);
    }

    private static final String SPARSE =
            """
            name: sparse
            fields:
              - name: button
                type: u8
                lookup: {1: short, 2: long, 3: double}
            """;

    @Test
    @DisplayName("a sparse mapping matches every key, including the last")
    void sparseMappingMatchesEveryKey() {
        assertEquals("short", decode(SPARSE, new byte[] {1}).get("button"));
        assertEquals("long", decode(SPARSE, new byte[] {2}).get("button"));
        assertEquals("double", decode(SPARSE, new byte[] {3}).get("button"));
    }

    @Test
    @DisplayName("an unmapped value omits the field")
    void unmappedValueOmitsField() {
        assertFalse(decode(SPARSE, new byte[] {9}).containsKey("button"));
    }

    @Test
    @DisplayName("a declared default replaces the omission")
    void defaultIsUsed() {
        String yaml =
                """
                name: with_default
                fields:
                  - name: state
                    type: u8
                    lookup: {1: on, default: unknown}
                """;
        assertEquals("unknown", decode(yaml, new byte[] {9}).get("state"));
    }

    @Test
    @DisplayName("the sequence form is applied and keeps the raw value out of range")
    void sequenceFormIsApplied() {
        String yaml =
                """
                name: seq
                fields:
                  - name: relay
                    type: u8
                    lookup: ["off", "on"]
                """;
        assertEquals("on", decode(yaml, new byte[] {1}).get("relay"));
        // PS-105: an out-of-bounds index is an error, not the raw value. This asserted
        // only that the key was present, which the raw index satisfied.
        SchemaException.DecodeException thrown = assertThrows(
                SchemaException.DecodeException.class, () -> decode(yaml, new byte[] {7}));
        assertEquals("lookup index 7 out of bounds for 2 entries", thrown.getMessage());
    }

    @Test
    @DisplayName("name_from builds the output key from the payload")
    void nameFromBuildsKey() {
        String yaml =
                """
                name: computed
                endian: little
                fields:
                  - name: region_id
                    type: u8
                  - name: avg_dwell
                    name_from: "region_${region_id}_avg_dwell"
                    type: u16
                """;
        Map<String, Object> out = decode(yaml, new byte[] {3, 0x10, 0x00});
        assertTrue(out.containsKey("region_3_avg_dwell"), () -> "got " + out);
    }

    @Test
    @DisplayName("an unresolved name_from reference is an error")
    void unresolvedNameFromThrows() {
        String yaml =
                """
                name: bad
                fields:
                  - name: v
                    type: u8
                    name_from: "x_${nope}"
                """;
        assertThrows(SchemaException.class, () -> decode(yaml, new byte[] {1}));
    }

    private static final String TAGGED =
            """
            name: tags
            endian: little
            fields:
              - tlv:
                  tag_fields:
                    - name: channel_id
                      type: u8
                    - name: channel_type
                      type: u8
                  tag_key: [channel_id, channel_type]
                  cases:
                    "[1, 200]":
                      - name: exact
                        type: u8
                    "[1, !0]":
                      - name: any_but_zero
                        type: u8
                    "[2, *]":
                      - name: any_type
                        type: u8
            """;

    @Test
    @DisplayName("exact keys beat negated, negated beat wildcard")
    void caseKeyPrecedence() {
        assertTrue(decode(TAGGED, new byte[] {1, (byte) 0xc8, 7}).containsKey("exact"));
        assertTrue(decode(TAGGED, new byte[] {1, 5, 1}).containsKey("any_but_zero"));
        assertTrue(decode(TAGGED, new byte[] {2, 0x63, 10}).containsKey("any_type"));
    }

    @Test
    @DisplayName("a negated key excludes its own value")
    void negatedKeyExcludes() {
        assertFalse(decode(TAGGED, new byte[] {1, 0, 9}).containsKey("any_but_zero"));
    }
}
