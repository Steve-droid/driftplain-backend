# B3 source fixture

`swe-bench-verified.json` is the unmodified official JSON at commit
`193160a463a435d05cf44a1fa9dc5eac832113c3`, fetched September 23, 2026:
https://raw.githubusercontent.com/swe-bench/swe-bench.github.io/193160a463a435d05cf44a1fa9dc5eac832113c3/data/leaderboards.json

Its 4,091,442 bytes and SHA-256
`83cd949a9582f4dd68b0a07148cd86ff0eae6a05b0f2d2298a8f3717baf89d2d` match the B1 registry.
The adapter reads only the Verified section (180 submissions); it never executes links or imports
other leaderboard sections as Verified results. Attribution: SWE-bench authors and submitting
systems. Benchmark code is MIT; submission artifact terms remain upstream.

The nine bounded reviewed fixtures are the shipped inputs in `data/catalog/b3`. Their README
records exact sources, source corrections, coverage gaps, attribution and interpretation limits.
Tests mutate disposable in-memory copies to exercise rejection, missing scores and changed data;
those fabricated mutations are not source claims and are never written into shipped manifests.
