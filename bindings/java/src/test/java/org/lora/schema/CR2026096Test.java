package org.lora.schema;

import org.junit.jupiter.api.Test;
import org.junit.jupiter.params.ParameterizedTest;
import org.junit.jupiter.params.provider.Arguments;
import org.junit.jupiter.params.provider.CsvSource;
import org.junit.jupiter.params.provider.MethodSource;

import java.util.ArrayList;
import java.util.List;
import java.util.Map;
import java.util.stream.Stream;

import static org.junit.jupiter.api.Assertions.*;

/**
 * CR-2026-096 (PS-490 to PS-497), with PS-175 to PS-181, PS-340 to PS-342, PS-371 to
 * PS-376, CR-2026-088's PS-480/481 and CR-2026-095's PS-489: the interpreter output's
 * {@code _meta}. Mirrors tests/test_cr_2026_096_meta.py. The fixtures
 * {@code _language-conformance/meta-*.yaml} hold the output itself, through the corpus
 * runner; what a fixture cannot state is here.
 */
public class CR2026096Test {

    private static final String FLAT = "name: flat\nfields:\n"
            + "  - {name: a, type: u8, unit: V}\n"
            + "  - {name: b, type: u8}\n";

    private static final byte[] TWO = {1, 2};

    private static InputContext none() {
        return new InputContext(null, null, null);
    }

    @Test
    void decodeIsUnchanged() {
        // td-tools reads decode(); _meta comes only from interpret().
        Schema schema = Schema.fromYaml(FLAT);
        Map<String, Object> decoded = schema.decode(TWO);
        assertEquals(List.of("a", "b"), new ArrayList<>(decoded.keySet()));
        assertTrue(schema.interpret(TWO, none()).containsKey("_meta"));
    }

    @Test
    void metaIsLastAndPresentWithoutAnnotations() {
        // PS-180: produced with every decoded result, whether or not the schema annotates.
        Map<String, Object> out = Schema.fromYaml(FLAT).interpret(TWO, none());
        List<String> keys = new ArrayList<>(out.keySet());
        assertEquals("_meta", keys.get(keys.size() - 1));
        assertEquals(Map.of("schema", "flat",
                        "fields", Map.of("a", Map.of("type", "u8", "unit", "V"),
                                "b", Map.of("type", "u8"))),
                out.get("_meta"));
    }

    @Test
    void aFailedDecodeHasNoMeta() {
        // PS-497: the interpret fails exactly as the decode does.
        Schema schema = Schema.fromYaml(FLAT);
        assertThrows(SchemaException.class, () -> schema.interpret(new byte[] {1}, none()));
    }

    @Test
    @SuppressWarnings("unchecked")
    void fPortIsAsSuppliedAndOnlyWhenSupplied() {
        Schema schema = Schema.fromYaml(FLAT);
        Map<String, Object> meta = (Map<String, Object>) schema.interpret(TWO,
                new InputContext(5, null, null)).get("_meta");
        assertEquals(5, meta.get("fPort"));
        meta = (Map<String, Object>) schema.interpret(TWO, none()).get("_meta");
        assertFalse(meta.containsKey("fPort"));             // PS-341
    }

    @Test
    void theRunnerTakesFPortFromTheVectorOrItsContext() {
        assertEquals(5, CorpusConformanceTest.inputContext(Map.of("fPort", 5)).fPort());
        assertEquals(5, CorpusConformanceTest.inputContext(Map.of("fport", 5)).fPort());
        assertEquals(6, CorpusConformanceTest.inputContext(
                Map.of("input_metadata", Map.of("fPort", 6))).fPort());
        assertEquals(5, CorpusConformanceTest.inputContext(
                Map.of("fPort", 5, "input_metadata", Map.of("fPort", 6))).fPort());
        assertNull(CorpusConformanceTest.inputContext(Map.of()).fPort());
    }

