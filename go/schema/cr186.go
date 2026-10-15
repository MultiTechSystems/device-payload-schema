// Copyright (c) 2024-2026 Multitech Systems, Inc.
// SPDX-License-Identifier: MIT

package schema

import (
	"fmt"
	"math"
	"math/big"
	"regexp"
	"sort"
	"strconv"
	"strings"
)

// CR-2026-186 (PS-610 to PS-615): a numeric field may declare `time`, and is then reported
// as an instant in UTC in place of its number. The value after the field's modifiers counts
// seconds since `epoch` (1970-01-01T00:00:00Z where absent); `format` is unix, iso8601,
// calendar or pattern. A `type: number` field may derive the instant from `parts` decoded
// earlier (PS-614). A reference binds the number; only the reported value is formatted.
// An encoder parses the value back in its format (PS-615).
//
// Mirrors the CR-2026-186 block of tools/schema_interpreter.py function by function
// (time_field_error, format_time, time_from_parts, time_readback_error, parse_time,
// time_input_seconds, expand_time_parts); the texts are the spec prototype's. Instants are
// exact rationals (math/big): a float enters through its shortest round-trip decimal, never
// through float arithmetic, so a fractional value does not drift.

var timeFormatKeys = map[string][]string{
	"unix":     {"since", "milliseconds"},
	"iso8601":  {"fraction"},
	"calendar": {"month_labels", "keys"},
	"pattern":  {"pattern"},
}

var timeCommonKeys = []string{"format", "epoch", "parts"}

var timeParts = []string{"year", "month", "day", "hour", "minute", "second"}

var monthAbbr = []string{"Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct",
	"Nov", "Dec"}

var dayAbbr = []string{"Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"}

var timeRFC3339 = regexp.MustCompile(
	`^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2}):(\d{2})(\.\d+)?(Z|\+00:00)$`)

var timeEpochPattern = regexp.MustCompile(`^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d+)?Z$`)

var timePatternToken = regexp.MustCompile(`(?s)%(.)`)

var timeBitRange = regexp.MustCompile(`^[us](\d+)\[(\d+):(\d+)\]$`)

// timeNumericTypes are the numeric types besides the bit ranges (PS-610).
var timeNumericTypes = map[string]bool{
	"number": true, "integer": true, "f16": true, "bf16": true, "f32": true, "f64": true,
	"f32le16": true, "f32be16le": true, "udec": true, "sdec": true,
	"uflt16": true, "sflt16": true, "sflt24": true,
	"u8": true, "uint8": true, "u16": true, "uint16": true, "u24": true, "uint24": true,
	"u32": true, "uint32": true, "u64": true, "uint64": true,
	"u32le16": true, "s32le16": true, "u32be16le": true, "s32be16le": true,
	"s8": true, "i8": true, "int8": true, "s16": true, "i16": true, "int16": true,
	"s24": true, "i24": true, "int24": true, "s32": true, "i32": true, "int32": true,
	"s64": true, "i64": true, "int64": true,
}

// timeUnwrittenPartTypes are the types of a part field that reads no bytes (PS-615).
var timeUnwrittenPartTypes = map[string]bool{"number": true, "integer": true, "string": true,
	"boolean": true}

// isTimeField reports a field declaring a `time` mapping (PS-610).
func isTimeField(f Field) bool { return f.Time != nil }

// timeHasParts reports a time field deriving its instant from parts (PS-614).
func timeHasParts(f Field) bool {
	if f.Time == nil {
		return false
	}
	_, ok := f.Time["parts"]
	return ok
}

// floorDiv and floorMod (schema.go) are the floored integer division used below.

// daysFromCivil is the days since 1970-01-01 of a proleptic Gregorian date (Howard
// Hinnant's algorithm).
func daysFromCivil(y, m, d int64) int64 {
	if m <= 2 {
		y--
	}
	era := floorDiv(y, 400)
	yoe := y - era*400
	mm := m + 9
	if m > 2 {
		mm = m - 3
	}
	doy := (153*mm+2)/5 + d - 1
	doe := yoe*365 + yoe/4 - yoe/100 + doy
	return era*146097 + doe - 719468
}

