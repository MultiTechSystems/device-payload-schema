// Copyright (c) 2024-2026 Multitech Systems, Inc.
// Author: Jason Reiss
// SPDX-License-Identifier: MIT

// Package schema provides a schema-based payload formatter for LoRaWAN devices.
// It implements the PayloadEncoderDecoder interface using declarative YAML/JSON schemas.
package schema

import (
	"bytes"
	"encoding/base64"
	"encoding/binary"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"math"
	"regexp"
	"sort"
	"strconv"
	"strings"

	"gopkg.in/yaml.v3"
)

// FieldType represents the type of a schema field.
type FieldType string

const (
	TypeByte    FieldType = "Byte"
	TypeUInt    FieldType = "UInt"
	TypeSInt    FieldType = "SInt"
	TypeBInt    FieldType = "BInt"
	TypeFloat16 FieldType = "Float16"
	TypeFloat32 FieldType = "Float32"
	TypeFloat64 FieldType = "Float64"
	TypeBool    FieldType = "Bool"
	TypeBits    FieldType = "Bits"
	TypeAscii   FieldType = "Ascii"
	TypeHex     FieldType = "Hex"
	// Schemas are written in lowercase; without these aliases a `type: hex` field
	// was reported as an unknown type and the whole schema failed to decode.
	TypeHexLower      FieldType = "hex"
	TypeHexUpperLower FieldType = "hex:upper"
	TypeBase64  FieldType = "Base64"
	TypeSkip    FieldType = "Skip"
	TypeString  FieldType = "String"
	TypeNumber  FieldType = "Number"
	TypeObject  FieldType = "Object"
	TypeMatch   FieldType = "Match"
	TypeTLV     FieldType = "TLV"

	// Shorthand types (lowercase)
	TypeU8  FieldType = "u8"
	TypeU16 FieldType = "u16"
	TypeU32 FieldType = "u32"
	TypeU64 FieldType = "u64"
	TypeS8  FieldType = "s8"
	TypeS16 FieldType = "s16"
	TypeS32 FieldType = "s32"
	TypeS64 FieldType = "s64"
	TypeI8  FieldType = "i8"
	TypeI16 FieldType = "i16"
	TypeI32 FieldType = "i32"
	TypeI64 FieldType = "i64"
	TypeF16 FieldType = "f16"
	TypeF32 FieldType = "f32"
	TypeF64 FieldType = "f64"

	// 24-bit integer types
	TypeU24 FieldType = "u24"
	TypeS24 FieldType = "s24"

	// Lowercase variants
	TypeBitsLower   FieldType = "bits"
	TypeSkipLower   FieldType = "skip"
	TypeMatchLower  FieldType = "match"
	TypeObjectLower FieldType = "object"
	TypeTLVLower    FieldType = "tlv"

	// Bytes type (raw bytes with format options)
	TypeBytes      FieldType = "Bytes"
	TypeBytesLower FieldType = "bytes"

	// Enum type (maps integer values to strings)
	TypeEnum      FieldType = "Enum"
	TypeEnumLower FieldType = "enum"

	// Bool lowercase
	TypeBoolLower FieldType = "bool"

	// String/Ascii lowercase
	TypeStringLower FieldType = "string"
	TypeAsciiLower  FieldType = "ascii"

	// Repeat type (arrays)
	TypeRepeat      FieldType = "Repeat"
	TypeRepeatLower FieldType = "repeat"

	// Bitfield string (version strings)
	TypeBitfieldString FieldType = "bitfield_string"
)

// Field represents a field definition in the schema.
type Field struct {
	Name        string         `json:"name,omitempty" yaml:"name,omitempty"`
	Type        FieldType      `json:"type" yaml:"type"`
	Length      int            `json:"length,omitempty" yaml:"length,omitempty"`
	ByteOffset  int            `json:"byte_offset,omitempty" yaml:"byte_offset,omitempty"`
	BitOffset   int            `json:"bit_offset,omitempty" yaml:"bit_offset,omitempty"`
	Bits        int            `json:"bits,omitempty" yaml:"bits,omitempty"`
	Endian      string         `json:"endian,omitempty" yaml:"endian,omitempty"`
	Add         *float64       `json:"add,omitempty" yaml:"add,omitempty"`
	Mult        *float64       `json:"mult,omitempty" yaml:"mult,omitempty"`
	Div         *float64       `json:"div,omitempty" yaml:"div,omitempty"`
	// Deprecated: retained so existing callers still compile. Modifier order is
	// fixed by PS-101 and this field is no longer read.
	ModOrder []string `json:"-" yaml:"-"`
	Transform   []Transform    `json:"transform,omitempty" yaml:"transform,omitempty"`
	Modifiers   []Transform    `json:"modifiers,omitempty" yaml:"modifiers,omitempty"` // Legacy support
	Lookup      map[int]string `json:"lookup,omitempty" yaml:"lookup,omitempty"`
	LookupArray []any          `json:"lookup_array,omitempty" yaml:"lookup_array,omitempty"`
	// Fallback for a mapping lookup with no entry for the decoded value (PS-269).
	LookupDefault *string `json:"-" yaml:"-"`
	// Output key template resolved against earlier fields (PS-265).
	NameFrom string `json:"name_from,omitempty" yaml:"name_from,omitempty"`
	Var         string         `json:"var,omitempty" yaml:"var,omitempty"`
	Value       any            `json:"value,omitempty" yaml:"value,omitempty"`
	Fields      []Field        `json:"fields,omitempty" yaml:"fields,omitempty"`
	On          string         `json:"on,omitempty" yaml:"on,omitempty"`
	Cases       []Case         `json:"cases,omitempty" yaml:"cases,omitempty"`
	// Repeat/array fields
	Count      any    `json:"count,omitempty" yaml:"count,omitempty"`           // Number of iterations or variable reference
	ByteLength any    `json:"byte_length,omitempty" yaml:"byte_length,omitempty"` // Byte-based repeat length
	Until      string `json:"until,omitempty" yaml:"until,omitempty"`           // "end" for until end of payload
	Max        int    `json:"max,omitempty" yaml:"max,omitempty"`               // Maximum iterations (safety limit)
	Min        int    `json:"min,omitempty" yaml:"min,omitempty"`               // Minimum required iterations
	// Bytes field options
	Format    string `json:"format,omitempty" yaml:"format,omitempty"`       // hex, hex:upper, base64, array
	Separator string `json:"separator,omitempty" yaml:"separator,omitempty"` // Byte separator for hex output
	// Enum field options
	Base       string         `json:"base,omitempty" yaml:"base,omitempty"`     // Base type (u8, u16, etc.)
	Values     map[int]string `json:"values,omitempty" yaml:"values,omitempty"` // Enum value mapping
	// EnumDefault is the value an unmapped enum reports (PS-068). Distinct from
	// LookupDefault, which serves the `lookup` construct.
	EnumDefault *string `json:"-" yaml:"-"`
	// Bool field options
	Bit     int  `json:"bit,omitempty" yaml:"bit,omitempty"`         // Bit position for bool extraction
	Consume int  `json:"consume,omitempty" yaml:"consume,omitempty"` // Bytes to consume after reading
	// Byte group (inline grouped bitfields)
	ByteGroup []Field `json:"byte_group,omitempty" yaml:"byte_group,omitempty"`
	Size      int     `json:"size,omitempty" yaml:"size,omitempty"` // Size of byte group in bytes
	// $ref for definitions
	Ref2 string `json:"$ref,omitempty" yaml:"$ref,omitempty"` // Reference to definition
	// TLV-specific fields
	TagSize    int                `json:"tag_size,omitempty" yaml:"tag_size,omitempty"`
	LengthSize int                `json:"length_size,omitempty" yaml:"length_size,omitempty"`
	TagFields  []Field            `json:"tag_fields,omitempty" yaml:"tag_fields,omitempty"`
	TagKey     any                `json:"tag_key,omitempty" yaml:"tag_key,omitempty"`
	Merge      *bool              `json:"merge,omitempty" yaml:"merge,omitempty"`
	Unknown    string             `json:"unknown,omitempty" yaml:"unknown,omitempty"`
	TLVCases   map[string][]Field `json:"-" yaml:"-"` // Populated during parsing for TLV
	// Bitfield string fields
	Parts     [][]any `json:"parts,omitempty" yaml:"parts,omitempty"`
	Delimiter string  `json:"delimiter,omitempty" yaml:"delimiter,omitempty"`
	Prefix    string  `json:"prefix,omitempty" yaml:"prefix,omitempty"`
	// Formula (can reference $field_name for computed values) - DEPRECATED
	Formula string `json:"formula,omitempty" yaml:"formula,omitempty"`
	// Semantic fields
	ValidRange []float64 `json:"valid_range,omitempty" yaml:"valid_range,omitempty"` // [min, max] bounds for quality checks
	Resolution *float64  `json:"resolution,omitempty" yaml:"resolution,omitempty"`   // Minimum detectable change
	UNECE      string    `json:"unece,omitempty" yaml:"unece,omitempty"`             // UNECE Rec 20 unit code
	// Phase 2: Declarative computed values
	Ref        string     `json:"ref,omitempty" yaml:"ref,omitempty"`               // Reference to another field ($field_name)
	Polynomial []float64  `json:"polynomial,omitempty" yaml:"polynomial,omitempty"` // Coefficients [a_n, ..., a_0] for Horner's method
	Compute    *ComputeDef `json:"-" yaml:"-"`                                       // Binary operation (div, mul, add, sub)
	Guard      *GuardDef   `json:"-" yaml:"-"`                                       // Conditional evaluation
	// Flagged construct (inline struct)
	Flagged *FlaggedDef `json:"-" yaml:"-"`
	// TLV inline (for port-based schemas where tlv: is a nested key)
	TLVInline *Field `json:"-" yaml:"-"`
	// Match inline (for Option B syntax: `- match: { field: $var, cases: {...} }`)
	MatchInline *Field `json:"-" yaml:"-"`
}

// Transform represents a single transformation stage.
type Transform struct {
	Add  *float64 `json:"add,omitempty" yaml:"add,omitempty"`
	Sub  *float64 `json:"sub,omitempty" yaml:"sub,omitempty"`
	Mult *float64 `json:"mult,omitempty" yaml:"mult,omitempty"`
	Div  *float64 `json:"div,omitempty" yaml:"div,omitempty"`
	// Named operation form, e.g. {op: round, decimals: 2}.
	Op       string `json:"op,omitempty" yaml:"op,omitempty"`
	Decimals int    `json:"decimals,omitempty" yaml:"decimals,omitempty"`
	// Unary maths stages. Sqrt, Abs, Log10 and Log are flags; Pow carries the
	// exponent. Pointers so an absent key is distinguishable from `pow: 0`.
	Sqrt  bool     `json:"sqrt,omitempty" yaml:"sqrt,omitempty"`
	Abs   bool     `json:"abs,omitempty" yaml:"abs,omitempty"`
	Log10 bool     `json:"log10,omitempty" yaml:"log10,omitempty"`
	Log   bool     `json:"log,omitempty" yaml:"log,omitempty"`
	Pow   *float64 `json:"pow,omitempty" yaml:"pow,omitempty"`
}

// Case represents a match case in conditional parsing.
type Case struct {
	Case    any     `json:"case,omitempty" yaml:"case,omitempty"`
	Match   any     `json:"match,omitempty" yaml:"match,omitempty"` // Legacy support
	Default bool    `json:"default,omitempty" yaml:"default,omitempty"`
	Fields  []Field `json:"fields,omitempty" yaml:"fields,omitempty"`
}

// ComputeDef represents a binary arithmetic operation.
type ComputeDef struct {
	Op string `json:"op" yaml:"op"` // div, mul, add, sub
	A  string `json:"a" yaml:"a"`   // First operand ($field or literal)
	B  string `json:"b" yaml:"b"`   // Second operand ($field or literal)
}

// GuardCondition represents a single guard condition.
type GuardCondition struct {
	Field string   `json:"field" yaml:"field"` // Field reference ($field_name)
	Gt    *float64 `json:"gt,omitempty" yaml:"gt,omitempty"`
	Gte   *float64 `json:"gte,omitempty" yaml:"gte,omitempty"`
	Lt    *float64 `json:"lt,omitempty" yaml:"lt,omitempty"`
	Lte   *float64 `json:"lte,omitempty" yaml:"lte,omitempty"`
	Eq    *float64 `json:"eq,omitempty" yaml:"eq,omitempty"`
	Ne    *float64 `json:"ne,omitempty" yaml:"ne,omitempty"`
}

// GuardDef represents conditional evaluation with fallback.
type GuardDef struct {
	When []GuardCondition `json:"when" yaml:"when"`
	Else float64          `json:"else" yaml:"else"`
}

// FlaggedGroup represents a single bitmask-gated field group.
type FlaggedGroup struct {
	Bit    int     `json:"bit" yaml:"bit"`
	Fields []Field `json:"fields" yaml:"fields"`
}

// FlaggedDef represents a flagged/bitmask field presence construct.
type FlaggedDef struct {
	Field  string         `json:"field" yaml:"field"`
	Groups []FlaggedGroup `json:"groups" yaml:"groups"`
}

// PortDef represents a port-specific schema definition.
type PortDef struct {
	Direction   string  `json:"direction,omitempty" yaml:"direction,omitempty"`
	Description string  `json:"description,omitempty" yaml:"description,omitempty"`
	Fields      []Field `json:"fields,omitempty" yaml:"fields,omitempty"`
}

// DefinitionDef represents a reusable field definition.
type DefinitionDef struct {
	Fields []Field `json:"fields,omitempty" yaml:"fields,omitempty"`
}

// Schema represents a payload schema definition.
type Schema struct {
	Name        string                    `json:"name,omitempty" yaml:"name,omitempty"`
	Version     int                       `json:"version,omitempty" yaml:"version,omitempty"`
	Description string                    `json:"description,omitempty" yaml:"description,omitempty"`
	Endian      string                    `json:"endian,omitempty" yaml:"endian,omitempty"`
	Header      []Field                   `json:"header,omitempty" yaml:"header,omitempty"`
	Fields      []Field                   `json:"fields,omitempty" yaml:"fields,omitempty"`
	Ports       map[string]*PortDef       `json:"-" yaml:"-"` // Port-based schema selection
	Definitions map[string]*DefinitionDef `json:"-" yaml:"-"` // Reusable definitions
}

// DecodeContext maintains state during decoding.
type DecodeContext struct {
	Data      []byte
	Offset    int
	Endian    string
	Variables map[string]any
	Quality   map[string]string   // Quality status for fields with valid_range
	Warnings  []string            // Quality warnings
	// TLVOrder is the sequence of TLV case keys as they were read, in payload order.
	// A Go map cannot carry that, and encoding needs it: without it channels come back
	// in ascending tag order, which is how most devices lay them out but not all.
	TLVOrder []string
}

// EncodeContext maintains state during encoding.
type EncodeContext struct {
	Buffer    []byte
	Endian    string
	Variables map[string]any
	// TLVOrder is the channel sequence to emit, as DecodeOrdered reported it. Empty
	// means no order is known, and encodeTLV falls back to ascending tag order.
	TLVOrder []string
}

// NewEncodeContext creates a new encode context.
func NewEncodeContext(endian string) *EncodeContext {
	if endian == "" {
		endian = "big"
	}
	return &EncodeContext{
		Buffer:    make([]byte, 0),
		Endian:    endian,
		Variables: make(map[string]any),
	}
}

// Write appends bytes to the buffer.
func (ctx *EncodeContext) Write(data []byte) {
	ctx.Buffer = append(ctx.Buffer, data...)
}

// inferLengthFromType returns the byte length for shorthand types like u8, s16, etc.
func inferLengthFromType(t FieldType) int {
	switch t {
	case TypeU8, TypeS8, TypeI8:
		return 1
	case TypeU16, TypeS16, TypeI16:
		return 2
	case TypeU24, TypeS24:
		return 3
	case TypeU32, TypeS32, TypeI32, TypeF32:
		return 4
	case TypeU64, TypeS64, TypeI64, TypeF64:
		return 8
	case TypeF16:
		return 2
	default:
		return 1
	}
}

// NewDecodeContext creates a new decode context.
func NewDecodeContext(data []byte, endian string) *DecodeContext {
	if endian == "" {
		endian = "big"
	}
	return &DecodeContext{
		Data:      data,
		Offset:    0,
		Endian:    endian,
		Variables: make(map[string]any),
		Quality:   make(map[string]string),
		Warnings:  []string{},
	}
}

// checkValidRange checks if value is within valid_range and updates quality.
// Returns "good" if in range (or no range defined), "out_of_range" otherwise.
func (ctx *DecodeContext) checkValidRange(value any, field Field) string {
	if len(field.ValidRange) < 2 {
		return "good"
	}
	
	numVal, ok := toFloat64(value)
	if !ok {
		return "good"
	}
	
	minVal, maxVal := field.ValidRange[0], field.ValidRange[1]
	
	if numVal < minVal || numVal > maxVal {
		warning := fmt.Sprintf("%s: value %v outside valid range [%v, %v]",
			field.Name, numVal, minVal, maxVal)
		ctx.Warnings = append(ctx.Warnings, warning)
		ctx.Quality[field.Name] = "out_of_range"
		return "out_of_range"
	}
	
	ctx.Quality[field.Name] = "good"
	return "good"
}

