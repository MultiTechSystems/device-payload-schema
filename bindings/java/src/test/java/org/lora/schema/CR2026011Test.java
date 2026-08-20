package org.lora.schema;

import org.junit.jupiter.api.Test;

import java.util.Map;

import static org.junit.jupiter.api.Assertions.*;

/**
 * PS-279, PS-293 to PS-295 (CR-2026-011): integer typing and exactness.
 *
 * <p>This binding reported 9007199254740992 for a u64 whose value was 9007199254740993,
 * because every numeric field went through {@code doubleValue()}; and -1 for
 * 18446744073709551615, because a Java long cannot hold it. It also wrapped s64 minimum to
 * s64 maximum, since Java masks a shift count to the operand's width and {@code 1L << 64}
 * is 1.
 */
public class CR2026011Test {

    private static Object decode(String declared, String hex) {
        Schema s = Schema.fromYaml(
            "name: t\nendian: big\nfields:\n  - {name: v, " + declared + "}\n");
        byte[] raw = new byte[hex.length() / 2];
        for (int i = 0; i < raw.length; i++) {
            raw[i] = (byte) Integer.parseInt(hex.substring(i * 2, i * 2 + 2), 16);
        }
        Map<String, Object> out = s.decode(raw);
        return out.get("v");
    }

    @Test
    public void anIntegerWidthReportsThroughAnIntegerChannel() {
        // PS-293.
        assertInstanceOf(Long.class, decode("type: u8", "01"));
        assertInstanceOf(Long.class, decode("type: u16", "003C"));
        assertInstanceOf(Long.class, decode("type: u32", "0000003C"));
        assertInstanceOf(Long.class, decode("type: u64", "000000000000003C"));
        assertInstanceOf(Long.class, decode("type: s16", "FFFF"));
    }

    @Test
    public void aModifierMakesTheFieldANumber() {
        // PS-279.
        assertEquals(23.5, (Double) decode("type: s16, div: 10", "00EB"), 1e-9);
    }

    @Test
    public void aU64JustAboveTheDoubleRangeIsExact() {
        // PS-294: the value six implementations rounded to 2^53.
        assertEquals(9007199254740993L, decode("type: u64", "0020000000000001"));
    }

    @Test
    public void aU64BeyondTheSignedRangeIsAnExactDecimalString() {
        // PS-295: a Java long cannot hold it, so the exact value is reported as a string
        // rather than as the -1 this binding used to report.
        assertEquals("18446744073709551615", decode("type: u64", "FFFFFFFFFFFFFFFF"));
    }

    @Test
    public void anS64AtTheBottomOfItsRangeIsExact() {
        assertEquals(Long.MIN_VALUE, decode("type: s64", "8000000000000000"));
    }

    @Test
    public void aSignedWidthBelowSixtyFourBitsStillSignExtends() {
        assertEquals(-1L, decode("type: s16", "FFFF"));
        assertEquals((long) Integer.MIN_VALUE, decode("type: s32", "80000000"));
    }
}
