package org.lora.schema;

import org.junit.jupiter.api.Test;
import java.util.HexFormat;
import java.util.List;
import java.util.Map;

import static org.junit.jupiter.api.Assertions.*;

/**
 * CR-2026-071 (a field's arithmetic pipeline) and CR-2026-073 (one operation per stage).
 *
 * <p>The decode order itself - source, modifiers, transform, lookup, with a failed guard's
 * {@code else} reported as declared - is held to every implementation by three corpus
 * fixtures: {@code _language-conformance/arithmetic-order-computed.yaml},
 * {@code arithmetic-order-read.yaml} and {@code guard-else-as-declared.yaml}. What a
 * fixture cannot state is a schema that must be refused, so the rejections are here.
 * Mirrors tests/test_cr_2026_071_073.py.
 */
public class CR2026071073Test {

    private static final String GUARD = "{when: [{field: $x, gt: 0}], else: 0}";

    private static String schemaOf(String field) {
        return "name: probe\nfields:\n- {name: x, type: u8}\n- " + field + "\n";
    }

    private static Map<String, Object> decode(String yaml, String hex) {
        return Schema.fromYaml(yaml).decode(HexFormat.of().parseHex(hex));
    }

    private static void assertRejected(String yaml, String tag, String held) {
        SchemaException e = assertThrows(SchemaException.class, () -> Schema.fromYaml(yaml), yaml);
        assertTrue(e.getMessage().contains(tag), e.getMessage());
        if (held != null) assertTrue(e.getMessage().contains(held), e.getMessage());
    }

    @Test
    public void aGuardOffAComputedFieldIsRejected() {
        // PS-445: ignored with success before - a guard on a u8 decoded every value.
        for (String field : List.of(
                "{name: v, type: u8, guard: " + GUARD + "}",
                "{name: v, type: s16, guard: " + GUARD + "}",
                // A literal reads nothing, but has no source a guard could precede either.
                "{name: v, type: number, value: 3, guard: " + GUARD + "}")) {
            assertRejected(schemaOf(field), "PS-445", null);
        }
    }

    @Test
    public void aGuardOnAComputedFieldIsAccepted() {
        for (String source : List.of("ref: $x", "compute: {op: add, a: $x, b: 1}")) {
            Map<String, Object> out = decode(
                    schemaOf("{name: v, type: number, " + source + ", guard: " + GUARD + "}"), "03");
            assertTrue(out.containsKey("v"), out.toString());
        }
    }

    @Test
    public void aStageWithoutExactlyOneOperationIsRejected() {
        // PS-452: {add: 1, mult: 2} decoded here as mult-then-add, 1 to 3.
        String[][] cases = {
            {"{add: 1, mult: 2}", "holds add, mult"},
            {"{div: 2, sqrt: true}", "holds div, sqrt"},
            {"{op: round, add: 1}", "holds op, add"},
            {"{}", "holds none"},
            {"{decimals: 2}", "holds none"},
            {"{ties: away}", "holds none"},
        };
        for (String[] c : cases) {
            assertRejected("name: probe\nfields:\n- {name: v, type: u8, transform: [" + c[0] + "]}\n",
                    "PS-452", c[1]);
        }
    }

    @Test
    public void unknownStageKeysStayPs390() {
        // An unknown key beside a known operation was accepted: noneMatch saw the `add`.
        assertRejected("name: probe\nfields:\n- {name: v, type: u8, transform: [{add: 1, sub: 3}]}\n",
                "PS-390", "sub");
        assertRejected("name: probe\nfields:\n- {name: v, type: u8, transform: [{round: 1}]}\n",
                "PS-390", null);
    }

    @Test
    public void theParametersOfAnOpStageArePartOfItsOneOperation() {
        Map<String, Object> out = decode("name: probe\nfields:\n- {name: v, type: u8, transform: "
                + "[{op: round, decimals: 1, ties: away}, {add: 1}]}\n", "03");
        assertEquals(Map.of("v", 4L), out);
    }