// Remaining returns the number of bytes remaining.
func (ctx *DecodeContext) Remaining() int {
	return len(ctx.Data) - ctx.Offset
}

// Read reads n bytes and advances the offset. A negative n means "the remainder of
// the payload", the convention `length: -1` uses for a trailing bytes field.
func (ctx *DecodeContext) Read(n int) ([]byte, error) {
	if n < 0 {
		// Guarded because the bounds check below passes for a negative n and the
		// slice expression then panics with "slice bounds out of range", taking the
		// caller down rather than returning an error.
		n = ctx.Remaining()
	}
	if ctx.Offset+n > len(ctx.Data) {
		return nil, fmt.Errorf("buffer underflow: need %d bytes at offset %d, but only %d remaining",
			n, ctx.Offset, ctx.Remaining())
	}
	result := ctx.Data[ctx.Offset : ctx.Offset+n]
	ctx.Offset += n
	return result, nil
}

// Peek reads n bytes without advancing the offset.
func (ctx *DecodeContext) Peek(n int, offset int) ([]byte, error) {
	pos := ctx.Offset + offset
	if n < 0 || pos < 0 {
		return nil, fmt.Errorf("invalid peek of %d bytes at offset %d", n, pos)
	}
	if pos+n > len(ctx.Data) {
		return nil, fmt.Errorf("buffer underflow at peek offset %d", pos)
	}
	return ctx.Data[pos : pos+n], nil
}

// applyCanonicalModifiers applies the bare mult, div and add modifiers in the
// canonical order defined by PS-101: mult, then div, then add, whatever order the
// keys appear in the source document. An absent modifier is the identity.
//
// The previous implementation carried the YAML key order in a ModOrder field and
// applied the modifiers in that order, which JSON input could not supply -- so
// this path and the JSON fallback disagreed with each other and with the other
// language implementations. Order-dependent arithmetic uses Transform instead.
func applyCanonicalModifiers(value float64, field Field) float64 {
	if field.Mult != nil {
		value = value * *field.Mult
	}
	if field.Div != nil && *field.Div != 0 {
		value = value / *field.Div
	}
	if field.Add != nil {
		value = value + *field.Add
	}
	return value
}

// roundHalfEvenDecimal rounds to `decimals` places, half-to-even, on the stored value
// rather than on value*10^decimals - see the note at the call site for why that matters.
func roundHalfEvenDecimal(value float64, decimals int) float64 {
	if math.IsNaN(value) || math.IsInf(value, 0) {
		return value
	}
	if decimals < 0 {
		decimals = 0
	}
	rounded, err := strconv.ParseFloat(strconv.FormatFloat(value, 'f', decimals, 64), 64)
	if err != nil {
		return value
	}
	return rounded
}

// applyTransformStages applies a transform array in list order. A stage normally
// carries one arithmetic op; where it carries several they apply in the canonical
// order mult, div, add, so a stage cannot mean different things in different
// languages. A stage may instead name an operation, as {op: round, decimals: N}.
func applyTransformStages(value float64, stages []Transform) float64 {
	for _, stage := range stages {
		if stage.Op == "round" {
			// Half-to-even AND decimal-correct, matching Python, Java and C#.
			//
			// This was math.RoundToEven(value*scale)/scale, which is half-to-even but
			// not decimal-correct: rounding v at d decimals is not rounding v*10^d.
			// 2.355 is stored as 2.35499999999999998 and must round down to 2.35, but
			// 2.355*100 lands on exactly 235.5, so the multiply manufactured a tie and
			// rounded up to 2.36 - and 2.675 to 2.68 - where the other three
			// implementations give 2.35 and 2.67.
			//
			// strconv.FormatFloat is correctly rounded on the stored value and breaks
			// ties to even, so it does both jobs; verified against Python's round() on
			// the exact-tie and representation-error cases.
			value = roundHalfEvenDecimal(value, stage.Decimals)
			continue
		}
		// Unary maths stages, each exclusive of the others and of the arithmetic
		// ops, in the same order the Python interpreter checks them. The domain
		// clamps match it exactly: sqrt of a negative and log of a non-positive
		// would otherwise return NaN and poison every later stage, where the
		// interpreter yields 0 and log(1e-10).
		if stage.Sqrt {
			value = math.Sqrt(math.Max(0, value))
			continue
		}
		if stage.Abs {
			value = math.Abs(value)
			continue
		}
		if stage.Pow != nil {
			value = math.Pow(value, *stage.Pow)
			continue
		}
		if stage.Log10 {
			value = math.Log10(math.Max(1e-10, value))
			continue
		}
		if stage.Log {
			value = math.Log(math.Max(1e-10, value))
			continue
		}
		if stage.Mult != nil {
			value = value * *stage.Mult
		}
		if stage.Div != nil && *stage.Div != 0 {
			value = value / *stage.Div
		}
		if stage.Sub != nil {
			value = value - *stage.Sub
		}
		if stage.Add != nil {
			value = value + *stage.Add
		}
	}
	return value
}

// reverseCanonicalModifiers inverts applyCanonicalModifiers for encoding:
// decoding computes ((raw * mult) / div) + add, so encoding subtracts add, then
// multiplies by div, then divides by mult.
func reverseCanonicalModifiers(value float64, field Field) float64 {
	if field.Add != nil {
		value = value - *field.Add
	}
	if field.Div != nil {
		value = value * *field.Div
	}
	if field.Mult != nil && *field.Mult != 0 {
		value = value / *field.Mult
	}
	return value
}

// findFieldNodes returns a mapping from field index to its yaml.Node for a fields sequence.
func findFieldNodes(root *yaml.Node, path ...string) []*yaml.Node {
	node := root
	for _, key := range path {
		if node == nil {
			return nil
		}
		if node.Kind == yaml.DocumentNode && len(node.Content) > 0 {
			node = node.Content[0]
		}
		if node.Kind != yaml.MappingNode {
			return nil
		}
		found := false
		for i := 0; i < len(node.Content)-1; i += 2 {
			if node.Content[i].Value == key {
				node = node.Content[i+1]
				found = true
				break
			}
		}
		if !found {
			return nil
		}
	}
	if node.Kind != yaml.SequenceNode {
		return nil
	}
	return node.Content
}

// ParseSchema parses a schema from YAML or JSON string.
func ParseSchema(data string) (*Schema, error) {
	// First parse raw to handle TLV cases (which use map instead of array)
	var raw map[string]any
	if err := yaml.Unmarshal([]byte(data), &raw); err != nil {
		if err := json.Unmarshal([]byte(data), &raw); err != nil {
			return nil, fmt.Errorf("failed to parse schema: %w", err)
		}
	}

	// Also parse into yaml.Node tree to extract YAML key ordering for modifiers
	var rootNode yaml.Node
	_ = yaml.Unmarshal([]byte(data), &rootNode)
	fieldNodes := findFieldNodes(&rootNode, "fields")

	schema := &Schema{}
	
	if name, ok := raw["name"].(string); ok {
		schema.Name = name
	}
	if version, ok := raw["version"].(int); ok {
		schema.Version = version
	}
	if endian, ok := raw["endian"].(string); ok {
		schema.Endian = endian
	}
	if schema.Endian == "" {
		schema.Endian = "big"
	}

	// Parse definitions
	if defsRaw, ok := raw["definitions"].(map[string]any); ok {
		schema.Definitions = make(map[string]*DefinitionDef)
		for defName, defVal := range defsRaw {
			if defMap, ok := defVal.(map[string]any); ok {
				dd := &DefinitionDef{}
				if defFields, ok := defMap["fields"].([]any); ok {
					dd.Fields = parseFieldsRaw(defFields)
				}
				schema.Definitions[defName] = dd
			}
		}
	}
	// Handle map[any]any from YAML
	if defsRaw, ok := raw["definitions"].(map[any]any); ok {
		schema.Definitions = make(map[string]*DefinitionDef)
		for defName, defVal := range defsRaw {
			name := fmt.Sprintf("%v", defName)
			if defMap, ok := defVal.(map[string]any); ok {
				dd := &DefinitionDef{}
				if defFields, ok := defMap["fields"].([]any); ok {
					dd.Fields = parseFieldsRaw(defFields)
				}
				schema.Definitions[name] = dd
			}
			if defMap, ok := defVal.(map[any]any); ok {
				dd := &DefinitionDef{}
				if defFields, ok := defMap["fields"].([]any); ok {
					dd.Fields = parseFieldsRaw(defFields)
				}
				schema.Definitions[name] = dd
			}
		}
	}

	// Parse fields
	if fieldsRaw, ok := raw["fields"].([]any); ok {
		schema.Fields = parseFieldsRawWithNodes(fieldsRaw, fieldNodes)
	}

	// Parse ports (port-based schema selection)
	if portsRaw, ok := raw["ports"].(map[string]any); ok {
		schema.Ports = make(map[string]*PortDef)
		for portKey, portVal := range portsRaw {
			if portMap, ok := portVal.(map[string]any); ok {
				pd := &PortDef{}
				if dir, ok := portMap["direction"].(string); ok {
					pd.Direction = dir
				}
				if desc, ok := portMap["description"].(string); ok {
					pd.Description = desc
				}
				if pFields, ok := portMap["fields"].([]any); ok {
					pd.Fields = parseFieldsRaw(pFields)
				}
				schema.Ports[portKey] = pd
			}
		}
	}
	// YAML may parse numeric port keys as int
	if portsRaw, ok := raw["ports"].(map[any]any); ok {
		schema.Ports = make(map[string]*PortDef)
		for portKey, portVal := range portsRaw {
			key := fmt.Sprintf("%v", portKey)
			if portMap, ok := portVal.(map[string]any); ok {
				pd := &PortDef{}
				if dir, ok := portMap["direction"].(string); ok {
					pd.Direction = dir
				}
				if desc, ok := portMap["description"].(string); ok {
					pd.Description = desc
				}
				if pFields, ok := portMap["fields"].([]any); ok {
					pd.Fields = parseFieldsRaw(pFields)
				}
				schema.Ports[key] = pd
			}
			if portMap, ok := portVal.(map[any]any); ok {
				pd := &PortDef{}
				if dir, ok := portMap["direction"].(string); ok {
					pd.Direction = dir
				}
				if desc, ok := portMap["description"].(string); ok {
					pd.Description = desc
				}
				if pFields, ok := portMap["fields"].([]any); ok {
					pd.Fields = parseFieldsRaw(pFields)
				}
				schema.Ports[key] = pd
			}
		}
	}

	return schema, nil
}

func parseFieldsRaw(fieldsRaw []any) []Field {
	return parseFieldsRawWithNodes(fieldsRaw, nil)
}

func parseFieldsRawWithNodes(fieldsRaw []any, nodes []*yaml.Node) []Field {
	var fields []Field
	for i, fr := range fieldsRaw {
		if fm, ok := fr.(map[string]any); ok {
			var node *yaml.Node
			if i < len(nodes) {
				node = nodes[i]
			}
			fields = append(fields, parseFieldMap(fm, node))
		}
	}
	return fields
}