func civilFromDays(z int64) (int64, int64, int64) {
	z += 719468
	era := floorDiv(z, 146097)
	doe := z - era*146097
	yoe := (doe - doe/1460 + doe/36524 - doe/146096) / 365
	doy := doe - (365*yoe + yoe/4 - yoe/100)
	mp := (5*doy + 2) / 153
	d := doy - (153*mp+2)/5 + 1
	m := mp + 3
	if mp >= 10 {
		m = mp - 9
	}
	y := yoe + era*400
	if m <= 2 {
		y++
	}
	return y, m, d
}

func daysInMonth(y, m int64) int64 {
	switch m {
	case 2:
		if (y%4 == 0 && y%100 != 0) || y%400 == 0 {
			return 29
		}
		return 28
	case 4, 6, 9, 11:
		return 30
	}
	return 31
}

func validCivil(y, mo, d, h, mi, s int64) bool {
	if !(1 <= y && y <= 9999 && 1 <= mo && mo <= 12 && 0 <= h && h <= 23 && 0 <= mi &&
		mi <= 59 && 0 <= s && s <= 59) {
		return false
	}
	return 1 <= d && d <= daysInMonth(y, mo)
}

func civilSeconds(y, mo, d, h, mi, s int64) int64 {
	return daysFromCivil(y, mo, d)*86400 + h*3600 + mi*60 + s
}

// parseRFC3339 is an RFC 3339 date-time in UTC (Z or +00:00) as exact seconds since 1970;
// nil where it is not one.
func parseRFC3339(v any) *big.Rat {
	text, ok := v.(string)
	if !ok {
		return nil
	}
	m := timeRFC3339.FindStringSubmatch(text)
	if m == nil {
		return nil
	}
	var p [6]int64
	for i := 0; i < 6; i++ {
		p[i], _ = strconv.ParseInt(m[i+1], 10, 64)
	}
	if !validCivil(p[0], p[1], p[2], p[3], p[4], p[5]) {
		return nil
	}
	out := new(big.Rat).SetInt64(civilSeconds(p[0], p[1], p[2], p[3], p[4], p[5]))
	if m[7] != "" {
		frac, _ := new(big.Rat).SetString("0" + m[7])
		out.Add(out, frac)
	}
	return out
}

// timeInstant is an `epoch` or `since`: an RFC 3339 date-time in UTC with a Z designator
// (PS-355); nil where it is not one.
func timeInstant(v any) *big.Rat {
	text, ok := v.(string)
	if !ok || !timeEpochPattern.MatchString(text) {
		return nil
	}
	return parseRFC3339(text)
}

// timeOffset is the instant a spec key names, or 0 where it is absent.
func timeOffset(spec map[string]any, key string) *big.Rat {
	if v, ok := spec[key]; ok {
		if r := timeInstant(v); r != nil {
			return r
		}
	}
	return new(big.Rat)
}

func timeNumeric(field map[string]any) bool {
	t, _ := field["type"].(string)
	return timeNumericTypes[t] || timeBitRange.MatchString(t)
}

func timeComputes(field map[string]any) bool {
	for _, k := range []string{"value", "compute", "ref", "polynomial"} {
		if _, ok := field[k]; ok {
			return true
		}
	}
	return false
}

func timeIsInt(v any) bool {
	switch v.(type) {
	case int, int8, int16, int32, int64, uint, uint8, uint16, uint32, uint64:
		return true
	}
	return false
}

