# Third-party notices

DeploySpecter's own code and documentation are licensed under the Apache License 2.0 ([`LICENSE`](LICENSE),
[`NOTICE`](NOTICE)). This file covers third-party material that the repository **redistributes**, in whole or in part,
as fixtures or evidence. That material stays under its own licence, reproduced below.

## GitLab (MIT Expat)

`scenarios_external/gitlab_authorization_sync/` is an independent, minimal model of a defect that GitLab published in
[MR !246505](https://gitlab.com/gitlab-org/gitlab/-/merge_requests/246505). Its schema and `corrected/migration.sql`
are derived from that merge request's diff and from GitLab's `db/structure.sql`; table names differ on purpose, and
`scenarios_external/gitlab_authorization_sync/SOURCE.md` lists each fact and its source. Evidence produced from the
fixture, which quotes its SQL, is under `results/external_public/gitlab_authorization_sync/`.

The merge request changed only files under `db/` and `spec/`. GitLab's `LICENSE` (master, read 2026-11-15) licenses
content outside its `doc/`, `ee/` and `jh/` directories under the "MIT Expat" licence:

```
Copyright (c) 2011-present GitLab Inc.

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
```