func parseFieldMap(fm map[string]any, node *yaml.Node) Field {
	f := Field{}
	
	if name, ok := fm["name"].(string); ok {
		f.Name = name
	}
	if typ, ok := fm["type"].(string); ok {
		f.Type = FieldType(typ)
	}
	if length, ok := fm["length"].(int); ok {
		f.Length = length
	}
	if length, ok := fm["length"].(float64); ok {
		f.Length = int(length)
	}
	// `length: remaining` consumes to the end of the payload (PS-014). It is stored
	// as the negative sentinel Read already resolves, so Length can stay an int.
	if length, ok := fm["length"].(string); ok && strings.EqualFold(strings.TrimSpace(length), "remaining") {
		f.Length = -1
	}
	if endian, ok := fm["endian"].(string); ok {
		f.Endian = endian
	}
	// Handle modifiers - could be float64 or int
	if mult, ok := fm["mult"].(float64); ok {
		f.Mult = &mult
	} else if mult, ok := fm["mult"].(int); ok {
		m := float64(mult)
		f.Mult = &m
	}
	if div, ok := fm["div"].(float64); ok {
		f.Div = &div
	} else if div, ok := fm["div"].(int); ok {
		d := float64(div)
		f.Div = &d
	}
	if add, ok := fm["add"].(float64); ok {
		f.Add = &add
	} else if add, ok := fm["add"].(int); ok {
		a := float64(add)
		f.Add = &a
	}
	// Modifier key order is deliberately not captured: the canonical order
	// (mult, div, add) applies regardless of how the source was written.

	// Parse transform array
	if transformRaw, ok := fm["transform"].([]any); ok {
		for _, tRaw := range transformRaw {
			if tm, ok := tRaw.(map[string]any); ok {
				t := Transform{}
				if add, ok := tm["add"].(float64); ok {
					t.Add = &add
				} else if add, ok := tm["add"].(int); ok {
					a := float64(add)
					t.Add = &a
				}
				if sub, ok := tm["sub"].(float64); ok {
					t.Sub = &sub
				} else if sub, ok := tm["sub"].(int); ok {
					s := float64(sub)
					t.Sub = &s
				}
				if mult, ok := tm["mult"].(float64); ok {
					t.Mult = &mult
				} else if mult, ok := tm["mult"].(int); ok {
					m := float64(mult)
					t.Mult = &m
				}
				if div, ok := tm["div"].(float64); ok {
					t.Div = &div
				} else if div, ok := tm["div"].(int); ok {
					d := float64(div)
					t.Div = &d
				}
				// {op: round, decimals: N}. Unparsed until now, so a schema
				// rounding its output reported the unrounded value instead.
				if op, ok := tm["op"].(string); ok {
					t.Op = op
				}
				if decimals, ok := tm["decimals"].(int); ok {
					t.Decimals = decimals
				} else if decimals, ok := tm["decimals"].(float64); ok {
					t.Decimals = int(decimals)
				}
				// Unary maths stages. Struct tags are not enough here: transform
				// stages are built by hand from this map, so a field added to
				// Transform without a line below is silently never populated -
				// the stage becomes a no-op and the value passes through
				// untouched rather than erroring.
				if flag, ok := tm["sqrt"].(bool); ok {
					t.Sqrt = flag
				}
				if flag, ok := tm["abs"].(bool); ok {
					t.Abs = flag
				}
				if flag, ok := tm["log10"].(bool); ok {
					t.Log10 = flag
				}
				if flag, ok := tm["log"].(bool); ok {
					t.Log = flag
				}
				if pow, ok := tm["pow"].(float64); ok {
					t.Pow = &pow
				} else if pow, ok := tm["pow"].(int); ok {
					p := float64(pow)
					t.Pow = &p
				}
				f.Transform = append(f.Transform, t)
			}
		}
	}

	// Parse modifiers array (legacy)
	if modifiersRaw, ok := fm["modifiers"].([]any); ok {
		for _, mRaw := range modifiersRaw {
			if mm, ok := mRaw.(map[string]any); ok {
				t := Transform{}
				if add, ok := mm["add"].(float64); ok {
					t.Add = &add
				} else if add, ok := mm["add"].(int); ok {
					a := float64(add)
					t.Add = &a
				}
				if mult, ok := mm["mult"].(float64); ok {
					t.Mult = &mult
				} else if mult, ok := mm["mult"].(int); ok {
					m := float64(mult)
					t.Mult = &m
				}
				if div, ok := mm["div"].(float64); ok {
					t.Div = &div
				} else if div, ok := mm["div"].(int); ok {
					d := float64(div)
					t.Div = &d
				}
				f.Modifiers = append(f.Modifiers, t)
			}
		}
	}

	if varName, ok := fm["var"].(string); ok {
		f.Var = varName
	}
	if on, ok := fm["on"].(string); ok {
		f.On = on
	}
	
	// Sequence form: `lookup: ["off", "on"]`, indexed from zero (PS-104). This was
	// unparsed, so every schema using the sequence form decoded a raw integer here
	// while the map form worked - the mirror image of the Python interpreter, which
	// supported sequences and mis-decoded maps.
	if lookup, ok := fm["lookup"].([]any); ok {
		f.LookupArray = lookup
	}
	if template, ok := fm["name_from"].(string); ok {
		f.NameFrom = template
	}
	// Lookup table - handle both string and int keys
	if lookup, ok := fm["lookup"].(map[string]any); ok {
		f.Lookup = make(map[int]string)
		for k, v := range lookup {
			if key, err := strconv.Atoi(k); err == nil {
				if str, ok := v.(string); ok {
					f.Lookup[key] = str
				}
			} else if k == "default" {
				if str, ok := v.(string); ok {
					fallback := str
					f.LookupDefault = &fallback
				}
			}
		}
	}
	// YAML may parse numeric keys as int
	if lookup, ok := fm["lookup"].(map[int]any); ok {
		f.Lookup = make(map[int]string)
		for k, v := range lookup {
			if str, ok := v.(string); ok {
				f.Lookup[k] = str
			}
		}
	}
	// Handle map[any]any from YAML
	if lookup, ok := fm["lookup"].(map[any]any); ok {
		f.Lookup = make(map[int]string)
		for k, v := range lookup {
			var key int
			switch kv := k.(type) {
			case int:
				key = kv
			case float64:
				key = int(kv)
			case string:
				if kv == "default" {
					if str, ok := v.(string); ok {
						fallback := str
						f.LookupDefault = &fallback
					}
					continue
				}
				key, _ = strconv.Atoi(kv)
			}
			if str, ok := v.(string); ok {
				f.Lookup[key] = str
			}
		}
	}
	
	// Nested fields (for Object type)
	if fieldsRaw, ok := fm["fields"].([]any); ok {
		f.Fields = parseFieldsRaw(fieldsRaw)
	}
	
	// Match cases (array format)
	if casesRaw, ok := fm["cases"].([]any); ok {
		for _, cr := range casesRaw {
			if cm, ok := cr.(map[string]any); ok {
				c := Case{}
				c.Case = cm["case"]
				if c.Case == nil {
					c.Case = cm["match"]
				}
				if def, ok := cm["default"].(bool); ok {
					c.Default = def
				}
				if caseFieldsRaw, ok := cm["fields"].([]any); ok {
					c.Fields = parseFieldsRaw(caseFieldsRaw)
				}
				f.Cases = append(f.Cases, c)
			}
		}
	}
	
	// TLV-specific fields
	if tagSize, ok := fm["tag_size"].(int); ok {
		f.TagSize = tagSize
	}
	if tagSize, ok := fm["tag_size"].(float64); ok {
		f.TagSize = int(tagSize)
	}
	if lengthSize, ok := fm["length_size"].(int); ok {
		f.LengthSize = lengthSize
	}
	if lengthSize, ok := fm["length_size"].(float64); ok {
		f.LengthSize = int(lengthSize)
	}
	if tagFieldsRaw, ok := fm["tag_fields"].([]any); ok {
		f.TagFields = parseFieldsRaw(tagFieldsRaw)
	}
	if tagKey, ok := fm["tag_key"]; ok {
		f.TagKey = tagKey
	}
	if merge, ok := fm["merge"].(bool); ok {
		f.Merge = &merge
	}
	if unknown, ok := fm["unknown"].(string); ok {
		f.Unknown = unknown
	}

	// Repeat/array fields
	if count, ok := fm["count"]; ok {
		f.Count = count
	}
	if byteLen, ok := fm["byte_length"]; ok {
		f.ByteLength = byteLen
	}
	if until, ok := fm["until"].(string); ok {
		f.Until = until
	}
	if max, ok := fm["max"].(int); ok {
		f.Max = max
	} else if max, ok := fm["max"].(float64); ok {
		f.Max = int(max)
	}
	if min, ok := fm["min"].(int); ok {
		f.Min = min
	} else if min, ok := fm["min"].(float64); ok {
		f.Min = int(min)
	}

	// Bytes format options
	if format, ok := fm["format"].(string); ok {
		f.Format = format
	}
	if separator, ok := fm["separator"].(string); ok {
		f.Separator = separator
	}

	// Bool field options
	if bit, ok := fm["bit"].(int); ok {
		f.Bit = bit
	} else if bit, ok := fm["bit"].(float64); ok {
		f.Bit = int(bit)
	}
	if consume, ok := fm["consume"].(int); ok {
		f.Consume = consume
	} else if consume, ok := fm["consume"].(float64); ok {
		f.Consume = int(consume)
	}

	// Enum field options
	if base, ok := fm["base"].(string); ok {
		f.Base = base
	}
	if def, ok := fm["default"].(string); ok && fm["values"] != nil {
		fallback := def
		f.EnumDefault = &fallback
	}
	if valuesRaw, ok := fm["values"].(map[string]any); ok {
		f.Values = make(map[int]string)
		for k, v := range valuesRaw {
			if key, err := strconv.Atoi(k); err == nil {
				if str, ok := v.(string); ok {
					f.Values[key] = str
				}
			}
		}
	}
	if valuesRaw, ok := fm["values"].(map[any]any); ok {
		f.Values = make(map[int]string)
		for k, v := range valuesRaw {
			var key int
			switch kv := k.(type) {
			case int:
				key = kv
			case float64:
				key = int(kv)
			case string:
				key, _ = strconv.Atoi(kv)
			}
			if str, ok := v.(string); ok {
				f.Values[key] = str
			}
		}
	}

	// Byte group (inline grouped bitfields) - array format
	if bgRaw, ok := fm["byte_group"].([]any); ok {
		f.ByteGroup = parseFieldsRaw(bgRaw)
	}
	// Byte group - Option B format: `- byte_group: { size: N, fields: [...] }`
	if bgMap, ok := fm["byte_group"].(map[string]any); ok {
		if bgSize, ok := bgMap["size"].(int); ok {
			f.Size = bgSize
		} else if bgSize, ok := bgMap["size"].(float64); ok {
			f.Size = int(bgSize)
		}
		if bgFields, ok := bgMap["fields"].([]any); ok {
			f.ByteGroup = parseFieldsRaw(bgFields)
		}
	}
	// Also handle map[any]any for YAML parsing quirks
	if bgMap, ok := fm["byte_group"].(map[any]any); ok {
		if bgSize, ok := bgMap["size"].(int); ok {
			f.Size = bgSize
		} else if bgSize, ok := bgMap["size"].(float64); ok {
			f.Size = int(bgSize)
		}
		if bgFields, ok := bgMap["fields"].([]any); ok {
			f.ByteGroup = parseFieldsRaw(bgFields)
		}
	}
	if size, ok := fm["size"].(int); ok {
		f.Size = size
	} else if size, ok := fm["size"].(float64); ok {
		f.Size = int(size)
	}

	// $ref for definitions
	if ref2, ok := fm["$ref"].(string); ok {
		f.Ref2 = ref2
	}

	// TLV cases. These are parsed whenever `cases` is present alongside anything
	// marking the block as tlv, not only when the type is already known to be tlv:
	// an inline `- tlv: {...}` block is parsed here before its caller sets the
	// type, so requiring the type first left TLVCases empty and every TLV schema in
	// the corpus decoded to an empty result with no error. tag_size counts as such
	// a marker - a block with a simple one-byte tag has neither tag_fields nor
	// tag_key, which is why elsys/ers decoded to nothing.
	if f.Type == TypeTLV || f.Type == "tlv" || fm["tag_fields"] != nil ||
		fm["tag_key"] != nil || fm["tag_size"] != nil {
		if casesMap, ok := fm["cases"].(map[string]any); ok {
			f.TLVCases = make(map[string][]Field)
			for key, value := range casesMap {
				if caseFieldsRaw, ok := value.([]any); ok {
					f.TLVCases[key] = parseFieldsRaw(caseFieldsRaw)
				}
			}
		}
		// YAML may hand back map[any]any for a mapping with non-string keys.
		if casesMap, ok := fm["cases"].(map[any]any); ok {
			f.TLVCases = make(map[string][]Field)
			for key, value := range casesMap {
				if caseFieldsRaw, ok := value.([]any); ok {
					f.TLVCases[fmt.Sprintf("%v", key)] = parseFieldsRaw(caseFieldsRaw)
				}
			}
		}
	}

	// Bitfield string fields
	if delimiter, ok := fm["delimiter"].(string); ok {
		f.Delimiter = delimiter
	}
	if prefix, ok := fm["prefix"].(string); ok {
		f.Prefix = prefix
	}
	if partsRaw, ok := fm["parts"].([]any); ok {
		for _, pRaw := range partsRaw {
			if pArr, ok := pRaw.([]any); ok {
				f.Parts = append(f.Parts, pArr)
			}
		}
	}

	// Formula (deprecated)
	if formula, ok := fm["formula"].(string); ok {
		f.Formula = formula
	}

	// Semantic fields
	if vrRaw, ok := fm["valid_range"].([]any); ok {
		for _, v := range vrRaw {
			if vf, ok := toFloat64(v); ok {
				f.ValidRange = append(f.ValidRange, vf)
			}
		}
	}
	if res, ok := fm["resolution"].(float64); ok {
		f.Resolution = &res
	} else if res, ok := fm["resolution"].(int); ok {
		r := float64(res)
		f.Resolution = &r
	}
	if unece, ok := fm["unece"].(string); ok {
		f.UNECE = unece
	}

	// Phase 2: ref (field reference)
	if ref, ok := fm["ref"].(string); ok {
		f.Ref = ref
	}

	// Phase 2: polynomial coefficients
	if polyRaw, ok := fm["polynomial"].([]any); ok {
		for _, c := range polyRaw {
			if cf, ok := toFloat64(c); ok {
				f.Polynomial = append(f.Polynomial, cf)
			}
		}
	}

	// Phase 2: compute (binary operation)
	if compRaw, ok := fm["compute"].(map[string]any); ok {
		cd := &ComputeDef{}
		if op, ok := compRaw["op"].(string); ok {
			cd.Op = op
		}
		if a, ok := compRaw["a"].(string); ok {
			cd.A = a
		} else if a, ok := compRaw["a"].(float64); ok {
			cd.A = strconv.FormatFloat(a, 'f', -1, 64)
		} else if a, ok := compRaw["a"].(int); ok {
			cd.A = strconv.Itoa(a)
		}
		if b, ok := compRaw["b"].(string); ok {
			cd.B = b
		} else if b, ok := compRaw["b"].(float64); ok {
			cd.B = strconv.FormatFloat(b, 'f', -1, 64)
		} else if b, ok := compRaw["b"].(int); ok {
			cd.B = strconv.Itoa(b)
		}
		f.Compute = cd
	}

	// Phase 2: guard (conditional evaluation)
	if guardRaw, ok := fm["guard"].(map[string]any); ok {
		gd := &GuardDef{}
		if elseVal, ok := guardRaw["else"].(float64); ok {
			gd.Else = elseVal
		} else if elseVal, ok := guardRaw["else"].(int); ok {
			gd.Else = float64(elseVal)
		}
		if whenRaw, ok := guardRaw["when"].([]any); ok {
			for _, w := range whenRaw {
				if wm, ok := w.(map[string]any); ok {
					gc := GuardCondition{}
					if field, ok := wm["field"].(string); ok {
						gc.Field = field
					}
					if gt, ok := wm["gt"].(float64); ok {
						gc.Gt = &gt
					} else if gt, ok := wm["gt"].(int); ok {
						gtf := float64(gt)
						gc.Gt = &gtf
					}
					if gte, ok := wm["gte"].(float64); ok {
						gc.Gte = &gte
					} else if gte, ok := wm["gte"].(int); ok {
						gtef := float64(gte)
						gc.Gte = &gtef
					}
					if lt, ok := wm["lt"].(float64); ok {
						gc.Lt = &lt
					} else if lt, ok := wm["lt"].(int); ok {
						ltf := float64(lt)
						gc.Lt = &ltf
					}
					if lte, ok := wm["lte"].(float64); ok {
						gc.Lte = &lte
					} else if lte, ok := wm["lte"].(int); ok {
						ltef := float64(lte)
						gc.Lte = &ltef
					}
					if eq, ok := wm["eq"].(float64); ok {
						gc.Eq = &eq
					} else if eq, ok := wm["eq"].(int); ok {
						eqf := float64(eq)
						gc.Eq = &eqf
					}
					// ne was neither parsed nor evaluated, so a guard written with
					// it never failed and its field kept a value it should not
					// have had: vicki's _tempStandard stayed live on the firmware
					// 3.5 path and was added to _tempFw35, giving 46.95 for 14.76.
					if ne, ok := wm["ne"].(float64); ok {
						gc.Ne = &ne
					} else if ne, ok := wm["ne"].(int); ok {
						nef := float64(ne)
						gc.Ne = &nef
					}
					gd.When = append(gd.When, gc)
				}
			}
		}
		f.Guard = gd
	}

	// Flagged construct (inline)
	if flaggedRaw, ok := fm["flagged"].(map[string]any); ok {
		fd := &FlaggedDef{}
		if field, ok := flaggedRaw["field"].(string); ok {
			fd.Field = field
		}
		if groupsRaw, ok := flaggedRaw["groups"].([]any); ok {
			for _, gRaw := range groupsRaw {
				if gMap, ok := gRaw.(map[string]any); ok {
					g := FlaggedGroup{}
					if bit, ok := gMap["bit"].(int); ok {
						g.Bit = bit
					} else if bit, ok := gMap["bit"].(float64); ok {
						g.Bit = int(bit)
					}
					if gFields, ok := gMap["fields"].([]any); ok {
						g.Fields = parseFieldsRaw(gFields)
					}
					fd.Groups = append(fd.Groups, g)
				}
			}
		}
		f.Flagged = fd
	}

	// TLV inline (for port-based schemas: `- tlv: { ... }`)
	if tlvRaw, ok := fm["tlv"].(map[string]any); ok {
		tlvField := parseFieldMap(tlvRaw, nil)
		tlvField.Type = "tlv"
		f.TLVInline = &tlvField
	}

	// Match inline (for Option B syntax: `- match: { field: $var, cases: {...} }`)
	if matchRaw, ok := fm["match"].(map[string]any); ok {
		matchField := Field{Type: TypeMatch}
		if fieldRef, ok := matchRaw["field"].(string); ok {
			matchField.On = fieldRef
		}
		if casesRaw, ok := matchRaw["cases"].(map[string]any); ok {
			for caseKey, caseVal := range casesRaw {
				c := Case{}
				// Parse case key (could be int or string)
				if keyInt, err := strconv.Atoi(caseKey); err == nil {
					c.Case = keyInt
				} else {
					c.Case = caseKey
				}
				// Parse case fields
				if caseFields, ok := caseVal.([]any); ok {
					c.Fields = parseFieldsRaw(caseFields)
				}
				matchField.Cases = append(matchField.Cases, c)
			}
		}
		// Also check for cases as map[any]any (YAML parsing quirk)
		if casesRaw, ok := matchRaw["cases"].(map[any]any); ok {
			for caseKey, caseVal := range casesRaw {
				c := Case{}
				switch k := caseKey.(type) {
				case int:
					c.Case = k
				case float64:
					c.Case = int(k)
				case string:
					if keyInt, err := strconv.Atoi(k); err == nil {
						c.Case = keyInt
					} else {
						c.Case = k
					}
				default:
					c.Case = fmt.Sprintf("%v", k)
				}
				if caseFields, ok := caseVal.([]any); ok {
					c.Fields = parseFieldsRaw(caseFields)
				}
				matchField.Cases = append(matchField.Cases, c)
			}
		}
		f.MatchInline = &matchField
	}
	
	return f
}

// FieldMetadata contains semantic annotations for a field.
type FieldMetadata struct {
	Unit        string    `json:"unit,omitempty"`
	ValidRange  []float64 `json:"valid_range,omitempty"`
	Resolution  *float64  `json:"resolution,omitempty"`
	UNECE       string    `json:"unece,omitempty"`
	Description string    `json:"description,omitempty"`
	IPSO        int       `json:"ipso,omitempty"`
	SenMLUnit   string    `json:"senml_unit,omitempty"`
}

// GetFieldMetadata returns semantic metadata for schema fields.
// If fieldName is empty, returns metadata for all fields.
func (s *Schema) GetFieldMetadata(fieldName string) map[string]FieldMetadata {
	result := make(map[string]FieldMetadata)
	collectFieldMetadata(s.Fields, result)
	
	if fieldName != "" {
		if meta, ok := result[fieldName]; ok {
			return map[string]FieldMetadata{fieldName: meta}
		}
		return map[string]FieldMetadata{}
	}
	return result
}

func collectFieldMetadata(fields []Field, result map[string]FieldMetadata) {
	for _, f := range fields {
		if f.Name == "" {
			continue
		}
		
		meta := FieldMetadata{
			ValidRange:  f.ValidRange,
			Resolution:  f.Resolution,
			UNECE:       f.UNECE,
		}
		
		// These would need to be added to Field struct if needed
		// For now, just include the semantic fields
		
		if len(meta.ValidRange) > 0 || meta.Resolution != nil || meta.UNECE != "" {
			result[f.Name] = meta
		}
		
		// Recurse into nested structures
		if len(f.Fields) > 0 {
			collectFieldMetadata(f.Fields, result)
		}
		if len(f.ByteGroup) > 0 {
			collectFieldMetadata(f.ByteGroup, result)
		}
	}
}

// ResolveFields returns the field set for a given fPort.
// If the schema uses ports, selects the matching port entry.
// Otherwise returns the top-level fields.
func (s *Schema) ResolveFields(fPort int) ([]Field, error) {
	if s.Ports == nil {
		return s.Fields, nil
	}

	portKey := strconv.Itoa(fPort)
	if pd, ok := s.Ports[portKey]; ok {
		return pd.Fields, nil
	}
	if pd, ok := s.Ports["default"]; ok {
		return pd.Fields, nil
	}
	return nil, fmt.Errorf("no port definition for fPort %d and no default in schema '%s'", fPort, s.Name)
}