// checkTimeField is PS-610 to PS-614 for one field mapping at load, in the spec
// prototype's text and order (check_time_fields).
func checkTimeField(field map[string]any) error {
	raw, has := field["time"]
	if !has {
		return nil
	}
	if _, typed := field["type"]; !typed {
		return nil
	}
	name := pyRepr(field["name"])
	if s, ok := field["name"].(string); ok {
		name = s
	}
	where := fmt.Sprintf("Field '%s'", name)
	spec := asStringMap(raw)
	format, _ := spec["format"].(string)
	if spec == nil || timeFormatKeys[format] == nil {
		return fmt.Errorf("%s: `time` declares a format of unix, iso8601, calendar or pattern "+
			"(PS-610)", where)
	}
	allowed := map[string]bool{}
	for _, k := range timeCommonKeys {
		allowed[k] = true
	}
	for _, k := range timeFormatKeys[format] {
		allowed[k] = true
	}
	var extra []string
	for k := range spec {
		if !allowed[k] {
			extra = append(extra, k)
		}
	}
	if len(extra) > 0 {
		sort.Strings(extra)
		return fmt.Errorf("%s: %s does not belong to the %s format (PS-610)", where,
			strings.Join(extra, ", "), format)
	}
	if _, lookup := field["lookup"]; !timeNumeric(field) || lookup {
		return fmt.Errorf("%s: `time` is declared on a numeric field, not %s (PS-610)", where,
			pyRepr(field["type"]))
	}
	for _, key := range []string{"epoch", "since"} {
		if v, ok := spec[key]; ok && timeInstant(v) == nil {
			return fmt.Errorf("%s: `%s` %s is not an RFC 3339 date-time in UTC with a Z "+
				"designator (PS-355, PS-610)", where, key, pyRepr(v))
		}
	}
	if v, ok := spec["milliseconds"]; ok {
		if _, isBool := v.(bool); !isBool {
			return fmt.Errorf("%s: `milliseconds` is true or false (PS-611)", where)
		}
	}
	if v, ok := spec["fraction"]; ok && v != nil {
		n, isInt := timeWhole(v) // a JSON-authored schema carries an integer as a float
		if !isInt || n < 1 || n > 9 {
			return fmt.Errorf("%s: `fraction` is an integer from 1 to 9 (PS-611)", where)
		}
	}
	if v, ok := spec["month_labels"]; ok && v != nil {
		labels, isList := v.([]any)
		good := isList && len(labels) == 12
		for _, l := range labels {
			if _, s := l.(string); !s {
				good = false
			}
		}
		if !good {
			return fmt.Errorf("%s: month_labels are exactly twelve strings (PS-411, PS-612)", where)
		}
	}
	if v, ok := spec["keys"]; ok && v != nil {
		keys := asStringMap(v)
		good := keys != nil
		for k, name := range keys {
			s, isText := name.(string)
			if !isTimePart(k) || !isText || s == "" {
				good = false
			}
		}
		if !good {
			return fmt.Errorf("%s: keys map part names to member names (PS-412)", where)
		}
		seen := map[string]bool{}
		for _, part := range timeParts {
			n := part
			if s, ok := keys[part].(string); ok {
				n = s
			}
			if seen[n] {
				return fmt.Errorf("%s: keys map two parts to one name (PS-412)", where)
			}
			seen[n] = true
		}
	}
	if format == "pattern" {
		pattern, isText := spec["pattern"].(string)
		if !isText {
			return fmt.Errorf("%s: a pattern format declares `pattern` (PS-613)", where)
		}
		tokens := timePatternToken.FindAllStringSubmatch(pattern, -1)
		percents := 0
		for _, t := range tokens {
			if !strings.Contains("YmdHMSLba%", t[1]) || len(t[1]) != 1 {
				return fmt.Errorf("%s: %%%s is not a pattern directive (PS-613)", where, t[1])
			}
			if t[1] == "%" {
				percents++
			}
		}
		if strings.Count(pattern, "%") != 2*percents+len(tokens)-percents {
			return fmt.Errorf("%s: a pattern ends with a lone %% (PS-613)", where)
		}
	}
	typ, _ := field["type"].(string)
	if rawParts, ok := spec["parts"]; ok && rawParts != nil {
		if typ != "number" || timeComputes(field) {
			return fmt.Errorf("%s: `parts` belongs to a `type: number` field that computes "+
				"nothing else (PS-614)", where)
		}
		parts := asStringMap(rawParts)
		good := parts != nil
		for k, v := range parts {
			s, isText := v.(string)
			if !isTimePart(k) || !isText || !strings.HasPrefix(s, "$") {
				good = false
			}
		}
		for _, k := range []string{"year", "month", "day"} {
			if _, has := parts[k]; !has {
				good = false
			}
		}
		if !good {
			return fmt.Errorf("%s: `parts` maps year, month and day, and optionally hour, "+
				"minute and second, to $references (PS-614)", where)
		}
		if _, has := spec["epoch"]; has {
			return fmt.Errorf("%s: `epoch` does not apply to `parts` (PS-614)", where)
		}
	} else if typ == "number" && !timeComputes(field) {
		return fmt.Errorf("%s: a computed `time` field declares `parts` or a computation "+
			"(PS-614)", where)
	}
	return nil
}