    @Test
    public void aStageIsCheckedWhereverTheFieldSits() {
        assertRejected("name: probe\nfields:\n- byte_group: [{name: hi, type: 'u8[4:7]', "
                + "transform: [{add: 1, div: 2}]}, {name: lo, type: 'u8[0:3]', consume: 1}]\n",
                "PS-452", null);
        assertRejected("name: probe\nfields:\n- {name: k, type: u8, var: k}\n"
                + "- match: {field: $k, cases: {1: [{name: a, type: u8, guard: "
                + "{when: [{field: $k, gt: 0}], else: 0}}]}}\n", "PS-445", null);
    }

    @Test
    public void computeCarriesItsModifiers() {
        // PS-443: `mult: 10` beside a compute was dropped, 2 where 20 is meant.
        Map<String, Object> out = decode("name: probe\nfields:\n- {name: g, type: u8}\n"
                + "- {name: v, type: number, compute: {op: add, a: $g, b: 0}, mult: 10}\n", "02");
        assertEquals(20L, out.get("v"));
    }

    @Test
    public void aFailedGuardsElseReachesNoLookup() {
        // PS-444: `else: 7` beside a sequence lookup reported the label at index 7.
        Map<String, Object> out = decode("name: probe\nfields:\n- {name: g, type: u8}\n"
                + "- {name: v, type: number, ref: $g, guard: {when: [{field: $g, gt: 0}], else: 7}, "
                + "lookup: [a, b, c, d, e, f, g, h]}\n", "00");
        assertEquals(7L, ((Number) out.get("v")).longValue());
    }

    @Test
    public void aFractionalValueIndexesNoEntryOfASequence() {
        // PS-105: an integral computed value is its index; one with a fraction is none.
        String yaml = schemaOf("{name: v, type: number, ref: $x, div: 2, lookup: [a, b, c, d]}");
        assertEquals("c", decode(yaml, "04").get("v"));
        SchemaException.DecodeException e = assertThrows(SchemaException.DecodeException.class,
                () -> decode(yaml, "03"));
        assertTrue(e.getMessage().contains("PS-105"), e.getMessage());
    }

    @Test
    public void aFractionalValueMatchesNoMappingKey() {
        // Not truncated to key 2: the field is absent (PS-269).
        String yaml = schemaOf("{name: v, type: number, ref: $x, div: 2, lookup: {1: one, 2: two}}");
        assertEquals("two", decode(yaml, "04").get("v"));
        assertFalse(decode(yaml, "05").containsKey("v"));
    }

    @Test
    public void aValueOutsideItsValidRangeIsFlagged() {
        // PS-131: reported with `_quality` and a warning, as Go and C# report it. Nothing
        // was produced before: {t=90}, unmarked.
        String yaml = "name: probe\nfields:\n- {name: t, type: s16, div: 10, valid_range: [-40, 85]}\n";
        Map<String, Object> out = decode(yaml, "0384");
        assertEquals(90L, out.get("t"));
        assertEquals(Map.of("t", "out_of_range"), out.get("_quality"));
        assertEquals(List.of("t: value 90 outside valid range [-40, 85]"), out.get("_warnings"));

        Map<String, Object> in = decode(yaml, "00E7");
        assertEquals(Map.of("t", "good"), in.get("_quality"));
        assertFalse(in.containsKey("_warnings"));
    }

    @Test
    public void anOmittedReadingJoinsQualityWhereItIsProduced() {
        // PS-427/PS-428 with PS-182: the mark is recorded only when `_quality` is produced.
        String withRange = "name: probe\nfields:\n- {name: a, type: u8, valid_range: [0, 100]}\n"
                + "- {name: b, type: u8, sentinel: [255]}\n";
        assertEquals(Map.of("a", "good", "b", "absent"), decode(withRange, "05FF").get("_quality"));
        String without = "name: probe\nfields:\n- {name: b, type: u8, sentinel: [255]}\n";
        assertFalse(decode(without, "FF").containsKey("_quality"));
    }
}
