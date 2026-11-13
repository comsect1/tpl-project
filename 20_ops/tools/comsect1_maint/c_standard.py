# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Kim Hyeongjeong (comsect1)
"""The one C standard of every Cell, host and embedded, and its verification.

Python standard library only. The reference name is the formal one,
ISO/IEC 9899:2018. "C17" and "C18" are informal aliases of that same standard,
not two editions: both name the text whose `__STDC_VERSION__` is `201710L`.
A lane's standard is therefore verified by the value of `__STDC_VERSION__` the
compiler reports under the lane's flags, never by the spelling of an option.

The spelling of ISO/IEC 9899:2018 per tool:

    registry (`cells.json` `standard`)       c17
    native Gate lane (`build.standard`)      c17 (the pinned LLVM analysis lane: -std=c17)
    GCC, Clang, arm-none-eabi-gcc            -std=c17 (never -std=gnu17: GNU extensions off)
    MSVC cl                                  /std:c17
    CMake                                    set(CMAKE_C_STANDARD 17)
                                             set(CMAKE_C_STANDARD_REQUIRED ON)
                                             set(CMAKE_C_EXTENSIONS OFF)
    IAR                                      Standard C language mode (--c89 absent, no IAR extensions)

A build description that names the standard writes the formal name beside the
option (a comment in the CMake file), so an alias is never read as another
edition.

The product line may move to a later edition (ISO/IEC 9899:2024, C23). That
move is one edit of the definitions below and nothing else: there is no list of
accepted standards and no per-Cell choice.
"""

# The registry and Gate token of the standard, as GCC and Clang spell it after -std=.
C_STANDARD = "c17"
# The formal name and its informal aliases (the same standard).
FORMAL_NAME = "ISO/IEC 9899:2018"
ALIASES = ("C17", "C18")
# The expansion of __STDC_VERSION__ under that standard.
STDC_VERSION = "201710L"
# The cl spelling of the same standard.
MSVC_FLAG = "/std:c17"

# The probe: one line that the preprocessor expands to the value under the lane's flags.
PROBE_FILE_NAME = "c_standard_probe.c"
PROBE_SOURCE = "__STDC_VERSION__\n"
# Preprocess only, without line markers: the GCC and Clang form and the cl form.
PREPROCESS_FLAGS = ["-E", "-P"]
MSVC_PREPROCESS_FLAGS = ["/EP"]


def title():
    """The standard for a message: the formal name first, then the aliases."""
    return "%s (%s)" % (FORMAL_NAME, "/".join(ALIASES))


def token_refusal(what, found):
    """The reason a declared standard token is refused."""
    return ("%s: standard %r is refused: every Cell is %s; write \"%s\""
            % (what, found, title(), C_STANDARD))


def observed_version(preprocessed):
    """The expansion of __STDC_VERSION__ in the preprocessed probe: its last non-empty line that is no
    line marker. A compiler that does not define the macro leaves the name itself."""
    lines = [line.strip() for line in preprocessed.splitlines()]
    lines = [line for line in lines if line and not line.startswith("#")]
    return lines[-1] if lines else ""


def verification(command, preprocessed):
    """The record of one probe: the command, the observed and the expected value, and whether they agree."""
    observed = observed_version(preprocessed)
    return {"command": [str(part) for part in command], "observed_stdc_version": observed,
            "expected_stdc_version": STDC_VERSION, "verified": observed == STDC_VERSION}


def mismatch(what, record):
    """The reason a lane is refused: its flags do not select the standard."""
    return ("%s is not compiled as %s: __STDC_VERSION__ expands to %r under %s, expected %s. Next step: select "
            "the standard as -std=%s (cl: %s) and remove any other standard option from the flags."
            % (what, title(), record["observed_stdc_version"], " ".join(record["command"][:-1]), STDC_VERSION,
               C_STANDARD, MSVC_FLAG))


def run_record(probes):
    """The member a build or Gate run record keeps: the standard and every probe of the run."""
    return {"standard": C_STANDARD, "formal_name": FORMAL_NAME, "aliases": list(ALIASES),
            "expected_stdc_version": STDC_VERSION, "probes": probes}
