#!/usr/bin/env python3
import os
import sys
import tarfile
import shutil
import urllib.request

DB = "/var/lib/fpkg"
REPO_URL = "https://codeberg.org/Decaying/fpkg-repository/src/branch/main/repo.db"
TMP_BASE = "/tmp/fpkg"


# ---------------------------
# utils
# ---------------------------

def ensure_dirs():
    os.makedirs(DB, exist_ok=True)
    os.makedirs(TMP_BASE, exist_ok=True)


def safe_rmtree(path):
    real_base = os.path.realpath(TMP_BASE)
    real_path = os.path.realpath(path)

    if real_path.startswith(real_base) and os.path.lexists(real_path):
        shutil.rmtree(real_path, ignore_errors=True)


# ---------------------------
# repo
# ---------------------------
    REPOS_CONF = "/etc/fpkg/repos.conf"
def load_repo():
    repo = {}

    with open(REPOS_CONF) as conf:
        for repo_url in conf:
            repo_url = repo_url.strip()

            if not repo_url:
                continue

            repo_file = f"{TMP_BASE}/repo.db"

            urllib.request.urlretrieve(repo_url, repo_file)

            with open(repo_file) as f:
                for line in f:
                    parts = line.strip().split()

                    if len(parts) < 3:
                        continue

                    name, ver, url = parts[:3]
                    repo[name] = (ver, url)

    return repo


# ---------------------------
# metadata
# ---------------------------

def read_metadata(path):
    meta = {}
    info_path = os.path.join(path, "FPKGINFO")

    if os.path.lexists(info_path):
        with open(info_path) as f:
            for line in f:
                line = line.strip()
                if "=" in line:
                    k, v = line.split("=", 1)
                    meta[k.strip()] = v.strip()

    return meta


# ---------------------------
# dependency graph
# ---------------------------

def build_graph(pkg, repo, graph=None):
    if graph is None:
        graph = {}

    if pkg in graph:
        return graph

    if pkg not in repo:
        graph[pkg] = []
        return graph

    ver, url = repo[pkg]

    tmp = f"{TMP_BASE}/{pkg}.fpkg"
    urllib.request.urlretrieve(url, tmp)

    tmp_dir = f"{TMP_BASE}/{pkg}_tmp"
    safe_rmtree(tmp_dir)
    os.makedirs(tmp_dir, exist_ok=True)

    with tarfile.open(tmp, "r:gz") as t:
        t.extractall(tmp_dir)

    meta = read_metadata(tmp_dir)

    deps_raw = meta.get("depends", "")
    deps = [d.strip() for d in deps_raw.split(",") if d.strip()] if deps_raw else []

    graph[pkg] = deps

    # cleanup temp graph extraction
    safe_rmtree(tmp_dir)
    os.remove(tmp)

    for dep in deps:
        build_graph(dep, repo, graph)

    return graph


# ---------------------------
# topo sort
# ---------------------------

def topo_sort(graph):
    visited = set()
    stack = []

    def visit(node):
        if node in visited:
            return
        visited.add(node)

        for dep in graph.get(node, []):
            visit(dep)

        stack.append(node)

    for node in graph:
        visit(node)

    return stack[::-1]


# ---------------------------
# install
# ---------------------------