// DecodeWithPort decodes binary data using the schema, selecting fields by fPort.
func (s *Schema) DecodeWithPort(data []byte, fPort int) (map[string]any, error) {
	fields, err := s.ResolveFields(fPort)
	if err != nil {
		return nil, err
	}

	ctx := NewDecodeContext(data, s.Endian)
	result := make(map[string]any)

	if len(s.Header) > 0 {
		headerResult, err := decodeFields(s.Header, ctx)
		if err != nil {
			return nil, err
		}
		for k, v := range headerResult {
			result[k] = v
		}
	}

	fieldsResult, err := decodeFields(fields, ctx)
	if err != nil {
		return nil, err
	}
	for k, v := range fieldsResult {
		result[k] = v
	}

	// Add quality dict to output if any quality flags were set
	if len(ctx.Quality) > 0 {
		result["_quality"] = ctx.Quality
	}

	return result, nil
}

// Decode decodes binary data using the schema.
func (s *Schema) Decode(data []byte) (map[string]any, error) {
	ctx := NewDecodeContext(data, s.Endian)
	result := make(map[string]any)

	// Decode header fields
	if len(s.Header) > 0 {
		headerResult, err := decodeFieldsWithSchema(s.Header, ctx, s)
		if err != nil {
			return nil, err
		}
		for k, v := range headerResult {
			result[k] = v
		}
	}

	// Decode main fields
	fieldsResult, err := decodeFieldsWithSchema(s.Fields, ctx, s)
	if err != nil {
		return nil, err
	}
	for k, v := range fieldsResult {
		result[k] = v
	}

	// Add quality dict to output if any quality flags were set
	if len(ctx.Quality) > 0 {
		result["_quality"] = ctx.Quality
	}

	return result, nil
}

func decodeFields(fields []Field, ctx *DecodeContext) (map[string]any, error) {
	return decodeFieldsWithSchema(fields, ctx, nil)
}

func decodeFieldsWithSchema(fields []Field, ctx *DecodeContext, schema *Schema) (map[string]any, error) {
	result := make(map[string]any)

	for _, field := range fields {
		// $ref to definition
		if field.Ref2 != "" && schema != nil {
			refResult, err := resolveRef(field.Ref2, ctx, schema)
			if err != nil {
				return nil, err
			}
			for k, v := range refResult {
				result[k] = v
				ctx.Variables[k] = v
			}
			continue
		}

		// Byte group (inline grouped bitfields)
		if len(field.ByteGroup) > 0 {
			bgResult, err := decodeByteGroup(field, ctx)
			if err != nil {
				return nil, err
			}
			for k, v := range bgResult {
				result[k] = v
				ctx.Variables[k] = v
			}
			continue
		}

		// TLV fields merge directly into result
		if field.Type == TypeTLV || field.Type == "tlv" {
			tlvResult, err := decodeTLV(field, ctx)
			if err != nil {
				return nil, err
			}
			for k, v := range tlvResult {
				result[k] = v
			}
			continue
		}

		// TLV inline (from port-based schemas)
		if field.TLVInline != nil {
			tlvResult, err := decodeTLV(*field.TLVInline, ctx)
			if err != nil {
				return nil, err
			}
			for k, v := range tlvResult {
				result[k] = v
			}
			continue
		}

		// Flagged construct
		if field.Flagged != nil {
			flaggedResult, err := decodeFlagged(field.Flagged, ctx)
			if err != nil {
				return nil, err
			}
			for k, v := range flaggedResult {
				result[k] = v
				ctx.Variables[k] = v
			}
			continue
		}

		// Match inline (Option B syntax: `- match: { field: $var, cases: {...} }`)
		if field.MatchInline != nil {
			matchResult, err := decodeMatch(*field.MatchInline, ctx)
			if err != nil {
				return nil, err
			}
			if matchMap, ok := matchResult.(map[string]any); ok {
				for k, v := range matchMap {
					result[k] = v
					ctx.Variables[k] = v
				}
			}
			continue
		}

		value, err := decodeField(field, ctx)
		if err != nil {
			return nil, err
		}

		if value == omitted {
			// A lookup with no entry and no default: the device reported nothing
			// this schema can name, so the field is left out (PS-269).
			continue
		}

		if value != nil && field.Name != "" {
			outputName, err := resolveFieldName(field, ctx)
			if err != nil {
				return nil, err
			}
			// A leading underscore marks an internal field: it becomes a variable
			// later fields can reference, but is not reported. The encode path
			// already honoured this; decoding did not, so an intermediate used to
			// combine two words appeared in the decoded result.
			if !strings.HasPrefix(field.Name, "_") {
				result[outputName] = value
			}
			// Keyed by the schema-level name so $references keep working when
			// name_from is in play (PS-267).
			ctx.Variables[field.Name] = value
			// Check valid_range and update quality
			if len(field.ValidRange) >= 2 {
				ctx.checkValidRange(value, field)
			}
		}
	}

	return result, nil
}

// resolveRef resolves a $ref reference to a definition.
func resolveRef(ref string, ctx *DecodeContext, schema *Schema) (map[string]any, error) {
	// Parse ref like "#/definitions/header"
	if !strings.HasPrefix(ref, "#/definitions/") {
		return nil, fmt.Errorf("unsupported $ref format: %s", ref)
	}
	defName := strings.TrimPrefix(ref, "#/definitions/")
	
	if schema.Definitions == nil {
		return nil, fmt.Errorf("no definitions in schema")
	}
	
	def, ok := schema.Definitions[defName]
	if !ok {
		return nil, fmt.Errorf("definition not found: %s", defName)
	}
	
	return decodeFieldsWithSchema(def.Fields, ctx, schema)
}

// decodeByteGroup decodes a byte group (multiple bitfields from shared bytes).
func decodeByteGroup(field Field, ctx *DecodeContext) (map[string]any, error) {
	size := field.Size
	if size == 0 {
		size = 1
	}
	
	data, err := ctx.Read(size)
	if err != nil {
		return nil, err
	}
	
	result := make(map[string]any)
	
	// Parse each subfield from the shared bytes
	for _, subfield := range field.ByteGroup {
		// Parse bit range from type like "u8[4:7]"
		typeStr := string(subfield.Type)
		bitStart, bitEnd := 0, 7
		
		if idx := strings.Index(typeStr, "["); idx >= 0 {
			rangeStr := typeStr[idx+1 : len(typeStr)-1]
			parts := strings.Split(rangeStr, ":")
			if len(parts) == 2 {
				bitStart, _ = strconv.Atoi(parts[0])
				bitEnd, _ = strconv.Atoi(parts[1])
			}
		}
		
		// Extract bits from the data. The group's bytes are assembled in the
		// schema's byte order - big-endian unless the document says otherwise.
		// Assembling little-endian unconditionally was invisible while every
		// multi-byte group happened to carry no bit range: rakwireless/qingping
		// packs a 12-bit temperature as u24[12:23], and bytes 2D F1 C4 became
		// 0xC4F12D rather than 0x2DF1C4, reporting 265.1 C for 23.5 C.
		var rawVal uint64
		if ctx.Endian == "little" {
			for i := len(data) - 1; i >= 0; i-- {
				rawVal = rawVal<<8 | uint64(data[i])
			}
		} else {
			for _, b := range data {
				rawVal = rawVal<<8 | uint64(b)
			}
		}
		
		bitLen := bitEnd - bitStart + 1
		mask := uint64((1 << bitLen) - 1)
		value := float64((rawVal >> bitStart) & mask)
		
		if subfield.Name != "" {
			result[subfield.Name] = value
		}
	}
	
	return result, nil
}

func decodeFlagged(fd *FlaggedDef, ctx *DecodeContext) (map[string]any, error) {
	flagsVal, ok := ctx.Variables[fd.Field]
	if !ok {
		return nil, fmt.Errorf("flagged field reference not found: %s", fd.Field)
	}
	flags, _ := toInt(flagsVal)

	result := make(map[string]any)

	for _, group := range fd.Groups {
		isPresent := (flags >> group.Bit) & 1
		if isPresent != 0 {
			groupResult, err := decodeFields(group.Fields, ctx)
			if err != nil {
				return nil, err
			}
			for k, v := range groupResult {
				result[k] = v
			}
		}
	}

	return result, nil
}

// bitRangePattern matches a bit-range type such as u8[0:0] or u16[4:11].
var bitRangePattern = regexp.MustCompile(`^([usf]\d+)\[(\d+):(\d+)\]$`)

// decodeBitRange extracts a contiguous bit range from an unsigned base value.
//
// An explicit range does not advance the read position by itself: several fields
// share one byte, and the last of them declares `consume` (PS-102 in practice, and
// how every flag byte in the corpus is written). Without this, a `u8[0:0]` field was
// reported as an unknown type and the whole schema failed to decode.
func decodeBitRange(field Field, ctx *DecodeContext, match []string) (any, int, error) {
	width := map[string]int{"u8": 1, "s8": 1, "u16": 2, "s16": 2, "u24": 3, "u32": 4, "s32": 4}[match[1]]
	if width == 0 {
		width = 1
	}
	data, err := ctx.Peek(width, 0)
	if err != nil {
		return nil, 0, err
	}
	base := uint64(0)
	if ctx.Endian == "little" {
		for i := width - 1; i >= 0; i-- {
			base = base<<8 | uint64(data[i])
		}
	} else {
		for i := 0; i < width; i++ {
			base = base<<8 | uint64(data[i])
		}
	}
	low, high := 0, 0
	fmt.Sscanf(match[2], "%d", &low)
	fmt.Sscanf(match[3], "%d", &high)
	if high < low {
		low, high = high, low
	}
	bits := high - low + 1
	mask := uint64(1)<<bits - 1
	return float64((base >> uint(low)) & mask), field.Consume, nil
}

func decodeField(field Field, ctx *DecodeContext) (any, error) {
	// Bit ranges are handled before the type switch: the type string carries the
	// range, so it never matches a plain type name.
	if match := bitRangePattern.FindStringSubmatch(string(field.Type)); match != nil {
		value, consume, err := decodeBitRange(field, ctx, match)
		if err != nil {
			return nil, err
		}
		if consume > 0 {
			if _, err := ctx.Read(consume); err != nil {
				return nil, err
			}
		}
		return applyLookupAndModifiers(value, field, ctx)
	}

	length := field.Length
	if length == 0 {
		// Infer length from shorthand type names
		length = inferLengthFromType(field.Type)
	}
	endian := field.Endian
	if endian == "" {
		endian = ctx.Endian
	}

	var value any
	var err error

	switch field.Type {
	case TypeByte, TypeUInt, TypeU8, TypeU16, TypeU32, TypeU64, TypeU24:
		data, err := ctx.Read(length)
		if err != nil {
			return nil, err
		}
		value = decodeUint(data, endian)

	case TypeSInt, TypeS8, TypeS16, TypeS32, TypeS64, TypeI8, TypeI16, TypeI32, TypeI64, TypeS24:
		data, err := ctx.Read(length)
		if err != nil {
			return nil, err
		}
		value = decodeSint(data, endian)

	case TypeBInt:
		data, err := ctx.Read(length)
		if err != nil {
			return nil, err
		}
		value = decodeUint(data, "big")

	case TypeFloat16, TypeFloat32, TypeFloat64, TypeF16, TypeF32, TypeF64:
		size := map[FieldType]int{
			TypeFloat16: 2, TypeFloat32: 4, TypeFloat64: 8,
			TypeF16: 2, TypeF32: 4, TypeF64: 8,
		}[field.Type]
		data, err := ctx.Read(size)
		if err != nil {
			return nil, err
		}
		value, err = decodeFloat(data, size, endian)
		if err != nil {
			return nil, err
		}

	case TypeBool, TypeBoolLower:
		// Bool extracts a single bit from the current byte
		data, err := ctx.Peek(1, 0)
		if err != nil {
			return nil, err
		}
		value = decodeBits(data[0], field.Bit, 1) != 0
		// Consume bytes if specified
		if field.Consume > 0 {
			ctx.Read(field.Consume)
		}

	case TypeBits, TypeBitsLower:
		data, err := ctx.Peek(1, field.ByteOffset)
		if err != nil {
			return nil, err
		}
		bits := field.Bits
		if bits == 0 {
			bits = 1
		}
		value = decodeBits(data[0], field.BitOffset, bits)

	case TypeString, TypeStringLower:
		// If length is specified, read bytes; otherwise use static value
		if length > 0 {
			data, err := ctx.Read(length)
			if err != nil {
				return nil, err
			}
			value = strings.TrimRight(string(data), "\x00")
		} else {
			value = field.Value
		}

	case TypeAscii, TypeAsciiLower:
		data, err := ctx.Read(length)
		if err != nil {
			return nil, err
		}
		value = strings.TrimRight(string(data), "\x00")

	case TypeEnum, TypeEnumLower:
		// Enum: read base type and map to string
		baseLen := 1
		switch field.Base {
		case "u8", "s8":
			baseLen = 1
		case "u16", "s16":
			baseLen = 2
		case "u32", "s32":
			baseLen = 4
		}
		data, err := ctx.Read(baseLen)
		if err != nil {
			return nil, err
		}
		intVal := int(decodeUint(data, endian))
		if field.Values != nil {
			if str, ok := field.Values[intVal]; ok {
				value = str
			} else if field.EnumDefault != nil {
				// An unmapped value takes the declared default (PS-068). Returning
				// the raw integer here ignored the default the schema asked for.
				value = *field.EnumDefault
			} else {
				value = intVal
			}
		} else {
			value = intVal
		}

	case TypeHex, TypeHexLower, TypeHexUpperLower:
		data, err := ctx.Read(length)
		if err != nil {
			return nil, err
		}
		// PS-074: `hex` is lowercase without separators; uppercase is `hex:upper`.
		encoded := hex.EncodeToString(data)
		if field.Type == TypeHexUpperLower {
			encoded = strings.ToUpper(encoded)
		}
		value = encoded

	case TypeSkip, TypeSkipLower:
		_, err := ctx.Read(length)
		if err != nil {
			return nil, err
		}
		return nil, nil

	case TypeBytes, TypeBytesLower:
		data, err := ctx.Read(length)
		if err != nil {
			return nil, err
		}
		value = formatBytes(data, field.Format, field.Separator)

	case TypeRepeat, TypeRepeatLower:
		value, err = decodeRepeat(field, ctx)
		if err != nil {
			return nil, err
		}

	case TypeBitfieldString:
		data, err := ctx.Read(length)
		if err != nil {
			return nil, err
		}
		intVal := decodeUint(data, endian)
		delimiter := field.Delimiter
		if delimiter == "" {
			delimiter = "."
		}
		prefix := field.Prefix
		var partStrs []string
		for _, part := range field.Parts {
			if len(part) < 2 {
				continue
			}
			bitOff, _ := toInt(part[0])
			bitLen, _ := toInt(part[1])
			format := "decimal"
			if len(part) >= 3 {
				if f, ok := part[2].(string); ok {
					format = f
				}
			}
			mask := (uint64(1) << bitLen) - 1
			raw := (intVal >> bitOff) & mask
			if format == "hex" {
				// Lowercase (PS-074), matching the vendor codecs and the generated JS.
				partStrs = append(partStrs, strconv.FormatUint(raw, 16))
			} else {
				partStrs = append(partStrs, strconv.FormatUint(raw, 10))
			}
		}
		value = prefix + strings.Join(partStrs, delimiter)

	case TypeNumber, "number":
		// Computed field — reads no bytes
		// Phase 2: ref with polynomial/transform, compute with guard
		if field.Ref != "" {
			refName := strings.TrimPrefix(field.Ref, "$")
			refVal, ok := ctx.Variables[refName]
			if !ok {
				return nil, fmt.Errorf("ref field not found: %s", refName)
			}
			numVal, _ := toFloat64(refVal)

			// Apply polynomial (Horner's method)
			if len(field.Polynomial) > 0 {
				numVal = evaluatePolynomial(field.Polynomial, numVal)
			}

			// Modifiers first, then the transform stages - the order the
			// interpreter uses. Running the stages first made a field that scales
			// with mult and then rounds with a stage round before it had scaled.
			numVal = applyCanonicalModifiers(numVal, field)
			numVal = applyTransformStages(numVal, field.Transform)

			value = numVal
		} else if field.Compute != nil {
			// A guard exists to avoid an invalid computation, so its conditions are
			// checked before evaluating one. Evaluating first made a guarded
			// division by zero abort the whole payload - dl-alb and vicki guard
			// exactly that case and decoded nothing here while other
			// implementations returned the guard's else value.
			if field.Guard != nil && !guardConditionsHold(field.Guard, ctx) {
				value = field.Guard.Else
			} else {
				result, err := evaluateCompute(field.Compute, ctx)
				if errors.Is(err, errComputeOmitted) {
					// Zero divisor (PS-278): report the field absent and carry on
					// with the payload, reusing the same `omitted` sentinel the
					// unmatched-lookup path uses. Returning the error here used to
					// abandon the whole decode.
					value = omitted
				} else if err != nil {
					return nil, err
				} else {
					// A compute takes its transform stages and no bare modifiers,
					// as the interpreter does. These were not applied at all, so a
					// computed field asking to be rounded was reported unrounded.
					value = applyTransformStages(result, field.Transform)
				}
			}
		} else if field.Formula != "" {
			// Legacy formula support
			val, err := evaluateFormula(field.Formula, 0, ctx)
			if err != nil {
				return nil, err
			}
			value = val
		} else {
			value = field.Value
		}

		// Apply guard if present (checks conditions on other fields, returns else if fail)
		if field.Guard != nil {
			if numVal, ok := toFloat64(value); ok {
				value = evaluateGuard(field.Guard, numVal, ctx)
			}
		}

	case TypeObject, TypeObjectLower:
		value, err = decodeFields(field.Fields, ctx)
		if err != nil {
			return nil, err
		}

	case TypeMatch, "CTRL-SWITCH", "Switch":
		value, err = decodeMatch(field, ctx)
		if err != nil {
			return nil, err
		}

	case TypeTLV, "tlv":
		return decodeTLV(field, ctx)

	default:
		return nil, fmt.Errorf("unknown field type: %s", field.Type)
	}

	return applyLookupAndModifiers(value, field, ctx)
}

