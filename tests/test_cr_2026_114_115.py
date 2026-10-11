"""CR-2026-114 and CR-2026-115 (la-payload-schema main c932961) in the reference.

PS-535: a read or skip past the end of the payload is an error naming the innermost
field that made it, in the spec prototype's words, so that the five implementations
compare equal strings. PS-534, PS-538: an ascii value drops trailing 0x00 only and
reports a byte above 0x7F as its ISO 8859-1 character, with PS-073's warning. PS-537:
it encodes padded with 0x00, rejecting a value too long or holding a character above
U+00FF, and writes U+0080..U+00FF as the byte of the same number with a warning.

Short reads and rejected values are failures, which no corpus vector kind expects, so
they are pinned here; `_language-conformance/ascii-latin1.yaml` pins the decodes.
"""

import pytest

from schema_interpreter import SchemaInterpreter


def decode(fields, payload_hex):
    schema = {'name': 'probe', 'version': 1, 'endian': 'big', 'fields': fields}
    return SchemaInterpreter(schema).decode(bytes.fromhex(payload_hex))


def encode(fields, data):
    schema = {'name': 'probe', 'version': 1, 'endian': 'big', 'fields': fields}
    return SchemaInterpreter(schema).encode(data)


def underflow(label, need, offset, remaining):
    return (f"Field {label}: Buffer underflow: need {need} bytes at offset {offset}, "
            f"but only {remaining} remaining (PS-535)")


def peek(label, offset):
    return f"Field {label}: Buffer underflow at peek offset {offset} (PS-535)"


SHORT_READS = [
    ('integer', [{'name': 'n', 'type': 'u16'}], '01', underflow("'n'", 2, 0, 1)),
    ('unnamed skip', [{'type': 'skip', 'length': 3}], '',
     underflow('an unnamed skip', 3, 0, 0)),
    ('named skip', [{'name': 'pad', 'type': 'skip', 'length': 3}], '00',
     underflow("'pad'", 3, 0, 1)),
    ('ascii', [{'name': 's', 'type': 'ascii', 'length': 4}], '4142',
     underflow("'s'", 4, 0, 2)),
    ('hex', [{'name': 'h', 'type': 'hex', 'length': 4}], '41', underflow("'h'", 4, 0, 1)),
    ('bytes', [{'name': 'b', 'type': 'bytes', 'length': 4}], '41',
     underflow("'b'", 4, 0, 1)),
    ('float', [{'name': 'f', 'type': 'f32'}], '0102', underflow("'f'", 4, 0, 2)),
    ('word-ordered', [{'name': 'w', 'type': 'u32le16'}], '0102',
     underflow("'w'", 4, 0, 2)),
    ('udec', [{'name': 'ud', 'type': 'udec'}], '', underflow("'ud'", 1, 0, 0)),
    ('internal', [{'name': '_k', 'type': 'u16'}], '01', underflow("'_k'", 2, 0, 1)),
    ('length from a field', [{'name': 'n', 'type': 'u8'},
                             {'name': 's', 'type': 'ascii', 'length': '$n'}], '0541',
     underflow("'s'", 5, 1, 1)),
    ('enum', [{'name': 'e', 'type': 'enum', 'base': 'u16', 'values': {1: 'one'}}], '00',
     underflow("'e'", 2, 0, 1)),
    ('object member', [{'name': 'o', 'type': 'object',
                        'fields': [{'name': 'x', 'type': 'u16'}]}], '01',
     underflow("'x'", 2, 0, 1)),
    ('counted element member', [{'name': 'r', 'type': 'repeat', 'count': 2,
                                 'fields': [{'name': 'v', 'type': 'u16'}]}], '000102',
     underflow("'v'", 2, 2, 1)),
    ('named element', [{'name': 'r', 'type': 'repeat', 'count': 2,
                        'element': {'name': 'v', 'type': 'u16'}}], '000102',
     underflow("'v'", 2, 2, 1)),
    # CR-2026-111: a nameless element is decoded as a record named `element`.
    ('unnamed element', [{'name': 'r', 'type': 'repeat', 'count': 2,
                          'element': {'type': 'u16'}}], '000102',
     underflow("'element'", 2, 2, 1)),
    ('case body member', [{'name': 'k', 'type': 'u8', 'var': 'k'},
                          {'match': {'field': '$k',
                                     'cases': {1: [{'name': 'm', 'type': 'u16'}]}}}],
     '0101', underflow("'m'", 2, 1, 1)),
    ('tagged entry member', [{'tlv': {'tag_size': 1,
                                      'cases': {1: [{'name': 't', 'type': 'u16'}]}}}],
     '0101', underflow("'t'", 2, 1, 1)),
    ('tagged entry member before a reserve',
     [{'tlv': {'tag_size': 1, 'reserve': 1,
               'cases': {1: [{'name': 't', 'type': 'u16'}]}}}],
     '010102', underflow("'t'", 2, 1, 1)),
    ('flag group member', [{'name': 'fl', 'type': 'u8'},
                           {'flagged': {'field': 'fl', 'groups': [
                               {'bit': 0, 'fields': [{'name': 'g', 'type': 'u16'}]}]}}],
     '0101', underflow("'g'", 2, 1, 1)),
    ('inline discriminator', [{'match': {'length': 2, 'name': 'kind',
                                         'cases': {1: [{'name': 'm', 'type': 'u8'}]}}}],
     '00', underflow("'kind'", 2, 0, 1)),
    ('unnamed inline discriminator', [{'match': {'length': 2, 'cases': {
        1: [{'name': 'm', 'type': 'u8'}]}}}], '00',
     underflow('an unnamed match', 2, 0, 1)),
    ('bit range', [{'name': 'b', 'type': 'u16[0:3]', 'consume': 2}], '01', peek("'b'", 0)),
    ('bool', [{'name': 'bo', 'type': 'bool', 'bit': 1}], '', peek("'bo'", 0)),
    ('bit range consuming past the end', [{'name': 'b', 'type': 'u8[0:3]', 'consume': 2}],
     '01', underflow("'b'", 2, 0, 1)),
    ('bool consuming past the end', [{'name': 'bo', 'type': 'bool', 'consume': 2}], '01',
     underflow("'bo'", 2, 0, 1)),
    ('shared-byte member', [{'name': 'x', 'type': 'u8'},
                            {'byte_group': {'size': 2, 'fields': [
                                {'name': 'a', 'type': 'u8[0:3]'},
                                {'name': 'c', 'type': 'u16[4:7]'}]}}],
     '0101', peek("'c'", 1)),
    ('shared bytes wider than every member', [{'byte_group': {'size': 2, 'fields': [
        {'name': 'a', 'type': 'u8[0:3]'}]}}], '01',
     underflow('an unnamed byte_group', 2, 0, 1)),
]