    @Test
    @SuppressWarnings("unchecked")
    void aDefaultPortStillReportsTheReceivedFPort() {
        // PS-340, PS-342.
        Schema schema = Schema.fromYaml("name: p\nports:\n"
                + "  1: {fields: [{name: battery, type: u16, unit: mV}]}\n"
                + "  default: {fields: [{name: level, type: u8}]}\n");
        Map<String, Object> meta = (Map<String, Object>) schema.interpret(new byte[] {90},
                new InputContext(9, null, null)).get("_meta");
        assertEquals(9, meta.get("fPort"));
        assertEquals(Map.of("level", Map.of("type", "u8")), meta.get("fields"));
        // No FPort on a ports schema fails as decode() does (PS-459).
        assertThrows(SchemaException.class, () -> schema.interpret(new byte[] {90}, none()));
    }

    @ParameterizedTest
    @CsvSource(delimiter = '|', nullValues = "NULL", value = {
        "0011223344556677|0011223344556677",
        "00-11-22-33-44-55-66-AA|00112233445566aa",
        "00:11:22:33:44:55:66:AA|00112233445566aa",
        "00 11 22 33 44 55 66 aa|00112233445566aa",
        "0011|NULL",
        "00112233445566GG|NULL",
    })
    void devEuiIsNormalised(String text, String eui) {
        assertEquals(eui, Meta.normaliseDevEui(text));      // PS-496
        assertNull(Meta.normaliseDevEui(1234));
    }

    @Test
    void aMalformedDevEuiFailsWithNoMeta() {
        Schema schema = Schema.fromYaml(FLAT);
        SchemaException e = assertThrows(SchemaException.class,
                () -> schema.interpret(TWO, new InputContext(null, null, "nonsense")));
        assertEquals("devEUI 'nonsense' is not 16 hexadecimal digits (PS-496)", e.getMessage());
    }

    @Test
    void aMalformedRecvTimeFailsWithNoMeta() {
        Schema schema = Schema.fromYaml(FLAT);
        SchemaException e = assertThrows(SchemaException.class,
                () -> schema.interpret(TWO, new InputContext(null, "yesterday", null)));
        assertEquals("recvTime 'yesterday' is not an ISO 8601 time or a number of seconds (PS-495)",
                e.getMessage());
    }

    static Stream<Arguments> recvTimes() {
        return Stream.of(
                Arguments.of("2026-08-26T12:00:00Z", 1787745600L),
                Arguments.of("2026-08-26T12:00:00.123Z", 1787745600.123),
                Arguments.of("2026-08-26T12:00:00.120000Z", 1787745600.12),
                Arguments.of("2026-08-26T12:00:00.1234Z", 1787745600.123),
                Arguments.of("2026-08-26T12:00:00.1235Z", 1787745600.124),
                Arguments.of("2026-08-26T12:00:00.1225Z", 1787745600.122),
                Arguments.of("2026-08-26T12:00:00.9996Z", 1787745601L),
                Arguments.of("2026-08-26T12:00:00.000Z", 1787745600L),
                Arguments.of("2026-08-26T14:00:00+02:00", 1787745600L),
                Arguments.of("2026-08-26T07:30:00-0430", 1787745600L),
                Arguments.of("2026-08-26T12:00:00", 1787745600L),
                Arguments.of(1787745600.5, 1787745600.5),
                Arguments.of("yesterday", null),
                Arguments.of(true, null));
    }

    @ParameterizedTest
    @MethodSource("recvTimes")
    void recvTimeIsUnixSeconds(Object recv, Object seconds) {
        // PS-177, PS-495: the value and whether it is a whole number or a fraction.
        Object got = Meta.rxTimeSeconds(recv);
        assertEquals(seconds, got);
        if (seconds != null) assertEquals(seconds.getClass(), got.getClass());
    }

    private static String agreement(String second) {
        return "name: x\nfields:\n"
                + "  - {name: _k, type: u8}\n"
                + "  - match:\n"
                + "      field: $_k\n"
                + "      cases:\n"
                + "        1: [{name: r, type: u8, unit: Cel}]\n"
                + "        2: [{name: r, " + second + "}]\n";
    }