// applyLookupAndModifiers runs the shared post-decode pipeline: formula, modifiers,
// transform, lookup and variable capture. Extracted so the bit-range path uses the
// same tail as the type switch rather than duplicating it.
func applyLookupAndModifiers(value any, field Field, ctx *DecodeContext) (any, error) {
	// Formula takes precedence over top-level modifiers (per spec section 03)
	// For TypeNumber with ref, transform is already applied in the ref block
	if field.Formula != "" && field.Type != TypeNumber {
		if numVal, ok := toFloat64(value); ok {
			result, err := evaluateFormula(field.Formula, numVal, ctx)
			if err != nil {
				return nil, err
			}
			value = result
		}
	} else if (field.Type == TypeNumber || field.Type == "number") &&
		(field.Ref != "" || field.Compute != nil) {
		// A computed field's transform stages were already applied where the value
		// was produced, so applying them again here doubles them. The ref case was
		// already skipped; compute was not, so a compute field carrying a transform
		// had it run twice - decentlab/dl-blg's voltage_ratio came out as
		// -0.4999999996 where the vendor decoder says 0.0064094, because its
		// `div: 16777216` and `add: -0.5` were each applied a second time. Nothing
		// caught it: that schema had no test vectors at all.
	} else if numVal, ok := toFloat64(value); ok {
		// Apply transformations in order
		// Support both top-level shortcuts and transform array
		if len(field.Transform) > 0 {
			// Routed through applyTransformStages rather than repeating the loop
			// here. This branch used to be its own copy, and being a copy it drifted:
			// it applied add before mult where PS-101 fixes the canonical order at
			// mult, div, add, and it knew nothing of {op: round} or the unary maths
			// stages - so on a plain typed field, unlike a ref or compute field, a
			// rounding stage did nothing and sqrt/log/pow passed the value through.
			// Bare modifiers first, then the stages - both when both are present.
			// This was an either/or, so a field that scales with `div` and then rounds
			// with a stage had the `div` silently dropped: round-half-to-even.yaml read
			// 2355 rather than 2.35. The same defect was corrected on the ref and
			// compute paths earlier; this branch was missed.
			numVal = applyCanonicalModifiers(numVal, field)
			numVal = applyTransformStages(numVal, field.Transform)
		// Check for legacy 'modifiers' array
		} else if len(field.Modifiers) > 0 {
			for _, stage := range field.Modifiers {
				if stage.Add != nil {
					numVal = numVal + *stage.Add
				}
				if stage.Mult != nil {
					numVal = numVal * *stage.Mult
				}
				if stage.Div != nil && *stage.Div != 0 {
					numVal = numVal / *stage.Div
				}
			}
		// Top-level shortcuts — canonical order: mult, div, add (PS-101).
		// This no longer depends on the source key order, which JSON input and
		// struct-based decoding cannot preserve, and which made this path
		// disagree with the JSON fallback and with the other interpreters.
		} else {
			numVal = applyCanonicalModifiers(numVal, field)
		}
		value = numVal
	}

	// Apply lookup. A mapping is matched on its keys, which need not start at
	// zero or be contiguous (PS-268); an unmatched value omits the field rather
	// than reporting the raw integer under a name that promises a label, unless a
	// default is declared (PS-269). A sequence stays indexed from zero (PS-104);
	// an out-of-bounds index is an error (PS-105), not the raw value, because the
	// payload does not match the schema's shape at all.
	if field.Lookup != nil {
		if intVal, ok := toInt(value); ok {
			if lookup, found := field.Lookup[intVal]; found {
				value = lookup
			} else if field.LookupDefault != nil {
				value = *field.LookupDefault
			} else {
				return omitted, nil
			}
		}
	}
	if field.LookupArray != nil {
		if intVal, ok := toInt(value); ok {
			if intVal >= 0 && intVal < len(field.LookupArray) {
				value = field.LookupArray[intVal]
			} else {
				return nil, fmt.Errorf("lookup index %d out of bounds for %d entries",
					intVal, len(field.LookupArray))
			}
		}
	}

	// Store variable
	if field.Var != "" {
		ctx.Variables[field.Var] = value
	}

	return value, nil
}

func decodeMatch(field Field, ctx *DecodeContext) (any, error) {
	var matchValue int

	if field.On != "" {
		// Variable-based match
		varName := strings.TrimPrefix(field.On, "$")
		val, ok := ctx.Variables[varName]
		if !ok {
			return nil, fmt.Errorf("variable not found: $%s", varName)
		}
		matchValue, _ = toInt(val)
	} else {
		// Inline match - read bytes
		length := field.Length
		if length == 0 {
			length = 1
		}
		data, err := ctx.Read(length)
		if err != nil {
			return nil, err
		}
		matchValue = int(decodeUint(data, ctx.Endian))
	}

	// Find matching case
	for _, c := range field.Cases {
		if c.Default {
			return decodeFields(c.Fields, ctx)
		}

		caseVal := c.Case
		if caseVal == nil {
			caseVal = c.Match // Legacy support
		}

		if caseVal == nil {
			continue
		}

		matched := false

		switch v := caseVal.(type) {
		case int:
			matched = matchValue == v
		case float64:
			matched = matchValue == int(v)
		case []any:
			for _, item := range v {
				if itemInt, ok := toInt(item); ok && matchValue == itemInt {
					matched = true
					break
				}
			}
		case map[string]any:
			minVal := math.MinInt
			maxVal := math.MaxInt
			if min, ok := v["min"]; ok {
				minVal, _ = toInt(min)
			}
			if max, ok := v["max"]; ok {
				maxVal, _ = toInt(max)
			}
			matched = matchValue >= minVal && matchValue <= maxVal
		}

		if matched {
			return decodeFields(c.Fields, ctx)
		}
	}

	return nil, nil
}

func decodeTLV(field Field, ctx *DecodeContext) (map[string]any, error) {
	tagSize := field.TagSize
	if tagSize == 0 {
		tagSize = 1
	}
	lengthSize := field.LengthSize
	merge := field.Merge == nil || *field.Merge // Default true
	unknownMode := field.Unknown
	if unknownMode == "" {
		unknownMode = "skip"
	}

	result := make(map[string]any)
	var channels []map[string]any

	// Parse until end of data
	for ctx.Remaining() > 0 {
		var tag []int
		var tagValues map[string]int

		if len(field.TagFields) > 0 {
			// Structured tag
			tagValues = make(map[string]int)
			for _, tf := range field.TagFields {
				length := tf.Length
				if length == 0 {
					length = 1
				}
				data, err := ctx.Read(length)
				if err != nil {
					break
				}
				val := int(decodeUint(data, ctx.Endian))
				if tf.Name != "" {
					tagValues[tf.Name] = val
				}
			}

			// Build tag key
			switch tk := field.TagKey.(type) {
			case []any:
				for _, k := range tk {
					if key, ok := k.(string); ok {
						tag = append(tag, tagValues[key])
					}
				}
			case []string:
				for _, key := range tk {
					tag = append(tag, tagValues[key])
				}
			case string:
				tag = []int{tagValues[tk]}
			default:
				// Use first tag field
				if len(field.TagFields) > 0 && field.TagFields[0].Name != "" {
					tag = []int{tagValues[field.TagFields[0].Name]}
				}
			}
		} else {
			// Simple numeric tag
			data, err := ctx.Read(tagSize)
			if err != nil {
				break
			}
			tag = []int{int(decodeUint(data, ctx.Endian))}
		}

		// Read length if specified
		var dataLength int = -1
		if lengthSize > 0 {
			data, err := ctx.Read(lengthSize)
			if err != nil {
				break
			}
			dataLength = int(decodeUint(data, ctx.Endian))
		}

		// Find matching case
		caseKey := findTLVCaseKey(field.TLVCases, tag)
		
		if caseKey != "" {
			ctx.TLVOrder = append(ctx.TLVOrder, caseKey)
			caseFields := field.TLVCases[caseKey]
			caseResult, err := decodeFields(caseFields, ctx)
			if err != nil {
				return nil, err
			}

			if merge {
				// Merge fields, converting to array if repeated
				for k, v := range caseResult {
					if existing, ok := result[k]; ok {
						if arr, isArr := existing.([]any); isArr {
							result[k] = append(arr, v)
						} else {
							result[k] = []any{existing, v}
						}
					} else {
						result[k] = v
					}
				}
			} else {
				entry := map[string]any{"tag": tag}
				for k, v := range caseResult {
					entry[k] = v
				}
				channels = append(channels, entry)
			}
		} else {
			// Unknown tag
			if unknownMode == "error" {
				return nil, fmt.Errorf("unknown TLV tag: %v", tag)
			} else if dataLength >= 0 {
				ctx.Read(dataLength) // Skip
			} else {
				break // Can't skip without length
			}
		}
	}

	if !merge {
		result["channels"] = channels
	}

	return result, nil
}

func findTLVCaseKey(cases map[string][]Field, tag []int) string {
	if cases == nil {
		return ""
	}

	// Try direct match for single tag
	if len(tag) == 1 {
		key := strconv.Itoa(tag[0])
		if _, ok := cases[key]; ok {
			return key
		}
	}

	// Try JSON array format
	tagJSON, _ := json.Marshal(tag)
	tagStr := string(tagJSON)
	if _, ok := cases[tagStr]; ok {
		return tagStr
	}

	// Compare composite keys numerically rather than as strings. json.Marshal
	// renders a tag as "[1,117]" while schemas are written "[1, 117]", so the
	// string comparison above missed every composite key -- which is why TLV
	// schemas decoded to an empty result here. This also handles keys that exclude
	// a value with `!` or ignore a tag field with `*`, taking exact keys first,
	// then negated, then wildcard, so a specific case is never shadowed (PS-270).
	for _, wanted := range []int{0, 1, 2} {
		for key := range cases {
			if matched, specificity := matchCompositeCaseKey(key, tag); matched && specificity == wanted {
				return key
			}
		}
	}

	return ""
}

// omitted marks a field that produced no value and is left out of the output.
var omitted = &struct{ name string }{"omitted"}

// nameFromPattern matches the ${field} references in a name_from template.
var nameFromPattern = regexp.MustCompile(`\$\{(\w+)\}`)

// matchCompositeCaseKey matches a composite TLV case key against a tag, allowing
// `!value` to exclude and `*` to ignore a tag field. The returned specificity is 0
// for an exact key, 1 when any element is negated and 2 when any is a wildcard.
func matchCompositeCaseKey(key string, tag []int) (bool, int) {
	trimmed := strings.TrimSpace(key)
	if !strings.HasPrefix(trimmed, "[") {
		return false, 0
	}
	trimmed = strings.TrimPrefix(trimmed, "[")
	trimmed = strings.TrimSuffix(trimmed, "]")
	parts := strings.Split(trimmed, ",")
	if len(parts) != len(tag) {
		return false, 0
	}
	specificity := 0
	for index, part := range parts {
		part = strings.Trim(strings.TrimSpace(part), `"'`)
		if part == "*" {
			if specificity < 2 {
				specificity = 2
			}
			continue
		}
		negated := strings.HasPrefix(part, "!")
		text := strings.TrimSpace(strings.TrimPrefix(part, "!"))
		expected, err := strconv.ParseInt(text, 0, 32)
		if err != nil {
			return false, 0
		}
		if negated {
			if specificity < 1 {
				specificity = 1
			}
			if tag[index] == int(expected) {
				return false, 0
			}
		} else if tag[index] != int(expected) {
			return false, 0
		}
	}
	return true, specificity
}

// resolveFieldName resolves a field's output key, honouring name_from (PS-265).
func resolveFieldName(field Field, ctx *DecodeContext) (string, error) {
	if field.NameFrom == "" {
		return field.Name, nil
	}
	var missing []string
	resolved := nameFromPattern.ReplaceAllStringFunc(field.NameFrom, func(match string) string {
		reference := match[2 : len(match)-1]
		value, ok := ctx.Variables[reference]
		if !ok {
			missing = append(missing, reference)
			return ""
		}
		if number, ok := toFloat64(value); ok && number == float64(int64(number)) {
			return strconv.FormatInt(int64(number), 10)
		}
		return fmt.Sprintf("%v", value)
	})
	if len(missing) > 0 {
		return "", fmt.Errorf("name_from for %q references %s, which has not been decoded",
			field.Name, strings.Join(missing, ", "))
	}
	return resolved, nil
}

// formatBytes formats a byte slice according to the specified format option.
func formatBytes(data []byte, format, separator string) any {
	if format == "" {
		format = "hex"
	}

	switch format {
	case "hex", "hex:lower":
		if separator != "" {
			parts := make([]string, len(data))
			for i, b := range data {
				parts[i] = fmt.Sprintf("%02x", b)
			}
			return strings.Join(parts, separator)
		}
		return hex.EncodeToString(data)

	case "hex:upper":
		if separator != "" {
			parts := make([]string, len(data))
			for i, b := range data {
				parts[i] = fmt.Sprintf("%02X", b)
			}
			return strings.Join(parts, separator)
		}
		return strings.ToUpper(hex.EncodeToString(data))

	case "base64":
		return base64.StdEncoding.EncodeToString(data)

	case "array":
		arr := make([]any, len(data))
		for i, b := range data {
			arr[i] = float64(b)
		}
		return arr

	default:
		return hex.EncodeToString(data)
	}
}

// decodeRepeat decodes a repeat/array field.
func decodeRepeat(field Field, ctx *DecodeContext) ([]any, error) {
	maxIterations := field.Max
	if maxIterations == 0 {
		maxIterations = 1000 // Safety limit
	}
	minIterations := field.Min

	var result []any

	// Determine iteration mode
	if field.Count != nil {
		// Count-based: fixed number of iterations
		var count int
		switch c := field.Count.(type) {
		case int:
			count = c
		case float64:
			count = int(c)
		case string:
			// Variable reference
			varName := strings.TrimPrefix(c, "$")
			if val, ok := ctx.Variables[varName]; ok {
				count, _ = toInt(val)
			} else {
				return nil, fmt.Errorf("repeat count variable not found: %s", varName)
			}
		default:
			return nil, fmt.Errorf("invalid count type: %T", field.Count)
		}

		if count > maxIterations {
			count = maxIterations
		}

		for i := 0; i < count; i++ {
			element, err := decodeFields(field.Fields, ctx)
			if err != nil {
				return nil, err
			}
			result = append(result, element)
		}

	} else if field.ByteLength != nil {
		// Byte-length based: consume specified number of bytes
		var byteLength int
		switch bl := field.ByteLength.(type) {
		case int:
			byteLength = bl
		case float64:
			byteLength = int(bl)
		case string:
			varName := strings.TrimPrefix(bl, "$")
			if val, ok := ctx.Variables[varName]; ok {
				byteLength, _ = toInt(val)
			} else {
				return nil, fmt.Errorf("repeat byte_length variable not found: %s", varName)
			}
		default:
			return nil, fmt.Errorf("invalid byte_length type: %T", field.ByteLength)
		}

		endOffset := ctx.Offset + byteLength
		iterations := 0

		for ctx.Offset < endOffset && iterations < maxIterations {
			element, err := decodeFields(field.Fields, ctx)
			if err != nil {
				return nil, err
			}
			result = append(result, element)
			iterations++
		}

		if ctx.Offset != endOffset {
			return nil, fmt.Errorf("repeat byte_length mismatch: expected end at %d, got %d",
				endOffset, ctx.Offset)
		}

	} else if field.Until == "end" {
		// Until-end: repeat until payload exhausted
		iterations := 0

		for ctx.Remaining() > 0 && iterations < maxIterations {
			element, err := decodeFields(field.Fields, ctx)
			if err != nil {
				return nil, err
			}
			result = append(result, element)
			iterations++
		}

	} else {
		return nil, fmt.Errorf("repeat field must specify one of: count, byte_length, or until")
	}

	// Validate minimum iterations
	if len(result) < minIterations {
		return nil, fmt.Errorf("repeat produced %d elements, but minimum is %d",
			len(result), minIterations)
	}

	return result, nil
}

// =============================================================================
// ENCODING
// =============================================================================

// Encode encodes data to binary using the schema.
func (s *Schema) Encode(data map[string]any) ([]byte, error) {
	return s.EncodeWithPort(data, 0)
}

