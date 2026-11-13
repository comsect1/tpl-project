# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Kim Hyeongjeong (comsect1)
"""object_cache: keys, hits and misses, invalidation, verification, concurrent writers, byte identity, pruning.

The compile cases use a real gcc (MSYS2 UCRT64 or the first gcc on PATH) and skip without one.
"""
import os
import shutil
import subprocess
import sys
import threading
import time
import unittest

import maint_support as support

cache_module = support.package_module("object_cache")
process = support.package_module("process")


def _find_gcc():
    for candidate in (r"C:\msys64\ucrt64\bin\gcc.exe", shutil.which("gcc")):
        if candidate and os.path.isfile(candidate):
            return os.path.abspath(candidate)
    return None


GCC = _find_gcc()
AR = os.path.join(os.path.dirname(GCC), "ar.exe" if os.name == "nt" else "ar") if GCC else None
NM = os.path.join(os.path.dirname(GCC), "nm.exe" if os.name == "nt" else "nm") if GCC else None


def _env():
    env = dict(os.environ)
    env["PATH"] = os.path.dirname(GCC) + os.pathsep + env.get("PATH", "")
    return env


def _run(argv):
    done = subprocess.run(argv, env=_env(), capture_output=True, text=True, stdin=subprocess.DEVNULL,
                          timeout=120, creationflags=process.window_flags())
    if done.returncode != 0:
        raise RuntimeError("%s failed: %s" % (argv[0], done.stderr))
    return done.stdout


class PureTest(unittest.TestCase):
    def test_depfile_parsing(self):
        text = ("F:\\run\\o_1.o: F:\\src\\a.c F:\\inc\\a.h \\\n C:/Program\\ Files/Arm/inc/b.h \\\r\n"
                " F:/x/d$$.h F:/x/\\#c.h\n")
        self.assertEqual(cache_module.parse_depfile(text),
                         ["F:\\src\\a.c", "F:\\inc\\a.h", "C:/Program Files/Arm/inc/b.h", "F:/x/d$.h", "F:/x/#c.h"])
        with self.assertRaises(ValueError):
            cache_module.parse_depfile("no rule here")

    def test_absolute_paths_and_include_roots(self):
        root = os.path.abspath(os.sep)
        source = os.path.join(root, "s.c")
        inc = os.path.join(root, "inc")
        command = ["gcc", "-I", inc, "-I" + inc, "-isystem" + inc, "-c", source, "-o", "x.o"]
        self.assertTrue(cache_module.command_paths_absolute(command, source))
        self.assertEqual(cache_module.include_roots(command), [inc, inc, inc])
        self.assertFalse(cache_module.command_paths_absolute(["gcc", "-Iinc", "-c", source], source))
        self.assertFalse(cache_module.command_paths_absolute(["gcc", "-I"], source))
        self.assertFalse(cache_module.command_paths_absolute(["gcc", "-c", "s.c"], "s.c"))

    def test_ordered_map_keeps_order_and_raises_the_earliest_failure(self):
        def slow(value):
            time.sleep(0.02 * (5 - value))
            return value * 2
        self.assertEqual(process.ordered_map(slow, range(5), 4), [0, 2, 4, 6, 8])
        self.assertEqual(process.ordered_map(slow, range(5), 1), [0, 2, 4, 6, 8])

        def failing(value):
            if value in (1, 3):
                raise KeyError(value)
            return value
        with self.assertRaises(KeyError) as caught:
            process.ordered_map(failing, range(5), 4)
        self.assertEqual(caught.exception.args, (1,))
        with self.assertRaises(ValueError):
            process.ordered_map(failing, range(2), 0)


