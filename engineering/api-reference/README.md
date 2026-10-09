# Statistics API coverage evidence

This directory owns the reviewed inventory that generates the resource pages in `docs/en/api-catalog/`.
The public reference describes specification-defined capabilities. This audit makes no tenant
queries, provisioning calls, report-generation requests, or traffic-generation calls.

The corrected input is `f5-sales-demo/api-specs` revision
`b8c092ce96db45bdd489ff06067ca7b2864359ca`, under `release/specs`. The enriched input is
`f5-sales-demo/api-specs-enriched` release `v12.0.4`, revision
`c5ce81d5fb15314a0f9398db954e0da111d89606`. Its downloaded ZIP SHA-256 matches the GitHub
release asset digest in `sources.json`. Source revisions belong in this engineering receipt;
customers do not need to install a particular specification version to read the reference.

`inventory.json` contains included method/path pairs, primary resource group, query/discovery/report
classification, returned information, principal inputs, scope restrictions, references, source
operations, and schema-closure fingerprints. `excluded.json` records every excluded operation
and its reason. `source-operations.json.gz` contains the sanitized extraction projection for all
corrected, enriched-domain, and merged-enriched operation occurrences. It preserves input/output
field names and system-only restrictions without copying source examples or runtime data.

The audit reads descriptions, resolves request and successful response schemas, and follows schema
references. Classification is reviewed separately from extraction: neither a POST method nor a
reporting path proves mutation or statistical content. Repeated domains collapse to one method/path;
versions and GET/POST variants remain distinct. Explicit source deprecation text is retained.
Configuration CRUD, feature changes, credential retrieval, probes, unrelated operational discovery,
and report creation are excluded. Relevant discovery and existing-report retrieval have separate
subsections. Network diagnostics capable of issuing probes are excluded even when exposed as GET.

The three cloud-connect metrics, the top cloud-connect query, two legacy API discovery queries,
and cloud-connect VPC discovery occur in corrected sources but are absent from the merged enriched
input. They retain direct corrected-source references. The API endpoint `/pdf` responses contain
probability density functions of sizes and latency; they are analytical queries, not report files.
The DDoS networks route has an inconsistent report-oriented description; its response schema returns
networks, so the reference describes network discovery.

Run the offline CI coverage and generation check:

```bash
python3 scripts/api_catalog.py
python3 tests/test_api_catalog.py
```

After editing reviewed inventory text, regenerate the marked endpoint-table region on each resource page:

```bash
python3 scripts/api_catalog.py --write
```

To verify original inputs, extract the corrected revision into `<SOURCE_ROOT>/corrected` and the
release ZIP into `<SOURCE_ROOT>/enriched`. The checker validates every source file digest and
recomputes operation coverage:

```bash
python3 scripts/api_catalog.py --sources '<SOURCE_ROOT>'
```

A source refresh requires a new extraction and semantic review. Missing dispositions, duplicates,
unknown groups, unsupported methods, stale occurrence evidence, missing restrictions, and table
drift fail validation. Regression tests cover nearby reporting mutations, read-only POST queries,
repeated domains, route versions, method variants, system restrictions, source-only cloud metrics,
and probability density responses. The repository shell test entrypoint runs these checks in CI.

The catalog contains an overview, query concepts, and fourteen resource pages. Coverage checks validate the
complete page set and exactly-once endpoint rows in their unchanged primary groups. The checker reports
SHA-256 hashes for all sixteen pages; endpoint tables belong only on resource pages.