class TestReadPastTheEnd:
    """PS-535."""

    @pytest.mark.parametrize('label, fields, payload, message', SHORT_READS,
                             ids=[case[0] for case in SHORT_READS])
    def test_the_error_names_the_innermost_field(self, label, fields, payload, message):
        result = decode(fields, payload)
        assert result.errors == [message]

    def test_remaining_at_the_end_is_not_an_error(self):
        result = decode([{'name': 'a', 'type': 'u8'},
                         {'name': 'rest', 'type': 'hex', 'length': 'remaining'}], '01')
        assert result.errors == []
        assert result.data == {'a': 1, 'rest': ''}

    def test_an_absent_optional_field_is_not_an_error(self):
        result = decode([{'name': 'a', 'type': 'u8'},
                         {'name': 'b', 'type': 'u16', 'optional': True}], '01')
        assert result.errors == []
        assert result.data == {'a': 1}

    def test_a_ragged_tail_keeps_its_own_error(self):
        result = decode([{'name': 'r', 'type': 'repeat', 'until': 'end',
                          'fields': [{'name': 'v', 'type': 'u16'}]}], '000102')
        assert len(result.errors) == 1
        assert 'PS-343' in result.errors[0] and 'PS-535' not in result.errors[0]


ASCII = [{'name': 'label', 'type': 'ascii', 'length': 6}]
HIGH_BYTE = "Field 'label': a byte above 0x7F in an ascii value (PS-073, PS-538)"
HIGH_CHAR = "Field 'label': a character above U+007F in an ascii value (PS-537)"


class TestAsciiDecode:
    """PS-534, PS-538."""

    def test_trailing_nuls_only_are_dropped(self):
        result = decode(ASCII, '414200430000')
        assert result.data == {'label': 'AB\x00C'}
        assert result.warnings == []

    def test_whitespace_is_kept(self):
        assert decode(ASCII, '204120000000').data == {'label': ' A '}

    def test_all_nuls_is_the_empty_string(self):
        assert decode(ASCII, '000000000000').data == {'label': ''}

    def test_a_high_byte_is_its_latin_1_character_with_a_warning(self):
        result = decode(ASCII, '41E900000000')
        assert result.data == {'label': 'Aé'}
        assert result.warnings == [HIGH_BYTE]

    def test_one_warning_per_field(self):
        result = decode(ASCII, '80A0FF410000')
        assert result.data == {'label': '\u0080 ÿA'}
        assert result.warnings == [HIGH_BYTE]


class TestAsciiEncode:
    """PS-537."""

    def test_a_short_value_is_padded(self):
        result = encode(ASCII, {'label': 'AB'})
        assert (result.payload.hex(), result.errors, result.warnings) == (
            '414200000000', [], [])

    def test_a_latin_1_character_is_its_byte_with_a_warning(self):
        result = encode(ASCII, {'label': 'ABCé'})
        assert result.payload.hex() == '414243e90000'
        assert result.errors == []
        assert result.warnings == [HIGH_CHAR]

    def test_a_long_value_is_rejected(self):
        result = encode(ASCII, {'label': 'ABCDEFG'})
        assert result.errors == ["Field 'label': an ascii value of 7 bytes is longer than "
                                 "its length of 6 (PS-537)"]

    @pytest.mark.parametrize('value', ['€', 'A\U0001F600'])
    def test_a_character_above_u00ff_is_rejected(self, value):
        result = encode(ASCII, {'label': value})
        assert result.errors == ["Field 'label': an ascii value holds a character above "
                                 "U+00FF, which has no byte (PS-537)"]

    @pytest.mark.parametrize('payload', ['41E900000000', '80A0FF410000', '414200430000'])
    def test_the_round_trip_is_lossless(self, payload):
        schema = {'name': 'probe', 'version': 1, 'endian': 'big', 'fields': ASCII}
        interpreter = SchemaInterpreter(schema)
        decoded = interpreter.decode(bytes.fromhex(payload))
        assert interpreter.encode(decoded.data).payload.hex() == payload.lower()

    def test_a_length_that_is_not_fixed_is_not_padded(self):
        fields = [{'name': 'rest', 'type': 'ascii', 'length': 'remaining'}]
        assert encode(fields, {'rest': 'Aé'}).payload.hex() == '41e9'
