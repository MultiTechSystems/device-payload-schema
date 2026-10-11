package org.lora.schema;

import org.junit.jupiter.api.Test;
import java.util.Map;

import static org.junit.jupiter.api.Assertions.*;

/**
 * CR-2026-109 (PS-523, PS-524): a reference to a looked-up field binds the number under a
 * text or boolean label, a numeric label as itself, and the number where the lookup has no
 * entry. A key template still writes the label (PS-532).
 *
 * <p>Measured before: Java bound the label everywhere, so arithmetic read a text label as 0,
 * a match on a labelled field took its default, and a miss left the reference unbound.
 */
public class CR2026109Test {

    private static Map<String, Object> decode(String lookup, String sites, int... payload) {
        byte[] b = new byte[payload.length];
        for (int i = 0; i < payload.length; i++) b[i] = (byte) payload[i];
        return Schema.fromYaml("name: p\nendian: big\nfields:\n"
                + "  - {name: code, type: u8, var: c, lookup: " + lookup + "}\n" + sites).decode(b);
    }

    private static final String COMPUTE =
            "  - {name: doubled, type: number, compute: {op: mul, a: $code, b: 2}}\n";
    private static final String VAR = "  - {name: held, type: number, ref: $c}\n";
    private static String select(int key) {
        return "  - match:\n      field: $code\n      cases:\n        " + key
                + ": [{name: hit, type: u8}]\n      default: [{name: other, type: u8}]\n";
    }
    private static final String TEMPLATE =
            "  - {name: reading, type: u8, name_from: \"reading_${code}\"}\n";

    private static void assertNum(double want, Object got) {
        assertNotNull(got);
        assertEquals(want, ((Number) got).doubleValue(), 1e-9);
    }

    @Test
    public void aTextLabelBindsItsNumber() {
        String lk = "{1: report, 129: report_rsp}";
        assertNum(258, decode(lk, COMPUTE, 0x81).get("doubled"));
        assertNum(129, decode(lk, VAR, 0x81).get("held"));
        Map<String, Object> m = decode(lk, select(129), 0x81, 7);
        assertNum(7, m.get("hit"));
        assertFalse(m.containsKey("other"));
        assertNum(7, decode(lk, TEMPLATE, 0x81, 7).get("reading_report_rsp"));
    }

    @Test
    public void aBooleanLabelBindsItsNumber() {
        String lk = "{0: false, 1: true}";
        assertNum(2, decode(lk, COMPUTE, 1).get("doubled"));
        assertNum(1, decode(lk, VAR, 1).get("held"));
        assertNum(7, decode(lk, select(1), 1, 7).get("hit"));
        assertNum(7, decode(lk, TEMPLATE, 1, 7).get("reading_true"));
    }

    @Test
    public void aNumericLabelBindsItself() {
        for (String lk : new String[] {"[0, 125, 250, 500]", "{1: 125, 2: 250}"}) {
            assertNum(250, decode(lk, COMPUTE, 1).get("doubled"));
            assertNum(125, decode(lk, VAR, 1).get("held"));
            assertNum(7, decode(lk, select(125), 1, 7).get("hit"));
            assertNum(7, decode(lk, TEMPLATE, 1, 7).get("reading_125"));
        }
    }

    @Test
    public void aMissIsOmittedButBindsTheNumber() {
        String lk = "{1: report, 129: report_rsp}";
        Map<String, Object> out = decode(lk, COMPUTE + VAR, 5);
        assertFalse(out.containsKey("code"));
        assertNum(10, out.get("doubled"));
        assertNum(5, out.get("held"));
        assertNum(7, decode(lk, select(5), 5, 7).get("hit"));
    }

    @Test
    public void aTemplateOverAMissIsAnError() {
        Exception e = assertThrows(RuntimeException.class,
                () -> decode("{1: report}", TEMPLATE, 5, 7));
        assertTrue(e.getMessage().contains("which reported no value (PS-266, PS-532)"),
                e.getMessage());
    }

    @Test
    public void aDefaultLabelBindsTheNumberAndANumericDefaultItself() {
        Map<String, Object> out = decode("{1: report, default: spare}", COMPUTE + TEMPLATE, 5, 7);
        assertEquals("spare", out.get("code"));
        assertNum(10, out.get("doubled"));
        assertNum(7, out.get("reading_spare"));
        assertNum(18, decode("{1: 10, default: 9}", COMPUTE, 5).get("doubled"));
    }

    @Test
    public void aSentinelLeavesTheReferenceUnbound() {
        Map<String, Object> out = Schema.fromYaml("name: p\nendian: big\nfields:\n"
                + "  - {name: code, type: u8, sentinel: [255], lookup: {1: report}}\n"
                + COMPUTE).decode(new byte[] {(byte) 0xFF});
        assertFalse(out.containsKey("code"));
        // Unbound, so not 510: Java reads an unbound reference as 0, as Python does
        // (AGENTS.md, wave 6a) - a separate, older divergence from Go and C#.
        assertNotEquals(510.0, ((Number) out.getOrDefault("doubled", 0)).doubleValue());
    }

