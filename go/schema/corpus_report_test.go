package schema

// Per-vector reporting for the corpus conformance runner, for tools/verdicts-gate.py.
//
// A pass count against a floor cannot say WHICH vectors an implementation fails, so a
// vector that decodes correctly in Python and differently here is invisible whenever the
// total still clears the floor. Two optional environment variables change that, and
// with neither set the runner behaves exactly as it always has:
//
//	CORPUS_REPORT=/path/file.json   also write one {schema, index, vector, status,
//	                                detail} entry per vector, status being pass, fail,
//	                                error or skip
//	CORPUS_ONLY=a/x.yaml,b/y.yaml   run only these schemas (paths relative to
//	                                schemas/devices; a leading "schemas/devices/" is
//	                                accepted). The floor is not applied to a restricted
//	                                run, because it counts the whole corpus.
//	CORPUS_ROOT=/some/dir           walk this directory instead of schemas/devices,
//	                                so a scratch schema can be run without being
//	                                placed in the repository.

import (
	"encoding/json"
	"os"
	"path/filepath"
	"strings"
	"testing"
)

type corpusReportEntry struct {
	Schema string `json:"schema"`
	Index  int    `json:"index"`
	Vector string `json:"vector"`
	Status string `json:"status"`
	Detail string `json:"detail"`
}

type corpusReport struct {
	path    string
	only    map[string]bool
	entries []corpusReportEntry
}

func newCorpusReport() *corpusReport {
	report := &corpusReport{path: os.Getenv("CORPUS_REPORT"), entries: []corpusReportEntry{}}
	if only := strings.TrimSpace(os.Getenv("CORPUS_ONLY")); only != "" {
		report.only = map[string]bool{}
		for _, item := range strings.Split(only, ",") {
			if item = corpusRelative(strings.TrimSpace(item)); item != "" {
				report.only[item] = true
			}
		}
	}
	return report
}

// corpusRelative normalises a schema path to the form the report uses.
func corpusRelative(path string) string {
	path = filepath.ToSlash(path)
	return strings.TrimPrefix(path, "schemas/devices/")
}

// restricted reports whether CORPUS_ONLY or CORPUS_ROOT moved the run off the whole
// corpus, which is what the floor counts.
func (r *corpusReport) restricted() bool { return r.only != nil || os.Getenv("CORPUS_ROOT") != "" }

// includes reports whether the schema at rel is part of this run.
func (r *corpusReport) includes(rel string) bool {
	return r.only == nil || r.only[rel]
}

func (r *corpusReport) add(schema string, index int, vector, status, detail string) {
	if r.path == "" {
		return
	}
	if len(detail) > 200 {
		detail = detail[:200]
	}
	r.entries = append(r.entries, corpusReportEntry{schema, index, vector, status, detail})
}

func (r *corpusReport) write(t *testing.T) {
	if r.path == "" {
		return
	}
	data, err := json.MarshalIndent(r.entries, "", " ")
	if err != nil {
		t.Errorf("CORPUS_REPORT: %v", err)
		return
	}
	if err := os.WriteFile(r.path, append(data, '\n'), 0o644); err != nil {
		t.Errorf("CORPUS_REPORT: %v", err)
		return
	}
	t.Logf("corpus report: %d vectors written to %s", len(r.entries), r.path)
}