func isTimePart(k string) bool {
	for _, p := range timeParts {
		if p == k {
			return true
		}
	}
	return false
}

// timeRat is a decoded or given number as an exact rational: a float by its shortest
// round-trip decimal text.
func timeRat(v any) (*big.Rat, bool) {
	switch x := v.(type) {
	case bool:
		return nil, false
	case int:
		return new(big.Rat).SetInt64(int64(x)), true
	case int8:
		return new(big.Rat).SetInt64(int64(x)), true
	case int16:
		return new(big.Rat).SetInt64(int64(x)), true
	case int32:
		return new(big.Rat).SetInt64(int64(x)), true
	case int64:
		return new(big.Rat).SetInt64(x), true
	case uint:
		return new(big.Rat).SetUint64(uint64(x)), true
	case uint8:
		return new(big.Rat).SetUint64(uint64(x)), true
	case uint16:
		return new(big.Rat).SetUint64(uint64(x)), true
	case uint32:
		return new(big.Rat).SetUint64(uint64(x)), true
	case uint64:
		return new(big.Rat).SetUint64(x), true
	case float32:
		return timeRat(float64(x))
	case float64:
		if math.IsNaN(x) || math.IsInf(x, 0) {
			return nil, false
		}
		r, ok := new(big.Rat).SetString(strconv.FormatFloat(x, 'g', -1, 64))
		return r, ok
	}
	return nil, false
}

// ratFloor is the largest integer not above r.
func ratFloor(r *big.Rat) *big.Int {
	return new(big.Int).Div(r.Num(), r.Denom()) // Euclidean: floor for a positive divisor
}

// fractionDigits is `count` fractional digits of a fraction in [0, 1), truncated.
func fractionDigits(frac *big.Rat, count int) string {
	scale := new(big.Int).Exp(big.NewInt(10), big.NewInt(int64(count)), nil)
	n := ratFloor(new(big.Rat).Mul(frac, new(big.Rat).SetInt(scale)))
	text := n.String()
	for len(text) < count {
		text = "0" + text
	}
	return text
}

// fractionNormalized is a finite decimal fraction in (0, 1) with no trailing zeros.
func fractionNormalized(frac *big.Rat) string {
	var b strings.Builder
	rest := new(big.Rat).Set(frac)
	ten := big.NewRat(10, 1)
	for i := 0; i < 1000 && rest.Sign() != 0; i++ {
		rest.Mul(rest, ten)
		digit := ratFloor(rest)
		b.WriteString(digit.String())
		rest.Sub(rest, new(big.Rat).SetInt(digit))
	}
	return b.String()
}

// ratDecimal writes an exact rational as a plain decimal, for a message.
func ratDecimal(r *big.Rat) string {
	if r.IsInt() {
		return r.Num().String()
	}
	sign := ""
	abs := new(big.Rat).Abs(r)
	if r.Sign() < 0 {
		sign = "-"
	}
	whole := ratFloor(abs)
	return sign + whole.String() + "." + fractionNormalized(new(big.Rat).Sub(abs, new(big.Rat).SetInt(whole)))
}

// jsNumber is an exact count as an integer where it is whole, else a float.
func jsNumber(r *big.Rat) any {
	if r.IsInt() && r.Num().IsInt64() {
		return r.Num().Int64()
	}
	f, _ := r.Float64()
	return f
}

