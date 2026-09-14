#!/usr/bin/env python3
"""Disposable native/container lock and SQLite WAL probe; no Codex state access.

Run: python3 investigations/codex-history/locking_probe.py
Requires an existing python:3.11-slim image and permission to run Apple Container.
Results and synthetic databases are retained in a new /private/tmp directory.
"""
import collections
import errno
import fcntl
import json
from pathlib import Path
import queue
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time


def emit(value):
    print(json.dumps(value), flush=True)


def worker(root, action, writer):
    root = Path(root)
    if action in ("hold", "contend"):
        handles, result = [], {}
        for name, operation in (("posix", fcntl.lockf), ("flock", fcntl.flock)):
            f = (root / (name + ".lock")).open("a+b")
            handles.append(f)
            try:
                operation(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
                result[name] = "acquired"
            except OSError as e:
                result[name] = "blocked" if e.errno in (errno.EACCES, errno.EAGAIN) else str(e)
        c = sqlite3.connect(root / "probe.sqlite", timeout=0)
        try:
            c.execute("BEGIN IMMEDIATE")
            result["sqlite_writer"] = "acquired"
        except sqlite3.Error as e:
            result["sqlite_writer"] = str(e)
        emit({"status": "ready", "locks": result, "sqlite_version": sqlite3.sqlite_version})
        if action == "hold":
            sys.stdin.readline()
        c.rollback()
        c.close()
        for f in handles:
            f.close()
        return

    c = sqlite3.connect(root / "probe.sqlite", timeout=0.3)
    c.execute("PRAGMA synchronous=FULL")
    emit({"status": "ready", "sqlite_version": sqlite3.sqlite_version})
    sys.stdin.readline()
    committed, errors = [], collections.Counter()
    for n in range(150):
        key = writer + ":" + str(n)
        try:
            c.execute("BEGIN IMMEDIATE")
            c.execute("INSERT INTO entries VALUES (?, ?)", (key, "x" * 500))
            c.commit()
            committed.append(key)
        except sqlite3.Error as e:
            errors[str(e)] += 1
            c.rollback()
        time.sleep(0.001)
    c.close()
    emit({"committed": committed, "errors": dict(errors)})


class Process:
    def __init__(self, cmd, log, name=None):
        self.name = name
        self.log = open(log, "w")
        self.p = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                  stderr=self.log, text=True, bufsize=1)
        self.q = queue.Queue()
        def read():
            for line in self.p.stdout:
                self.q.put(line)
            self.q.put(None)
        threading.Thread(target=read, daemon=True).start()

    def receive(self):
        deadline = time.monotonic() + 55
        while time.monotonic() < deadline:
            line = self.q.get(timeout=max(0.01, deadline - time.monotonic()))
            if line is None:
                raise RuntimeError("worker exited; inspect " + self.log.name)
            try:
                return json.loads(line)
            except json.JSONDecodeError:
                pass
        raise TimeoutError("worker did not respond")

    def send(self):
        self.p.stdin.write("continue\n")
        self.p.stdin.flush()

    def close(self):
        if self.p.poll() is None:
            try:
                self.send()
                self.p.wait(timeout=8)
            except (BrokenPipeError, subprocess.TimeoutExpired):
                if self.name:
                    subprocess.run(["container", "stop", self.name], timeout=15,
                                   stdout=self.log, stderr=self.log)
                self.p.terminate()
                self.p.wait(timeout=5)
        self.log.close()


def main():
    root = Path(tempfile.mkdtemp(prefix="codex-boundary-probe-", dir="/private/tmp"))
    shutil.copyfile(__file__, root / "worker.py")
    results = {"directory": str(root), "lock_cases": {}, "write_cases": {}}
    print("Disposable data:", root, flush=True)
    processes = []
    def start(location, action, folder, label):
        if location == "host":
            cmd = [sys.executable, str(root / "worker.py"), "--worker", str(folder), action, label]
            name = None
        else:
            name = root.name + "-" + label
            cmd = ["container", "run", "--rm", "-i", "--name", name,
                   "--mount", "type=bind,src=" + str(root) + ",dst=/probe",
                   "python:3.11-slim", "python3", "/probe/worker.py", "--worker",
                   "/probe/" + folder.name, action, label]
        p = Process(cmd, root / (label + ".stderr"), name)
        processes.append(p)
        return p
    def initialize(name):
        folder = root / name
        folder.mkdir()
        with sqlite3.connect(folder / "probe.sqlite") as c:
            c.execute("PRAGMA journal_mode=WAL")
            c.execute("CREATE TABLE entries (id TEXT PRIMARY KEY, payload TEXT)")
        c.close()
        return folder
    try:
        for a, b in [("host", "host"), ("host", "container"),
                     ("container", "host"), ("container", "container")]:
            name = a + "-" + b
            folder = initialize("locks-" + name)
            holder = start(a, "hold", folder, name + "-holder")
            held = holder.receive()
            if set(held["locks"].values()) != {"acquired"}:
                raise RuntimeError("holder failed: " + str(held))
            contender = start(b, "contend", folder, name + "-contender")
            attempt = contender.receive()
            contender.close()
            holder.close()
            results["lock_cases"][name] = {"holder": held, "contender": attempt}
            print("Locks", name, attempt, flush=True)
        for a, b in [("host", "host"), ("host", "container"), ("container", "container")]:
            name = a + "-" + b
            folder = initialize("writes-" + name)
            first = start(a, "write", folder, name + "-writer-a")
            first.receive()
            second = start(b, "write", folder, name + "-writer-b")
            second.receive()
            first.send()
            second.send()
            outcomes = [first.receive(), second.receive()]
            first.close()
            second.close()
            expected = set().union(*(set(x["committed"]) for x in outcomes))
            check = {"reported_commits": len(expected), "writer_errors": [x["errors"] for x in outcomes]}
            try:
                with sqlite3.connect(folder / "probe.sqlite") as c:
                    check["integrity_check"] = c.execute("PRAGMA integrity_check").fetchall()
                    actual = {r[0] for r in c.execute("SELECT id FROM entries")}
                    check["stored_rows"] = len(actual)
                    check["missing_acknowledged_commits"] = sorted(expected - actual)
                    check["unexpected_rows"] = sorted(actual - expected)
                c.close()
            except sqlite3.Error as e:
                check["read_error"] = str(e)
            results["write_cases"][name] = check
            display = dict(check)
            if 'missing_acknowledged_commits' in display:
                display['missing_acknowledged_commits'] = len(display['missing_acknowledged_commits'])
            print("Writes", name, json.dumps(display), flush=True)
    finally:
        for p in processes:
            if not p.log.closed:
                p.close()
        (root / "results.json").write_text(json.dumps(results, indent=2) + "\n")
        print("Results:", root / "results.json", flush=True)


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--worker":
        worker(*sys.argv[2:])
    else:
        main()