// EncodeWithPort encodes data to binary using port-based schema selection.
func (s *Schema) EncodeWithPort(data map[string]any, fPort int) ([]byte, error) {
	ctx := NewEncodeContext(s.Endian)

	// Encode header fields first
	if len(s.Header) > 0 {
		if err := encodeFields(s.Header, data, ctx); err != nil {
			return nil, err
		}
	}

	// Resolve fields (port-based or top-level)
	fields, _ := s.ResolveFields(fPort)

	// Encode main fields
	if err := encodeFields(fields, data, ctx); err != nil {
		return nil, err
	}

	return ctx.Buffer, nil
}


// DecodeOrdered decodes and also reports the TLV channel sequence in payload order.
//
// A map cannot carry that order, and encoding needs it to put the channels back where
// they were - Encode alone has to assume ascending tags. Pair this with EncodeOrdered
// for a faithful round trip.
func (s *Schema) DecodeOrdered(data []byte) (map[string]any, []string, error) {
	return s.DecodeOrderedWithPort(data, 0)
}

// DecodeOrderedWithPort is DecodeOrdered with port-based schema selection.
func (s *Schema) DecodeOrderedWithPort(data []byte, fPort int) (map[string]any, []string, error) {
	fields, err := s.ResolveFields(fPort)
	if err != nil {
		return nil, nil, err
	}
	ctx := NewDecodeContext(data, s.Endian)
	result := make(map[string]any)
	if len(s.Header) > 0 {
		headerResult, err := decodeFieldsWithSchema(s.Header, ctx, s)
		if err != nil {
			return nil, nil, err
		}
		for k, v := range headerResult {
			result[k] = v
		}
	}
	fieldsResult, err := decodeFieldsWithSchema(fields, ctx, s)
	if err != nil {
		return nil, nil, err
	}
	for k, v := range fieldsResult {
		result[k] = v
	}
	return result, ctx.TLVOrder, nil
}

// EncodeOrdered encodes with an explicit TLV channel order, as DecodeOrdered reported it.
func (s *Schema) EncodeOrdered(data map[string]any, order []string) ([]byte, error) {
	return s.EncodeOrderedWithPort(data, 0, order)
}

// EncodeOrderedWithPort is EncodeOrdered with port-based schema selection.
func (s *Schema) EncodeOrderedWithPort(data map[string]any, fPort int, order []string) ([]byte, error) {
	ctx := NewEncodeContext(s.Endian)
	ctx.TLVOrder = order
	if len(s.Header) > 0 {
		if err := encodeFields(s.Header, data, ctx); err != nil {
			return nil, err
		}
	}
	fields, _ := s.ResolveFields(fPort)
	if err := encodeFields(fields, data, ctx); err != nil {
		return nil, err
	}
	return ctx.Buffer, nil
}

// --- encoding the constructs -------------------------------------------------
//
// Ported from tools/schema_interpreter.py, whose round-trip corpus found these gaps.
// Encoding had none of the constructs: a TLV schema emitted its channel values with no
// tag or length framing, a match emitted nothing, and a byte_group emitted one zero byte.

// integerRange gives the inclusive bounds a field of this type can hold, so a candidate
// TLV case can be asked whether the value it claims would even fit.
func integerRange(t FieldType) (int64, int64, bool) {
	var size int
	var signed bool
	switch t {
	case TypeU8, "uint8", TypeByte:
		size, signed = 1, false
	case TypeU16, "uint16":
		size, signed = 2, false
	case TypeU24, "uint24":
		size, signed = 3, false
	case TypeU32, "uint32":
		size, signed = 4, false
	case "s8", "i8", "int8":
		size, signed = 1, true
	case "s16", "i16", "int16":
		size, signed = 2, true
	case "s24", "i24", "int24":
		size, signed = 3, true
	case "s32", "i32", "int32":
		size, signed = 4, true
	default:
		return 0, 0, false
	}
	if signed {
		half := int64(1) << (8*size - 1)
		return -half, half - 1, true
	}
	return 0, (int64(1) << (8 * size)) - 1, true
}

// rawForField undoes a field's lookup, transform chain and canonical modifiers, without
// the rounding that encodeField applies - so a caller can see whether the value could
// have come from this field at all.
func rawForField(field Field, value any) (float64, bool) {
	if strVal, ok := value.(string); ok && field.Lookup != nil {
		found := false
		for k, v := range field.Lookup {
			if v == strVal {
				value = k
				found = true
				break
			}
		}
		if !found {
			return 0, false
		}
	}
	num, ok := toFloat64(value)
	if !ok {
		return 0, false
	}
	for i := len(field.Transform) - 1; i >= 0; i-- {
		stage := field.Transform[i]
		switch {
		case stage.Add != nil:
			num -= *stage.Add
		case stage.Mult != nil && *stage.Mult != 0:
			num /= *stage.Mult
		case stage.Div != nil:
			num *= *stage.Div
		case stage.Op != "", stage.Sqrt, stage.Abs, stage.Log, stage.Log10, stage.Pow != nil:
			// Rounding and clamping are identity in reverse; the rest are not
			// invertible, which the caller treats as "this case did not write it".
			if stage.Sqrt || stage.Abs || stage.Log || stage.Log10 || stage.Pow != nil {
				return 0, false
			}
		}
	}
	return reverseCanonicalModifiers(num, field), true
}

// caseFidelity reports how well a candidate TLV case explains the data: how many of its
// fields are present, and whether each could have produced the value it claims. am308
// defines `tvoc` under [8, 125] with div: 100 and under [8, 230] raw; 43.69 came from
// 4369 through the divide exactly, and 4369 raw cannot have come from the divide case
// because 436900 does not fit a u16.

// caseMatchesValue reports whether a discriminator selects this case: a single value, a
// list of values, or an "a..b" range, the same spellings decodeMatch accepts.
func caseMatchesValue(value int, c Case) bool {
	raw := c.Case
	if raw == nil {
		raw = c.Match
	}
	if raw == nil {
		return false
	}
	switch v := raw.(type) {
	case []any:
		for _, item := range v {
			if n, ok := toInt(item); ok && n == value {
				return true
			}
		}
		return false
	case string:
		if strings.Contains(v, "..") {
			parts := strings.SplitN(v, "..", 2)
			lo, errLo := parseIntAny(parts[0])
			hi, errHi := parseIntAny(parts[1])
			return errLo == nil && errHi == nil && int64(value) >= lo && int64(value) <= hi
		}
		n, err := parseIntAny(v)
		return err == nil && int64(value) == n
	}
	if n, ok := toInt(raw); ok {
		return n == value
	}
	return false
}

func caseFidelity(caseFields []Field, data map[string]any) (int, bool) {
	matches, lossless := 0, true
	for _, f := range caseFields {
		if f.Name == "" || strings.HasPrefix(f.Name, "_") || f.Type == TypeNumber || f.Type == "number" {
			continue
		}
		value, ok := data[f.Name]
		if !ok {
			continue
		}
		matches++
		raw, invertible := rawForField(f, value)
		if !invertible {
			lossless = false
			continue
		}
		if math.Abs(raw-math.Round(raw)) > 1e-9 {
			lossless = false
		}
		if lo, hi, known := integerRange(f.Type); known {
			r := int64(math.Round(raw))
			if r < lo || r > hi {
				lossless = false
			}
		}
	}
	return matches, lossless
}

// encodeTLVTag rebuilds a tag from the case key that matched it while decoding. The
// composite form carries the values in the key ("[3, 103]" against tag_key), so each goes
// back through its own tag_fields entry. A key using ! or * names a range of tags rather
// than one (PS-270), so it cannot be encoded.
func encodeTLVTag(caseKey string, field Field, ctx *EncodeContext) ([]byte, error) {
	text := strings.TrimSpace(caseKey)
	if strings.HasPrefix(text, "[") {
		text = strings.TrimSuffix(strings.TrimPrefix(text, "["), "]")
		parts := strings.Split(text, ",")
		if len(field.TagFields) == 0 {
			return nil, fmt.Errorf("composite tlv case %q with no tag_fields", caseKey)
		}
		names := tagKeyNames(field.TagKey)
		if len(names) != len(parts) {
			return nil, fmt.Errorf("tlv case %q does not match tag_key %v", caseKey, names)
		}
		values := map[string]int64{}
		for i, part := range parts {
			p := strings.Trim(strings.TrimSpace(part), "\"'")
			if p == "*" || strings.HasPrefix(p, "!") {
				return nil, fmt.Errorf("tlv case %q matches a range of tags, so encoding cannot choose one", caseKey)
			}
			v, err := parseIntAny(p)
			if err != nil {
				return nil, fmt.Errorf("tlv case %q: %v", caseKey, err)
			}
			values[names[i]] = v
		}
		out := []byte{}
		for _, tf := range field.TagFields {
			v, ok := values[tf.Name]
			if !ok {
				return nil, fmt.Errorf("tlv case %q gives no value for %q", caseKey, tf.Name)
			}
			width := 1
			if lo, hi, known := integerRange(tf.Type); known {
				_ = lo
				switch {
				case hi > 0xFFFFFF:
					width = 4
				case hi > 0xFFFF:
					width = 3
				case hi > 0xFF:
					width = 2
				}
			}
			out = append(out, encodeUint(uint64(v), width, ctx.Endian)...)
		}
		return out, nil
	}
	v, err := parseIntAny(text)
	if err != nil {
		return nil, fmt.Errorf("tlv case %q: %v", caseKey, err)
	}
	size := field.TagSize
	if size <= 0 {
		size = 1
	}
	return encodeUint(uint64(v), size, ctx.Endian), nil
}

func tagKeyNames(tagKey any) []string {
	switch v := tagKey.(type) {
	case string:
		return []string{v}
	case []any:
		names := make([]string, 0, len(v))
		for _, item := range v {
			names = append(names, fmt.Sprintf("%v", item))
		}
		return names
	case []string:
		return v
	}
	return nil
}

func parseIntAny(text string) (int64, error) {
	text = strings.TrimSpace(text)
	base := 10
	if strings.HasPrefix(strings.ToLower(text), "0x") {
		text, base = text[2:], 16
	}
	return strconv.ParseInt(text, base, 64)
}

// encodeTLV rebuilds a TLV payload from decoded output. Decoding flattens every channel
// into one map, so the channels are recovered from which field names are present and
// ordered by where those names appear in the decoded output.
func encodeTLV(field Field, data map[string]any, ctx *EncodeContext) error {
	// Python recovers channel order from its decoded output, whose keys are in payload
	// order. A Go map has no order at all, so the channels are emitted in ascending tag
	// order instead - which is how devices in this corpus lay them out, but is an
	// assumption rather than recovered information. The floor in corpus_encode_test.go
	// records how far it gets.
	type candidate struct {
		tag     []byte
		lossy   int
		matches int
		key     string
		fields  []Field
		claimed []string
	}
	candidates := []candidate{}
	for key, caseFields := range field.TLVCases {
		claimed := []string{}
		for _, f := range caseFields {
			if f.Name == "" || strings.HasPrefix(f.Name, "_") || f.Type == TypeNumber || f.Type == "number" {
				continue
			}
			if _, ok := data[f.Name]; ok {
				claimed = append(claimed, f.Name)
			}
		}
		if len(claimed) == 0 {
			continue
		}
		tag, err := encodeTLVTag(key, field, ctx)
		if err != nil {
			// A wildcard or negated key names a range of tags (PS-270); it cannot be
			// written, so it is not a candidate.
			continue
		}
		matches, lossless := caseFidelity(caseFields, data)
		lossy := 0
		if !lossless {
			lossy = 1
		}
		candidates = append(candidates, candidate{tag, lossy, matches, key, caseFields, claimed})
	}

	sort.SliceStable(candidates, func(i, j int) bool {
		if c := bytes.Compare(candidates[i].tag, candidates[j].tag); c != 0 {
			return c < 0
		}
		if candidates[i].lossy != candidates[j].lossy {
			return candidates[i].lossy < candidates[j].lossy
		}
		return candidates[i].matches > candidates[j].matches
	})

	// With the order DecodeOrdered reported, the channels go back exactly where they
	// were - including a tag that appears twice. Without it the sort above stands in.
	if len(ctx.TLVOrder) > 0 {
		byKey := map[string]candidate{}
		for _, c := range candidates {
			byKey[c.key] = c
		}
		emittedAny := false
		for _, key := range ctx.TLVOrder {
			c, ok := byKey[key]
			if !ok {
				continue
			}
			inner := NewEncodeContext(ctx.Endian)
			inner.Variables = ctx.Variables
			if err := encodeFieldList(c.fields, data, inner); err != nil {
				return err
			}
			ctx.Write(c.tag)
			if field.LengthSize > 0 {
				ctx.Write(encodeUint(uint64(len(inner.Buffer)), field.LengthSize, ctx.Endian))
			}
			ctx.Write(inner.Buffer)
			emittedAny = true
		}
		if emittedAny {
			return nil
		}
	}

	spent := map[string]bool{}
	chosen := []candidate{}
	for _, c := range candidates {
		allSpent := true
		for _, name := range c.claimed {
			if !spent[name] {
				allSpent = false
				break
			}
		}
		if allSpent {
			continue
		}
		for _, name := range c.claimed {
			spent[name] = true
		}
		chosen = append(chosen, c)
	}
	sort.SliceStable(chosen, func(i, j int) bool { return bytes.Compare(chosen[i].tag, chosen[j].tag) < 0 })

	for _, c := range chosen {
		inner := NewEncodeContext(ctx.Endian)
		inner.Variables = ctx.Variables
		if err := encodeFieldList(c.fields, data, inner); err != nil {
			return err
		}
		ctx.Write(c.tag)
		if field.LengthSize > 0 {
			ctx.Write(encodeUint(uint64(len(inner.Buffer)), field.LengthSize, ctx.Endian))
		}
		ctx.Write(inner.Buffer)
	}
	return nil
}

// encodeMatch writes a match construct's bytes. An inline match (length: N) read those
// bytes itself, so encoding writes them back; a match on field: $var read nothing,
// because the variable came from a field earlier in the list that is encoded on its own.
func encodeMatch(field Field, data map[string]any, ctx *EncodeContext) error {
	var discriminator any
	if field.Name != "" {
		if v, ok := data[field.Name]; ok {
			discriminator = v
		}
	}
	if discriminator == nil && field.On != "" {
		varName := strings.TrimPrefix(field.On, "$")
		if v, ok := data[varName]; ok {
			discriminator = v
		} else if v, ok := ctx.Variables[varName]; ok {
			discriminator = v
		}
	}

	var chosen []Field
	chosenKey := ""
	if discriminator != nil {
		if intVal, ok := toInt(discriminator); ok {
			for _, c := range field.Cases {
				if caseMatchesValue(intVal, c) {
					chosen, chosenKey = c.Fields, fmt.Sprintf("%v", c.Case)
					break
				}
			}
		}
	}
	if chosen == nil {
		best := -1
		for _, c := range field.Cases {
			hits := 0
			for _, f := range c.Fields {
				if f.Name != "" {
					if _, ok := data[f.Name]; ok {
						hits++
					}
				}
			}
			if hits > best {
				best, chosen, chosenKey = hits, c.Fields, fmt.Sprintf("%v", c.Case)
			}
		}
		if best <= 0 {
			return nil
		}
	}

	if field.Length > 0 {
		value := int64(0)
		if intVal, ok := toInt(discriminator); ok {
			value = int64(intVal)
		} else if v, err := parseIntAny(chosenKey); err == nil {
			value = v
		}
		ctx.Write(encodeUint(uint64(value), field.Length, ctx.Endian))
	}
	return encodeFieldList(chosen, data, ctx)
}

// encodeByteGroup packs a group's bit ranges back into their shared byte(s). Encoding had
// no case for the construct, so it emitted a single zero byte: right length, wrong bits.
func encodeByteGroup(field Field, data map[string]any, ctx *EncodeContext) error {
	size := field.Size
	if size <= 0 {
		size = 1
	}
	packed := uint64(0)
	for _, gf := range field.ByteGroup {
		var value any = 0
		if gf.Name != "" && !strings.HasPrefix(gf.Name, "_") {
			if v, ok := data[gf.Name]; ok {
				value = v
			}
		}
		raw, ok := rawForField(gf, value)
		if !ok {
			continue
		}
		num := uint64(int64(math.Round(raw)))
		if m := bitRangePattern.FindStringSubmatch(string(gf.Type)); m != nil {
			bits, _ := strconv.Atoi(strings.TrimLeft(m[1], "usf"))
			start, _ := strconv.Atoi(m[2])
			end, _ := strconv.Atoi(m[3])
			if base := bits / 8; base > size {
				size = base
			}
			width := uint(end - start + 1)
			packed |= (num & ((1 << width) - 1)) << uint(start)
		} else {
			packed |= num
		}
	}
	ctx.Write(encodeUint(packed, size, ctx.Endian))
	return nil
}

