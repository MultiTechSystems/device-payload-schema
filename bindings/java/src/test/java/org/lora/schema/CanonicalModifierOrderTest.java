// Copyright (c) 2024-2026 Multitech Systems, Inc.
// SPDX-License-Identifier: MIT

package org.lora.schema;

import static org.junit.jupiter.api.Assertions.assertEquals;

import java.util.Map;
import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.params.ParameterizedTest;
import org.junit.jupiter.params.provider.ValueSource;

/**
 * Canonical modifier order (PS-101 / PS-102).
 *
 * <p>Bare {@code mult}, {@code div} and {@code add} apply in the order mult, div, add
 * whatever order the keys appear in. This decoder previously held the source key order
 * in a {@code modOrder} list and applied the modifiers in that order, with a
 * differently-ordered fallback when the list was empty, so it disagreed with the C and
 * Python interpreters. This package had no tests at all, which is why that survived.
 * See CR-2026-002.
 */
class CanonicalModifierOrderTest {

    // raw = 0x0271 = 625, so the canonical result is (625 / 10) - 400 = -337.5.
    private static final byte[] PAYLOAD = new byte[] {0x02, 0x71};

    private static final String ADD_FIRST =
            """
            name: canonical_add_first
            endian: big
            fields:
              - name: soil_temperature
                type: u16
                add: -400
                div: 10
            """;

    private static final String DIV_FIRST =
            """
            name: canonical_div_first
            endian: big
            fields:
              - name: soil_temperature
                type: u16
                div: 10
                add: -400
            """;

    private static final String WITH_TRANSFORM =
            """
            name: transform_order
            endian: big
            fields:
              - name: soil_temperature
                type: u16
                transform:
                  - add: -400
                  - div: 10
            """;

    private static double decodeTemperature(String yaml) {
        Schema schema = Schema.fromYaml(yaml);
        Map<String, Object> result = schema.decode(PAYLOAD);
        return ((Number) result.get("soil_temperature")).doubleValue();
    }

    @ParameterizedTest
    @ValueSource(strings = {ADD_FIRST, DIV_FIRST})
    @DisplayName("key order does not change the decoded value")
    void keyOrderDoesNotChangeTheResult(String yaml) {
        assertEquals(-337.5, decodeTemperature(yaml), 0.001);
    }

    @Test
    @DisplayName("both key orders agree with each other")
    void bothKeyOrdersAgree() {
        assertEquals(decodeTemperature(ADD_FIRST), decodeTemperature(DIV_FIRST), 0.001);
    }

    @Test
    @DisplayName("transform expresses an offset applied before the division")
    void transformExpressesOffsetFirst() {
        assertEquals(22.5, decodeTemperature(WITH_TRANSFORM), 0.001);
    }

    @Test
    @DisplayName("all three modifiers apply in canonical order")
    void allThreeModifiersApplyInCanonicalOrder() {
        String yaml =
                """
                name: three_modifiers
                endian: big
                fields:
                  - name: v
                    type: u16
                    add: 1
                    div: 2
                    mult: 3
                """;
        Schema schema = Schema.fromYaml(yaml);
        Map<String, Object> result = schema.decode(new byte[] {0x00, 0x64});
        // ((100 * 3) / 2) + 1 = 151
        assertEquals(151.0, ((Number) result.get("v")).doubleValue(), 0.001);
    }
}