    @ParameterizedTest
    @CsvSource(delimiter = '|', value = {
        "type: s16, unit: Cel",
        "type: u8, unit: Cel, description: other",
    })
    void declarationsMayDifferInTypeAndDescription(String second) {
        assertDoesNotThrow(() -> Schema.fromYaml(agreement(second)));
    }

    @ParameterizedTest
    @CsvSource(delimiter = '|', value = {
        "type: u8, unit: K|unit",
        "type: u8, unit: Cel, senml: {name: t}|senml",
        "type: u8, unit: Cel, ipso: {object: 3303}|ipso",
    })
    void declarationsThatDisagreeOnMeaningAreRejected(String second, String differs) {
        // PS-491: unit, senml and ipso must agree.
        SchemaException e = assertThrows(SchemaException.class, () -> Schema.fromYaml(agreement(second)));
        assertEquals("Field 'r' is declared 2 times in fields and its declarations differ in "
                + differs + "; the declarations of one reported name must agree on unit, senml "
                + "and ipso (PS-491)", e.getMessage());
    }

    @Test
    void namesOnDifferentPortsNeedNotAgree() {
        // PS-491's scope is one field list.
        assertDoesNotThrow(() -> Schema.fromYaml("name: x\nports:\n"
                + "  1: {fields: [{name: r, type: u8, unit: Cel}]}\n"
                + "  2: {fields: [{name: r, type: u8, unit: K}]}\n"));
    }

    @Test
    void aNestedListIsNamedInTheMessage() {
        SchemaException e = assertThrows(SchemaException.class, () -> Schema.fromYaml(
                "name: x\nports:\n  3:\n    fields:\n"
                        + "      - name: o\n        type: object\n        fields:\n"
                        + "          - {name: r, type: u8, unit: Cel}\n"
                        + "          - {name: r, type: u8, unit: K}\n"));
        assertTrue(e.getMessage().startsWith("Field 'r' is declared 2 times in port 3/o and"),
                e.getMessage());
    }

    @Test
    @SuppressWarnings("unchecked")
    void theProducerOfAValueDescribesIt() {
        // PS-490: one name declared in two cases is described by the one that decoded.
        Schema schema = Schema.fromYaml(agreement("type: s16, unit: Cel, description: two"));
        Map<String, Object> meta = (Map<String, Object>) schema.interpret(new byte[] {2, (byte) 0xFF, (byte) 0x9C},
                none()).get("_meta");
        assertEquals(Map.of("r", Map.of("type", "s16", "unit", "Cel", "description", "two")),
                meta.get("fields"));
        meta = (Map<String, Object>) schema.interpret(new byte[] {1, 5}, none()).get("_meta");
        assertEquals(Map.of("r", Map.of("type", "u8", "unit", "Cel")), meta.get("fields"));
    }

    @Test
    void metaMismatchIsExact() {
        assertNull(CorpusConformanceTest.metaMismatch(Map.of("a", 1), Map.of("a", 1.0), "_meta"));
        assertNotNull(CorpusConformanceTest.metaMismatch(Map.of("a", 1), Map.of("a", 1, "b", 2), "_meta"));
        assertNotNull(CorpusConformanceTest.metaMismatch(Map.of("a", 1, "b", 2), Map.of("a", 1), "_meta"));
        assertNotNull(CorpusConformanceTest.metaMismatch(Map.of("a", true), Map.of("a", 1), "_meta"));
        assertNotNull(CorpusConformanceTest.metaMismatch(Map.of("a", List.of(1, 2)),
                Map.of("a", List.of(2, 1)), "_meta"));
        String detail = CorpusConformanceTest.metaMismatch(Map.of("f", Map.of("x", Map.of("type", "u8"))),
                Map.of("f", Map.of("x", Map.of("type", "s8"))), "_meta");
        assertTrue(detail.contains("_meta.f.x.type"), detail);
    }
}