// formatTime is PS-610 to PS-613: the instant `seconds` after the field's epoch, in its
// format.
func formatTime(field Field, seconds any) (any, error) {
	spec := field.Time
	count, ok := timeRat(seconds)
	if !ok {
		return seconds, nil
	}
	instant := new(big.Rat).Add(timeOffset(spec, "epoch"), count)
	format, _ := spec["format"].(string)
	if format == "unix" {
		c := new(big.Rat).Sub(instant, timeOffset(spec, "since"))
		if ms, _ := spec["milliseconds"].(bool); ms {
			n := ratFloor(new(big.Rat).Mul(c, big.NewRat(1000, 1)))
			if n.IsInt64() {
				return n.Int64(), nil
			}
			f, _ := new(big.Float).SetInt(n).Float64()
			return f, nil
		}
		return jsNumber(c), nil
	}
	wholeBig := ratFloor(instant)
	fraction := new(big.Rat).Sub(instant, new(big.Rat).SetInt(wholeBig))
	outside := &timeOmitted{fmt.Sprintf("Field '%s': the time %s s is outside years 1 to "+
		"9999; the field is omitted (PS-610)", field.Name, ratDecimal(instant))}
	if !wholeBig.IsInt64() {
		return nil, outside
	}
	whole := wholeBig.Int64()
	days, rest := floorDiv(whole, 86400), floorMod(whole, 86400)
	year, month, day := civilFromDays(days)
	if year < 1 || year > 9999 {
		return nil, outside
	}
	hour, minute, second := rest/3600, rest/60%60, rest%60
	switch format {
	case "iso8601":
		text := fmt.Sprintf("%04d-%02d-%02dT%02d:%02d:%02d", year, month, day, hour, minute, second)
		if digits, has := spec["fraction"]; has {
			n, _ := toInt(digits)
			text += "." + fractionDigits(fraction, n)
		} else if fraction.Sign() != 0 {
			text += "." + fractionNormalized(fraction)
		}
		return text + "Z", nil
	case "calendar":
		keys := asStringMap(spec["keys"])
		var monthValue any = month
		if labels, ok := spec["month_labels"].([]any); ok && len(labels) > 0 {
			monthValue = labels[month-1]
		}
		values := map[string]any{"year": year, "month": monthValue, "day": day,
			"hour": hour, "minute": minute, "second": second}
		out := make(map[string]any, len(timeParts))
		for _, part := range timeParts {
			key := part
			if s, ok := keys[part].(string); ok {
				key = s
			}
			out[key] = values[part]
		}
		return out, nil
	}
	out := map[string]string{
		"Y": fmt.Sprintf("%04d", year), "m": fmt.Sprintf("%02d", month),
		"d": fmt.Sprintf("%02d", day), "H": fmt.Sprintf("%02d", hour),
		"M": fmt.Sprintf("%02d", minute), "S": fmt.Sprintf("%02d", second),
		"L": fractionDigits(fraction, 3), "b": monthAbbr[month-1],
		"a": dayAbbr[floorMod(days+3, 7)], "%": "%",
	}
	pattern, _ := spec["pattern"].(string)
	return timePatternToken.ReplaceAllStringFunc(pattern, func(token string) string {
		return out[token[1:]]
	}), nil
}

// timeOmitted is PS-610/PS-614 as amended 2026-10-15: a time field with no instant to
// report. formatTime returns it for a year outside 1 to 9999; the decoder omits the field,
// reports the message as a warning and continues.
type timeOmitted struct{ msg string }

func (e *timeOmitted) Error() string { return e.msg }