@unittest.skipUnless(GCC, "gcc is absent")
class CompileTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.work = support.Run("object_cache")
        cls.identity = cache_module.toolchain_identity(GCC, _env())

    @classmethod
    def tearDownClass(cls):
        cls.work.close()

    def setUp(self):
        self.base = self.work.folder(self.id().rsplit(".", 1)[-1])
        self.inc = self.base / "inc"
        self.early = self.base / "early"
        self.early.mkdir()
        support.write(self.inc / "unit.h", "#define UNIT_VALUE 7\nint unit_value(void);\n")
        self.source = str(support.write(self.base / "src" / "unit.c",
                                        '#include "unit.h"\nint unit_value(void) { return UNIT_VALUE; }\n'))
        self.scratch = self.base / "scratch"
        self.scratch.mkdir()
        self.root = str(self.base / "cache")
        self.outputs = 0

    def cache(self):
        return cache_module.ObjectCache(self.root, str(support.REPO_ROOT))

    def command(self, output, extra=()):
        return [GCC, "-std=c17", "-O2", "-ffunction-sections", "-fno-ident"] + list(extra) + [
            "-I", str(self.early), "-I", str(self.inc), "-c", "-o", output, self.source]

    def build(self, cache, extra=(), identity=None):
        self.outputs += 1
        output = str(self.base / ("out_%d.o" % self.outputs))
        hit = cache.compile(self.command(output, extra), output, self.source, _env(), _run, str(self.scratch),
                            identity or self.identity, "test")
        with open(output, "rb") as handle:
            return hit, handle.read()

    def test_identity_names_driver_compiler_and_assembler(self):
        self.assertEqual(sorted(self.identity["files"]), ["as", "cc1", "driver"])
        self.assertEqual(cache_module.toolchain_identity(GCC, _env())["digest"], self.identity["digest"])

    def test_miss_then_hit_with_identical_bytes(self):
        cache = self.cache()
        hit, cold = self.build(cache)
        self.assertFalse(hit)
        hit, warm = self.build(self.cache())
        self.assertTrue(hit)
        self.assertEqual(warm, cold)
        self.assertEqual((cache.counts["misses"], cache.counts["stores"]), (1, 1))

    def test_cached_and_uncached_builds_are_byte_identical_down_to_the_archive(self):
        _hit, plain = self.build(cache_module.ObjectCache(None))
        self.build(self.cache())
        hit, warm = self.build(self.cache())
        self.assertTrue(hit)
        self.assertEqual(plain, warm)
        archives = []
        for name, data in (("plain", plain), ("warm", warm)):
            folder = self.base / name
            folder.mkdir()
            (folder / "unit.o").write_bytes(data)
            _run([AR, "rcsD", str(folder / "libunit.a"), str(folder / "unit.o")])
            archives.append((folder / "libunit.a").read_bytes())
        self.assertEqual(archives[0], archives[1])

    def test_run_mode_reuses_inside_the_process_only(self):
        cache = cache_module.ObjectCache(None)
        self.assertFalse(self.build(cache)[0])
        self.assertTrue(self.build(cache)[0])
        self.assertFalse(self.build(cache_module.ObjectCache(None))[0])
        self.assertEqual(cache.statistics()["mode"], "run")

    def test_header_change_invalidates_and_the_old_record_still_hits(self):
        self.build(self.cache())
        header = self.inc / "unit.h"
        original = header.read_text(encoding="utf-8")
        support.write(header, original.replace("7", "8"))
        hit, changed = self.build(self.cache())
        self.assertFalse(hit)
        support.write(header, original)
        hit, again = self.build(self.cache())
        self.assertTrue(hit)
        self.assertNotEqual(changed, again)

    def test_shadowing_header_on_the_search_path_invalidates(self):
        self.build(self.cache())
        support.write(self.early / "unit.h", "#define UNIT_VALUE 9\nint unit_value(void);\n")
        hit, _data = self.build(self.cache())
        self.assertFalse(hit)

    def test_compiler_identity_argument_and_environment_are_keyed(self):
        self.build(self.cache())
        other = dict(self.identity, digest="0" * 64)
        self.assertFalse(self.build(self.cache(), identity=other)[0])
        self.assertFalse(self.build(self.cache(), extra=["-O1"])[0])
        cache = self.cache()
        output = str(self.base / "env.o")
        env = dict(_env(), CPATH=str(self.early))
        self.assertFalse(cache.compile(self.command(output), output, self.source, env, _run, str(self.scratch),
                                       self.identity, "test"))
        self.assertTrue(self.build(self.cache())[0])

    def test_source_change_and_target_are_keyed(self):
        self.build(self.cache())
        support.write(self.source, '#include "unit.h"\nint unit_value(void) { return UNIT_VALUE + 1; }\n')
        self.assertFalse(self.build(self.cache())[0])
        cache = self.cache()
        output = str(self.base / "target.o")
        self.assertFalse(cache.compile(self.command(output), output, self.source, _env(), _run, str(self.scratch),
                                       self.identity, "other-target"))

    def test_corrupt_blob_is_detected_and_repaired(self):
        _hit, good = self.build(self.cache())
        blobs = [os.path.join(d, n) for d, _, names in os.walk(os.path.join(self.root, "blobs")) for n in names]
        self.assertEqual(len(blobs), 1)
        with open(blobs[0], "r+b") as handle:
            handle.write(b"\0\0\0\0")
        cache = self.cache()
        hit, rebuilt = self.build(cache)
        self.assertFalse(hit)
        self.assertEqual(cache.counts["corrupt"], 1)
        self.assertEqual(rebuilt, good)
        self.assertTrue(self.build(self.cache())[0])

    def test_relative_command_is_never_cached_and_a_failure_is_never_stored(self):
        cache = self.cache()
        output = str(self.base / "rel.o")
        cache.compile([GCC, "-Iinc", "-c", "-o", output, self.source], output, self.source, _env(),
                      lambda argv: _run(argv[:1] + ["-I", str(self.inc)] + argv[2:]), str(self.scratch),
                      self.identity, "test")
        self.assertEqual(cache.counts["uncached"], 1)
        support.write(self.source, "int broken(void) { return }\n")
        for _ in range(2):
            with self.assertRaises(RuntimeError):
                self.build(cache)
        self.assertEqual(cache.counts["stores"], 0)

    def test_tool_output_is_cached_by_object_digest(self):
        _hit, _data = self.build(self.cache())
        output = str(self.base / ("out_%d.o" % self.outputs))
        calls = []

        def nm(argv):
            calls.append(argv)
            return _run(argv)
        first = self.cache().tool_output([NM, "--defined-only", output], output, nm)
        second = self.cache().tool_output([NM, "--defined-only", output], output, nm)
        self.assertEqual(first, second)
        self.assertIn("unit_value", first)
        self.assertEqual(len(calls), 1)

    def test_concurrent_writers_in_threads_and_processes(self):
        results, errors = [], []

        def writer(index):
            try:
                output = str(self.base / ("thread_%d.o" % index))
                cache = self.cache()
                cache.compile(self.command(output), output, self.source, _env(), _run, str(self.scratch),
                              self.identity, "test")
                with open(output, "rb") as handle:
                    results.append(handle.read())
            except Exception as error:  # noqa: BLE001 - reported below
                errors.append(error)
        threads = [threading.Thread(target=writer, args=(index,)) for index in range(8)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        self.assertEqual(errors, [])
        self.assertEqual(len(set(results)), 1)
        script = support.write(self.base / "writer.py", WRITER_SCRIPT)
        children = [subprocess.Popen([sys.executable, "-I", "-B", str(script), str(support.PACKAGE_ROOT), GCC,
                                      self.root, str(support.REPO_ROOT), self.source, str(self.inc),
                                      str(self.base / ("process_%d.o" % index)), str(self.scratch)],
                                     stdout=subprocess.PIPE, stderr=subprocess.PIPE, stdin=subprocess.DEVNULL,
                                     creationflags=process.window_flags())
                    for index in range(4)]
        for child in children:
            out, err = child.communicate(timeout=120)
            self.assertEqual(child.returncode, 0, err.decode("utf-8", "replace"))
        for index in range(4):
            self.assertEqual((self.base / ("process_%d.o" % index)).read_bytes(),
                             (self.base / "process_0.o").read_bytes())
        leftovers = [n for _, _, names in os.walk(self.root) for n in names if n.endswith(".tmp")]
        self.assertEqual(leftovers, [])
        self.assertTrue(self.build(self.cache())[0])

    def test_prune_evicts_old_and_least_recently_used_entries_through_99_trash(self):
        def entries():
            return {os.path.join(d, n) for d, _, names in os.walk(os.path.join(self.root, "entries")) for n in names}
        self.build(self.cache())
        first = entries()
        support.write(self.source, '#include "unit.h"\nint unit_value(void) { return 2; }\n')
        self.build(self.cache())
        self.assertEqual(len(entries()), 2)
        old = time.time() - 30 * 86400
        stale, = first
        os.utime(stale, (old, old))
        counts = cache_module.prune(self.root, str(support.REPO_ROOT), max_age_days=14)
        self.assertEqual(counts["evicted_entries"], 1)
        remaining = [n for _, _, names in os.walk(os.path.join(self.root, "entries")) for n in names]
        self.assertEqual(len(remaining), 1)
        blobs = [n for _, _, names in os.walk(os.path.join(self.root, "blobs")) for n in names]
        self.assertEqual(len(blobs), 1)
        self.assertTrue(self.build(self.cache())[0])
        counts = cache_module.prune(self.root, str(support.REPO_ROOT), max_bytes=0)
        self.assertEqual((counts["evicted_entries"], counts["kept_bytes"]), (1, 0))
        self.assertFalse(self.build(self.cache())[0])
        leftovers = [n for n in os.listdir(support.REPO_ROOT / "99_trash") if n.startswith("object_cache_prune-")] \
            if (support.REPO_ROOT / "99_trash").is_dir() else []
        self.assertEqual(leftovers, [])

    def test_a_linked_cache_root_is_refused(self):
        target = self.base / "real"
        target.mkdir()
        link = self.base / "linked"
        try:
            os.symlink(str(target), str(link), target_is_directory=True)
        except (OSError, NotImplementedError):
            if os.name != "nt":
                self.skipTest("symbolic links unavailable")
            done = subprocess.run(["cmd", "/c", "mklink", "/J", str(link), str(target)], capture_output=True,
                                  stdin=subprocess.DEVNULL, creationflags=process.window_flags())
            if done.returncode != 0:
                self.skipTest("junctions unavailable")
        try:
            with self.assertRaises(cache_module.CacheError):
                cache_module.ObjectCache(str(link / "cache"), str(support.REPO_ROOT))
        finally:
            os.rmdir(str(link)) if os.name == "nt" else os.unlink(str(link))


WRITER_SCRIPT = '''import importlib, importlib.util, os, subprocess, sys
package_root, gcc, root, repo, source, inc, output, scratch = sys.argv[1:]
spec = importlib.util.spec_from_file_location("comsect1_maint", os.path.join(package_root, "__init__.py"),
                                              submodule_search_locations=[package_root])
package = importlib.util.module_from_spec(spec)
sys.modules["comsect1_maint"] = package
spec.loader.exec_module(package)
cache_module = importlib.import_module("comsect1_maint.object_cache")
env = dict(os.environ, PATH=os.path.dirname(gcc) + os.pathsep + os.environ.get("PATH", ""))
def run(argv):
    done = subprocess.run(argv, env=env, capture_output=True, stdin=subprocess.DEVNULL)
    if done.returncode:
        raise SystemExit(done.stderr.decode())
identity = cache_module.toolchain_identity(gcc, env)
cache = cache_module.ObjectCache(root, repo)
early = os.path.join(os.path.dirname(inc), "early")
command = [gcc, "-std=c17", "-O2", "-ffunction-sections", "-fno-ident", "-I", early, "-I", inc, "-c", "-o", output, source]
cache.compile(command, output, source, env, run, scratch, identity, "test")
'''


if __name__ == "__main__":
    unittest.main()