def install_package(pkg, repo):
    if pkg not in repo:
        print("missing package:", pkg)
        return

    ver, url = repo[pkg]
    out = f"{TMP_BASE}/{pkg}.fpkg"

    urllib.request.urlretrieve(url, out)

    tmp = f"{TMP_BASE}/{pkg}_install"
    safe_rmtree(tmp)
    os.makedirs(tmp, exist_ok=True)

    with tarfile.open(out, "r:gz") as t:
        t.extractall(tmp)

    meta = read_metadata(tmp)
    name = meta.get("name", pkg)

    installed_files = []

    for root, _, files in os.walk(tmp):
        for f in files:
            src = os.path.join(root, f)
            rel = os.path.relpath(src, tmp)

            if rel == "FPKGINFO":
                continue

            dst = "/" + rel
            os.makedirs(os.path.dirname(dst), exist_ok=True)

            if os.path.islink(src):
                target = os.readlink(src)

                if os.path.lexists(dst):
                    os.remove(dst)

                os.symlink(target, dst)
            else:
                shutil.copy2(src, dst)

            installed_files.append(dst)

    pkg_db = f"{DB}/{name}"
    os.makedirs(pkg_db, exist_ok=True)

    with open(f"{pkg_db}/files.txt", "w") as f:
        f.write("\n".join(installed_files))

    with open(f"{pkg_db}/info", "w") as f:
        for k, v in meta.items():
            f.write(f"{k}={v}\n")

    safe_rmtree(tmp)
    os.remove(out)

    print(f"installed {name}")

# ---------------------------
# add
# ---------------------------

def add(name):
    repo = load_repo()

    if name not in repo:
        print("package not found:", name)
        return

    graph = build_graph(name, repo)
    order = topo_sort(graph)

    for pkg in order:
        if os.path.lexists(f"{DB}/{pkg}"):
            continue

        install_package(pkg, repo)


# ---------------------------
# del
# ---------------------------

def del_pkg(name):
    path = f"{DB}/{name}/files.txt"

    if not os.path.lexists(path):
        print("not installed")
        return

    with open(path) as f:
        for file in f.read().splitlines():
            try:
                os.remove(file)
            except FileNotFoundError:
                pass

    shutil.rmtree(f"{DB}/{name}", ignore_errors=True)
    print("removed", name)


# ---------------------------
# info
# ---------------------------

def info(name):
    path = f"{DB}/{name}/info"

    if not os.path.lexists(path):
        print("not installed")
        return

    with open(path) as f:
        print(f.read())


# ---------------------------
# update
# ---------------------------

def update():
    urllib.request.urlretrieve(REPO_URL, f"{TMP_BASE}/repo.db")
    print("repo updated")


# ---------------------------
# upgrade
# ---------------------------

def upgrade():
    repo = {}

    repo_file = f"{TMP_BASE}/repo.db"
    if not os.path.lexists(repo_file):
        update()

    with open(repo_file) as f:
        for line in f:
            parts = line.strip().split()
            if len(parts) < 3:
                continue
            name, ver, url = parts[:3]
            repo[name] = ver

    for pkg in os.listdir(DB):
        info_path = f"{DB}/{pkg}/info"
        if not os.path.lexists(info_path):
            continue

        meta = {}
        with open(info_path) as f:
            for line in f:
                if "=" in line:
                    k, v = line.strip().split("=", 1)
                    meta[k] = v

        local_ver = meta.get("version", "")
        remote_ver = repo.get(pkg)

        if remote_ver and remote_ver != local_ver:
            print(f"upgrading {pkg}")
            add(pkg)


# ---------------------------
# search
# ---------------------------

def search(query):
    repo_file = f"{TMP_BASE}/repo.db"
    urllib.request.urlretrieve(REPO_URL, repo_file)

    found = False

    with open(repo_file) as f:
        for line in f:
            parts = line.strip().split()
            if len(parts) < 3:
                continue

            name, ver, url = parts[:3]

            if query.lower() in name.lower():
                print(f"{name} {ver}")
                found = True

    if not found:
        print("no packages found")


# ---------------------------
# main
# ---------------------------

if __name__ == "__main__":
    ensure_dirs()

    if len(sys.argv) < 2:
        print("usage: fpkg <add|del|info|update|upgrade|search> [package]")
        sys.exit(1)

    cmd = sys.argv[1]
    name = sys.argv[2] if len(sys.argv) > 2 else None

    if cmd == "add":
        add(name)
    elif cmd == "del":
        del_pkg(name)
    elif cmd == "info":
        info(name)
    elif cmd == "update":
        update()
    elif cmd == "upgrade":
        upgrade()
    elif cmd == "search":
        search(name)
    else:
        print("unknown command:", cmd)