package schema

import (
	"encoding/hex"
	"strings"
	"testing"
)

// CR-2026-131 (PS-070): a byte_group covers its widest member's base, `size` may widen it
// and a narrower `size` is rejected; a member is a bit range, a whole integer or a bool
// with `bit`, each read from the group's start. The messages are the spec prototype's.

func TestCR131NarrowSizeIsRejected(t *testing.T) {
	for _, group := range []string{
		"  - byte_group: {size: 5, fields: [{name: a, type: \"u64[0:3]\"}]}\n",
		"  - byte_group: [{name: a, type: \"u64[0:3]\"}]\n    size: 5\n",
	} {
		_, err := ParseSchema("name: t\nfields:\n" + group)
		want := "byte_group declares size 5, narrower than its widest member's 8 bytes (PS-070)"
		if err == nil || err.Error() != want {
			t.Errorf("%s: got %v\nwant %s", group, err, want)
		}
	}
}

func TestCR131NarrowSizeInATLVCaseIsRejected(t *testing.T) {
	_, err := ParseSchema("name: t\nfields:\n  - tlv:\n      tag_size: 1\n      cases:\n        1:\n" +
		"          - byte_group: {size: 1, fields: [{name: a, type: \"u16[0:3]\"}]}\n")
	want := "byte_group declares size 1, narrower than its widest member's 2 bytes (PS-070)"
	if err == nil || !strings.Contains(err.Error(), want) {
		t.Errorf("got %v\nwant ...%s", err, want)
	}
}

func TestCR131MemberKinds(t *testing.T) {
	cases := []struct{ fields, want string }{
		{"  - byte_group: [{name: w, type: u8}, {name: f, type: bool, bit: 0}]\n",
			"members 'w' and 'f' overlap (PS-397)"},
		{"  - byte_group: [{name: f, type: bool, bit: 9}]\n",
			"byte_group member 'f' is a bool at bit 9; a bool member is one bit of a u8, 0 to 7 (PS-070)"},
		{"  - byte_group: [{name: f, type: bool}]\n",
			"byte_group member 'f' is not a bit range, a whole integer or a bool with `bit` (PS-364)"},
	}
	for _, c := range cases {
		_, err := ParseSchema("name: t\nfields:\n" + c.fields)
		if err == nil || !strings.HasSuffix(err.Error(), c.want) {
			t.Errorf("%s: got %v\nwant ...%s", c.fields, err, c.want)
		}
	}
}

func TestCR131WholeIntegerReadsFromTheStart(t *testing.T) {
	s := cr111Parse(t, "  - byte_group: {endian: little, fields: [{name: lo, type: u8}, {name: hi, type: \"u16[8:11]\"}]}\n  - {name: n, type: u8}\n")
	if got := asJSON(t, cr111Decode(t, s, "050307")); got != `{"hi":3,"lo":5,"n":7}` {
		t.Errorf("got %s", got)
	}
	payload, err := s.Encode(map[string]any{"lo": 5, "hi": 3, "n": 7})
	if err != nil || hex.EncodeToString(payload) != "050307" {
		t.Errorf("encode: %x %v", payload, err)
	}
}

func TestCR131SizelessGroupIsItsWidestBase(t *testing.T) {
	s := cr111Parse(t, "  - byte_group: [{name: lo, type: \"u16[0:3]\"}]\n  - {name: n, type: u8}\n")
	if got := asJSON(t, cr111Decode(t, s, "A00507")); got != `{"lo":5,"n":7}` {
		t.Errorf("got %s", got)
	}
}
