// Copyright (c) 2024-2026 Multitech Systems, Inc.
// SPDX-License-Identifier: MIT

package org.lora.schema;

import static org.junit.jupiter.api.Assertions.*;

import java.util.Map;
import org.junit.jupiter.api.Test;

/**
 * CR-2026-008: the declared type decides the reported type, and an integral value
 * reports without a fraction.
 *
 * <p>The corpus runner compares numerically, so it passes whether an integer comes back
 * as a Long or a Double. These assertions pin the type itself, which is what decides
 * what a serializer writes: Jackson renders a Double 5 as {@code 5.0}, where the
 * interpreters and every deployed JS codec write {@code 5}.
 */
class DecodedValueTypesTest {

    @Test
    void plainIntegerFieldReportsAnIntegerType() {
        String yaml = """
                name: t
                endian: big
                fields:
                  - name: count
                    type: u16
                """;
        Map<String, Object> out = Schema.fromYaml(yaml).decode(new byte[] {0x00, 0x0A});

        assertEquals(10L, ((Number) out.get("count")).longValue());
        assertFalse(out.get("count") instanceof Double,
                "a u16 must not be reported as a Double - Jackson would write 10.0");
    }

    @Test
    void scaledFieldReportsAnIntegerWhenTheReadingIsWhole() {
        String yaml = """
                name: t
                endian: big
                fields:
                  - name: temperature
                    type: u16
                    div: 10
                """;
        // 150 / 10 = 15.0 exactly.
        Map<String, Object> out = Schema.fromYaml(yaml).decode(new byte[] {0x00, (byte) 0x96});

        assertFalse(out.get("temperature") instanceof Double,
                "an integral result must not keep its fraction (PS-280)");
        assertEquals(15L, ((Number) out.get("temperature")).longValue());
    }

    @Test
    void scaledFieldKeepsAFractionWhenItHasOne() {
        String yaml = """
                name: t
                endian: big
                fields:
                  - name: temperature
                    type: u16
                    div: 10
                """;
        Map<String, Object> out = Schema.fromYaml(yaml).decode(new byte[] {0x00, (byte) 0x97});

        assertEquals(15.1, ((Number) out.get("temperature")).doubleValue(), 1e-9);
    }

    @Test
    void bytesFieldReportsLowercaseHex() {
        String yaml = """
                name: t
                endian: big
                fields:
                  - name: eui
                    type: bytes
                    length: 4
                """;
        Map<String, Object> out = Schema.fromYaml(yaml).decode(
                new byte[] {(byte) 0xDE, (byte) 0xAD, (byte) 0xBE, (byte) 0xEF});

        assertEquals("deadbeef", out.get("eui"), "PS-281");
    }

    @Test
    void zeroDivisorOmitsTheFieldAndDecodingContinues() {
        String yaml = """
                name: t
                endian: big
                fields:
                  - name: a
                    type: u8
                  - name: zero
                    type: u8
                  - name: ratio
                    type: number
                    compute: {op: div, a: $a, b: $zero}
                  - name: after
                    type: u8
                """;
        Map<String, Object> out = Schema.fromYaml(yaml).decode(new byte[] {7, 0, 42});

        assertFalse(out.containsKey("ratio"), "PS-278/PS-282: NaN is not a JSON value");
        assertEquals(42L, ((Number) out.get("after")).longValue(),
                "decoding continues past the omitted field");
    }
}
