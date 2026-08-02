#!/usr/bin/env bash
# Print the body of one `## [X.Y]` section from CHANGELOG.md (everything
# after that heading up to, but not including, the next `## [` heading).
# Usage: extract-changelog-section.sh X.Y [CHANGELOG.md path]
set -euo pipefail

version="${1:?usage: extract-changelog-section.sh X.Y [CHANGELOG.md]}"
changelog="${2:-CHANGELOG.md}"

awk -v ver="## [$version]" '
  $0 == ver { flag=1; next }
  flag && /^## \[/ { flag=0 }
  flag { print }
' "$changelog"
