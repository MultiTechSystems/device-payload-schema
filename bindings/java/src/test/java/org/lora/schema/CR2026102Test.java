package org.lora.schema;

import org.junit.jupiter.api.Test;
import java.util.Map;

import static org.junit.jupiter.api.Assertions.*;

/**
 * CR-2026-102: a field's ipso is a mapping carrying object, and its senml a mapping
 * (PS-510), wherever the field sits. The same cases are in
 * tests/test_cr_2026_102_ipso_senml_mappings.py, cr102_test.go and CR2026_102Tests.
 */
public class CR2026102Test {
    static final String IPSO = "The ipso of 't' is a mapping that carries `object`, such as {object: 3303}; "
            + "a bare object number is not a shorthand (PS-510)";
    static final String SENML = "The senml of 't' is a mapping, such as {unit: Cel} (PS-510)";

    static final Map<String, String> CONTEXTS = Map.of(
        "top", "fields:\n  - FIELD\n",
        "port", "ports:\n  1:\n    fields:\n      - FIELD\n",
        "match", "fields:\n  - {name: kind, type: u8}\n  - match:\n      field: $kind\n      cases:\n        1:\n          - FIELD\n",
        "tlv", "fields:\n  - tlv:\n      tag_size: 1\n      cases:\n        1:\n          - FIELD\n",
        "object", "fields:\n  - name: o\n    type: object\n    fields:\n      - FIELD\n",
        "repeat", "fields:\n  - name: r\n    type: repeat\n    count: 2\n    fields:\n      - FIELD\n",
        "byte_group", "fields:\n  - byte_group:\n      size: 1\n      fields:\n        - BITS\n",
        "flagged", "fields:\n  - {name: mask, type: u8}\n  - flagged:\n      field: mask\n      groups:\n"
            + "        - bit: 0\n          fields:\n            - FIELD\n",
        "definition", "definitions:\n  d:\n    fields:\n      - FIELD\nfields:\n  - $ref: '#/definitions/d'\n"
    );

    static String schema(String context, String annotation) {
        return "name: p\n" + CONTEXTS.get(context)
                .replace("FIELD", "{name: t, type: u8, " + annotation + "}")
                .replace("BITS", "{name: t, type: 'u8[0:7]', " + annotation + "}");
    }

    @Test
    public void aNonMappingAnnotationIsRefused() {
        String[][] refused = {
            {"ipso: 3303", IPSO}, {"ipso: '3303'", IPSO}, {"ipso: null", IPSO},
            {"ipso: [3303]", IPSO}, {"ipso: {resource: 5700}", IPSO},
            {"senml: 'Cel'", SENML}, {"senml: null", SENML}, {"senml: ['Cel']", SENML},
        };
        for (String context : CONTEXTS.keySet()) {
            for (String[] c : refused) {
                Exception e = assertThrows(SchemaException.class,
                        () -> Schema.fromYaml(schema(context, c[0])), context + ", " + c[0]);
                assertEquals(c[1], e.getMessage(), context + ", " + c[0]);
            }
        }
    }

    @Test
    public void aMappingAnnotationIsAccepted() {
        String[] accepted = {
            "ipso: {object: 3303}", "ipso: {object: 3303, instance: 1, resource: 5700}",
            "senml: {unit: Cel}", "senml: {name: x, unit: Cel}",
        };
        for (String context : CONTEXTS.keySet()) {
            for (String annotation : accepted) {
                assertDoesNotThrow(() -> Schema.fromYaml(schema(context, annotation)),
                        context + ", " + annotation);
            }
        }
    }

    @Test
    public void anUnreferencedDefinitionIsHeldToItToo() {
        Exception e = assertThrows(SchemaException.class, () -> Schema.fromYaml(
                "name: p\ndefinitions:\n  d:\n    fields:\n      - {name: t, type: u8, ipso: 3303}\n"
                + "fields:\n  - {name: a, type: u8}\n"));
        assertEquals(IPSO, e.getMessage());
    }
}