// encodeFieldList encodes a list of plain fields - a TLV case's or match case's value
// bytes - resolving nested constructs the same way the top-level loop does.
func encodeFieldList(fields []Field, data map[string]any, ctx *EncodeContext) error {
	for _, f := range fields {
		if f.MatchInline != nil {
			if err := encodeMatch(*f.MatchInline, data, ctx); err != nil {
				return err
			}
			continue
		}
		if f.TLVInline != nil {
			if err := encodeTLV(*f.TLVInline, data, ctx); err != nil {
				return err
			}
			continue
		}
		if len(f.ByteGroup) > 0 {
			if err := encodeByteGroup(f, data, ctx); err != nil {
				return err
			}
			continue
		}
		if f.Type == TypeNumber || f.Type == "number" {
			continue
		}
		if f.Type == "skip" {
			length := f.Length
			if length < 0 {
				length = 0
			}
			ctx.Write(make([]byte, length))
			continue
		}
		if f.Name == "" || strings.HasPrefix(f.Name, "_") {
			if err := encodeField(f, 0, ctx); err != nil {
				return err
			}
			continue
		}
		value, ok := data[f.Name]
		if !ok {
			value = 0
		}
		if f.Type == TypeBitfieldString {
			if strVal, isStr := value.(string); isStr {
				if err := encodeBitfieldString(f, strVal, ctx); err != nil {
					return err
				}
			}
			continue
		}
		if err := encodeField(f, value, ctx); err != nil {
			return err
		}
	}
	return nil
}


// encodeRepeat writes a repeat's records back to back. The framing costs no bytes here:
// count: $n and byte_length: $len name a field earlier in the list, encoded on its own.
func encodeRepeat(field Field, data map[string]any, ctx *EncodeContext) error {
	raw, ok := data[field.Name]
	if !ok {
		return nil
	}
	records, ok := raw.([]any)
	if !ok {
		return fmt.Errorf("repeat field %q: expected a list of records", field.Name)
	}
	for _, item := range records {
		record, ok := item.(map[string]any)
		if !ok {
			return fmt.Errorf("repeat field %q: expected each record to be a mapping", field.Name)
		}
		if err := encodeFieldList(field.Fields, record, ctx); err != nil {
			return err
		}
	}
	return nil
}

func encodeFields(fields []Field, data map[string]any, ctx *EncodeContext) error {
	// Pre-scan flagged constructs to compute flag values
	flagsPatches := map[string]int{}
	for _, field := range fields {
		if field.Flagged != nil {
			flags := 0
			for _, group := range field.Flagged.Groups {
				for _, gf := range group.Fields {
					if gf.Name != "" {
						if _, ok := data[gf.Name]; ok {
							flags |= (1 << group.Bit)
							break
						}
					}
				}
			}
			flagsPatches[field.Flagged.Field] = flags
		}
	}

	for _, field := range fields {
		// Flagged construct
		if field.Flagged != nil {
			if err := encodeFlagged(field.Flagged, data, ctx); err != nil {
				return err
			}
			continue
		}

		if field.TLVInline != nil {
			if err := encodeTLV(*field.TLVInline, data, ctx); err != nil {
				return err
			}
			continue
		}

		if field.MatchInline != nil {
			if err := encodeMatch(*field.MatchInline, data, ctx); err != nil {
				return err
			}
			continue
		}

		if len(field.ByteGroup) > 0 {
			if err := encodeByteGroup(field, data, ctx); err != nil {
				return err
			}
			continue
		}

		if field.Type == "repeat" {
			if err := encodeRepeat(field, data, ctx); err != nil {
				return err
			}
			continue
		}

		if field.Name == "" || strings.HasPrefix(field.Name, "_") {
			continue
		}

		// Skip computed fields
		// A derived value is computed from other fields and occupies no bytes of its own.
		// This required the deprecated `formula` spelling, so `ref`, `compute`,
		// `polynomial` and `guard` fields were encoded as though they were on the wire.
		if field.Type == TypeNumber || field.Type == "number" {
			continue
		}

		// Bitfield string encoding
		if field.Type == TypeBitfieldString {
			if strVal, ok := data[field.Name].(string); ok {
				if err := encodeBitfieldString(field, strVal, ctx); err != nil {
					return err
				}
			}
			continue
		}

		// Patch flags value
		var value any
		if patchedFlags, ok := flagsPatches[field.Name]; ok {
			value = float64(patchedFlags)
		} else {
			var exists bool
			value, exists = data[field.Name]
			if !exists {
				continue
			}
		}

		if err := encodeField(field, value, ctx); err != nil {
			return err
		}
	}
	return nil
}

func encodeFlagged(fd *FlaggedDef, data map[string]any, ctx *EncodeContext) error {
	flags := 0
	for _, group := range fd.Groups {
		for _, gf := range group.Fields {
			if gf.Name != "" {
				if _, ok := data[gf.Name]; ok {
					flags |= (1 << group.Bit)
					break
				}
			}
		}
	}

	for _, group := range fd.Groups {
		if (flags>>group.Bit)&1 == 0 {
			continue
		}
		for _, gf := range group.Fields {
			if gf.Name == "" || strings.HasPrefix(gf.Name, "_") {
				continue
			}
			if gf.Formula != "" && (gf.Type == TypeNumber || gf.Type == "number") {
				continue
			}
			value, ok := data[gf.Name]
			if !ok {
				continue
			}
			if err := encodeField(gf, value, ctx); err != nil {
				return err
			}
		}
	}
	return nil
}

func encodeBitfieldString(field Field, strVal string, ctx *EncodeContext) error {
	parts := field.Parts
	delimiter := field.Delimiter
	if delimiter == "" {
		delimiter = "."
	}
	prefix := field.Prefix

	if prefix != "" && strings.HasPrefix(strVal, prefix) {
		strVal = strVal[len(prefix):]
	}

	segments := strings.Split(strVal, delimiter)
	length := field.Length
	if length == 0 {
		length = 2
	}
	endian := field.Endian
	if endian == "" {
		endian = ctx.Endian
	}

	var intVal uint64
	for i, part := range parts {
		if len(part) < 2 {
			continue
		}
		bitOff := 0
		bitLen := 8
		format := "decimal"
		if f, ok := part[0].(float64); ok {
			bitOff = int(f)
		} else if f, ok := part[0].(int); ok {
			bitOff = f
		}
		if f, ok := part[1].(float64); ok {
			bitLen = int(f)
		} else if f, ok := part[1].(int); ok {
			bitLen = f
		}
		if len(part) > 2 {
			if s, ok := part[2].(string); ok {
				format = s
			}
		}
		seg := "0"
		if i < len(segments) {
			seg = segments[i]
		}
		var val uint64
		if format == "hex" {
			v, _ := strconv.ParseUint(seg, 16, 64)
			val = v
		} else {
			v, _ := strconv.ParseUint(seg, 10, 64)
			val = v
		}
		mask := uint64((1 << bitLen) - 1)
		intVal |= (val & mask) << bitOff
	}

	ctx.Write(encodeUint(intVal, length, endian))
	return nil
}

func encodeField(field Field, value any, ctx *EncodeContext) error {
	length := field.Length
	if length == 0 {
		length = inferLengthFromType(field.Type)
	}
	endian := field.Endian
	if endian == "" {
		endian = ctx.Endian
	}

	// Reverse lookup if value is a string and lookup exists
	if strVal, ok := value.(string); ok && field.Lookup != nil {
		for k, v := range field.Lookup {
			if v == strVal {
				value = float64(k)
				break
			}
		}
	}

	// Reverse modifiers for numeric values
	if numVal, ok := toFloat64(value); ok {
		// Reverse stages in reverse order; within each stage, reverse ops
		if len(field.Transform) > 0 {
			for i := len(field.Transform) - 1; i >= 0; i-- {
				stage := field.Transform[i]
				if stage.Div != nil {
					numVal = numVal * *stage.Div
				}
				if stage.Mult != nil {
					numVal = numVal / *stage.Mult
				}
				if stage.Add != nil {
					numVal = numVal - *stage.Add
				}
			}
		} else if len(field.Modifiers) > 0 {
			for i := len(field.Modifiers) - 1; i >= 0; i-- {
				stage := field.Modifiers[i]
				if stage.Div != nil {
					numVal = numVal * *stage.Div
				}
				if stage.Mult != nil {
					numVal = numVal / *stage.Mult
				}
				if stage.Add != nil {
					numVal = numVal - *stage.Add
				}
			}
		// Top-level shortcuts — inverse of the canonical decode order. Decoding
		// computes ((raw * mult) / div) + add, so encoding subtracts add first,
		// then multiplies by div, then divides by mult (PS-101).
		} else {
			numVal = reverseCanonicalModifiers(numVal, field)
		}
		value = numVal
	}

	switch field.Type {
	case TypeByte, TypeUInt, TypeU8, TypeU16, TypeU32, TypeU64:
		if numVal, ok := toFloat64(value); ok {
			ctx.Write(encodeUint(uint64(numVal), length, endian))
		}

	case TypeSInt, TypeS8, TypeS16, TypeS32, TypeS64, TypeI8, TypeI16, TypeI32, TypeI64:
		if numVal, ok := toFloat64(value); ok {
			ctx.Write(encodeSint(int64(numVal), length, endian))
		}

	case TypeFloat32, TypeF32:
		if numVal, ok := toFloat64(value); ok {
			ctx.Write(encodeFloat32(float32(numVal), endian))
		}

	case TypeFloat64, TypeF64:
		if numVal, ok := toFloat64(value); ok {
			ctx.Write(encodeFloat64(numVal, endian))
		}

	// Encoding covered far fewer type spellings than decoding: `hex`, `ascii`,
	// `string`, `bool` and `enum` are all written lowercase in every schema, and the
	// switch matched only the capitalised constants, so those fields silently wrote no
	// bytes. am102's 8-byte serial number emitted its tag and then nothing.
	case TypeAscii, TypeAsciiLower, TypeString, TypeStringLower:
		if strVal, ok := value.(string); ok {
			if length <= 0 {
				length = len(strVal)
			}
			data := make([]byte, length)
			copy(data, []byte(strVal))
			ctx.Write(data)
		}

	case TypeHex, TypeHexLower, TypeHexUpperLower:
		if strVal, ok := value.(string); ok {
			strVal = strings.ReplaceAll(strVal, ":", "")
			strVal = strings.ReplaceAll(strVal, "-", "")
			data, err := hex.DecodeString(strVal)
			if err != nil {
				return fmt.Errorf("hex field %q: %v", field.Name, err)
			}
			if length <= 0 {
				length = len(data)
			}
			padded := make([]byte, length)
			copy(padded, data)
			ctx.Write(padded)
		}

	case TypeBool, TypeBoolLower:
		b := byte(0)
		switch v := value.(type) {
		case bool:
			if v {
				b = 1
			}
		default:
			if num, ok := toFloat64(value); ok && num != 0 {
				b = 1
			}
		}
		ctx.Write([]byte{b})

	case TypeEnum, TypeEnumLower:
		// The label maps back through `values`; an unmapped one cannot be recovered.
		if strVal, ok := value.(string); ok {
			// A `type: enum` field keeps its mapping in Values; Lookup is the separate
			// `lookup:` modifier. Reading the wrong one made every label unknown.
			tables := []map[int]string{field.Values, field.Lookup}
			for _, table := range tables {
				for k, v := range table {
					if v == strVal {
						ctx.Write(encodeUint(uint64(k), maxInt(length, 1), endian))
						return nil
					}
				}
			}
			return fmt.Errorf(
				"enum field %q: %q is not one of its values; an unmapped value is "+
					"reported through `default` (PS-068), and that label cannot be "+
					"traced back to the value that produced it", field.Name, strVal)
		}
		if num, ok := toFloat64(value); ok {
			ctx.Write(encodeUint(uint64(int64(num)), maxInt(length, 1), endian))
		}

	case TypeBytes, TypeBytesLower:
		if err := encodeBytes(field, value, length, ctx); err != nil {
			return err
		}

	case TypeObject, TypeObjectLower:
		if mapVal, ok := value.(map[string]any); ok {
			if err := encodeFields(field.Fields, mapVal, ctx); err != nil {
				return err
			}
		}

	case TypeRepeat, TypeRepeatLower:
		if arrVal, ok := value.([]any); ok {
			for _, elem := range arrVal {
				if elemMap, ok := elem.(map[string]any); ok {
					if err := encodeFields(field.Fields, elemMap, ctx); err != nil {
						return err
					}
				}
			}
		}

	case TypeSkip, TypeSkipLower:
		ctx.Write(make([]byte, length))
	}

	return nil
}

func encodeBytes(field Field, value any, length int, ctx *EncodeContext) error {
	var data []byte

	switch v := value.(type) {
	case string:
		// Try to detect format
		if strings.Contains(v, ":") || strings.Contains(v, "-") {
			// Has separator - strip it
			hexStr := strings.ReplaceAll(v, ":", "")
			hexStr = strings.ReplaceAll(hexStr, "-", "")
			data, _ = hex.DecodeString(hexStr)
		} else if len(v)%4 == 0 && len(v) > 0 {
			// Try base64
			if decoded, err := base64.StdEncoding.DecodeString(v); err == nil && len(decoded) == length {
				data = decoded
			} else {
				data, _ = hex.DecodeString(v)
			}
		} else {
			data, _ = hex.DecodeString(v)
		}

	case []any:
		data = make([]byte, len(v))
		for i, b := range v {
			if num, ok := toFloat64(b); ok {
				data[i] = byte(num)
			}
		}

	case []byte:
		data = v
	}

	// `length: remaining` (PS-014) parses to the negative sentinel, and it gives no
	// fixed count when encoding - the value supplies its own. make([]byte, -1) panicked,
	// which is how radio-bridge's stored downlink brought the encoder down rather than
	// reporting anything.
	if length < 0 {
		length = len(data)
	}

	// Pad or truncate to exact length
	padded := make([]byte, length)
	copy(padded, data)
	ctx.Write(padded)

	return nil
}


func maxInt(a, b int) int {
	if a > b {
		return a
	}
	return b
}

func encodeUint(val uint64, length int, endian string) []byte {
	buf := make([]byte, length)
	if endian == "little" {
		for i := 0; i < length; i++ {
			buf[i] = byte(val >> (8 * i))
		}
	} else {
		for i := length - 1; i >= 0; i-- {
			buf[i] = byte(val)
			val >>= 8
		}
	}
	return buf
}

func encodeSint(val int64, length int, endian string) []byte {
	// Convert to unsigned for encoding
	if val < 0 {
		val = (1 << (length * 8)) + val
	}
	return encodeUint(uint64(val), length, endian)
}

func encodeFloat32(val float32, endian string) []byte {
	buf := make([]byte, 4)
	bits := math.Float32bits(val)
	if endian == "little" {
		binary.LittleEndian.PutUint32(buf, bits)
	} else {
		binary.BigEndian.PutUint32(buf, bits)
	}
	return buf
}

func encodeFloat64(val float64, endian string) []byte {
	buf := make([]byte, 8)
	bits := math.Float64bits(val)
	if endian == "little" {
		binary.LittleEndian.PutUint64(buf, bits)
	} else {
		binary.BigEndian.PutUint64(buf, bits)
	}
	return buf
}

// =============================================================================
// Helper functions
// =============================================================================

func decodeUint(data []byte, endian string) uint64 {
	var val uint64
	if endian == "little" {
		for i := len(data) - 1; i >= 0; i-- {
			val = (val << 8) | uint64(data[i])
		}
	} else {
		for _, b := range data {
			val = (val << 8) | uint64(b)
		}
	}
	return val
}

func decodeSint(data []byte, endian string) int64 {
	uval := decodeUint(data, endian)
	bits := len(data) * 8
	signBit := uint64(1) << (bits - 1)
	if uval >= signBit {
		return int64(uval) - (1 << bits)
	}
	return int64(uval)
}

func decodeFloat(data []byte, size int, endian string) (float64, error) {
	switch size {
	case 2:
		// Float16
		var u16 uint16
		if endian == "little" {
			u16 = binary.LittleEndian.Uint16(data)
		} else {
			u16 = binary.BigEndian.Uint16(data)
		}
		return float16ToFloat64(u16), nil
	case 4:
		var u32 uint32
		if endian == "little" {
			u32 = binary.LittleEndian.Uint32(data)
		} else {
			u32 = binary.BigEndian.Uint32(data)
		}
		return float64(math.Float32frombits(u32)), nil
	case 8:
		var u64 uint64
		if endian == "little" {
			u64 = binary.LittleEndian.Uint64(data)
		} else {
			u64 = binary.BigEndian.Uint64(data)
		}
		return math.Float64frombits(u64), nil
	default:
		return 0, fmt.Errorf("unsupported float size: %d", size)
	}
}

func float16ToFloat64(u16 uint16) float64 {
	sign := (u16 >> 15) & 0x1
	exp := (u16 >> 10) & 0x1f
	mant := u16 & 0x3ff

	var val float64
	if exp == 0 {
		// Subnormal or zero
		val = math.Pow(2, -14) * float64(mant) / 1024
	} else if exp == 31 {
		// Inf or NaN
		if mant != 0 {
			return math.NaN()
		}
		val = math.Inf(1)
	} else {
		val = math.Pow(2, float64(exp)-15) * (1 + float64(mant)/1024)
	}

	if sign == 1 {
		val = -val
	}
	return val
}

func decodeBits(byteVal byte, bitOffset, bits int) int {
	mask := (1 << bits) - 1
	return (int(byteVal) >> bitOffset) & mask
}

func toFloat64(v any) (float64, bool) {
	switch val := v.(type) {
	case float64:
		return val, true
	case float32:
		return float64(val), true
	case int:
		return float64(val), true
	case int64:
		return float64(val), true
	case uint64:
		return float64(val), true
	case uint:
		return float64(val), true
	}
	return 0, false
}