// timeFromParts is PS-614: the seconds since 1970 of the UTC instant the referenced parts
// name, or `omitted` where a part has no value (PS-314). A part that is not a whole number,
// and parts that name no valid date and time, omit the field with a warning naming it
// (amended 2026-10-15); decoding continues.
func (ctx *DecodeContext) timeFromParts(field Field) (any, error) {
	parts := asStringMap(field.Time["parts"])
	values := map[string]int64{}
	var shown []string
	for _, part := range timeParts {
		ref, has := parts[part]
		if !has {
			continue
		}
		key := strings.TrimPrefix(fmt.Sprint(ref), "$")
		value, bound := ctx.Variables[key]
		if !bound || value == omitted || value == absentSentinel {
			return omitted, nil
		}
		if _, isBool := value.(bool); isBool {
			return omitted, nil
		}
		number, numeric := toFloat64(value)
		if !numeric {
			return omitted, nil
		}
		if number != math.Trunc(number) {
			ctx.Warnings = append(ctx.Warnings, fmt.Sprintf("Field '%s': its %s is %s, not a "+
				"whole number; the field is omitted (PS-614)", field.Name, part, sampledRepr(number)))
			return omitted, nil
		}
		n := int64(number)
		if u, ok := value.(uint64); ok {
			n = int64(u)
		} else if i, ok := value.(int64); ok {
			n = i
		}
		values[part] = n
		shown = append(shown, fmt.Sprintf("'%s': %d", part, n))
	}
	if !validCivil(values["year"], values["month"], values["day"], values["hour"],
		values["minute"], values["second"]) {
		ctx.Warnings = append(ctx.Warnings, fmt.Sprintf("Field '%s': its parts {%s} do not "+
			"name a valid UTC date and time; the field is omitted (PS-614)", field.Name,
			strings.Join(shown, ", ")))
		return omitted, nil
	}
	return civilSeconds(values["year"], values["month"], values["day"], values["hour"],
		values["minute"], values["second"]), nil
}

// timeReadbackError is PS-615: a pattern that cannot be parsed back to an instant decodes
// but is not encoded; "" where it can be.
func timeReadbackError(field Field) string {
	if field.Time["format"] != "pattern" {
		return ""
	}
	pattern, _ := field.Time["pattern"].(string)
	tokens := map[string]bool{}
	for _, t := range timePatternToken.FindAllStringSubmatch(pattern, -1) {
		tokens[t[1]] = true
	}
	if tokens["Y"] && tokens["d"] && tokens["H"] && tokens["M"] && tokens["S"] &&
		(tokens["m"] || tokens["b"]) {
		return ""
	}
	return fmt.Sprintf("Field '%s': its pattern %s cannot be read back to an instant; the "+
		"schema decodes but is not encoded (PS-615)", field.Name, pyRepr(pattern))
}

// timeRepr writes a given value as Python's repr would, for a PS-615 message. A JSON input
// carries no int/float distinction, so an integral float is written as an integer.
func timeRepr(v any) string {
	switch x := v.(type) {
	case string, bool, nil:
		return pyRepr(x)
	case float64:
		if x == math.Trunc(x) && math.Abs(x) < 1e16 {
			return strconv.FormatInt(int64(x), 10)
		}
		return sampledRepr(x)
	case []any:
		parts := make([]string, len(x))
		for i, item := range x {
			parts[i] = timeRepr(item)
		}
		return "[" + strings.Join(parts, ", ") + "]"
	}
	if m := asStringMap(v); m != nil {
		keys := make([]string, 0, len(m))
		for k := range m {
			keys = append(keys, k)
		}
		// The parts in the order year, month, day, hour, minute, second, then the rest.
		rank := func(k string) int {
			for i, p := range timeParts {
				if p == k {
					return i
				}
			}
			return len(timeParts)
		}
		sort.SliceStable(keys, func(a, b int) bool {
			if rank(keys[a]) != rank(keys[b]) {
				return rank(keys[a]) < rank(keys[b])
			}
			return keys[a] < keys[b]
		})
		parts := make([]string, len(keys))
		for i, k := range keys {
			parts[i] = pyRepr(k) + ": " + timeRepr(m[k])
		}
		return "{" + strings.Join(parts, ", ") + "}"
	}
	return givenText(v)
}

// timeWhole is a given calendar member as an integer: an integer, or an integral float
// (which is how JSON carries one).
func timeWhole(v any) (int64, bool) {
	switch x := v.(type) {
	case bool:
		return 0, false
	case float64:
		if x != math.Trunc(x) || math.IsInf(x, 0) {
			return 0, false
		}
		return int64(x), true
	case float32:
		return timeWhole(float64(x))
	}
	if timeIsInt(v) {
		n, _ := toInt(v)
		return int64(n), true
	}
	return 0, false
}

