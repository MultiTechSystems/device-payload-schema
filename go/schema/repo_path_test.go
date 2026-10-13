package schema

import (
	"os"
	"path/filepath"
	"testing"
)

// repoPath names a file or directory of the repository this package sits in
// (go/schema/../..), for the tests that read the corpus or the shared case files.
// When the package is vendored without them - the specification repository carries
// go/schema alone - the test is skipped rather than failed. In the full repository
// the corpus directory always exists, so a single missing file there still fails the
// build: a CI run that finds the corpus absent is a checkout problem, not a pass.
func repoPath(t testing.TB, parts ...string) string {
	t.Helper()
	root := filepath.Join("..", "..")
	p := filepath.Join(append([]string{root}, parts...)...)
	if _, err := os.Stat(filepath.Join(root, "schemas", "devices")); err != nil {
		t.Skipf("repository data not present (%s); skipping", p)
	}
	return p
}