    @Test
    public void aByteGroupMemberBindsItsNumber() {
        Map<String, Object> out = Schema.fromYaml("name: p\nendian: big\nfields:\n"
                + "  - byte_group:\n      size: 1\n      fields:\n"
                + "        - {name: mode, type: 'u8[4:7]', lookup: {2: eco, 3: boost}}\n"
                + "        - {name: low, type: 'u8[0:3]'}\n"
                + "  - {name: doubled, type: number, compute: {op: mul, a: $mode, b: 2}}\n")
                .decode(new byte[] {0x31});
        assertEquals("boost", out.get("mode"));
        assertNum(6, out.get("doubled"));
    }

    @Test
    public void aTlvCaseFieldBindsItsNumber() {
        Map<String, Object> out = Schema.fromYaml("name: p\nendian: big\nfields:\n"
                + "  - tlv:\n      tag_size: 1\n      cases:\n        1:\n"
                + "          - {name: code, type: u8, lookup: {129: report_rsp}}\n"
                + "          - match:\n              field: $code\n              cases:\n"
                + "                129: [{name: hit, type: u8}]\n")
                .decode(new byte[] {0x01, (byte) 0x81, 0x07});
        assertEquals("report_rsp", out.get("code"));
        assertNum(7, out.get("hit"));
    }

    @Test
    public void aTextLabelSelectsItsCaseOnEncode() {
        Schema schema = Schema.fromYaml("name: p\nendian: big\nfields:\n"
                + "  - {name: code, type: u8, lookup: {1: report, 129: report_rsp}}\n"
                + select(129));
        byte[] payload = {(byte) 0x81, 0x07};
        assertArrayEquals(payload, schema.encode(schema.decode(payload)).getPayload());
    }

    // CR-2026-132: an enum binds its code, and a tag component binds as any field does.

    private static Map<String, Object> decodeEnum(String extra, String sites, int... payload) {
        byte[] b = new byte[payload.length];
        for (int i = 0; i < payload.length; i++) b[i] = (byte) payload[i];
        return Schema.fromYaml("name: p\nendian: big\nfields:\n"
                + "  - {name: code, type: enum, base: u8, var: c, values: {1: \"on\", 2: \"off\"}"
                + extra + "}\n" + sites).decode(b);
    }

    @Test
    public void anEnumBindsItsCode() {
        assertNum(4, decodeEnum("", COMPUTE, 2).get("doubled"));
        assertNum(2, decodeEnum("", VAR, 2).get("held"));
        assertNum(7, decodeEnum("", select(2), 2, 7).get("hit"));
        Map<String, Object> m = decodeEnum("", TEMPLATE, 2, 7);
        assertEquals("off", m.get("code"));
        assertNum(7, m.get("reading_off"));
        // A default name binds the code read.
        assertNum(10, decodeEnum(", default: spare", COMPUTE, 5).get("doubled"));
        assertNum(7, decodeEnum(", default: spare", TEMPLATE, 5, 7).get("reading_spare"));
    }

    @Test
    public void anEnumSelectsItsCaseOnEncode() {
        Schema schema = Schema.fromYaml("name: p\nendian: big\nfields:\n"
                + "  - {name: code, type: enum, base: u8, values: {1: \"on\", 2: \"off\"}}\n"
                + select(2));
        byte[] payload = {0x02, 0x07};
        assertArrayEquals(payload, schema.encode(schema.decode(payload)).getPayload());
    }

    private static Map<String, Object> decodeTag(String lookup, int key, int... payload) {
        byte[] b = new byte[payload.length];
        for (int i = 0; i < payload.length; i++) b[i] = (byte) payload[i];
        return Schema.fromYaml("name: p\nendian: big\nfields:\n"
                + "  - tlv:\n      tag_fields:\n"
                + "        - {name: side, type: u8, var: s, lookup: " + lookup + "}\n"
                + "        - {name: kind, type: u8}\n"
                + "      tag_key: [side, kind]\n      cases:\n        \"[*, 0x10]\":\n"
                + "          - {name: reading, type: u8, name_from: \"reading_${s}\"}\n"
                + "          - {name: doubled, type: number, compute: {op: mul, a: $side, b: 2}}\n"
                + "          - match:\n              field: $s\n              cases:\n"
                + "                " + key + ": [{name: hit, type: u8}]\n"
                + "              default: [{name: other, type: u8}]\n").decode(b);
    }

    @Test
    public void aTagComponentWithATextLabelBindsItsNumber() {
        Map<String, Object> m = decodeTag("{1: left, 2: right}", 2, 2, 0x10, 7, 5);
        assertNum(7, m.get("reading_right"));
        assertNum(4, m.get("doubled"));
        assertNum(5, m.get("hit"));
        assertFalse(m.containsKey("other"));
    }

    @Test
    public void aTagComponentWithANumericLabelBindsIt() {
        Map<String, Object> m = decodeTag("[0, 125, 250]", 125, 1, 0x10, 7, 5);
        assertNum(7, m.get("reading_125"));
        assertNum(250, m.get("doubled"));
        assertNum(5, m.get("hit"));
    }
}