// parseTime is PS-615: a time field's value in its format, as exact seconds since 1970.
func parseTime(field Field, value any) (*big.Rat, error) {
	spec := field.Time
	format, _ := spec["format"].(string)
	bad := func(why string) error {
		return fmt.Errorf("Field '%s': %s is not a time in its %s format%s (PS-615)",
			field.Name, timeRepr(value), format, why)
	}
	instant := func(y, mo, d, h, mi, s int64, frac *big.Rat) (*big.Rat, error) {
		if !validCivil(y, mo, d, h, mi, s) {
			return nil, bad("")
		}
		out := new(big.Rat).SetInt64(civilSeconds(y, mo, d, h, mi, s))
		if frac != nil {
			out.Add(out, frac)
		}
		return out, nil
	}
	switch format {
	case "unix":
		count, ok := timeRat(value)
		if !ok {
			return nil, bad("")
		}
		if ms, _ := spec["milliseconds"].(bool); ms {
			count.Quo(count, big.NewRat(1000, 1))
		}
		return count.Add(count, timeOffset(spec, "since")), nil
	case "iso8601":
		parsed := parseRFC3339(value)
		if parsed == nil {
			return nil, bad("")
		}
		return parsed, nil
	case "calendar":
		given := asStringMap(value)
		if given == nil {
			return nil, bad("")
		}
		keys := asStringMap(spec["keys"])
		got := map[string]any{}
		for _, part := range timeParts {
			key := part
			if s, ok := keys[part].(string); ok {
				key = s
			}
			got[part] = given[key]
		}
		if labels, ok := spec["month_labels"].([]any); ok && len(labels) > 0 {
			index := -1
			if text, isText := got["month"].(string); isText {
				for i, l := range labels {
					if l == text {
						index = i
						break
					}
				}
			}
			if index < 0 {
				return nil, bad(fmt.Sprintf("; month %s is not a label", timeRepr(got["month"])))
			}
			got["month"] = int64(index + 1)
		}
		var p [6]int64
		for i, part := range timeParts {
			n, ok := timeWhole(got[part])
			if !ok {
				return nil, bad("")
			}
			p[i] = n
		}
		return instant(p[0], p[1], p[2], p[3], p[4], p[5], nil)
	}
	if msg := timeReadbackError(field); msg != "" {
		return nil, fmt.Errorf("%s", msg)
	}
	pattern, _ := spec["pattern"].(string)
	var regex strings.Builder
	regex.WriteString("^")
	var groups []string
	pos := 0
	for _, loc := range timePatternToken.FindAllStringSubmatchIndex(pattern, -1) {
		regex.WriteString(regexp.QuoteMeta(pattern[pos:loc[0]]))
		token := pattern[loc[2]:loc[3]]
		switch token {
		case "%":
			regex.WriteString("%")
		case "Y":
			regex.WriteString(`(\d{4})`)
		case "L":
			regex.WriteString(`(\d{3})`)
		case "b":
			regex.WriteString("(" + strings.Join(monthAbbr, "|") + ")")
		case "a":
			regex.WriteString("(" + strings.Join(dayAbbr, "|") + ")")
		default:
			regex.WriteString(`(\d{2})`)
		}
		if token != "%" {
			groups = append(groups, token)
		}
		pos = loc[1]
	}
	regex.WriteString(regexp.QuoteMeta(pattern[pos:]))
	regex.WriteString("$")
	text, isText := value.(string)
	var m []string
	if isText {
		m = regexp.MustCompile(regex.String()).FindStringSubmatch(text)
	}
	if m == nil {
		return nil, bad("")
	}
	got := map[string]string{}
	for i, token := range groups {
		if prior, seen := got[token]; seen && prior != m[i+1] {
			return nil, bad("")
		}
		got[token] = m[i+1]
	}
	num := func(token string) int64 {
		n, _ := strconv.ParseInt(got[token], 10, 64)
		return n
	}
	var month int64
	if b, has := got["b"]; has {
		for i, name := range monthAbbr {
			if name == b {
				month = int64(i + 1)
			}
		}
	} else {
		month = num("m")
	}
	if _, hasM := got["m"]; hasM {
		if _, hasB := got["b"]; hasB && num("m") != month {
			return nil, bad("; its month and month name disagree")
		}
	}
	frac := new(big.Rat)
	if l, has := got["L"]; has {
		n, _ := strconv.ParseInt(l, 10, 64)
		frac.SetFrac64(n, 1000)
	}
	result, err := instant(num("Y"), month, num("d"), num("H"), num("M"), num("S"), frac)
	if err != nil {
		return nil, err
	}
	if a, has := got["a"]; has {
		days := floorDiv(ratFloor(result).Int64(), 86400)
		if dayAbbr[floorMod(days+3, 7)] != a {
			return nil, bad(fmt.Sprintf("; %s is not that date's weekday", a))
		}
	}
	return result, nil
}