func toInt(v any) (int, bool) {
	switch val := v.(type) {
	case int:
		return val, true
	case int64:
		return int(val), true
	case uint64:
		return int(val), true
	case float64:
		return int(val), true
	case float32:
		return int(val), true
	}
	return 0, false
}

// Compact format parsing

var compactFormatPattern = regexp.MustCompile(`(\d*)([a-zA-Z?]):?(\w*)`)

var structFormats = map[byte]struct {
	Type   FieldType
	Length int
}{
	'b': {TypeSInt, 1},
	'B': {TypeUInt, 1},
	'h': {TypeSInt, 2},
	'H': {TypeUInt, 2},
	'i': {TypeSInt, 4},
	'I': {TypeUInt, 4},
	'l': {TypeSInt, 4},
	'L': {TypeUInt, 4},
	'q': {TypeSInt, 8},
	'Q': {TypeUInt, 8},
	'e': {TypeFloat16, 2},
	'f': {TypeFloat32, 4},
	'd': {TypeFloat64, 8},
	'?': {TypeBool, 1},
	'c': {TypeByte, 1},
	'x': {TypeSkip, 1},
	's': {TypeAscii, 0},
	'p': {TypeAscii, 0},
}

var byteOrderPrefixes = map[byte]string{
	'>': "big",
	'<': "little",
	'!': "big",
	'=': "native",
	'@': "native",
}

// ParseCompactFormat parses a Python struct-like format string into fields.
func ParseCompactFormat(format string) ([]Field, string, error) {
	endian := "big"

	if len(format) > 0 {
		if e, ok := byteOrderPrefixes[format[0]]; ok {
			endian = e
			format = format[1:]
		}
	}

	var fields []Field
	matches := compactFormatPattern.FindAllStringSubmatch(format, -1)

	for _, match := range matches {
		countStr, fmtChar, name := match[1], match[2][0], match[3]

		count := 1
		if countStr != "" {
			count, _ = strconv.Atoi(countStr)
		}

		spec, ok := structFormats[fmtChar]
		if !ok {
			return nil, "", fmt.Errorf("unknown format character: %c", fmtChar)
		}

		length := spec.Length
		if fmtChar == 's' || fmtChar == 'p' {
			length = count
			count = 1
		}

		for i := 0; i < count; i++ {
			field := Field{
				Type:   spec.Type,
				Length: length,
				Endian: endian,
			}
			if name != "" {
				if count > 1 {
					field.Name = fmt.Sprintf("%s_%d", name, i)
				} else {
					field.Name = name
				}
			}
			fields = append(fields, field)
		}
	}

	return fields, endian, nil
}

// DecodeCompact decodes binary data using a compact format string.
func DecodeCompact(format string, data []byte) (map[string]any, error) {
	fields, endian, err := ParseCompactFormat(format)
	if err != nil {
		return nil, err
	}

	ctx := NewDecodeContext(data, endian)
	return decodeFields(fields, ctx)
}

// =============================================================================
// Formula evaluator
// =============================================================================

// evaluateFormula evaluates a formula expression with variable substitution.
// evaluatePolynomial evaluates a polynomial using Horner's method.
// coefficients are in order [a_n, a_{n-1}, ..., a_1, a_0].
func evaluatePolynomial(coeffs []float64, x float64) float64 {
	if len(coeffs) == 0 {
		return 0
	}
	result := coeffs[0]
	for i := 1; i < len(coeffs); i++ {
		result = result*x + coeffs[i]
	}
	return result
}

// evaluateCompute evaluates a binary operation.
func evaluateCompute(cd *ComputeDef, ctx *DecodeContext) (float64, error) {
	a, err := resolveOperand(cd.A, ctx)
	if err != nil {
		return 0, err
	}
	b, err := resolveOperand(cd.B, ctx)
	if err != nil {
		return 0, err
	}

	switch cd.Op {
	case "div":
		if b == 0 {
			return 0, errComputeOmitted
		}
		return a / b, nil
	case "mul":
		return a * b, nil
	case "add":
		return a + b, nil
	case "sub":
		return a - b, nil
	case "mod":
		if b == 0 {
			return 0, errComputeOmitted
		}
		// PS-277 floored: the remainder takes the divisor's sign, so mod(a, 8) stays
		// in 0..7. Go's native % truncates, which gave -1 where the floored answer
		// is 2. PS-284: operands truncate toward zero first.
		return float64(floorMod(int64(a), int64(b))), nil
	case "idiv":
		if b == 0 {
			return 0, errComputeOmitted
		}
		// PS-276 floored: rounds toward negative infinity, not toward zero.
		return float64(floorDiv(int64(a), int64(b))), nil
	default:
		return 0, fmt.Errorf("unknown compute op: %s", cd.Op)
	}
}

// errComputeOmitted signals a zero divisor. PS-278: the field is reported absent and
// decoding of the rest of the payload continues. Previously this was a plain error,
// which abandoned the field and, through the caller, could take the payload with it.
var errComputeOmitted = errors.New("compute omitted: division by zero")

// floorDiv is integer division rounded toward negative infinity (PS-276).
//
// Corrected on the integers deliberately. The obvious
// int64(math.Floor(float64(a)/float64(b))) is wrong above 2^53, where an int64 is not
// exactly representable as a float64: for a = 2^53+1, b = 1 it yields 9007199254740992
// instead of 9007199254740993.
func floorDiv(a, b int64) int64 {
	q := a / b
	if (a%b != 0) && ((a < 0) != (b < 0)) {
		q--
	}
	return q
}

// floorMod is the remainder matching floorDiv, so that
// a == floorDiv(a, b)*b + floorMod(a, b) holds for every combination of signs
// (PS-277). Exact for the full int64 range, and gives the divisor's sign.
func floorMod(a, b int64) int64 {
	return ((a % b) + b) % b
}

// resolveOperand resolves a compute operand (field reference or literal).
func resolveOperand(op string, ctx *DecodeContext) (float64, error) {
	if strings.HasPrefix(op, "$") {
		name := op[1:]
		if val, ok := ctx.Variables[name]; ok {
			if f, ok := toFloat64(val); ok {
				return f, nil
			}
		}
		return 0, fmt.Errorf("operand field not found: %s", name)
	}
	return strconv.ParseFloat(op, 64)
}

// evaluateGuard applies guard conditions, returning value if all pass or else.
// guardConditionsHold reports whether every condition of a guard is satisfied.
func guardConditionsHold(gd *GuardDef, ctx *DecodeContext) bool {
	for _, cond := range gd.When {
		fieldName := strings.TrimPrefix(cond.Field, "$")
		fieldVal, ok := ctx.Variables[fieldName]
		if !ok {
			return false
		}
		fv, ok := toFloat64(fieldVal)
		if !ok {
			return false
		}
		if cond.Gt != nil && !(fv > *cond.Gt) {
			return false
		}
		if cond.Gte != nil && !(fv >= *cond.Gte) {
			return false
		}
		if cond.Lt != nil && !(fv < *cond.Lt) {
			return false
		}
		if cond.Lte != nil && !(fv <= *cond.Lte) {
			return false
		}
		if cond.Eq != nil && !(fv == *cond.Eq) {
			return false
		}
		if cond.Ne != nil && !(fv != *cond.Ne) {
			return false
		}
	}
	return true
}

func evaluateGuard(gd *GuardDef, value float64, ctx *DecodeContext) float64 {
	for _, cond := range gd.When {
		fieldName := strings.TrimPrefix(cond.Field, "$")
		fieldVal, ok := ctx.Variables[fieldName]
		if !ok {
			return gd.Else
		}
		fv, ok := toFloat64(fieldVal)
		if !ok {
			return gd.Else
		}

		// Check all conditions on this field
		if cond.Gt != nil && !(fv > *cond.Gt) {
			return gd.Else
		}
		if cond.Gte != nil && !(fv >= *cond.Gte) {
			return gd.Else
		}
		if cond.Lt != nil && !(fv < *cond.Lt) {
			return gd.Else
		}
		if cond.Lte != nil && !(fv <= *cond.Lte) {
			return gd.Else
		}
		if cond.Eq != nil && fv != *cond.Eq {
			return gd.Else
		}
		if cond.Ne != nil && fv == *cond.Ne {
			return gd.Else
		}
	}
	return value
}

// evaluateFormula (DEPRECATED - use polynomial/compute/guard instead)
// Supports: $field_name references, x (raw value), pow/abs/sqrt/min/max,
// arithmetic operators, ternary (cond ? a : b), and/or.
func evaluateFormula(formula string, x float64, ctx *DecodeContext) (float64, error) {
	expr := formula

	// Substitute $field_name references
	varPattern := regexp.MustCompile(`\$([a-zA-Z_][a-zA-Z0-9_]*)`)
	expr = varPattern.ReplaceAllStringFunc(expr, func(match string) string {
		name := match[1:]
		if val, ok := ctx.Variables[name]; ok {
			if f, ok := toFloat64(val); ok {
				return strconv.FormatFloat(f, 'f', -1, 64)
			}
		}
		return "0"
	})

	// Replace standalone 'x' with raw value
	xPattern := regexp.MustCompile(`\bx\b`)
	expr = xPattern.ReplaceAllString(expr, strconv.FormatFloat(x, 'f', -1, 64))

	// Replace 'and'/'or' with Go-compatible tokens for our evaluator
	expr = regexp.MustCompile(`\band\b`).ReplaceAllString(expr, "&&")
	expr = regexp.MustCompile(`\bor\b`).ReplaceAllString(expr, "||")

	return evalExpr(expr)
}

// evalExpr is a simple recursive descent expression parser.
// Supports: +, -, *, /, >, <, >=, <=, ==, !=, &&, ||, ternary (? :),
// pow(), abs(), sqrt(), min(), max(), parentheses, and numeric literals.
func evalExpr(expr string) (float64, error) {
	p := &exprParser{input: strings.TrimSpace(expr), pos: 0}
	val, err := p.parseTernary()
	if err != nil {
		return 0, fmt.Errorf("formula eval failed for %q: %w", expr, err)
	}
	return val, nil
}

type exprParser struct {
	input string
	pos   int
}

func (p *exprParser) skipSpaces() {
	for p.pos < len(p.input) && p.input[p.pos] == ' ' {
		p.pos++
	}
}

func (p *exprParser) peek() byte {
	p.skipSpaces()
	if p.pos >= len(p.input) {
		return 0
	}
	return p.input[p.pos]
}

func (p *exprParser) peekStr(n int) string {
	p.skipSpaces()
	end := p.pos + n
	if end > len(p.input) {
		end = len(p.input)
	}
	return p.input[p.pos:end]
}

func (p *exprParser) parseTernary() (float64, error) {
	val, err := p.parseOr()
	if err != nil {
		return 0, err
	}
	p.skipSpaces()
	if p.pos < len(p.input) && p.input[p.pos] == '?' {
		p.pos++
		trueVal, err := p.parseTernary()
		if err != nil {
			return 0, err
		}
		p.skipSpaces()
		if p.pos < len(p.input) && p.input[p.pos] == ':' {
			p.pos++
			falseVal, err := p.parseTernary()
			if err != nil {
				return 0, err
			}
			if val != 0 {
				return trueVal, nil
			}
			return falseVal, nil
		}
		return 0, fmt.Errorf("expected ':' in ternary")
	}
	return val, nil
}

func (p *exprParser) parseOr() (float64, error) {
	val, err := p.parseAnd()
	if err != nil {
		return 0, err
	}
	for {
		if p.peekStr(2) == "||" {
			p.pos += 2
			right, err := p.parseAnd()
			if err != nil {
				return 0, err
			}
			if val != 0 || right != 0 {
				val = 1
			} else {
				val = 0
			}
		} else {
			break
		}
	}
	return val, nil
}

func (p *exprParser) parseAnd() (float64, error) {
	val, err := p.parseComparison()
	if err != nil {
		return 0, err
	}
	for {
		if p.peekStr(2) == "&&" {
			p.pos += 2
			right, err := p.parseComparison()
			if err != nil {
				return 0, err
			}
			if val != 0 && right != 0 {
				val = 1
			} else {
				val = 0
			}
		} else {
			break
		}
	}
	return val, nil
}

func (p *exprParser) parseComparison() (float64, error) {
	val, err := p.parseAddSub()
	if err != nil {
		return 0, err
	}
	for {
		p.skipSpaces()
		if p.peekStr(2) == ">=" {
			p.pos += 2
			right, err := p.parseAddSub()
			if err != nil {
				return 0, err
			}
			if val >= right { val = 1 } else { val = 0 }
		} else if p.peekStr(2) == "<=" {
			p.pos += 2
			right, err := p.parseAddSub()
			if err != nil {
				return 0, err
			}
			if val <= right { val = 1 } else { val = 0 }
		} else if p.peekStr(2) == "==" {
			p.pos += 2
			right, err := p.parseAddSub()
			if err != nil {
				return 0, err
			}
			if val == right { val = 1 } else { val = 0 }
		} else if p.peekStr(2) == "!=" {
			p.pos += 2
			right, err := p.parseAddSub()
			if err != nil {
				return 0, err
			}
			if val != right { val = 1 } else { val = 0 }
		} else if p.peek() == '>' {
			p.pos++
			right, err := p.parseAddSub()
			if err != nil {
				return 0, err
			}
			if val > right { val = 1 } else { val = 0 }
		} else if p.peek() == '<' {
			p.pos++
			right, err := p.parseAddSub()
			if err != nil {
				return 0, err
			}
			if val < right { val = 1 } else { val = 0 }
		} else {
			break
		}
	}
	return val, nil
}

func (p *exprParser) parseAddSub() (float64, error) {
	val, err := p.parseMulDiv()
	if err != nil {
		return 0, err
	}
	for {
		p.skipSpaces()
		if p.peek() == '+' {
			p.pos++
			right, err := p.parseMulDiv()
			if err != nil {
				return 0, err
			}
			val += right
		} else if p.peek() == '-' {
			p.pos++
			right, err := p.parseMulDiv()
			if err != nil {
				return 0, err
			}
			val -= right
		} else {
			break
		}
	}
	return val, nil
}

func (p *exprParser) parseMulDiv() (float64, error) {
	val, err := p.parseUnary()
	if err != nil {
		return 0, err
	}
	for {
		p.skipSpaces()
		if p.peek() == '*' {
			p.pos++
			right, err := p.parseUnary()
			if err != nil {
				return 0, err
			}
			val *= right
		} else if p.peek() == '/' {
			p.pos++
			right, err := p.parseUnary()
			if err != nil {
				return 0, err
			}
			if right == 0 {
				val = 0
			} else {
				val /= right
			}
		} else {
			break
		}
	}
	return val, nil
}

func (p *exprParser) parseUnary() (float64, error) {
	p.skipSpaces()
	if p.peek() == '-' {
		p.pos++
		val, err := p.parsePrimary()
		if err != nil {
			return 0, err
		}
		return -val, nil
	}
	return p.parsePrimary()
}

func (p *exprParser) parsePrimary() (float64, error) {
	p.skipSpaces()

	// Parenthesized expression
	if p.peek() == '(' {
		p.pos++
		val, err := p.parseTernary()
		if err != nil {
			return 0, err
		}
		p.skipSpaces()
		if p.peek() == ')' {
			p.pos++
		}
		return val, nil
	}

	// Function calls: pow, abs, sqrt, min, max
	for _, fname := range []string{"pow", "abs", "sqrt", "min", "max"} {
		if strings.HasPrefix(p.input[p.pos:], fname+"(") {
			p.pos += len(fname) + 1
			arg1, err := p.parseTernary()
			if err != nil {
				return 0, err
			}
			p.skipSpaces()

			switch fname {
			case "abs":
				if p.peek() == ')' { p.pos++ }
				return math.Abs(arg1), nil
			case "sqrt":
				if p.peek() == ')' { p.pos++ }
				return math.Sqrt(arg1), nil
			}

			// Two-argument functions
			if p.peek() == ',' {
				p.pos++
			}
			arg2, err := p.parseTernary()
			if err != nil {
				return 0, err
			}
			p.skipSpaces()
			if p.peek() == ')' { p.pos++ }

			switch fname {
			case "pow":
				return math.Pow(arg1, arg2), nil
			case "min":
				return math.Min(arg1, arg2), nil
			case "max":
				return math.Max(arg1, arg2), nil
			}
		}
	}

	// Number literal
	start := p.pos
	if p.pos < len(p.input) && (p.input[p.pos] == '-' || p.input[p.pos] == '+') {
		p.pos++
	}
	for p.pos < len(p.input) && (p.input[p.pos] >= '0' && p.input[p.pos] <= '9' || p.input[p.pos] == '.' || p.input[p.pos] == 'e' || p.input[p.pos] == 'E') {
		p.pos++
	}
	if p.pos > start {
		numStr := p.input[start:p.pos]
		val, err := strconv.ParseFloat(numStr, 64)
		if err != nil {
			return 0, fmt.Errorf("invalid number: %s", numStr)
		}
		return val, nil
	}

	return 0, fmt.Errorf("unexpected token at position %d: %q", p.pos, p.input[p.pos:])
}
