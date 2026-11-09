# Third-party notices

DeploySpecter's own code and documentation carry no open-source licence; they are the copyright of the repository
owner. This file covers third-party material that the repository **redistributes**, in whole or in part, as research
evidence. Each project is used unmodified, except for declared, hash-verified mutation controls on isolated build copies.

## Chancy (MIT)

Source trees of Chancy (https://github.com/TkTech/chancy) are redistributed as normalized `tar` archives of the
`chancy/` package at commits `80f570f` and `2895c4a`:

- `docs/experimental_mvp/confirmatory/executor_rehearsal_{1,2,3,4}/corpus/cases/sim-chancy-pair/R{1,2}.tar`
- `docs/experimental_mvp/confirmatory/executor_rehearsal_{1,2,3,4}/evaluator_package/cases/sim-chancy-pair/R{1,2}.tar`

Excerpts (SQL statements, schema exports, file hashes, short code quotations) of Chancy 0.25.1 and 0.26.0 appear in
evidence under `docs/experimental_mvp/`. The archives omit Chancy's licence file, so its notice is reproduced here:

```
Copyright (c) 2024 Tyler Kennedy <tk@tkte.ch>

Permission is hereby granted, free of charge, to any person obtaining a copy of
this software and associated documentation files (the "Software"), to deal in
the Software without restriction, including without limitation the rights to
use, copy, modify, merge, publish, distribute, sublicense, and/or sell copies of
the Software, and to permit persons to whom the Software is furnished to do so,
subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY, FITNESS
FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE AUTHORS OR
COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER LIABILITY, WHETHER
IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM, OUT OF OR IN
CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE SOFTWARE.```

## procrastinate (MIT)

Excerpts of procrastinate 3.3.0 and 3.4.0 (SQL statements, schema and migration text, trigger definitions, hashes)
appear in evidence under `docs/experimental_mvp/rolling_upgrade/`, `state_construction/` and `results/`.

```
MIT License

Copyright (c) 2019-2021, PeopleDoc
Copyright (c) 2021-2023, Joachim Jablon, Eric Lemoine
Copyright (c) 2024-, Joachim Jablon, Kai Schlamp

Permission is hereby granted, free of charge, to any person obtaining a copy of this
software and associated documentation files (the "Software"), to deal in the Software
without restriction, including without limitation the rights to use, copy, modify,
merge, publish, distribute, sublicense, and/or sell copies of the Software, and to
permit persons to whom the Software is furnished to do so, subject to the following
conditions:

The above copyright notice and this permission notice shall be included in all copies or
substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR IMPLIED,
INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY, FITNESS FOR A PARTICULAR
PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE AUTHORS OR COPYRIGHT HOLDERS BE
LIABLE FOR ANY CLAIM, DAMAGES OR OTHER LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT
OR OTHERWISE, ARISING FROM, OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR
OTHER DEALINGS IN THE SOFTWARE.
```

## PGQueuer (MIT)

Excerpts of PGQueuer 1.6.0 (SQL statements and schema) appear in evidence under `docs/experimental_mvp/results/`.

```
MIT License

Copyright (c) 2024 janbjorge

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

## saq (MIT)

Excerpts of saq 0.26.5 appear in `docs/experimental_mvp/onboarding/pilot_1/`.

```
MIT License

Copyright (c) 2021 Toby Mao

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

## taskiq-postgresql (MIT)

Excerpts of taskiq-postgresql 0.4.0 appear in `docs/experimental_mvp/onboarding/pilot_1/`. Its sdist declares
`License-Expression: MIT` and ships no licence file; see https://pypi.org/project/taskiq-postgresql/0.4.0/.

## saq source archives (MIT)

Normalized `saq/` package trees at commits `59f32fc`, `c570ba4`, `f827b52` and `addee31` are redistributed as
evidence copies in a refused invocation:
`docs/experimental_mvp/nonblind_corpus/track_b/runs/_refused_invocation_1/{saq-c570ba4,saq-addee31}/package/cases/*/R{1,2}.tar`.
saq's licence is reproduced above.

## pgtq (MIT)

Excerpts of pgtq 0.5.0 (SQL statements, API names) appear in `docs/experimental_mvp/onboarding/pilot_2/` and
`pilot_3/` session evidence.

```
# License

Copyright (c) 2025 Kyle Ketchell

Permission is hereby granted, free of charge,  to any person obtaining a copy of
this software  and associated documentation files  (the “Software”),  to deal in
the Software  without restriction,  including  without limitation  the rights to
use,  copy,  modify,  merge, publish, distribute, sublicense, and/or sell copies
of the Software,  and to permit persons to whom the Software  is furnished to do
so, subject to the following conditions:

The above copyright notice  and  this permission notice shall be included in all
copies or substantial portions of the Software.

THE  SOFTWARE  IS  PROVIDED  “AS IS”, WITHOUT WARRANTY OF ANY KIND,  EXPRESS  OR
IMPLIED,  INCLUDING  BUT  NOT  LIMITED  TO  THE  WARRANTIES  OF MERCHANTABILITY,
FITNESS  FOR A  PARTICULAR PURPOSE  AND NONINFRINGEMENT.  IN NO EVENT  SHALL THE
AUTHORS  OR  COPYRIGHT  HOLDERS  BE  LIABLE  FOR  ANY  CLAIM,  DAMAGES  OR OTHER
LIABILITY,  WHETHER  IN  AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF  OR  IN CONNECTION WITH THE SOFTWARE  OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
```

## postgres-tq (MIT)

Excerpts of postgres-tq 1.2.1 (SQL statements, API names) appear in `docs/experimental_mvp/onboarding/pilot_2/` and
`pilot_3/` session evidence.

```
MIT License

Copyright (c) 2023 FlixMobility Tech GmbH

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