// timeInputSeconds is PS-615: the number PS-610 reads for a time field's value - seconds
// since its epoch - which the field's modifiers then invert.
func timeInputSeconds(field Field, value any) (any, error) {
	instant, err := parseTime(field, value)
	if err != nil {
		return nil, err
	}
	return jsNumber(new(big.Rat).Sub(instant, timeOffset(field.Time, "epoch"))), nil
}

// expandTimeParts is PS-615: a `parts` time field's value gives its part fields' values,
// which the encoder then writes. A part that reads no bytes cannot be written, and the
// field is refused for encoding. The input itself is not modified.
func expandTimeParts(fields []Field, data map[string]any) (map[string]any, error) {
	if data == nil {
		return data, nil
	}
	known := map[string]Field{}
	for _, f := range fields {
		if f.Name != "" {
			if _, seen := known[f.Name]; !seen {
				known[f.Name] = f
			}
		}
	}
	var out map[string]any
	for _, field := range fields {
		if !timeHasParts(field) {
			continue
		}
		given, ok := data[field.Name]
		if !ok || given == nil {
			continue
		}
		parsed, err := parseTime(field, given)
		if err != nil {
			return nil, err
		}
		floor := ratFloor(parsed)
		if !floor.IsInt64() {
			return nil, fmt.Errorf("Field '%s': %s is not a time in its %v format (PS-615)",
				field.Name, timeRepr(given), field.Time["format"])
		}
		whole := floor.Int64()
		days, rest := floorDiv(whole, 86400), floorMod(whole, 86400)
		year, month, day := civilFromDays(days)
		moment := map[string]int64{"year": year, "month": month, "day": day,
			"hour": rest / 3600, "minute": rest / 60 % 60, "second": rest % 60}
		if out == nil {
			out = make(map[string]any, len(data)+6)
			for k, v := range data {
				out[k] = v
			}
		}
		parts := asStringMap(field.Time["parts"])
		for _, part := range timeParts {
			ref, has := parts[part]
			if !has {
				continue
			}
			key := strings.TrimPrefix(fmt.Sprint(ref), "$")
			if source, ok := known[key]; ok {
				typ, _ := source.decl["type"].(string)
				if typ == "" {
					typ = string(source.Type)
				}
				if timeUnwrittenPartTypes[typ] {
					return nil, fmt.Errorf("Field '%s': its %s part '%s' reads no bytes, so "+
						"the schema decodes but is not encoded (PS-615)", field.Name, part, key)
				}
			}
			value := moment[part]
			if prior, has := out[key]; has && prior != nil {
				n, numeric := toFloat64(prior)
				if !numeric || n != float64(value) {
					return nil, fmt.Errorf("Field '%s': its %s is %d, but '%s' is given %s "+
						"(PS-615)", field.Name, part, value, key, timeRepr(prior))
				}
			}
			out[key] = value
		}
	}
	if out == nil {
		return data, nil
	}
	return out, nil
}
