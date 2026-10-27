    # SigmaOS-mozjs

    SpiderMonkey (mozjs) build wrapper

    Part of the **SigmaOS** sovereign operating-system ecosystem — a multi-repository
    distribution modelled on Linux Mint and Omarchy Linux. See the parent
    [`SigmaOS`](https://github.com/AaryanSinghChauhan09/SigmaOS) repository for the
    full component index.

    ## Upstream inspirations
    - https://github.com/linuxmint/mozjs102

    ## Design principles applied
    - **Separation of Concerns**
- **Defensive Programming**
- **Fail-Fast**
- **Least Astonishment**
- **KISS**
- **Deterministic Testing**

    ## Usage
    Verbs: `eval-plan, repl-plan, sandbox, module`

    ```
    $ sigma-js eval-plan "Math.max(1, 41)" --engine mozjs
js102 -e 'Math.max(1, 41)'
$ sigma-js repl-plan --engine cjs
cjs
$ sigma-js sandbox "Math.sqrt(16) + 2"
safe
$ sigma-js sandbox "require('fs').readFileSync('/etc/shadow')"
error: blocked identifier: require
$ sigma-js module --name calc --format cjs

    ```

    ## Build & test
    ```
    make build
    make test
    ```

    ## Licence
    GNU General Public License v3.0 (FOSS).
