package org.lora.schema;

import org.junit.jupiter.api.DynamicTest;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.TestFactory;
import org.yaml.snakeyaml.Yaml;

import java.io.IOException;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.ArrayList;
import java.util.HashSet;
import java.util.HexFormat;
import java.util.List;
import java.util.Map;

import static org.junit.jupiter.api.Assertions.*;

/**
 * CR-2026-162 (PS-572 to PS-574): the decode record. {@code interpret(..., true)} adds
 * {@code _decode}; an encoder given it follows it and rejects input contradicting it;
 * where neither the input nor the record gives a match's case, this binding derives it.
 * The cases are shared with every implementation in tests/cr-2026-162-cases.yaml.
 */
public class CR2026162Test {

    @SuppressWarnings("unchecked")
    private static List<Map<String, Object>> cases() throws IOException {
        Path file = Path.of("..", "..", "tests", "cr-2026-162-cases.yaml");
        Map<String, Object> doc = new Yaml().load(Files.readString(file));
        return (List<Map<String, Object>>) doc.get("cases");
    }

    /** JSON values equal, numbers by value, a boolean only a boolean. */
    static boolean same(Object want, Object got) {
        if (want instanceof Boolean || got instanceof Boolean) return want != null && want.equals(got);
        if (want instanceof Number a && got instanceof Number b) return a.doubleValue() == b.doubleValue();
        if (want instanceof Map<?, ?> a && got instanceof Map<?, ?> b) {
            if (!new HashSet<>(stringKeys(a)).equals(new HashSet<>(stringKeys(b)))) return false;
            for (Map.Entry<?, ?> e : a.entrySet()) {
                if (!same(e.getValue(), getByString(b, String.valueOf(e.getKey())))) return false;
            }
            return true;
        }
        if (want instanceof List<?> a && got instanceof List<?> b) {
            if (a.size() != b.size()) return false;
            for (int i = 0; i < a.size(); i++) if (!same(a.get(i), b.get(i))) return false;
            return true;
        }
        return want == null ? got == null : want.equals(got);
    }

    private static List<String> stringKeys(Map<?, ?> m) {
        List<String> out = new ArrayList<>();
        for (Object k : m.keySet()) out.add(String.valueOf(k));
        return out;
    }

    private static Object getByString(Map<?, ?> m, String key) {
        for (Map.Entry<?, ?> e : m.entrySet()) if (String.valueOf(e.getKey()).equals(key)) return e.getValue();
        return null;
    }

    private static Integer fport(Map<String, Object> c) {
        return c.get("fport") instanceof Number n ? n.intValue() : null;
    }

    private static EncodeResult encode(Schema schema, Map<String, Object> data, Integer fport,
                                       Map<String, Object> record) {
        return fport == null ? schema.encode(data, record)
                : schema.encodeWithPort(data, fport, null, record);
    }

    @SuppressWarnings("unchecked")
    @TestFactory
    List<DynamicTest> everySharedCase() throws IOException {
        List<DynamicTest> tests = new ArrayList<>();
        for (Map<String, Object> c : cases()) {
            tests.add(DynamicTest.dynamicTest(String.valueOf(c.get("name")), () -> {
                Schema schema = Schema.fromYaml(String.valueOf(c.get("schema")));
                Integer fport = fport(c);
                Map<String, Object> decoded = null;
                if (c.get("decode") instanceof Map<?, ?> d) {
                    byte[] payload = HexFormat.of().parseHex(String.valueOf(d.get("payload")));
                    decoded = schema.interpret(payload, new InputContext(fport, null, null), true);
                    Map<String, Object> record = (Map<String, Object>) decoded.get("_decode");
                    assertNotNull(record, "interpret with the record carries _decode");
                    if (d.containsKey("record")) {
                        Object want = new Yaml().load(String.valueOf(d.get("record")));
                        assertTrue(same(want, record), "want " + want + ", got " + record);
                    }
                    if (d.containsKey("matches")) {
                        List<Object> want = new Yaml().load(String.valueOf(d.get("matches")));
                        assertEquals(new HashSet<>(want),
                                new HashSet<>(((Map<String, Object>) record.get("matches")).keySet()));
                    }
                    Map<String, Object> plain = fport == null ? schema.decode(payload)
                            : schema.decodeWithPort(payload, fport);
                    assertFalse(plain.containsKey("_decode"), "decode() never carries _decode");
                    if (Boolean.TRUE.equals(d.get("round_trip"))) {
                        EncodeResult out = encode(schema, decoded, fport, null);
                        assertTrue(out.isSuccess(), out.getErrors().toString());
                        assertEquals(String.valueOf(d.get("payload")), HexFormat.of().formatHex(out.getPayload()));
                    }
                }
                if (c.get("encode") instanceof List<?> steps) {
                    for (Object s : steps) {
                        Map<String, Object> step = (Map<String, Object>) s;
                        Map<String, Object> input = new Yaml().load(String.valueOf(step.get("input")));
                        Map<String, Object> record = null;
                        if ("decoded".equals(step.get("record"))) {
                            record = (Map<String, Object>) decoded.get("_decode");
                        } else if (step.get("record") != null) {
                            record = new Yaml().load(String.valueOf(step.get("record")));
                        }
                        EncodeResult result = encode(schema, input, fport, record);
                        if (step.containsKey("error")) {
                            String want = String.valueOf(step.get("error"));
                            assertTrue(result.getErrors().stream().anyMatch(e -> e.contains(want)),
                                    "want '" + want + "', got " + result.getErrors());
                            assertEquals(0, result.getPayload().length, "a rejected input writes no payload");
                        } else {
                            assertTrue(result.isSuccess(), result.getErrors().toString());
                            assertEquals(String.valueOf(step.get("payload")),
                                    HexFormat.of().formatHex(result.getPayload()));
                        }
                    }
                }
            }));
        }
        return tests;
    }

    @Test
    void aFieldNamedDecodeIsReserved() {
        // PS-433 as amended: `_decode` joins the reserved interpreter-metadata names.
        assertThrows(SchemaException.class, () -> Schema.fromYaml(
                "name: t\nfields:\n  - {name: _decode, type: u8}\n"));
    }
}
